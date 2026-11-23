"""
Отпечатки профилей на основе записанных пресетов Camoufox.

ГЛАВНЫЙ ПРИНЦИП: личность профиля — это ОДИН записанный пресет
(реальное устройство, снятое с настоящего Firefox). Он хранится
в profile.json и передаётся в Camoufox как fingerprint_preset.

Почему так, а не «сгенерировать случайно при каждом запуске»:
  * fpgen при каждом запуске рисует НОВЫЙ отпечаток (другой экран,
    другое число ядер) — для сайта это признак подмены;
  * пресет даёт побайтово одинаковый конфиг между запусками
    (проверено: хеш CAMOU_CONFIG совпадает), то есть одно устройство
    ведёт себя как одно устройство;
  * пресетов много (71 windows / 25 macos / 17 linux), поэтому
    у каждого профиля может быть своя уникальная личность.

Ручная правка UA/WebGL/экрана здесь сознательно НЕ поддерживается:
эти поля должны совпадать друг с другом, иначе антифрод видит
рассинхрон. Ротация личности = выбор другого пресета.
"""
import hashlib
import json
import logging
import random
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Поддерживаемые ОС (по ним есть записанные пресеты)
OS_CHOICES = [
    ("windows", "Windows"),
    ("macos", "macOS"),
    ("linux", "Linux"),
]

# Режимы геолокации/языка/таймзоны
GEO_AUTO = "auto"        # по IP прокси (рекомендуется)
GEO_MANUAL = "manual"    # задать таймзону и язык вручную
GEO_DEFAULT = "default"  # не трогать

TIMEZONES = [
    "Europe/Kaliningrad", "Europe/Moscow", "Europe/Samara",
    "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Krasnoyarsk",
    "Asia/Irkutsk", "Asia/Yakutsk", "Asia/Vladivostok",
    "Asia/Magadan", "Asia/Kamchatka",
    "Europe/Kiev", "Europe/Minsk", "Asia/Almaty",
    "Asia/Tashkent", "Asia/Tbilisi", "Asia/Yerevan", "Asia/Baku",
    "Europe/Istanbul", "Europe/Berlin", "Europe/London",
    "America/New_York", "Asia/Dubai",
]

LOCALES = [
    "ru-RU", "en-US", "en-GB", "uk-UA", "de-DE", "tr-TR",
    "kk-KZ", "uz-UZ", "ka-GE", "hy-AM", "az-AZ", "be-BY",
]


# ══════════════════════════════════════════════════════════
# Пресеты
# ══════════════════════════════════════════════════════════
def _load_presets() -> Dict[str, List[dict]]:
    """Записанные отпечатки, сгруппированные по ОС."""
    try:
        from camoufox.fingerprints import load_presets
        data = load_presets() or {}
        presets = data.get("presets") or {}
        return {k: list(v) for k, v in presets.items() if isinstance(v, list)}
    except Exception as e:
        logger.error(f"[fingerprint] не удалось загрузить пресеты: {e}")
        return {}


