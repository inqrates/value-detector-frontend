# core/adspower_browser.py
import asyncio
import logging
from typing import Optional
import aiohttp
from playwright.async_api import async_playwright, Browser, Page

logger = logging.getLogger(__name__)


class AdsPowerBrowser:
    def __init__(
        self,
        profile_id: str,
        api_key: str,
        api_url: str = "http://localhost:50325",  # используем localhost
        headless: bool = False
    ):
        self.profile_id = profile_id
        self.api_key = api_key
        self.api_url = api_url.rstrip('/')
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._playwright = None
        self._context = None
        self._page: Optional[Page] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        if not self.profile_id:
            raise ValueError("❌ Не указан profile_id для AdsPower")
        if not self.api_key:
            raise ValueError("❌ Не указан API-ключ AdsPower")

        logger.info(f"🚀 Запуск профиля AdsPower {self.profile_id}")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        start_url = f"{self.api_url}/api/v2/browser-profile/start"
        payload = {
            "profile_id": self.profile_id,
            "headless": "1" if self.headless else "0",
            "last_opened_tabs": "1",
            "proxy_detection": "1",
            "cdp_mask": "1"
        }

        self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(start_url, headers=headers, json=payload) as resp:
                data = await resp.json()
                logger.debug(f"📦 Ответ AdsPower API: {data}")

                if data.get("code") != 0:
                    raise Exception(f"❌ Ошибка AdsPower API: {data.get('msg')}")

                ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
                if not ws_endpoint:
                    raise Exception("❌ Не получен WebSocket endpoint для Playwright")

                logger.debug(f"🔗 WebSocket endpoint: {ws_endpoint}")

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(ws_endpoint)

                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    pages = self._context.pages
                    self._page = pages[-1] if pages else await self._context.new_page()
                else:
                    self._context = await self._browser.new_context()
                    self._page = await self._context.new_page()

                await self._close_extra_tabs()

                if not self._page or self._page.is_closed():
                    self._page = await self._context.new_page()

                logger.info(f"✅ Браузер для профиля {self.profile_id} готов")
                return self

        except Exception as e:
            logger.error(f"❌ Ошибка запуска AdsPower: {e}")
            if self._session:
                await self._session.close()
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @property
    def page(self) -> Page:
        return self._page

    @property
    def browser(self) -> Browser:
        return self._browser

    async def stop(self):
        try:
            if self._session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                stop_url = f"{self.api_url}/api/v2/browser-profile/stop"
                payload = {"profile_id": self.profile_id}
                async with self._session.post(stop_url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ Профиль {self.profile_id} остановлен")
                    else:
                        logger.warning(f"⚠️ Не удалось остановить профиль: {resp.status}")
                await self._session.close()
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при остановке профиля AdsPower: {e}")

        if self._playwright:
            await self._playwright.stop()
        logger.info(f"🧹 Профиль AdsPower {self.profile_id} закрыт")

    async def _close_extra_tabs(self):
        if not self._context:
            return
        pages = self._context.pages
        if len(pages) > 1:
            logger.debug(f"🧹 Закрываем {len(pages) - 1} лишних вкладок...")
            for page in pages[:-1]:
                await page.close()