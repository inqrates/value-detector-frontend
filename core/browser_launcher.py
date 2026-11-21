"""
Запуск браузера через Patchright (вместо AdsPower).
Тот же интерфейс что у AdsPowerBrowser — замена прозрачная.

Установка:
    pip install patchright
    patchright install chromium
"""
import logging
from typing import Optional

from patchright.async_api import async_playwright, Page, BrowserContext

from core.profile_config import load_profile_config, get_user_data_dir

logger = logging.getLogger(__name__)


class PatchrightBrowser:
    """
    Drop-in замена AdsPowerBrowser на Patchright.

    Использование (точно так же как раньше):
        wrapper = PatchrightBrowser(profile_id="zenit_1", headless=False)
        await wrapper.__aenter__()
        page = wrapper.page
        new_tab = await wrapper.new_page()
        await wrapper.stop()
    """

    def __init__(
        self,
        profile_id: str,
        headless: bool = False,
        # Совместимость со старым конструктором (игнорируем):
        api_key: str = "",
        api_url: str = "",
        **kwargs,
    ):
        self.profile_id = profile_id
        self.headless = headless
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self):
        if not self.profile_id:
            raise ValueError("❌ Не указан profile_id")

        logger.info(f"🚀 Запуск профиля {self.profile_id} (Patchright)")

        config = load_profile_config(self.profile_id)
        user_data_dir = get_user_data_dir(self.profile_id)

        from core.fingerprint import (
            derive_fingerprint, build_chromium_args, build_init_script,
        )

        proxy_cfg = config.get("proxy", {})
        proxy_server = proxy_cfg.get("server", "")
        proxy = None
        if proxy_server:
            server = proxy_server
            username = proxy_cfg.get("username", "")
            password = proxy_cfg.get("password", "")
            if username and "://" in server:
                scheme, rest = server.split("://", 1)
                server = f"{scheme}://{username}:{password}@{rest}"
            proxy = {"server": server}
            logger.info(f"[{self.profile_id}] Прокси: {proxy_server}")

        # ── Отпечаток: разворачиваем 'auto' из seed ──
        fp = derive_fingerprint(config.get("fingerprint", {}),
                                proxy_server=proxy_server)

        launch_kwargs = dict(
            user_data_dir=user_data_dir,
            headless=self.headless,
            proxy=proxy,
            args=build_chromium_args(fp),
            viewport={"width": fp["screen"]["width"],
                      "height": fp["screen"]["height"]},
            locale=fp["locale"],
            timezone_id=fp["timezone"],
            ignore_default_args=["--enable-automation"],
        )
        if fp["ua_override"]:
            launch_kwargs["user_agent"] = fp["user_agent"]
        if fp["geo_mode"] == "coords" and fp["geo_lat"] is not None:
            launch_kwargs["geolocation"] = {"latitude": fp["geo_lat"],
                                            "longitude": fp["geo_lon"]}
            launch_kwargs["permissions"] = ["geolocation"]
        if fp["dnt"] != "default":
            launch_kwargs["extra_http_headers"] = {"DNT": fp["dnt"]}

        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception as e:
            logger.error(f"❌ Ошибка запуска браузера: {e}")
            if self._playwright:
                await self._playwright.stop()
            raise

        # ── JS-слой отпечатка: canvas/webgl/audio/rects/voices/cpu/ram ──
        await self._context.add_init_script(build_init_script(fp))
        logger.info(
            f"[{self.profile_id}] FP: seed={fp['seed']} "
            f"tz={fp['timezone']} screen={fp['screen']['width']}x{fp['screen']['height']} "
            f"cpu={fp['cpu_cores']} ram={fp['ram_gb']} gl={fp['webgl']['renderer'][:28]}"
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        await self._close_extra_tabs()

        if not self._page or self._page.is_closed():
            self._page = await self._context.new_page()

        logger.info(f"✅ Браузер для профиля {self.profile_id} готов")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    # ── Свойства (совместимость с AdsPowerBrowser) ──

    @property
    def page(self) -> Page:
        return self._page

    @property
    def browser(self):
        """
        Возвращает обёртку самого себя, чтобы код вида
        `wrapper.browser.is_connected()` продолжал работать.
        """
        return self

    def is_connected(self) -> bool:
        """Жив ли контекст браузера."""
        try:
            return self._context is not None and not self._context.is_closed()
        except Exception:
            return False

    # ── Методы ──

    async def new_page(self) -> Page:
        """Открывает НОВУЮ вкладку в существующем контексте профиля."""
        if not self._context:
            raise RuntimeError("PatchrightBrowser: context не инициализирован")
        new_p = await self._context.new_page()
        logger.debug(f"📄 Открыта новая вкладка профиля {self.profile_id}")
        return new_p

    async def stop(self):
        """Закрывает браузер и освобождает ресурсы."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info(f"🧹 Профиль {self.profile_id} закрыт (Patchright)")

    async def _close_extra_tabs(self):
        """Закрывает все вкладки кроме последней."""
        if not self._context:
            return
        pages = self._context.pages
        if len(pages) > 1:
            logger.debug(f"🧹 Закрываем {len(pages) - 1} лишних вкладок...")
            for page in pages[:-1]:
                try:
                    await page.close()
                except Exception:
                    pass