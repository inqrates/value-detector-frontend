"""
Сбор cookies для fast БК через локальный профиль (Patchright).
Логика:
Для каждой БК:
  - открываем новую вкладку в запущенном профиле
  - идём на live-раздел
  - ждём прохождения challenge (наличие qrator_jsr, непустой body)
  - снимаем cookies через context.cookies()
  - сохраняем в %APPDATA%/ValueDetectorPro/fast_cookies/{bk}.json
  - закрываем вкладку
Playwright закрываем после сбора.
"""
import asyncio
import logging
import time
from typing import Callable, Optional, Dict

from core.browser_launcher import PatchrightBrowser
from core.fast_config import save_cookies

logger = logging.getLogger(__name__)

# Live-раздел каждой БК — отсюда надёжно ставятся cookies
LIVE_URLS: Dict[str, str] = {
    "fonbet":     "https://fon.bet/live",
    "pari":       "https://pari.ru/live",
    "olimp":      "https://www.olimp.bet/live",
    "sportbet":   "https://sportbet.ru/live?isTime=1",
    "betcity":    "https://betcity.ru/ru/live",
    "leon":       "https://leon.ru/live",
    "marathon":   "https://new.marathonbet.ru/su/live",
    "ligastavok": "https://www.ligastavok.ru/live",
    "winline":    "https://winline.ru/live",
    "zenit":      "https://zenit.win/live",
}

# БК, для которых критично пройти Qrator (или аналогичный антибот)
QRATOR_PROTECTED_BKS = {"ligastavok"}
QRATOR_COOKIE_MARKERS = ("qrator_jsr", "qrator_jsid", "qrator_ssid")

# Таймауты ожидания готовности (секунды)
MAX_WAIT_PLAIN_SEC = 25      # для обычных БК
MAX_WAIT_QRATOR_SEC = 60     # для Qrator-защищённых

# Минимум cookies для "успешного" сбора
MIN_COOKIES_PLAIN = 3

# Подстроки в title, которые означают "ещё на challenge/заблокировано"
BLOCKED_TITLE_MARKERS = (
    "проверка", "qrator", "challenge", "just a moment",
    "ddos-guard", "проверяем", "безопасности",
)