def preset_signature(preset: dict) -> str:
    """
    Короткий хеш личности. Два пресета с одинаковой сигнатурой
    неразличимы для сайта — их нельзя давать разным профилям.
    """
    try:
        key = json.dumps(
            {
                "n": preset.get("navigator", {}),
                "s": preset.get("screen", {}),
                "w": preset.get("webgl", {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    except Exception:
        key = str(preset)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def available_presets(os_name: str = "windows") -> List[dict]:
    """Все пресеты для ОС (с дублями — для подсчёта ёмкости)."""
    return _load_presets().get(os_name, [])


def unique_preset_count(os_name: str = "windows") -> int:
    """Сколько РАЗНЫХ личностей доступно для этой ОС."""
    seen = set()
    for p in available_presets(os_name):
        seen.add(preset_signature(p))
    return len(seen)


def pick_identity_preset(
    os_name: str = "windows",
    used_signatures: Optional[set] = None,
) -> Optional[dict]:
    """
    Выбирает записанный отпечаток, по возможности не совпадающий
    с уже занятыми профилями.

    used_signatures — сигнатуры личностей, уже выданных другим профилям.
    Если все уникальные заняты, начинается повторное использование
    (лучше повторить личность, чем выдать синтетическую).
    """
    presets = available_presets(os_name)
    if not presets:
        logger.error(f"[fingerprint] нет пресетов для ОС '{os_name}'")
        return None

    used = set(used_signatures or ())

    # 1) сначала — только уникальные и не занятые
    seen = set()
    fresh = []
    for p in presets:
        sig = preset_signature(p)
        if sig in seen:
            continue
        seen.add(sig)
        if sig not in used:
            fresh.append(p)

    if fresh:
        return random.choice(fresh)

    # 2) все уникальные заняты — берём наименее используемую сигнатуру
    unique = []
    seen = set()
    for p in presets:
        sig = preset_signature(p)
        if sig not in seen:
            seen.add(sig)
            unique.append(p)
    logger.warning(
        f"[fingerprint] уникальные личности для '{os_name}' закончились "
        f"({len(unique)} шт.), начинаю повторное использование"
    )
    return random.choice(unique)


def preset_summary(preset: Optional[dict]) -> dict:
    """Человекочитаемая карточка личности для UI."""
    if not preset:
        return {}
    nav = preset.get("navigator", {}) or {}
    scr = preset.get("screen", {}) or {}
    wgl = preset.get("webgl", {}) or {}
    return {
        "os": nav.get("platform", "—"),
        "user_agent": nav.get("userAgent", "—"),
        "cpu_cores": nav.get("hardwareConcurrency"),
        "touch_points": nav.get("maxTouchPoints"),
        "screen": (
            f"{scr.get('width')}×{scr.get('height')}"
            if scr.get("width") else "—"
        ),
        "dpr": scr.get("devicePixelRatio", 1),
        "color_depth": scr.get("colorDepth"),
        "gpu_vendor": wgl.get("unmaskedVendor", "—"),
        "gpu_renderer": wgl.get("unmaskedRenderer", "—"),
        "voices": len(preset.get("speechVoices") or []),
        "signature": preset_signature(preset),
    }


# ══════════════════════════════════════════════════════════
# Параметры запуска Camoufox
# ══════════════════════════════════════════════════════════
def build_launch_kwargs(cfg: dict) -> dict:
    """
    Собирает kwargs для AsyncCamoufox из конфига профиля.

    Возвращает только «безопасные» настройки: личность берётся
    из пресета целиком, а прокси/гео/WebRTC — из конфига.
    """
    identity = cfg.get("identity") or {}
    fp = cfg.get("fingerprint") or {}
    proxy_cfg = cfg.get("proxy") or {}

    os_name = identity.get("os") or fp.get("platform") or "windows"
    preset = identity.get("preset")

    kwargs: dict = {
        "os": os_name,
        "headless": bool(cfg.get("headless", False)),
        "humanize": True,              # человекоподобные движения мыши/клавиатуры
        "i_know_what_im_doing": True,  # мы сознательно фиксируем отпечаток
        # Держим число ядер ровно как в пресете, иначе движок подставит
        # ядра хоста и личность рассыплется (проверено: 6 -> 12 без флага)
        "pin_cpu_cores": True,
    }

    if preset:
        kwargs["fingerprint_preset"] = preset
    else:
        logger.warning(
            "[fingerprint] у профиля нет пресета — Camoufox сгенерирует "
            "новый отпечаток при каждом запуске (нестабильная личность)"
        )

    # ── Прокси ──
    server = (proxy_cfg.get("server") or "").strip()
    if server:
        proxy = {"server": server}
        user = (proxy_cfg.get("username") or "").strip()
        if user:
            proxy["username"] = user
            proxy["password"] = proxy_cfg.get("password") or ""
        kwargs["proxy"] = proxy

    # ── Геолокация / язык / таймзона ──
    geo_mode = fp.get("geo_mode", GEO_AUTO)
    if geo_mode == GEO_AUTO:
        # Camoufox сам определит таймзону, локаль и координаты по IP
        # (через прокси, если он задан) — это самый согласованный вариант
        kwargs["geoip"] = True
    elif geo_mode == GEO_MANUAL:
        tz = (fp.get("timezone") or "").strip()
        if tz and tz != "system":
            kwargs["timezone"] = tz
        loc = (fp.get("locale") or "").strip()
        if loc and loc != "system":
            kwargs["locale"] = loc

    # ── WebRTC ──
    # block_webrtc полностью выключает media.peerconnection — сайт
    # не может узнать локальный IP в обход прокси
    if fp.get("webrtc", "block") in ("block", "proxy_only"):
        kwargs["block_webrtc"] = True

    return kwargs


def describe_geo_risk(geo_mode: str, has_proxy: bool) -> Optional[str]:
    """Предупреждение о рассогласовании гео и IP (для UI)."""
    if geo_mode == GEO_DEFAULT and has_proxy:
        return (
            "Гео не переопределяется: таймзона и язык останутся системными "
            "и могут не совпасть с IP прокси."
        )
    if geo_mode == GEO_MANUAL and has_proxy:
        return (
            "Ручная таймзона/язык: убедитесь, что они совпадают со страной прокси, "
            "иначе это признак подмены."
        )
    return None
