"""
Конфиги отпечатка и geoip.
Camoufox применяет отпечаток на уровне C++ движка —
JS-инъекции не нужны и даже вредны (prototype lies).
"""
import json
import logging
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
# Разрешения экрана — по частоте встречаемости (desktop)
# ══════════════════════════════════════════════════════════
SCREEN_RESOLUTIONS = [
    (1920, 1080, "1920×1080 — самое частое"),
    (1366, 768,  "1366×768 — ноутбуки"),
    (1536, 864,  "1536×864 — масштаб 125%"),
    (1440, 900,  "1440×900"),
    (1280, 720,  "1280×720"),
    (1600, 900,  "1600×900"),
    (2560, 1440, "2560×1440 — 2K"),
    (1280, 1024, "1280×1024 — 5:4 офис"),
    (1680, 1050, "1680×1050"),
    (3840, 2160, "3840×2160 — 4K"),
    (1920, 1200, "1920×1200 — 16:10"),
    (2560, 1600, "2560×1600 — 16:10"),
    (3440, 1440, "3440×1440 — ultrawide"),
    (2560, 1080, "2560×1080 — ultrawide"),
    (1280, 800,  "1280×800 — ноутбук"),
    (1024, 768,  "1024×768 — старый ПК"),
]

TIMEZONES = [
    "auto", "system",
    "Europe/Kaliningrad", "Europe/Moscow", "Europe/Samara",
    "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Krasnoyarsk",
    "Asia/Irkutsk", "Asia/Yakutsk", "Asia/Vladivostok",
    "Asia/Magadan", "Asia/Kamchatka",
    "Europe/Kiev", "Europe/Minsk", "Asia/Almaty",
    "Asia/Tashkent", "Asia/Tbilisi", "Asia/Yerevan", "Asia/Baku",
    "Europe/Istanbul", "Europe/Berlin", "Europe/London",
    "America/New_York", "Asia/Dubai",
]

LOCALE_BY_COUNTRY = {
    "RU": "ru-RU", "BY": "ru-RU", "KZ": "ru-RU", "KG": "ru-RU",
    "UA": "uk-UA", "UZ": "ru-RU", "AZ": "ru-RU", "AM": "ru-RU",
    "GE": "ru-RU", "MD": "ru-RU", "TR": "tr-TR", "DE": "de-DE",
    "GB": "en-GB", "US": "en-US", "AE": "en-US",
}

# ══════════════════════════════════════════════════════════
# GPU — реальные строки Chrome/Windows (ANGLE + D3D11)
# Camoufox использует эти же строки в C++ патчах
# ══════════════════════════════════════════════════════════
def _nv(model: str) -> dict:
    return {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": f"ANGLE (NVIDIA, NVIDIA {model} Direct3D11 vs_5_0 ps_5_0, D3D11)",
    }
def _intel(model: str) -> dict:
    return {
        "vendor": "Google Inc. (Intel)",
        "renderer": f"ANGLE (Intel, Intel(R) {model} Direct3D11 vs_5_0 ps_5_0, D3D11)",
    }
def _amd(model: str) -> dict:
    return {
        "vendor": "Google Inc. (AMD)",
        "renderer": f"ANGLE (AMD, AMD {model} Direct3D11 vs_5_0 ps_5_0, D3D11)",
    }

WEBGL_PROFILES = [
    _nv("GeForce GTX 1050 Ti"), _nv("GeForce GTX 1060 6GB"),
    _nv("GeForce GTX 1650"), _nv("GeForce GTX 1660 SUPER"),
    _nv("GeForce RTX 2060"), _nv("GeForce RTX 2070 SUPER"),
    _nv("GeForce RTX 3050"), _nv("GeForce RTX 3060"),
    _nv("GeForce RTX 3060 Ti"), _nv("GeForce RTX 3070"),
    _nv("GeForce RTX 3080"), _nv("GeForce RTX 4060"),
    _nv("GeForce RTX 4060 Ti"), _nv("GeForce RTX 4070"),
    _nv("GeForce RTX 4070 SUPER"), _nv("GeForce RTX 4080"),
    _nv("GeForce RTX 4090"),
    _intel("UHD Graphics 620"), _intel("UHD Graphics 630"),
    _intel("UHD Graphics 730"), _intel("UHD Graphics 770"),
    _intel("Iris(R) Xe Graphics"), _intel("Iris(R) Plus Graphics 655"),
    _amd("Radeon RX 550"), _amd("Radeon RX 570"),
    _amd("Radeon RX 580"), _amd("Radeon RX 5500 XT"),
    _amd("Radeon RX 6600"), _amd("Radeon RX 6650 XT"),
    _amd("Radeon RX 6700 XT"), _amd("Radeon RX 7600"),
    _amd("Radeon RX 7800 XT"), _amd("Radeon(TM) Vega 8 Graphics"),
]

# ══════════════════════════════════════════════════════════
# GeoIP
# ══════════════════════════════════════════════════════════
_GEO_CACHE: dict = {}
_GEO_TTL = 1800.0


