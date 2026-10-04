# core/fast_config.py
"""
Конфиг fast-профиля AdsPower для live-value стратегии.

Хранится в %APPDATA%/ValueDetectorPro/fast_config.json.

Пользователь вводит только Profile ID и API Key — всё остальное
система делает сама. Список fast БК фиксированный, не настраивается.

Структура:
{
  "profile_id": "k1abc1234",
  "api_key": "...",
  "headless": true,
  "refresh_interval_hours": 4,
  "last_collect_at": "2026-10-04T18:30:00",
  "last_collect_status": {
    "betcity": "ok",
    "leon": "ok",
    "marathon": "error: timeout"
  }
}
"""
import json
import logging
import os
import time
from typing import Optional, List, Dict

from ui.paths import get_app_data_dir

logger = logging.getLogger(__name__)


# ============================================================
# Список всех fast БК (фиксированный, не настраивается)
# ============================================================
# needs_cookies:
#   False — работает без cookies (HTTP через curl_cffi)
#   True  — нужны cookies из AdsPower-профиля
#
# method:
#   "http"    — обычный HTTP-парсинг (curl_cffi / httpx)
#   "sse"     — HTTP + Server-Sent Events
#   "ws"      — WebSocket
#   "http_ws" — HTTP + WebSocket
FAST_BKS_ALL: Dict[str, dict] = {
    "fonbet":     {"needs_cookies": False, "method": "http",    "label": "Fonbet"},
    "pari":       {"needs_cookies": False, "method": "http",    "label": "Pari"},
    "olimp":      {"needs_cookies": False, "method": "http",    "label": "Olimp"},
    "sportbet":   {"needs_cookies": False, "method": "http",    "label": "Sportbet"},
    "betcity":    {"needs_cookies": True,  "method": "http",    "label": "Betcity"},
    "leon":       {"needs_cookies": True,  "method": "http",    "label": "Leon"},
    "marathon":   {"needs_cookies": True,  "method": "sse",     "label": "Marathon"},
    "ligastavok": {"needs_cookies": True,  "method": "http_ws", "label": "LigaStavok"},
    "winline":    {"needs_cookies": True,  "method": "ws",      "label": "Winline"},
    "zenit":      {"needs_cookies": True,  "method": "http_ws", "label": "Zenit"},
}

# Обратная совместимость со старым именем
ALL_FAST_BKS = FAST_BKS_ALL

# Дефолт — весь список
DEFAULT_FAST_BKS = list(FAST_BKS_ALL.keys())

# TTL cookies (часы). Если cookies старше — движок их пересобирает.
COOKIES_TTL_HOURS_DEFAULT = 4


# ============================================================
# Пути
# ============================================================
def _path() -> str:
    return os.path.join(get_app_data_dir(), "fast_config.json")


def _cookies_dir() -> str:
    d = os.path.join(get_app_data_dir(), "fast_cookies")
    os.makedirs(d, exist_ok=True)
    return d


def cookies_path(bk: str) -> str:
    """Путь к файлу cookies для БК."""
    return os.path.join(_cookies_dir(), f"{bk.lower()}.json")


# ============================================================
# Загрузка / сохранение конфига
# ============================================================
def _default_config() -> dict:
    return {
        "profile_id": "",
        "api_key": "",
        "adspower_exe_path": "",  
        "headless": True,
        "refresh_interval_hours": COOKIES_TTL_HOURS_DEFAULT,
        "last_collect_at": "",
        "last_collect_status": {},
    }


def load_fast_config() -> dict:
    """Загружает fast_config.json. Если нет — создаёт дефолт."""
    p = _path()
    if not os.path.exists(p):
        default = _default_config()
        save_fast_config(default)
        return default

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"fast_config.json повреждён: {e}")
        return _default_config()

    # Мягкая валидация
    data.setdefault("profile_id", "")
    data.setdefault("api_key", "")
    data.setdefault("headless", True)
    data.setdefault("refresh_interval_hours", COOKIES_TTL_HOURS_DEFAULT)
    data.setdefault("last_collect_at", "")
    data.setdefault("last_collect_status", {})
    data.setdefault("adspower_exe_path", "")

    # fast_bks — всегда полный список, из файла игнорируем
    data["fast_bks"] = list(FAST_BKS_ALL.keys())

    return data


