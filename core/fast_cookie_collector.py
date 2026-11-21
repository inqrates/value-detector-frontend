# core/fast_cookie_collector.py
"""
Сбор cookies для fast БК через AdsPower-профиль.

Логика:
  1. Форсим остановку профиля (если запущен) через AdsPower API.
  2. Запускаем профиль заново — HEADFUL (headless=False) по умолчанию,
     иначе Qrator/антиботы палят режим и не пропускают challenge.
  3. Для каждой БК:
     - открываем новую вкладку
     - идём на live-раздел
     - ждём прохождения challenge (наличие qrator_jsr, непустой body,
       нормальный title) + симулируем активность (мышь, скролл)
     - снимаем cookies через context.cookies()
     - сохраняем в %APPDATA%/ValueDetectorPro/fast_cookies/{bk}.json
     - закрываем вкладку
  4. Playwright закрываем, но профиль AdsPower НЕ останавливаем —
     при следующем сборе стартует быстрее.

ВАЖНО:
  - clear_cookies() перед каждой БК — чтобы cookies одной БК не попали
    в файл другой. AdsPower-профиль в этом сценарии специально
    авторизуется только на самих БК, поэтому побочных эффектов нет.
  - Для БК, защищённых Qrator (ligastavok), сбор считается успешным
    ТОЛЬКО если появился qrator_jsr — иначе файл бесполезен,
    страницы будут отдавать пустой body (проверено 2026-10-16).
"""
import asyncio
import logging
import time
from typing import Callable, Optional, Dict

import aiohttp

from core.adspower_browser import AdsPowerBrowser
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

# ── БК, для которых критично пройти Qrator (или аналогичный антибот) ──
QRATOR_PROTECTED_BKS = {"ligastavok"}
QRATOR_COOKIE_MARKERS = ("qrator_jsr", "qrator_jsid", "qrator_ssid")

# ── Таймауты ожидания готовности (секунды) ──
MAX_WAIT_PLAIN_SEC = 25      # для обычных БК
MAX_WAIT_QRATOR_SEC = 60     # для Qrator-защищённых (challenge может долго решаться)

# ── Минимум cookies для "успешного" сбора ──
MIN_COOKIES_PLAIN = 3

# ── Подстроки в title, которые означают "ещё на challenge/заблокировано" ──
BLOCKED_TITLE_MARKERS = (
    "проверка",
    "qrator",
    "challenge",
    "just a moment",
    "ddos-guard",
    "проверяем",
    "безопасности",
)

ADSPOWER_API_URL = "http://localhost:50325"


async def _force_stop_profile(profile_id: str, api_key: str) -> bool:
    """
    Пытается остановить профиль (если запущен).
    Игнорирует ошибки — если профиль не запущен, это не проблема.
    """
    url = f"{ADSPOWER_API_URL}/api/v2/browser-profile/stop"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"profile_id": profile_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload, timeout=10
            ) as resp:
                data = await resp.json()
                return data.get("code") == 0
    except Exception:
        return False


