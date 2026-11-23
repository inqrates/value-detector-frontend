# ui/secure_key.py
"""
Локальное хранилище мастер-ключа шифрования через Windows DPAPI.

ЗАЧЕМ:
  Раньше ключ Fernet выводился из захардкоженной строки _SECRET.
  Такой секрет можно извлечь из .pyc/бинарника (strings) и
  расшифровать session.enc / tokens.enc / cookies.

КАК ТЕПЕРЬ:
  Секрет (32 случайных байта) генерируется ОДИН раз при первом
  запуске и сохраняется в %APPDATA%\\ValueDetectorPro\\.key,
  зашифрованный через Windows DPAPI (CryptProtectData).

  DPAPI-ключ привязан к учётной записи Windows + машине:
    - файл .key нельзя расшифровать на другом ПК;
    - нельзя расшифровать под другим пользователем Windows;
    - в самом коде приложения секрета НЕТ.

  Итог: даже если распакуют EXE — секрета там нет, а .key
  бесполезен на чужой машине.

FALLBACK:
  Если DPAPI недоступен (не-Windows / нет pywin32) — ключ
  выводится из HWID (хуже, но приложение продолжает работать).
"""
import os
import base64
import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_KEY_FILENAME = ".key"

# Кэш, чтобы не дёргать DPAPI на каждый encrypt/decrypt
_cached_key: Optional[bytes] = None


def _key_path() -> str:
    from ui.paths import get_app_data_dir
    return os.path.join(get_app_data_dir(), _KEY_FILENAME)


# ============================================================
# DPAPI
# ============================================================
def _dpapi_protect(data: bytes) -> bytes:
    """Шифрует данные ключом текущего пользователя Windows."""
    import win32crypt
    # CryptProtectData(DataBlob, Description, OptionalEntropy,
    #                  Reserved, PromptStruct, Flags)
    return win32crypt.CryptProtectData(
        data, "ValueDetectorPro", None, None, None, 0
    )


def _dpapi_unprotect(blob: bytes) -> bytes:
    """Расшифровывает данные ключом текущего пользователя Windows."""
    import win32crypt
    # CryptUnprotectData возвращает (description, data)
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


def _fallback_derive() -> bytes:
    """
    Резервный ключ для не-Windows окружений.
    Хуже DPAPI (выводится из HWID), но приложение не падает.
    """
    try:
        from ui.login_window import get_hwid
        hwid = get_hwid()
    except Exception:
        hwid = ""
    return hashlib.sha256(f"vdp-fallback|{hwid}".encode("utf-8")).digest()


# ============================================================
# Публичный API
# ============================================================
def get_secret_key() -> bytes:
    """
    Возвращает 32-байтный мастер-ключ.

    Порядок:
      1) читаем .key и расшифровываем через DPAPI;
      2) если файла нет — генерируем случайный ключ,
         шифруем DPAPI и сохраняем;
      3) если DPAPI недоступен — fallback на HWID.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    path = _key_path()

    # 1) Существующий ключ
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                blob = f.read()
            raw = _dpapi_unprotect(blob)
            if len(raw) >= 32:
                _cached_key = raw
                logger.debug("Мастер-ключ загружен (DPAPI)")
                return raw
            logger.warning("Файл .key слишком короткий — перегенерирую")
        except Exception as e:
            logger.warning(f"Не удалось расшифровать .key через DPAPI: {e}")

    # 2) Новый случайный ключ
    raw = os.urandom(32)
    try:
        blob = _dpapi_protect(raw)
        with open(path, "wb") as f:
            f.write(blob)
        # Скрываем файл (Windows hidden attribute, best-effort)
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x02)
        except Exception:
            pass
        logger.info("Создан новый локальный мастер-ключ (DPAPI)")
    except Exception as e:
        logger.warning(
            f"DPAPI недоступен ({e}) — использую fallback-ключ из HWID"
        )
        raw = _fallback_derive()

    _cached_key = raw
    return raw


def get_fernet_key() -> bytes:
    """
    Возвращает ключ в формате Fernet (base64url от 32 байт).
    Используется в session_store и fast_config.
    """
    return base64.urlsafe_b64encode(get_secret_key()[:32])


def reset_key() -> None:
    """
    Удаляет .key и сбрасывает кэш.
    ВНИМАНИЕ: после этого старые session.enc / cookies.enc
    станут нечитаемыми (пользователь перелогинится).
    """
    global _cached_key
    _cached_key = None
    try:
        path = _key_path()
        if os.path.exists(path):
            os.remove(path)
            logger.info("Мастер-ключ удалён")
    except Exception as e:
        logger.warning(f"Не удалось удалить .key: {e}")
