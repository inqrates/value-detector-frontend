# core/fast_cookie_collector.py
"""
Сбор cookies для fast БК через AdsPower-профиль.

Логика:
  1. Форсим остановку профиля (если запущен) через AdsPower API.
  2. Запускаем профиль заново.
  3. Для каждой БК:
     - открываем новую вкладку
     - идём на live-раздел
     - ждём 6 сек + скроллим (триггерим ленивые запросы)
     - снимаем cookies через context.cookies()
     - сохраняем в %APPDATA%/ValueDetectorPro/fast_cookies/{bk}.json
     - закрываем вкладку
  4. Playwright закрываем, но профиль AdsPower НЕ останавливаем —
     при следующем сборе стартует быстрее.
"""
import asyncio
import logging
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

# Сколько ждать после domcontentloaded, чтобы cookies дозаписались
WAIT_AFTER_LOAD_MS = 6000
SCROLL_WAIT_MS = 1500

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
            headless=True,
            progress_cb=print,
        )
        status = await collector.collect_all(["fonbet", "betcity"])
    """

    def __init__(
        self,
        profile_id: str,
        api_key: str,
        headless: bool = True,
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
                    ok = await self._collect_one(wrapper, bk)
                    status[bk] = "ok" if ok else "error: no cookies"
                    if ok:
                        self._progress(f"  ✅ {bk}: готово")
                    else:
                        self._progress(f"  ⚠️ {bk}: cookies не получены")
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

    async def _collect_one(self, wrapper: AdsPowerBrowser, bk: str) -> bool:
        url = LIVE_URLS.get(bk)
        if not url:
            raise ValueError(f"Нет URL для {bk}")

        page = await wrapper.new_page()
        try:
            # Чистим cookies ПЕРЕД каждой БК, чтобы в её файл не попали
            # чужие домены из предыдущих итераций (Betcity, Leon, Marathon).
            try:
                await page.context.clear_cookies()
                logger.info(f"[fast_collector] {bk}: cookies очищены")
            except Exception as e:
                logger.warning(f"[fast_collector] {bk}: clear_cookies: {e}")

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Для LigaStavok даём больше времени: нужен не только Qrator
            # challenge, но и первый API-запрос (auth/cookies).
            if bk == "ligastavok":
                await page.wait_for_timeout(10000)
                # Скроллим несколько раз, чтобы триггерить API
                for _ in range(3):
                    try:
                        await page.evaluate(
                            "window.scrollTo(0, document.body.scrollHeight)"
                        )
                        await page.wait_for_timeout(1500)
                        await page.evaluate("window.scrollTo(0, 0)")
                        await page.wait_for_timeout(1000)
                    except Exception:
                        pass
                # Даём ещё немного времени после скролла
                await page.wait_for_timeout(3000)
            else:
                await page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
                try:
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(SCROLL_WAIT_MS)
                except Exception:
                    pass

            cookies = await page.context.cookies()
            if not cookies:
                return False

            logger.info(f"[fast_collector] {bk}: собрано {len(cookies)} cookies")
            save_cookies(bk, cookies)
            return True
        finally:
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass

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