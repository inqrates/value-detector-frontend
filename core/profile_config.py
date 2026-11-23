"""
Менеджер конфигураций профилей.

Каждый профиль = папка с user-data-dir + JSON конфиг.

Схема v3: личность профиля хранится как записанный пресет Camoufox.
Один профиль = один пресет = стабильный отпечаток между запусками.
Смена личности = выбор другого пресета (кнопка «Новая личность»).
"""
import json
import logging
import os
from typing import List, Optional

from ui.paths import get_app_data_dir
from core.fingerprint import (
    GEO_AUTO,
    pick_identity_preset,
    preset_signature,
)

logger = logging.getLogger(__name__)

PROFILES_DIR_NAME = "profiles"
CONFIG_VERSION = 3


def _profiles_dir() -> str:
    d = os.path.join(get_app_data_dir(), PROFILES_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _profile_dir(profile_id: str) -> str:
    d = os.path.join(_profiles_dir(), profile_id)
    os.makedirs(d, exist_ok=True)
    return d


def _config_path(profile_id: str) -> str:
    return os.path.join(_profile_dir(profile_id), "profile.json")


# ══════════════════════════════════════════════════════════
# Занятые личности
# ══════════════════════════════════════════════════════════
def used_identity_signatures(exclude_profile: Optional[str] = None) -> set:
    """
    Сигнатуры личностей, уже выданных другим профилям.
    Нужны, чтобы новому профилю досталась уникальная личность.
    """
    result = set()
    d = _profiles_dir()
    try:
        names = os.listdir(d)
    except Exception:
        return result

    for name in names:
        if name == exclude_profile:
            continue
        path = os.path.join(d, name, "profile.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            preset = (cfg.get("identity") or {}).get("preset")
            if preset:
                result.add(preset_signature(preset))
        except Exception:
            continue
    return result


# ══════════════════════════════════════════════════════════
# Конфиг по умолчанию
# ══════════════════════════════════════════════════════════
def _default_profile_config(profile_id: str) -> dict:
    used = used_identity_signatures(exclude_profile=profile_id)
    preset = pick_identity_preset("windows", used)

    return {
        "profile_id": profile_id,
        "config_version": CONFIG_VERSION,
        "proxy": {
            "server": "",
            "username": "",
            "password": "",
        },
        # Личность: записанный отпечаток устройства
        "identity": {
            "os": "windows",
            "preset": preset,
        },
        # Настройки, которые безопасно менять поверх личности
        "fingerprint": {
            "geo_mode": GEO_AUTO,   # таймзона/язык/координаты по IP прокси
            "timezone": "",         # только для geo_mode=manual
            "locale": "",           # только для geo_mode=manual
            "webrtc": "block",      # не раскрывать локальный IP
        },
        "headless": False,
    }


# ══════════════════════════════════════════════════════════
# Миграция
# ══════════════════════════════════════════════════════════
def _migrate_config(profile_id: str, cfg: dict) -> bool:
    """
    Приводит конфиги v1/v2 к схеме v3.

    v1/v2 хранили набор оверрайдов (seed, UA, экран, WebGL, CPU...),
    которые либо игнорировались движком, либо ломали запуск, либо
    делали отпечаток нестабильным. Всё это заменяется на один пресет.
    """
    try:
        ver = int(cfg.get("config_version", 1))
    except (TypeError, ValueError):
        ver = 1

    if ver >= CONFIG_VERSION and (cfg.get("identity") or {}).get("preset"):
        return False

    old_fp = cfg.get("fingerprint") or {}
    used = used_identity_signatures(exclude_profile=profile_id)
    preset = pick_identity_preset("windows", used)

    # Сохраняем осмысленный выбор пользователя, если он был
    geo_old = old_fp.get("geolocation")
    if geo_old == "auto" or old_fp.get("timezone") == "auto":
        geo_mode = GEO_AUTO
    elif geo_old == "default":
        geo_mode = GEO_AUTO
    else:
        geo_mode = GEO_AUTO

    cfg["identity"] = {"os": "windows", "preset": preset}
    cfg["fingerprint"] = {
        "geo_mode": geo_mode,
        "timezone": "",
        "locale": "",
        "webrtc": "block" if old_fp.get("webrtc", "proxy_only") != "real" else "allow",
    }
    cfg["config_version"] = CONFIG_VERSION

    logger.warning(
        f"[profile_config] {profile_id}: конфиг мигрирован v{ver}→v{CONFIG_VERSION}. "
        f"Назначена записанная личность (пресет), ручные оверрайды отпечатка убраны."
    )
    return True


# ══════════════════════════════════════════════════════════
# Чтение / запись
# ══════════════════════════════════════════════════════════
def load_profile_config(profile_id: str) -> dict:
    p = _config_path(profile_id)
    if not os.path.exists(p):
        cfg = _default_profile_config(profile_id)
        save_profile_config(profile_id, cfg)
        logger.info(f"[profile_config] Создан конфиг профиля {profile_id} с новой личностью")
        return cfg

    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("ожидался JSON-объект")
    except Exception as e:
        logger.error(f"[profile_config] Ошибка чтения {p}: {e}")
        cfg = _default_profile_config(profile_id)
        save_profile_config(profile_id, cfg)
        return cfg

    cfg.setdefault("profile_id", profile_id)
    cfg.setdefault("proxy", {"server": "", "username": "", "password": ""})
    cfg.setdefault("fingerprint", {})
    cfg.setdefault("headless", False)

    fp = cfg["fingerprint"]
    fp.setdefault("geo_mode", GEO_AUTO)
    fp.setdefault("timezone", "")
    fp.setdefault("locale", "")
    fp.setdefault("webrtc", "block")

    if _migrate_config(profile_id, cfg):
        save_profile_config(profile_id, cfg)

    return cfg


def save_profile_config(profile_id: str, cfg: dict) -> None:
    p = _config_path(profile_id)
    try:
        cfg["config_version"] = CONFIG_VERSION
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[profile_config] Ошибка записи {p}: {e}")


def get_user_data_dir(profile_id: str) -> str:
    return _profile_dir(profile_id)


def list_profiles() -> List[str]:
    d = _profiles_dir()
    result = []
    try:
        for name in os.listdir(d):
            cfg_path = os.path.join(d, name, "profile.json")
            if os.path.isfile(cfg_path):
                result.append(name)
    except Exception:
        pass
    return sorted(result)


# ══════════════════════════════════════════════════════════
# Смена личности
# ══════════════════════════════════════════════════════════
def regenerate_identity(profile_id: str, os_name: str = "windows") -> Optional[dict]:
    """
    Выдаёт профилю НОВУЮ личность (другой записанный отпечаток).

    ВАЖНО: менять личность на том же IP — это ротация, которую
    антифрод замечает. Использовать только когда профиль
    действительно «испорчен» или переезжает на другой прокси.
    """
    cfg = load_profile_config(profile_id)
    used = used_identity_signatures(exclude_profile=profile_id)
    preset = pick_identity_preset(os_name, used)
    if not preset:
        logger.error(f"[profile_config] {profile_id}: нет пресетов для '{os_name}'")
        return None

    cfg["identity"] = {"os": os_name, "preset": preset}
    save_profile_config(profile_id, cfg)
    logger.info(
        f"[profile_config] {profile_id}: выдана новая личность "
        f"(os={os_name}, sig={preset_signature(preset)})"
    )
    return preset
