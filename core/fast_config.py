"""
Конфиг fast-профиля для live-value стратегии.
Хранится в %APPDATA%/ValueDetectorPro/fast_config.json.
Пользователь вводит только Profile ID — всё остальное система делает сама.

Cookies хранятся в зашифрованном виде (%APPDATA%/fast_cookies/*.enc).
"""
import json
import logging
import os
import time
import hashlib
import base64
import glob
from typing import Optional, List, Dict

from ui.paths import get_app_data_dir

logger = logging.getLogger(__name__)

# ============================================================
# Ключ шифрования cookies (локальный мастер-ключ через DPAPI)
# ============================================================
def _derive_cookies_key() -> bytes:
    """
    Возвращает Fernet-ключ из локального мастер-ключа (DPAPI).
    Секрета в коде НЕТ — ключ хранится в %APPDATA%\\.key.
    """
    from ui.secure_key import get_fernet_key
    return get_fernet_key()

def _encrypt_cookies(data: str) -> bytes:
    """Шифрует JSON-строку cookies."""
    from cryptography.fernet import Fernet
    f = Fernet(_derive_cookies_key())
    return f.encrypt(data.encode("utf-8"))

def _decrypt_cookies(raw: bytes) -> str:
    """Расшифровывает cookies из bytes."""
    from cryptography.fernet import Fernet
    f = Fernet(_derive_cookies_key())
    return f.decrypt(raw).decode("utf-8")

# ============================================================
# Список всех fast БК (фиксированный, не настраивается)
# ============================================================
# needs_cookies:
# False — работает без cookies (HTTP через curl_cffi)
# True  — нужны cookies из профиля
# method:
# "http"    — обычный HTTP-парсинг (curl_cffi / httpx)
# "sse"     — HTTP + Server-Sent Events
# "ws"      — WebSocket
# "http_ws" — HTTP + WebSocket
FAST_BKS_ALL: Dict[str, dict] = {
    "fonbet":     {"needs_cookies": False,  "method": "http",    "label": "Fonbet"},
    "pari":       {"needs_cookies": False,  "method": "http",    "label": "Pari"},
    "olimp":      {"needs_cookies": False,  "method": "http",    "label": "Olimp"},
    "sportbet":   {"needs_cookies": False,  "method": "http",    "label": "Sportbet"},
    "betcity":    {"needs_cookies": True,   "method": "http",    "label": "Betcity"},
    "leon":       {"needs_cookies": True,   "method": "http",    "label": "Leon"},
    "marathon":   {"needs_cookies": True,   "method": "sse",     "label": "Marathon"},
    "ligastavok": {"needs_cookies": True,   "method": "http_ws", "label": "LigaStavok"},
    "winline":    {"needs_cookies": True,   "method": "ws",      "label": "Winline"},
    "zenit":      {"needs_cookies": True,   "method": "http_ws", "label": "Zenit"},
    "onewin":     {"needs_cookies": True,   "method": "http_ws", "label": "Onewin"},
}

ALL_FAST_BKS = FAST_BKS_ALL
DEFAULT_FAST_BKS = list(FAST_BKS_ALL.keys())
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

def cookies_path_encrypted(bk: str) -> str:
    """Путь к зашифрованному файлу cookies."""
    return os.path.join(_cookies_dir(), f"{bk.lower()}.enc")

def cookies_path_plaintext(bk: str) -> str:
    """Путь к старому plaintext-файлу (для миграции)."""
    return os.path.join(_cookies_dir(), f"{bk.lower()}.json")

def cookies_path(bk: str) -> str:
    """Возвращает зашифрованный путь (с поддержкой миграции)."""
    return cookies_path_encrypted(bk)