def save_fast_config(cfg: dict) -> None:
    try:
        # Всегда сохраняем полный список
        cfg["fast_bks"] = list(FAST_BKS_ALL.keys())
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить fast_config.json: {e}")


# ============================================================
# Геттеры
# ============================================================
def get_fast_profile_id() -> str:
    return (load_fast_config().get("profile_id") or "").strip()


def get_fast_api_key() -> str:
    return (load_fast_config().get("api_key") or "").strip()


def get_refresh_interval_hours() -> int:
    try:
        v = int(load_fast_config().get("refresh_interval_hours",
                                       COOKIES_TTL_HOURS_DEFAULT))
        return max(1, min(24, v))
    except Exception:
        return COOKIES_TTL_HOURS_DEFAULT


def is_fast_configured() -> bool:
    """Настроен ли профиль и API key."""
    cfg = load_fast_config()
    return bool(cfg.get("profile_id") and cfg.get("api_key"))


def get_enabled_fast_bks() -> List[str]:
    """Всегда возвращает весь список. Пользователь его не выбирает."""
    return list(FAST_BKS_ALL.keys())


def is_fast_bk_enabled(bk: str) -> bool:
    """Оставлено для совместимости. Все БК всегда включены."""
    bk = (bk or "").lower().strip()
    return bk in FAST_BKS_ALL


def bks_needing_cookies() -> List[str]:
    """Список БК, для которых нужно собирать cookies."""
    return [bk for bk, meta in FAST_BKS_ALL.items()
            if meta.get("needs_cookies")]


def bks_without_cookies() -> List[str]:
    """Список БК, работающих без cookies."""
    return [bk for bk, meta in FAST_BKS_ALL.items()
            if not meta.get("needs_cookies")]


# ============================================================
# Cookies: сохранение / загрузка / возраст
# ============================================================
def save_cookies(bk: str, cookies: list) -> None:
    try:
        with open(cookies_path(bk), "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies}, f, ensure_ascii=False, indent=2)
        logger.info(f"[fast_cookies] {bk}: сохранено {len(cookies)} cookies")
    except Exception as e:
        logger.error(f"[fast_cookies] {bk}: {e}")


def load_cookies(bk: str) -> list:
    p = cookies_path(bk)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get("cookies", [])
    except Exception:
        return []


def cookies_age_hours(bk: str) -> Optional[float]:
    """Сколько часов назад сняли cookies. None если файла нет."""
    p = cookies_path(bk)
    if not os.path.exists(p):
        return None
    try:
        return (time.time() - os.path.getmtime(p)) / 3600.0
    except Exception:
        return None


def cookies_need_refresh(bk: str) -> bool:
    """
    True, если cookies отсутствуют или старше TTL.
    Для БК без cookies — всегда False.
    """
    meta = FAST_BKS_ALL.get(bk.lower(), {})
    if not meta.get("needs_cookies"):
        return False
    age = cookies_age_hours(bk)
    if age is None:
        return True
    return age >= get_refresh_interval_hours()


def bks_need_refresh() -> List[str]:
    """Список БК, которым нужно пересобрать cookies прямо сейчас."""
    result = []
    for bk in bks_needing_cookies():
        if cookies_need_refresh(bk):
            result.append(bk)
    return result

def get_adspower_exe_path() -> str:
    return (load_fast_config().get("adspower_exe_path") or "").strip()


def set_adspower_exe_path(path: str) -> None:
    cfg = load_fast_config()
    cfg["adspower_exe_path"] = (path or "").strip()
    save_fast_config(cfg)