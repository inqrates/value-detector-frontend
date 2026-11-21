"""
Менеджер конфигураций профилей.
Каждый профиль = папка с user-data-dir + JSON конфиг.

Пример конфигурации профиля:
{
  "profile_id": "zenit_1",
  "proxy": {"server": "http://proxy:8080", "username": "", "password": ""},
  "fingerprint": {
    "seed": 12345,
    "platform": "windows",
    "user_agent": "",
    "timezone": "Europe/Moscow",
    "locale": "ru-RU",
    "geolocation": "deny",
    "screen": "auto",
    "webgl": "auto",
    "canvas": "noise",
    "audio": "noise",
    "client_rects": "noise",
    "speech_voices": "noise",
    "cpu_cores": "auto",
    "ram_gb": "auto",
    "webrtc": "proxy_only",
    "dnt": "default",
    "hardware_accel": true
  },
  "viewport": {"width": 1920, "height": 1080},
  "headless": false
}
"""
import json
import os
import logging
import uuid
from typing import List, Dict, Optional

from ui.paths import get_app_data_dir

logger = logging.getLogger(__name__)

PROFILES_DIR_NAME = "profiles"


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


def _generate_seed() -> int:
    """Генерирует уникальный сид отпечатка."""
    return uuid.uuid4().int % 90000 + 10000


def _default_profile_config(profile_id: str) -> dict:
    return {
        "profile_id": profile_id,
        "proxy": {
            "server": "",
            "username": "",
            "password": "",
        },
        "fingerprint": {
            "seed": _generate_seed(),
            "platform": "windows",
            "user_agent": "",
            "timezone": "Europe/Moscow",
            "locale": "ru-RU",
            "geolocation": "deny",
            "screen": "auto",
            "webgl": "auto",
            "canvas": "noise",
            "audio": "noise",
            "client_rects": "noise",
            "speech_voices": "noise",
            "cpu_cores": "auto",
            "ram_gb": "auto",
            "webrtc": "proxy_only",
            "dnt": "default",
            "hardware_accel": True,
        },
        "viewport": {"width": 1920, "height": 1080},
        "headless": False,
    }


def load_profile_config(profile_id: str) -> dict:
    """Загружает конфиг профиля. Если нет — создаёт дефолт."""
    p = _config_path(profile_id)
    if not os.path.exists(p):
        cfg = _default_profile_config(profile_id)
        save_profile_config(profile_id, cfg)
        logger.info(f"[profile_config] Создан дефолтный конфиг для {profile_id}")
        return cfg
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        logger.error(f"[profile_config] Ошибка чтения {p}: {e}")
        cfg = _default_profile_config(profile_id)
    # Мягкая валидация
    cfg.setdefault("profile_id", profile_id)
    cfg.setdefault("proxy", {"server": "", "username": "", "password": ""})
    cfg.setdefault("fingerprint", {})
    cfg["fingerprint"].setdefault("seed", _generate_seed())
    cfg["fingerprint"].setdefault("platform", "windows")
    cfg["fingerprint"].setdefault("timezone", "Europe/Moscow")
    cfg["fingerprint"].setdefault("locale", "ru-RU")
    cfg["fingerprint"].setdefault("geolocation", "deny")
    cfg["fingerprint"].setdefault("screen", "auto")
    cfg["fingerprint"].setdefault("webgl", "auto")
    cfg["fingerprint"].setdefault("canvas", "noise")
    cfg["fingerprint"].setdefault("audio", "noise")
    cfg["fingerprint"].setdefault("client_rects", "noise")
    cfg["fingerprint"].setdefault("speech_voices", "noise")
    cfg["fingerprint"].setdefault("cpu_cores", "auto")
    cfg["fingerprint"].setdefault("ram_gb", "auto")
    cfg["fingerprint"].setdefault("webrtc", "proxy_only")
    cfg["fingerprint"].setdefault("dnt", "default")
    cfg["fingerprint"].setdefault("hardware_accel", True)
    cfg.setdefault("user_agent", "")
    cfg.setdefault("viewport", {"width": 1920, "height": 1080})
    cfg.setdefault("headless", False)
    return cfg


def save_profile_config(profile_id: str, cfg: dict) -> None:
    """Сохраняет конфиг профиля."""
    p = _config_path(profile_id)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[profile_config] Ошибка записи {p}: {e}")


def get_user_data_dir(profile_id: str) -> str:
    """Путь к папке браузера (куки, сессии, история)."""
    return _profile_dir(profile_id)


def list_profiles() -> List[str]:
    """Список всех профилей."""
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


def update_proxy(profile_id: str, server: str, username: str = "",
                 password: str = "") -> None:
    """Обновляет прокси для профиля."""
    cfg = load_profile_config(profile_id)
    cfg["proxy"] = {"server": server, "username": username, "password": password}
    save_profile_config(profile_id, cfg)


def update_fingerprint(profile_id: str, seed: Optional[int] = None,
                       platform: Optional[str] = None,
                       timezone: Optional[str] = None,
                       locale: Optional[str] = None) -> None:
    """Обновляет параметры отпечатка для профиля."""
    cfg = load_profile_config(profile_id)
    fp = cfg["fingerprint"]
    if seed is not None:
        fp["seed"] = seed
    if platform is not None:
        fp["platform"] = platform
    if timezone is not None:
        fp["timezone"] = timezone
    if locale is not None:
        fp["locale"] = locale
    save_profile_config(profile_id, cfg)


def update_user_agent(profile_id: str, ua: str) -> None:
    """Обновляет User-Agent для профиля."""
    cfg = load_profile_config(profile_id)
    cfg["user_agent"] = ua
    save_profile_config(profile_id, cfg)


def update_viewport(profile_id: str, width: int, height: int) -> None:
    """Обновляет размер окна для профиля."""
    cfg = load_profile_config(profile_id)
    cfg["viewport"] = {"width": width, "height": height}
    save_profile_config(profile_id, cfg)


def regenerate_fingerprint(profile_id: str) -> int:
    """Новая личность профиля: новый seed (отпечаток изменится целиком)."""
    cfg = load_profile_config(profile_id)
    new_seed = _generate_seed()
    cfg["fingerprint"]["seed"] = new_seed
    save_profile_config(profile_id, cfg)
    return new_seed