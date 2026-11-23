"""
Запуск профиля через Camoufox (Firefox с C++ патчами отпечатка).
Тот же интерфейс, что и у старого браузера, чтобы остальной код не ломался.
"""
import asyncio
import logging
from typing import Optional

from core.profile_config import load_profile_config, get_user_data_dir
from core.fingerprint import build_launch_kwargs, preset_summary

logger = logging.getLogger(__name__)


class PatchrightBrowser:
    """
    Drop-in замена старого браузера на Camoufox.
    Интерфейс тот же: page / new_page() / stop() / is_connected().
    """

    def __init__(
        self,
        profile_id: str,
        headless: bool = False,
        api_key: str = "",
        api_url: str = "",
        **kwargs,
    ):
        self.profile_id = profile_id
        self.headless = headless
        self._launcher = None   # AsyncCamoufox (контекстный менеджер)
        self._browser = None    # реальный Browser/BrowserContext
        self._page = None
        self._used_channel = "camoufox"

    async def __aenter__(self):
        if not self.profile_id:
            raise ValueError("❌ Не указан profile_id")

        logger.info(f"🚀 Запуск профиля {self.profile_id} (Camoufox)")

        config = load_profile_config(self.profile_id)
        user_data_dir = get_user_data_dir(self.profile_id)
        identity = config.get("identity") or {}
        fp = config.get("fingerprint") or {}
        proxy_cfg = config.get("proxy") or {}

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            raise RuntimeError(
                "camoufox не установлен. Выполните:\n"
                "  pip install camoufox\n"
                "  python -m camoufox fetch"
            )

        # ── Параметры запуска: личность из пресета + прокси/гео/WebRTC ──
        launch_kwargs = build_launch_kwargs(config)
        launch_kwargs["persistent_context"] = True
        launch_kwargs["user_data_dir"] = user_data_dir
        launch_kwargs["headless"] = self.headless

        # Прокси — для лога
        proxy_server = (proxy_cfg.get("server") or "").strip()
        if proxy_server:
            logger.info(f"[{self.profile_id}] Прокси: {proxy_server}")

        if not identity.get("preset"):
            logger.warning(
                f"[{self.profile_id}] личность не зафиксирована — "
                f"отпечаток будет меняться между запусками"
            )

        # ── Запуск ──
        self._launcher = AsyncCamoufox(**launch_kwargs)
        await self._launcher.__aenter__()

        # После __aenter__ реальный браузер лежит в .browser
        # (при persistent_context=True это BrowserContext — интерфейс тот же)
        actual_browser = self._launcher.browser
        if actual_browser is None:
            raise RuntimeError("Camoufox не вернул browser после запуска")
        self._browser = actual_browser

        # ── Первая страница ──
        pages = actual_browser.pages if hasattr(actual_browser, "pages") else []
        self._page = pages[0] if pages else await actual_browser.new_page()

        # ── Лог фактической личности ──
        try:
            ua = await self._page.evaluate("navigator.userAgent")
            cores = await self._page.evaluate("navigator.hardwareConcurrency")
            screen = await self._page.evaluate(
                "({w: screen.width, h: screen.height, d: window.devicePixelRatio})"
            )
            logger.info(
                f"[{self.profile_id}] отпечаток: UA={ua[:60]}… "
                f"cores={cores} screen={screen.get('w')}x{screen.get('h')} "
                f"dpr={screen.get('d')}"
            )
        except Exception:
            pass

        summary = preset_summary(identity.get("preset"))
        logger.info(
            f"✅ профиль {self.profile_id} готов "
            f"(os={identity.get('os', 'windows')}, "
            f"прокси={'да' if proxy_server else 'нет'}, "
            f"гео={fp.get('geo_mode', 'auto')}, "
            f"sig={summary.get('signature', '—')})"
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @property
    def page(self):
        return self._page

    @property
    def browser(self):
        return self

    @property
    def used_channel(self):
        return self._used_channel

    def is_connected(self) -> bool:
        try:
            return self._browser is not None and self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    async def new_page(self):
        if self._browser is None:
            raise RuntimeError("browser не инициализирован")
        return await self._browser.new_page()

    async def stop(self):
        # Закрывать нужно лаунчер: он корректно завершит persistent-профиль
        if self._launcher is not None:
            try:
                await self._launcher.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"[{self.profile_id}] ошибка при закрытии: {e}")
        self._launcher = None
        self._browser = None
        self._page = None
        logger.info(f"🧹 профиль {self.profile_id} закрыт (Camoufox)")