"""
Запуск профиля через Camoufox (Firefox с C++ патчами отпечатка).

Camoufox патчит navigator, WebGL, AudioContext, Canvas, ClientRects,
SpeechVoices, hardwareConcurrency, deviceMemory — всё на уровне движка.
Worker видит те же значения, Function.toString() остаётся [native code].

Установка:
    pip install camoufox
    python -m camoufox fetch
"""
import asyncio
import logging
from typing import Optional

from core.profile_config import load_profile_config, get_user_data_dir

logger = logging.getLogger(__name__)


class PatchrightBrowser:
    """
    Drop-in замена старого PatchrightBrowser.
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
        self._browser = None
        self._page = None
        self._used_channel = "camoufox"

    async def __aenter__(self):
        if not self.profile_id:
            raise ValueError("❌ Не указан profile_id")

        logger.info(f"🚀 Запуск профиля {self.profile_id} (Camoufox)")

        config = load_profile_config(self.profile_id)
        user_data_dir = get_user_data_dir(self.profile_id)
        fp = config.get("fingerprint", {})
        proxy_cfg = config.get("proxy", {})

        # ── Импорт camoufox лениво ──
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            raise RuntimeError(
                "camoufox не установлен. Выполните:\n"
                "  pip install camoufox\n"
                "  python -m camoufox fetch"
            )

        # ── Прокси ──
        proxy = None
        proxy_server = proxy_cfg.get("server", "")
        if proxy_server:
            proxy = {
                "server": proxy_server,
                "username": proxy_cfg.get("username", ""),
                "password": proxy_cfg.get("password", ""),
            }
            if not proxy["username"]:
                proxy.pop("username", None)
                proxy.pop("password", None)
            logger.info(f"[{self.profile_id}] Прокси: {proxy_server}")

        # ── Параметры Camoufox ──
        # geoip=True → таймзона/локаль по IP прокси (или своему)
        # humanize=True → человекоподобные мышь/клавиатура/скролл
        # persistent_context=True → куки/сессии сохраняются в user_data_dir
        launch_kwargs = dict(
            headless=self.headless,
            persistent_context=True,
            user_data_dir=user_data_dir,
            geoip=bool(proxy_server),
            humanize=True,
        )
        if proxy:
            launch_kwargs["proxy"] = proxy

        # ── Платформа ──
        platform = fp.get("platform", "windows")
        if platform == "macos":
            launch_kwargs["os"] = "macos"
        else:
            launch_kwargs["os"] = "windows"

        # ── Стабильный отпечаток через seed ──
        # Camoufox генерирует отпечаток из seed — один seed = один человек
        seed = int(fp.get("seed") or 1)
        launch_kwargs["fingerprint"] = {"seed": seed}

        # ── Точечные оверрайды (только то, что задано явно) ──
        # Camoufox применяет их в движке, Worker видит те же значения
        overrides = {}
        if isinstance(fp.get("cpu_cores"), int):
            overrides["navigator.hardwareConcurrency"] = int(fp["cpu_cores"])
        if isinstance(fp.get("ram_gb"), int):
            overrides["navigator.deviceMemory"] = int(fp["ram_gb"])
        scr = fp.get("screen")
        if isinstance(scr, dict) and scr.get("width"):
            overrides["screen.width"] = int(scr["width"])
            overrides["screen.height"] = int(scr["height"])
        wgl = fp.get("webgl")
        if isinstance(wgl, dict) and wgl.get("renderer"):
            launch_kwargs["webgl_config"] = (
                wgl.get("vendor", "Google Inc."),
                wgl["renderer"],
            )
        if overrides:
            launch_kwargs["config"] = overrides

        # ── Locale / timezone (если заданы явно) ──
        tz = fp.get("timezone", "system")
        if tz and tz not in ("system", "auto"):
            launch_kwargs["timezone"] = tz
        locale = fp.get("locale", "system")
        if locale and locale not in ("system", "auto"):
            launch_kwargs["locale"] = locale

        # ── Запуск ──
        self._browser = AsyncCamoufox(**launch_kwargs)
        await self._browser.__aenter__()

        # ── Первая страница ──
        pages = self._browser.pages if hasattr(self._browser, "pages") else []
        self._page = pages[0] if pages else await self._browser.new_page()

        # ── UA лог ──
        try:
            ua = await self._page.evaluate("navigator.userAgent")
            logger.info(
                f"[{self.profile_id}] Camoufox UA: {ua[:80]}..."
            )
        except Exception:
            pass

        logger.info(
            f"✅ профиль {self.profile_id} готов "
            f"(seed={seed}, platform={platform}, "
            f"proxy={'да' if proxy else 'нет'})"
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
            return self._browser is not None and self._page is not None
        except Exception:
            return False

    async def new_page(self):
        if not self._browser:
            raise RuntimeError("browser не инициализирован")
        return await self._browser.new_page()

    async def stop(self):
        try:
            if self._browser:
                await self._browser.__aexit__(None, None, None)
        except Exception:
            pass
        self._browser = None
        self._page = None
        logger.info(f"🧹 профиль {self.profile_id} закрыт (Camoufox)")