class FastCookieCollector:
    """
    Один экземпляр = один запуск сбора cookies.

    Использование:
        collector = FastCookieCollector(
            profile_id="k1abc",
            api_key="...",
            headless=False,     # ← рекомендуется False: Qrator палит headless
            progress_cb=print,
        )
        status = await collector.collect_all(["fonbet", "ligastavok"])
    """

    def __init__(
        self,
        profile_id: str,
        api_key: str,
        headless: bool = False,   # ← ФИКС: было True, теперь False
        progress_cb: Optional[Callable[[str], None]] = None,
    ):
        self.profile_id = (profile_id or "").strip()
        self.api_key = (api_key or "").strip()
        self.headless = headless
        self._progress_cb = progress_cb or (lambda msg: None)

    def _progress(self, msg: str):
        logger.info(f"[fast_collector] {msg}")
        try:
            self._progress_cb(msg)
        except Exception:
            pass

    async def collect_all(self, bks: list) -> Dict[str, str]:
        """
        Возвращает {bk: "ok" | "error: ..."}.
        """
        bks = [b.lower().strip() for b in bks if b]
        if not bks:
            return {}
        if not self.profile_id or not self.api_key:
            return {bk: "error: нет profile_id или api_key" for bk in bks}

        # ── 1. Форсим остановку, если профиль уже был запущен ──
        self._progress("🔄 Подготовка профиля AdsPower...")
        await _force_stop_profile(self.profile_id, self.api_key)
        await asyncio.sleep(1)

        # ── 2. Запускаем ──
        self._progress(
            f"🚀 Запуск профиля {self.profile_id} "
            f"(headless={self.headless})..."
        )
        if self.headless:
            logger.warning(
                "[fast_collector] ⚠️ headless=True — Qrator и другие "
                "антиботы могут не пропустить. Рекомендуется headless=False."
            )

        wrapper = AdsPowerBrowser(
            profile_id=self.profile_id,
            api_key=self.api_key,
            api_url=ADSPOWER_API_URL,
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
                    logger.warning(
                        f"[fast_collector] {bk}: {e}", exc_info=True
                    )
                    status[bk] = f"error: {e}"
                    self._progress(f"  ❌ {bk}: {e}")

        finally:
            # Профиль НЕ останавливаем — при следующем сборе быстрее.
            # Но Playwright и HTTP-сессию надо закрыть.
            await self._close_playwright_only(wrapper)

        return status

    async def _collect_one(
        self, wrapper: AdsPowerBrowser, bk: str
    ) -> tuple[bool, str]:
        """
        Возвращает (ok, reason).

        ok=True  — cookies собраны и (для Qrator-БК) challenge пройден
        ok=False — reason содержит причину
        """
        url = LIVE_URLS.get(bk)
        if not url:
            return False, f"Нет URL для {bk}"

        page = await wrapper.new_page()
        try:
            # Чистим cookies ПЕРЕД каждой БК, чтобы в её файл не попали
            # чужие домены из предыдущих итераций (Betcity, Leon, Marathon).
            try:
                await page.context.clear_cookies()
                logger.info(f"[fast_collector] {bk}: cookies очищены")
            except Exception as e:
                logger.warning(f"[fast_collector] {bk}: clear_cookies: {e}")

            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=30000
                )
            except Exception as e:
                return False, f"goto: {e}"

            # ── Ждём реальной готовности (Qrator-прохождение, body) ──
            ready = await self._wait_for_ready(page, bk)
            if not ready:
                # Возвращаем что есть + причина
                try:
                    cookies = await page.context.cookies()
                except Exception:
                    cookies = []
                names = {(c.get("name") or "").lower() for c in cookies}
                if bk in QRATOR_PROTECTED_BKS and not any(
                    n in names for n in QRATOR_COOKIE_MARKERS
                ):
                    return False, (
                        f"Qrator не пройден "
                        f"(cookies={len(cookies)}, нет qrator_*)"
                    )
                return False, f"не готово (cookies={len(cookies)})"

            # ── Собираем cookies ──
            cookies = await page.context.cookies()
            names = {(c.get("name") or "").lower() for c in cookies}

            # Для Qrator-защищённых — требуем qrator_jsr
            if bk in QRATOR_PROTECTED_BKS:
                if not any(n in names for n in QRATOR_COOKIE_MARKERS):
                    logger.warning(
                        f"[fast_collector] {bk}: Qrator-маркеры не найдены "
                        f"в {len(cookies)} cookies: {sorted(names)[:10]}"
                    )
                    return False, (
                        f"нет qrator_* (cookies={len(cookies)})"
                    )
            else:
                if len(cookies) < MIN_COOKIES_PLAIN:
                    logger.warning(
                        f"[fast_collector] {bk}: мало cookies ({len(cookies)})"
                    )
                    return False, f"мало cookies ({len(cookies)})"

            logger.info(
                f"[fast_collector] {bk}: собрано {len(cookies)} cookies"
            )
            save_cookies(bk, cookies)
            return True, "ok"

        finally:
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass

    async def _wait_for_ready(self, page, bk: str) -> bool:
        """
        Ждёт пока страница реально загрузится и (для Qrator-БК) пройдёт
        challenge.

        Признаки готовности:
          - title не содержит маркеров блокировки (проверка/qrator/…)
          - body содержит достаточно текста
          - для Qrator-БК — наличие cookie qrator_jsr

        Возвращает True если готово за отведённый таймаут.
        """
        needs_qrator = bk in QRATOR_PROTECTED_BKS
        max_timeout = MAX_WAIT_QRATOR_SEC if needs_qrator else MAX_WAIT_PLAIN_SEC

        start = time.monotonic()
        last_logged = 0.0

        while True:
            elapsed = time.monotonic() - start

            # ── Симулируем активность (каждые ~1.5с) ──
            # Qrator и другие антиботы любят живое поведение
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

            # ── Проверяем cookies ──
            try:
                cookies = await page.context.cookies()
            except Exception:
                cookies = []
            names = {(c.get("name") or "").lower() for c in cookies}
            has_qrator_jsr = "qrator_jsr" in names
            has_qrator_any = any(n in names for n in QRATOR_COOKIE_MARKERS)

            # ── Проверяем title ──
            try:
                title = (await page.title()) or ""
            except Exception:
                title = ""
            title_low = title.lower()
            title_blocked = any(m in title_low for m in BLOCKED_TITLE_MARKERS)

            # ── Проверяем body ──
            try:
                body_text = (await page.locator("body").inner_text()) or ""
                body_len = len(body_text)
            except Exception:
                body_len = 0

            # ── Условия готовности ──
            if needs_qrator:
                if has_qrator_jsr and not title_blocked and body_len > 500:
                    logger.info(
                        f"[fast_collector] {bk}: Qrator пройден "
                        f"({elapsed:.1f}с, cookies={len(cookies)}, "
                        f"body={body_len}, title={title!r})"
                    )
                    return True
            else:
                if (
                    len(cookies) >= MIN_COOKIES_PLAIN
                    and not title_blocked
                    and body_len > 200
                ):
                    logger.info(
                        f"[fast_collector] {bk}: готов "
                        f"({elapsed:.1f}с, cookies={len(cookies)}, "
                        f"body={body_len})"
                    )
                    return True

            # ── Логируем прогресс раз в 5 секунд ──
            if elapsed - last_logged >= 5.0:
                last_logged = elapsed
                logger.info(
                    f"[fast_collector] {bk}: ждём "
                    f"({elapsed:.0f}с/{max_timeout}с, "
                    f"cookies={len(cookies)}, "
                    f"qrator_jsr={has_qrator_jsr}, "
                    f"qrator_any={has_qrator_any}, "
                    f"title={title[:60]!r}, body={body_len})"
                )

            # ── Таймаут ──
            if elapsed >= max_timeout:
                logger.warning(
                    f"[fast_collector] {bk}: ТАЙМАУТ "
                    f"({elapsed:.1f}с, cookies={len(cookies)}, "
                    f"qrator_jsr={has_qrator_jsr}, "
                    f"title={title!r}, body={body_len})"
                )
                return False

            await asyncio.sleep(1.0)

    async def _close_playwright_only(self, wrapper: AdsPowerBrowser):
        """
        Закрывает Playwright, но НЕ шлёт AdsPower API stop —
        профиль остаётся запущенным для следующих сборов.
        """
        try:
            if wrapper._playwright is not None:
                await wrapper._playwright.stop()
                wrapper._playwright = None
        except Exception as e:
            logger.debug(f"playwright.stop: {e}")

        try:
            if wrapper._session is not None:
                await wrapper._session.close()
                wrapper._session = None
        except Exception as e:
            logger.debug(f"session.close: {e}")

        # Обнуляем ссылки, чтобы wrapper точно не пытался что-то ещё делать
        wrapper._browser = None
        wrapper._context = None
        wrapper._page = None