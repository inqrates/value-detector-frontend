"""
Менеджер конфигураций профилей.
Каждый профиль = папка с user-data-dir + JSON конфиг.

Пример profile.json:
{
  "profile_id": "zenit_1",
  "config_version": 2,
  "proxy": {"server": "http://proxy:8080", "username": "", "password": ""},
        "fingerprint": {
            "seed": _generate_seed(),
            "platform": "windows",
            "user_agent": "",
            "timezone": "system",
            "locale": "system",
            "geolocation": "default",
            "screen": "auto",
            "webgl": "auto",
            "canvas": "real",
            "audio": "real",
            "client_rects": "real",
            "speech_voices": "real",
            "cpu_cores": "auto",
            "ram_gb": "auto",
            "webrtc": "proxy_only",
            "dnt": "default",
            "hardware_accel": True,
        },
  "headless": false
}

ГЛАВНЫЙ ПРИНЦИП: по умолчанию НИЧЕГО не подменяем.
"auto" / "real" / "system" / "default" = реальное значение браузера.
Любая JS-подмена детектируется через Function.toString() и создаёт
кросс-слойные нестыковки (UA vs sec-ch-ua, WebGL vs WebGPU).
"""
import json
import os
import logging
import uuid
from typing import List, Dict, Optional

from ui.paths import get_app_data_dir

logger = logging.getLogger(__name__)

PROFILES_DIR_NAME = "profiles"

# Версия схемы конфига. При bump старые конфиги автомигрируются.
# v2: отказ от JS-шумов и ручного UA (детектировались антифродом).
CONFIG_VERSION = 2


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
        "config_version": CONFIG_VERSION,
        # auto = Chrome → Edge → встроенный Chromium
        "browser_channel": "auto",
        "proxy": {
            "server": "",
            "username": "",
            "password": "",
        },
        "fingerprint": {
            "seed": _generate_seed(),
            "platform": "windows",
            "user_agent": "",            # пусто = реальный UA Chrome
            "timezone": "system",        # system = системная таймзона
            "locale": "system",          # system = системный язык
            "geolocation": "default",    # default = не трогать
            "screen": "auto",            # auto = реальный экран
            "webgl": "auto",             # auto = реальный GPU
            "canvas": "real",
            "audio": "real",
            "client_rects": "real",
            "speech_voices": "real",
            "cpu_cores": "auto",
            "ram_gb": "auto",
            "webrtc": "proxy_only",
            "dnt": "default",
            "hardware_accel": True,
        },
        "headless": False,
    }