class FastCookieCollector:
    """
    Один экземпляр = один запуск сбора cookies.
    Использование:
        collector = FastCookieCollector(
            profile_id="zenit_1",
            headless=True,      # окно браузера не показывается пользователю
            progress_cb=print,
        )
        status = await collector.collect_all(["fonbet", "ligastavok"])
    """
    def __init__(
        self,
        profile_id: str,
        headless: bool = True,
        progress_cb: Optional[Callable[[str], None]] = None,
    ):
        self.profile_id = (profile_id or "").strip()
        self.headless = headless
        self._progress_cb = progress_cb or (lambda msg: None)

    def _progress(self, msg: str):
        logger.info(f"[fast_collector] {msg}")
        try:
            self._progress_cb(msg)
        except Exception:
            pass

    async def collect_all(self, bks: list) -> Dict[str, str]:
        """Возвращает {bk: "ok" | "error: ..."}."""
        bks = [b.lower().strip() for b in bks if b]
        if not bks:
            return {}
        if not self.profile_id:
            return {bk: "error: нет profile_id" for bk in bks}

        self._progress(f"🚀 Запуск профиля {self.profile_id} (headless={self.headless})...")

        # Запускаем браузер через Patchright
        wrapper = PatchrightBrowser(
            profile_id=self.profile_id,
            headless=self.headless,
        )
        try:
            await wrapper.__aenter__()
        except Exception as e:
            self._progress(f"❌ Не удалось запустить профиль: {e}")
            return {bk: f"error: start: {e}" for bk in bks}

        status: Dict[str, str] = {}
        try:
            for i, bk in enumerate(bks, 1):
                self._progress(f"[{i}/{len(bks)}] {bk}: собираю cookies...")
                try:
                    ok, reason = await self._collect_one(wrapper, bk)
                    status[bk] = "ok" if ok else f"error: {reason}"
                    if ok:
                        self._progress(f"  ✅ {bk}: готово")
                    else:
                        self._progress(f"  ⚠️ {bk}: {reason}")
                except Exception as e:
                    logger.warning(f"[fast_collector] {bk}: {e}", exc_info=True)
                    status[bk] = f"error: {e}"
                    self._progress(f"  ❌ {bk}: {e}")
        finally:
            # Закрываем браузер и освобождаем ресурсы
            await wrapper.stop()
            
        return status

    async def _collect_one(self, wrapper: PatchrightBrowser, bk: str) -> tuple:
        """Возвращает (ok, reason)."""
        url = LIVE_URLS.get(bk)
        if not url:
            return False, f"Нет URL для {bk}"

        page = await wrapper.new_page()
        try:
            # Чистим cookies ПЕРЕД каждой БК
            try:
                await page.context.clear_cookies()
                logger.info(f"[fast_collector] {bk}: cookies очищены")
            except Exception as e:
                logger.warning(f"[fast_collector] {bk}: clear_cookies: {e}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                return False, f"goto: {e}"

            # Ждём реальной готовности (Qrator-прохождение, body)
            ready = await self._wait_for_ready(page, bk)
            if not ready:
                try:
                    cookies = await page.context.cookies()
                except Exception:
                    cookies = []
                names = {(c.get("name") or "").lower() for c in cookies}
                if bk in QRATOR_PROTECTED_BKS and not any(n in names for n in QRATOR_COOKIE_MARKERS):
                    return False, f"Qrator не пройден (cookies={len(cookies)}, нет qrator_*)"
                return False, f"не готово (cookies={len(cookies)})"

            # Собираем cookies
            cookies = await page.context.cookies()
            names = {(c.get("name") or "").lower() for c in cookies}

            # Для Qrator-защищённых — требуем qrator_jsr
            if bk in QRATOR_PROTECTED_BKS:
                if not any(n in names for n in QRATOR_COOKIE_MARKERS):
                    logger.warning(f"[fast_collector] {bk}: Qrator-маркеры не найдены в {len(cookies)} cookies")
                    return False, f"нет qrator_* (cookies={len(cookies)})"
            else:
                if len(cookies) < MIN_COOKIES_PLAIN:
                    logger.warning(f"[fast_collector] {bk}: мало cookies ({len(cookies)})")
                    return False, f"мало cookies ({len(cookies)})"

            logger.info(f"[fast_collector] {bk}: собрано {len(cookies)} cookies")
            save_cookies(bk, cookies)
            return True, "ok"
        finally:
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass

    async def _wait_for_ready(self, page, bk: str) -> bool:
        """Ждёт пока страница реально загрузится и пройдёт challenge."""
        needs_qrator = bk in QRATOR_PROTECTED_BKS
        max_timeout = MAX_WAIT_QRATOR_SEC if needs_qrator else MAX_WAIT_PLAIN_SEC
        start = time.monotonic()
        last_logged = 0.0

        while True:
            elapsed = time.monotonic() - start
            
            # Симулируем активность (каждые ~1.5с)
            try:
                x = 200 + int((elapsed * 137) % 900)
                y = 150 + int((elapsed * 91) % 500)
                await page.mouse.move(x, y)
            except Exception:
                pass
            try:
                y_scroll = int((elapsed * 150) % 1200)
                await page.evaluate(f"window.scrollTo(0, {y_scroll})")
            except Exception:
                pass

            # Проверяем cookies
            try:
                cookies = await page.context.cookies()
            except Exception:
                cookies = []
            names = {(c.get("name") or "").lower() for c in cookies}
            has_qrator_jsr = "qrator_jsr" in names
            has_qrator_any = any(n in names for n in QRATOR_COOKIE_MARKERS)

            # Проверяем title
            try:
                title = (await page.title()) or ""
            except Exception:
                title = ""
            title_low = title.lower()
            title_blocked = any(m in title_low for m in BLOCKED_TITLE_MARKERS)

            # Проверяем body
            try:
                body_text = (await page.locator("body").inner_text()) or ""
                body_len = len(body_text)
            except Exception:
                body_len = 0

            # Условия готовности
            if needs_qrator:
                if has_qrator_jsr and not title_blocked and body_len > 500:
                    logger.info(f"[fast_collector] {bk}: Qrator пройден ({elapsed:.1f}с, cookies={len(cookies)}, body={body_len})")
                    return True
            else:
                if len(cookies) >= MIN_COOKIES_PLAIN and not title_blocked and body_len > 200:
                    logger.info(f"[fast_collector] {bk}: готов ({elapsed:.1f}с, cookies={len(cookies)}, body={body_len})")
                    return True

            # Логируем прогресс раз в 5 секунд
            if elapsed - last_logged >= 5.0:
                last_logged = elapsed
                logger.info(f"[fast_collector] {bk}: ждём ({elapsed:.0f}с/{max_timeout}с, cookies={len(cookies)}, qrator_jsr={has_qrator_jsr})")

            # Таймаут
            if elapsed >= max_timeout:
                logger.warning(f"[fast_collector] {bk}: ТАЙМАУТ ({elapsed:.1f}с, cookies={len(cookies)}, qrator_jsr={has_qrator_jsr})")
                return False

            await asyncio.sleep(1.0)