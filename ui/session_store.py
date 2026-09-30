# ui/session_store.py
"""
Сохранение сессии между запусками приложения.

Позволяет не вводить логин/пароль каждый раз. Файл лежит в
%APPDATA%\\ValueDetectorPro\\session.json — доступен только
текущему пользователю Windows.

НЕ хранит пароль — только login/tariff/days_left + HWID + даты.
При старте проверяем:
  - файл существует
  - HWID совпадает (не копировали на другой ПК)
  - не истёк TTL (12 часов по умолчанию)
"""
import json
import os
import time
from typing import Optional

from ui.paths import get_app_data_dir
from ui.user_session import user_session


SESSION_TTL_SEC = 12 * 3600   # 12 часов


def _path() -> str:
    return os.path.join(get_app_data_dir(), "session.json")


def _get_hwid() -> str:
    """Импорт из login_window, чтобы не дублировать логику."""
    try:
        from ui.login_window import get_hwid
        return get_hwid()
    except Exception:
        return ""


def save_session(login: str, tariff: str, days_left: int,
                 days_total: int = 30) -> None:
    """Сохраняет сессию после успешного входа."""
    data = {
        "login": login,
        "tariff": tariff,
        "days_left": int(days_left),
        "days_total": int(days_total),
        "hwid": _get_hwid(),
        "saved_at": int(time.time()),
    }
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить session.json: {e}")


def clear_session() -> None:
    """Удаляет сохранённую сессию (при выходе)."""
    try:
        if os.path.exists(_path()):
            os.remove(_path())
    except Exception as e:
        print(f"⚠️ Не удалось удалить session.json: {e}")


def try_restore_session() -> bool:
    """
    Пытается восстановить сессию при старте.
    Если успешно — заполняет user_session и возвращает True.
    Иначе — False (надо логиниться).
    """
    path = _path()
    if not os.path.exists(path):
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        clear_session()
        return False

    # Проверка HWID
    hwid_now = _get_hwid()
    if not hwid_now or data.get("hwid") != hwid_now:
        print("⚠️ Сессия не подходит: HWID не совпадает")
        clear_session()
        return False

    # Проверка TTL (12 часов)
    saved_at = int(data.get("saved_at", 0))
    age = time.time() - saved_at
    if age > SESSION_TTL_SEC:
        hours = int(age / 3600)
        print(f"⚠️ Сессия устарела ({hours} ч.)")
        clear_session()
        return False

    # Заполняем user_session
    user_session.set_user(
        login=data.get("login", "User"),
        tariff=data.get("tariff", "Trial"),
        days_left=int(data.get("days_left", 30)),
        days_total=int(data.get("days_total", 30)),
    )
    print(f"✅ Сессия восстановлена: {user_session.login} "
          f"({user_session.tariff}, {user_session.days_left} дн.)")
    return True