def _migrate_config(profile_id: str, cfg: dict) -> bool:
    """
    Приводит старые конфиги к текущей схеме.
    Возвращает True, если что-то изменилось (нужно перезаписать файл).

    v1 → v2: старые дефолты включали JS-шумы (canvas/webgl/audio/rects),
    принудительные timezone/locale и могли содержать ручной User-Agent.
    Всё это антифрод ловит:
      - Function.toString() подменённых функций не содержит [native code]
      - ручной UA расходится с sec-ch-ua реальной версии браузера
      - locale через CDP даёт Accept-Language без q-весов
    Сбрасываем на реальные значения.
    """
    try:
        ver = int(cfg.get("config_version", 1))
    except (TypeError, ValueError):
        ver = 1
    if ver >= CONFIG_VERSION:
        return False

    fp = cfg.setdefault("fingerprint", {})
    changed = []

    for key in ("canvas", "audio", "client_rects", "speech_voices"):
        if fp.get(key) == "noise":
            fp[key] = "real"
            changed.append(key)

    if fp.get("geolocation") == "deny":
        fp["geolocation"] = "default"
        changed.append("geolocation")

    if fp.get("timezone") == "Europe/Moscow":
        fp["timezone"] = "system"
        changed.append("timezone")

    if fp.get("locale") == "ru-RU":
        fp["locale"] = "system"
        changed.append("locale")

    if (fp.get("user_agent") or "").strip():
        fp["user_agent"] = ""
        changed.append("user_agent")

    # WebGL из старого списка профилей тоже подменялся — сбрасываем.
    if isinstance(fp.get("webgl"), dict):
        fp["webgl"] = "auto"
        changed.append("webgl")

    cfg["config_version"] = CONFIG_VERSION
    logger.warning(
        f"[profile_config] {profile_id}: конфиг мигрирован v{ver}→v{CONFIG_VERSION}"
        + (f", сброшено: {', '.join(changed)}" if changed else "")
    )
    return True


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
        if not isinstance(cfg, dict):
            raise ValueError("ожидался JSON-объект")
    except Exception as e:
        logger.error(f"[profile_config] Ошибка чтения {p}: {e}")
        cfg = _default_profile_config(profile_id)
        save_profile_config(profile_id, cfg)
        return cfg

    # Мягкая валидация: дозаполняем отсутствующие ключи
    cfg.setdefault("profile_id", profile_id)
    cfg.setdefault("proxy", {"server": "", "username": "", "password": ""})
    cfg["proxy"].setdefault("server", "")
    cfg["proxy"].setdefault("username", "")
    cfg["proxy"].setdefault("password", "")
    cfg.setdefault("fingerprint", {})
    fp = cfg["fingerprint"]
    fp.setdefault("seed", _generate_seed())
    fp.setdefault("platform", "windows")
    fp.setdefault("user_agent", "")
    fp.setdefault("timezone", "system")
    fp.setdefault("locale", "system")
    fp.setdefault("geolocation", "default")
    fp.setdefault("screen", "auto")
    fp.setdefault("webgl", "auto")
    fp.setdefault("canvas", "real")
    fp.setdefault("audio", "real")
    fp.setdefault("client_rects", "real")
    fp.setdefault("speech_voices", "real")
    fp.setdefault("cpu_cores", "auto")
    fp.setdefault("ram_gb", "auto")
    fp.setdefault("webrtc", "proxy_only")
    fp.setdefault("dnt", "default")
    fp.setdefault("hardware_accel", True)
    cfg.setdefault("headless", False)
    cfg.setdefault("browser_channel", "auto")

    # Автомиграция старых конфигов (v1 с JS-шумами и ручным UA)
    if _migrate_config(profile_id, cfg):
        save_profile_config(profile_id, cfg)

    return cfg


def save_profile_config(profile_id: str, cfg: dict) -> None:
    """Сохраняет конфиг профиля."""
    p = _config_path(profile_id)
    try:
        cfg.setdefault("config_version", CONFIG_VERSION)
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
    """
    Обновляет User-Agent. Пустая строка = реальный UA браузера.
    ВНИМАНИЕ: ручной UA расходится с sec-ch-ua — антифрод это ловит.
    Пишем в fingerprint.user_agent (оттуда читает derive_fingerprint).
    """
    cfg = load_profile_config(profile_id)
    cfg["fingerprint"]["user_agent"] = (ua or "").strip()
    save_profile_config(profile_id, cfg)


def update_viewport(profile_id: str, width: int, height: int) -> None:
    """
    Задаёт размер экрана профиля.
    Пишем в fingerprint.screen (оттуда читает derive_fingerprint).
    width=0 → 'auto' (реальный экран).
    """
    cfg = load_profile_config(profile_id)
    if width and height:
        cfg["fingerprint"]["screen"] = {
            "width": int(width), "height": int(height)
        }
    else:
        cfg["fingerprint"]["screen"] = "auto"
    save_profile_config(profile_id, cfg)


def regenerate_fingerprint(profile_id: str) -> int:
    """Новая личность профиля: новый seed."""
    cfg = load_profile_config(profile_id)
    new_seed = _generate_seed()
    cfg["fingerprint"]["seed"] = new_seed
    save_profile_config(profile_id, cfg)
    return new_seed