def resolve_geo(proxy_server: str = "", timeout: float = 5.0) -> Optional[dict]:
    """Таймзона/локаль/координаты по IP. Работает и без прокси."""
    key = proxy_server or "__direct__"
    cached = _GEO_CACHE.get(key)
    if cached and time.time() - cached[1] < _GEO_TTL:
        return cached[0]
    try:
        if proxy_server:
            handler = urllib.request.ProxyHandler(
                {"http": proxy_server, "https": proxy_server}
            )
        else:
            handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        url = "http://ip-api.com/json/?fields=status,countryCode,timezone,lat,lon,query"
        with opener.open(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") != "success":
            logger.warning(f"[fingerprint] geoip: {data}")
            return None
        tz = data.get("timezone") or ""
        cc = (data.get("countryCode") or "").upper()
        result = {
            "timezone": tz,
            "locale": LOCALE_BY_COUNTRY.get(cc, "en-US"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "ip": data.get("query"),
            "country": cc,
        }
        _GEO_CACHE[key] = (result, time.time())
        logger.info(
            f"[fingerprint] geoip: ip={result['ip']} {cc} "
            f"tz={tz} locale={result['locale']}"
        )
        return result
    except Exception as e:
        logger.warning(f"[fingerprint] geoip не сработал: {e}")
        return None


resolve_geo_via_proxy = resolve_geo


def derive_fingerprint(fp_cfg: dict, proxy_server: str = "") -> dict:
    """
    Возвращает словарь параметров для Camoufox.
    Camoufox сам применяет их на уровне C++.
    """
    seed = int(fp_cfg.get("seed") or 1)
    platform = fp_cfg.get("platform", "windows")

    geo = None
    if "auto" in (fp_cfg.get("timezone"), fp_cfg.get("locale"),
                  fp_cfg.get("geolocation")):
        geo = resolve_geo(proxy_server)

    scr = fp_cfg.get("screen")
    screen = None
    if isinstance(scr, dict) and scr.get("width"):
        screen = {"width": int(scr["width"]), "height": int(scr["height"])}

    wgl = fp_cfg.get("webgl")
    webgl = None
    if isinstance(wgl, dict) and wgl.get("renderer"):
        webgl = {"vendor": wgl.get("vendor", "Google Inc."),
                 "renderer": wgl["renderer"]}

    tz_cfg = fp_cfg.get("timezone", "system")
    if tz_cfg == "auto":
        timezone = (geo or {}).get("timezone") or None
        if not timezone:
            logger.warning("[fingerprint] timezone=auto, но geoip недоступен")
    elif tz_cfg in ("system", "", None):
        timezone = None
    else:
        timezone = tz_cfg

    loc_cfg = fp_cfg.get("locale", "system")
    if loc_cfg == "auto":
        locale = (geo or {}).get("locale") or None
    elif loc_cfg in ("system", "", None):
        locale = None
    else:
        locale = loc_cfg

    ua = (fp_cfg.get("user_agent") or "").strip()

    fp = {
        "seed": seed,
        "platform": platform,
        "user_agent": ua,
        "ua_override": bool(ua),
        "timezone": timezone,
        "locale": locale,
        "geo_mode": fp_cfg.get("geolocation", "default"),
        "geo_lat": (geo or {}).get("lat") if fp_cfg.get("geolocation") == "auto" else None,
        "geo_lon": (geo or {}).get("lon") if fp_cfg.get("geolocation") == "auto" else None,
        "screen": screen,
        "webgl": webgl,
        "canvas": fp_cfg.get("canvas", "real"),
        "audio": fp_cfg.get("audio", "real"),
        "client_rects": fp_cfg.get("client_rects", "real"),
        "speech_voices": fp_cfg.get("speech_voices", "real"),
        "cpu_cores": fp_cfg.get("cpu_cores") if isinstance(fp_cfg.get("cpu_cores"), int) else None,
        "ram_gb": fp_cfg.get("ram_gb") if isinstance(fp_cfg.get("ram_gb"), int) else None,
        "webrtc": fp_cfg.get("webrtc", "proxy_only"),
        "dnt": fp_cfg.get("dnt", "default"),
        "hardware_accel": fp_cfg.get("hardware_accel", True),
        # Camoufox-специфичные флаги
        "camoufox_overrides": {},
    }
    if isinstance(fp_cfg.get("geolocation"), dict):
        fp["geo_mode"] = "coords"
        fp["geo_lat"] = fp_cfg["geolocation"].get("lat")
        fp["geo_lon"] = fp_cfg["geolocation"].get("lon")

    # Оверрайды для Camoufox (применяются в движке)
    overrides = {}
    if fp["cpu_cores"]:
        overrides["navigator.hardwareConcurrency"] = fp["cpu_cores"]
    if fp["ram_gb"]:
        overrides["navigator.deviceMemory"] = fp["ram_gb"]
    if screen:
        overrides["screen.width"] = screen["width"]
        overrides["screen.height"] = screen["height"]
    fp["camoufox_overrides"] = overrides

    # Предупреждение о палимых оверрайдах
    risky = []
    if fp["cpu_cores"] or fp["ram_gb"]:
        risky.append("CPU/RAM")
    if webgl:
        risky.append("WebGL")
    if risky:
        fp["risk_warning"] = "; ".join(risky)

    return fp


def build_chromium_args(fp: dict, has_proxy: bool = False) -> list:
    """Для fallback на Chromium (если Camoufox не запустится)."""
    args = ["--no-first-run", "--no-default-browser-check"]
    scr = fp.get("screen")
    if isinstance(scr, dict) and scr.get("width"):
        args.append(f"--window-size={scr['width']},{scr['height']}")
    else:
        args.append("--start-maximized")
    if has_proxy and fp.get("webrtc") == "proxy_only":
        args += [
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--enforce-webrtc-ip-permission-check",
        ]
    if not fp.get("hardware_accel", True):
        args.append("--disable-gpu")
    return args