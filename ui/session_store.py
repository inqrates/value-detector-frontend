# ui/session_store.py
"""
Сохранение сессии между запусками приложения.

Позволяет не вводить логин/пароль каждый раз. Файл лежит в
%APPDATA%\\ValueDetectorPro\\session.enc — зашифрован через Fernet.

НЕ хранит пароль — только login/tariff/days_left + HWID + даты.
При старте проверяем:
  - файл существует и расшифровывается
  - HWID совпадает (не копировали на другой ПК)
  - не истёк TTL (12 часов по умолчанию)

Шифрование: Fernet (AES-128-CBC + HMAC-SHA256), ключ
выводится из фиксированного секрета + HWID, поэтому
файл привязан к конкретному ПК.

JWT-токены сохраняются отдельно в tokens.enc.
"""
import json
import os
import time
import hashlib
import base64
import logging
from typing import Optional

from ui.paths import get_app_data_dir
from ui.user_session import user_session

logger = logging.getLogger(__name__)

SESSION_TTL_SEC = 12 * 3600   # 12 часов


def _derive_key() -> bytes:
    """
    Возвращает Fernet-ключ из локального мастер-ключа (DPAPI).
    Секрета в коде НЕТ — ключ хранится в %APPDATA%\\.key,
    зашифрованном Windows DPAPI (привязан к ПК и пользователю).
    """
    from ui.secure_key import get_fernet_key
    return get_fernet_key()


def _encrypt(plaintext: str) -> bytes:
    """Шифрует строку в bytes через Fernet."""
    from cryptography.fernet import Fernet
    f = Fernet(_derive_key())
    return f.encrypt(plaintext.encode("utf-8"))


def _decrypt(ciphertext: bytes) -> str:
    """Расшифровывает bytes в строку через Fernet."""
    from cryptography.fernet import Fernet
    f = Fernet(_derive_key())
    return f.decrypt(ciphertext).decode("utf-8")


def _path() -> str:
    """Зашифрованный файл сессии."""
    return os.path.join(get_app_data_dir(), "session.enc")


def _old_path() -> str:
    """Старый plaintext-файл (для миграции)."""
    return os.path.join(get_app_data_dir(), "session.json")


def _tokens_path() -> str:
    """Зашифрованный файл JWT-токенов."""
    return os.path.join(get_app_data_dir(), "tokens.enc")


def _get_hwid() -> str:
    try:
        from ui.login_window import get_hwid
        return get_hwid()
    except Exception:
        return ""