# ============================================================
# Загрузка / сохранение конфига
# ============================================================
def _default_config() -> dict:
    return {
        "profile_id": "",
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
    
    # Мягкая валидация (без api_key и exe_path)
    data.setdefault("profile_id", "")
    data.setdefault("headless", True)
    data.setdefault("refresh_interval_hours", COOKIES_TTL_HOURS_DEFAULT)
    data.setdefault("last_collect_at", "")
    data.setdefault("last_collect_status", {})
    
    # fast_bks — всегда полный список, из файла игнорируем
    data["fast_bks"] = list(FAST_BKS_ALL.keys())
    return data

def save_fast_config(cfg: dict) -> None:
    try:
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

def get_refresh_interval_hours() -> int:
    try:
        v = int(load_fast_config().get("refresh_interval_hours", COOKIES_TTL_HOURS_DEFAULT))
        return max(1, min(24, v))
    except Exception:
        return COOKIES_TTL_HOURS_DEFAULT

def is_fast_configured() -> bool:
    """Настроен ли профиль."""
    cfg = load_fast_config()
    return bool(cfg.get("profile_id"))

def get_enabled_fast_bks() -> List[str]:
    """Всегда возвращает весь список."""
    return list(FAST_BKS_ALL.keys())

def is_fast_bk_enabled(bk: str) -> bool:
    """Оставлено для совместимости. Все БК всегда включены."""
    bk = (bk or "").lower().strip()
    return bk in FAST_BKS_ALL

def bks_needing_cookies() -> List[str]:
    """Список БК, для которых нужно собирать cookies."""
    return [bk for bk, meta in FAST_BKS_ALL.items() if meta.get("needs_cookies")]

def bks_without_cookies() -> List[str]:
    """Список БК, работающих без cookies."""
    return [bk for bk, meta in FAST_BKS_ALL.items() if not meta.get("needs_cookies")]

# ============================================================
# Cookies: шифрованное сохранение / загрузка / возраст
# ============================================================
def save_cookies(bk: str, cookies: list) -> None:
    """Сохраняет cookies в зашифрованном виде."""
    try:
        json_data = json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2)
        encrypted = _encrypt_cookies(json_data)
        
        enc_path = cookies_path_encrypted(bk)
        with open(enc_path, "wb") as f:
            f.write(encrypted)
        
        # Удаляем старый plaintext-файл если есть
        old_path = cookies_path_plaintext(bk)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
        
        logger.info(f"[fast_cookies] {bk}: сохранено {len(cookies)} cookies (encrypted)")
    except Exception as e:
        logger.error(f"[fast_cookies] {bk}: {e}")

def load_cookies(bk: str) -> list:
    """Загружает cookies с поддержкой миграции (шифрованные → plaintext)."""
    enc_path = cookies_path_encrypted(bk)
    old_path = cookies_path_plaintext(bk)
    
    cookies_data = None
    
    # 1) Пробуем зашифрованный файл
    if os.path.exists(enc_path):
        try:
            with open(enc_path, "rb") as f:
                raw = f.read()
            decrypted = _decrypt_cookies(raw)
            data = json.loads(decrypted)
            cookies_data = data.get("cookies", [])
        except Exception as e:
            logger.warning(f"[fast_cookies] {bk}: не удалось расшифровать: {e}")
            cookies_data = None
    
    # 2) Fallback: старый plaintext
    if cookies_data is None and os.path.exists(old_path):
        try:
            with open(old_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cookies_data = data.get("cookies", [])
            # Мигрируем в зашифрованный формат
            save_cookies(bk, cookies_data)
            logger.info(f"[fast_cookies] {bk}: мигрировано из plaintext")
        except Exception as e:
            logger.warning(f"[fast_cookies] {bk}: не удалось прочитать plaintext: {e}")
            cookies_data = []
    
    return cookies_data or []

def cookies_age_hours(bk: str) -> Optional[float]:
    """Сколько часов назад сняли cookies. None если файла нет."""
    enc_path = cookies_path_encrypted(bk)
    old_path = cookies_path_plaintext(bk)
    
    # Проверяем зашифрованный файл
    p = enc_path if os.path.exists(enc_path) else old_path
    if not os.path.exists(p):
        return None
    try:
        return (time.time() - os.path.getmtime(p)) / 3600.0
    except Exception:
        return None

def cookies_need_refresh(bk: str) -> bool:
    """True, если cookies отсутствуют или старше TTL."""
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