def save_session(login: str, tariff: str, days_left: int,
                 days_total: int = 30) -> None:
    """Сохраняет сессию после успешного входа (зашифрованную)."""
    data = {
        "login": login,
        "tariff": tariff,
        "days_left": int(days_left),
        "days_total": int(days_total),
        "hwid": _get_hwid(),
        "saved_at": int(time.time()),
    }
    try:
        encrypted = _encrypt(json.dumps(data, ensure_ascii=False))
        with open(_path(), "wb") as f:
            f.write(encrypted)
        # Удаляем старый plaintext-файл если есть
        old = _old_path()
        if os.path.exists(old):
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Не удалось сохранить session.enc: {e}")
        # Fallback: plaintext (для отладки)
        try:
            with open(_old_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def save_session_tokens(login: str, access_token: str,
                        refresh_token: str) -> None:
    """Сохраняет JWT-токены в зашифрованном виде."""
    data = {
        "login": login,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "saved_at": int(time.time()),
    }
    try:
        encrypted = _encrypt(json.dumps(data, ensure_ascii=False))
        with open(_tokens_path(), "wb") as f:
            f.write(encrypted)
        logger.info("JWT-токены сохранены")
    except Exception as e:
        logger.warning(f"Не удалось сохранить JWT-токены: {e}")


def clear_session() -> None:
    """Удаляет сохранённую сессию (при выходе)."""
    for p in (_path(), _old_path(), _tokens_path()):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            logger.warning(f"Не удалось удалить {p}: {e}")


def clear_tokens() -> None:
    """Удаляет сохранённые JWT-токены."""
    try:
        if os.path.exists(_tokens_path()):
            os.remove(_tokens_path())
    except Exception as e:
        logger.warning(f"Не удалось удалить токены: {e}")


# ════════════════════════════════════════════════════════════
# Продление access-токена
# ════════════════════════════════════════════════════════════
# Access живёт 2 часа, WS-клиент ходит с ним вечно → через 2 часа
# сервер отвечает 403 (Starlette отдаёт 403, когда обработчик закрывает
# соединение до accept). Без продления приложение «ожидает» сигналы,
# которых не приходит, и выглядит как упавший бэкенд.
# Refresh-токен живёт столько же, сколько SESSION_TTL_SEC, поэтому
# продление возможно весь срок жизни сохранённой сессии.

ACCESS_REFRESH_MARGIN_SEC = 120      # продлеваем заранее, а не в ноль
_last_refresh_at: float = 0.0
REFRESH_MIN_INTERVAL_SEC = 30.0      # не дёргать /auth/refresh чаще


def jwt_expires_at(token: str) -> int:
    """exp из payload. Подпись НЕ проверяем — нам нужен только срок."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data.get("exp", 0) or 0)
    except Exception:
        return 0


def access_token_valid(token: str,
                       margin: int = ACCESS_REFRESH_MARGIN_SEC) -> bool:
    """True, если access-токен годен ещё дольше margin секунд."""
    exp = jwt_expires_at(token)
    return bool(exp) and exp > time.time() + margin


def refresh_access_token() -> Optional[str]:
    """
    Продлевает access по сохранённому refresh-токену.
    Возвращает свежий access (и сохраняет его) либо None — тогда
    нужна повторная авторизация.
    """
    global _last_refresh_at

    tokens = get_saved_tokens()
    if not tokens:
        return None
    access, refresh = tokens
    if access_token_valid(access):
        return access
    if not refresh:
        return None

    # Защита от штурма сервера, если refresh стабильно отклоняют
    now = time.time()
    if now - _last_refresh_at < REFRESH_MIN_INTERVAL_SEC:
        return None
    _last_refresh_at = now

    try:
        import requests
        from ui.config_loader import load_config
        backend_url = load_config().get(
            "backend_url", "http://localhost:8000")

        try:
            from ui.ssl_cert import build_requests_verify
            verify_target = build_requests_verify()
        except Exception:
            verify_target = True

        r = requests.post(
            f"{backend_url}/auth/refresh",
            json={"refresh_token": refresh},
            timeout=5,
            headers={
                "User-Agent": "ValueDetectorPro/1.0",
                "Content-Type": "application/json",
            },
            verify=verify_target,
        )
        data = r.json()
    except Exception as e:
        logger.warning(f"JWT refresh запрос не прошёл: {e}")
        return None

    new_access = data.get("access_token", "") if data.get("success") else ""
    if not new_access:
        logger.warning(
            f"JWT refresh отклонён сервером: {data.get('message', '')!r}"
        )
        return None

    save_session_tokens(
        login=user_session.login or data.get("login", ""),
        access_token=new_access,
        refresh_token=refresh,
    )
    logger.info("JWT access-токен продлён")
    return new_access


def try_restore_session() -> bool:
    """
    Пытается восстановить сессию при старте.
    Если успешно — заполняет user_session и возвращает True.

    Поддерживает миграцию: если session.enc не читается,
    пытается прочитать старый session.json и перешифровать.
    """
    data = None

    # 1) Пробуем зашифрованный файл
    path = _path()
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                encrypted = f.read()
            decrypted = _decrypt(encrypted)
            data = json.loads(decrypted)
        except Exception:
            data = None

    # 2) Fallback: старый plaintext
    if data is None:
        old = _old_path()
        if os.path.exists(old):
            try:
                with open(old, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Мигрируем: перешифровываем и удаляем старый
                if data:
                    save_session(
                        login=data.get("login", ""),
                        tariff=data.get("tariff", "Trial"),
                        days_left=int(data.get("days_left", 30)),
                        days_total=int(data.get("days_total", 30)),
                    )
                    logger.info("Сессия мигрирована из plaintext в зашифрованный формат")
            except Exception:
                data = None

    if not data:
        return False

    # Проверка HWID
    hwid_now = _get_hwid()
    if not hwid_now or data.get("hwid") != hwid_now:
        logger.info("Сессия не подходит: HWID не совпадает")
        clear_session()
        return False

    # Проверка TTL (12 часов)
    saved_at = int(data.get("saved_at", 0))
    age = time.time() - saved_at
    if age > SESSION_TTL_SEC:
        hours = int(age / 3600)
        logger.info(f"Сессия устарела ({hours} ч.)")
        clear_session()
        return False

    # Заполняем user_session
    user_session.set_user(
        login=data.get("login", "User"),
        tariff=data.get("tariff", "Trial"),
        days_left=int(data.get("days_left", 30)),
        days_total=int(data.get("days_total", 30)),
    )
    logger.info(
        f"Сессия восстановлена: {user_session.login} "
        f"({user_session.tariff}, {user_session.days_left} дн.)"
    )
    return True


def get_saved_tokens() -> Optional[tuple]:
    """
    Возвращает сохранённые JWT-токены: (access_token, refresh_token).
    Если токены не найдены или невалидны — возвращает None.
    """
    path = _tokens_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as f:
            encrypted = f.read()
        decrypted = _decrypt(encrypted)
        data = json.loads(decrypted)
        saved_at = int(data.get("saved_at", 0))

        # Токены тоже имеют TTL (совпадает с ACCESS_TOKEN_MINUTES)
        if time.time() - saved_at > SESSION_TTL_SEC:
            logger.info("JWT-токены устарели")
            clear_tokens()
            return None

        access = data.get("access_token", "")
        refresh = data.get("refresh_token", "")
        if access and refresh:
            logger.info("JWT-токены загружены")
            return (access, refresh)
    except Exception as e:
        logger.warning(f"Не удалось загрузить JWT-токены: {e}")
        clear_tokens()

    return None