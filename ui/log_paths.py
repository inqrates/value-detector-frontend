# ui/log_paths.py
"""
Пути для логов приложения.

Все логи пишутся в %APPDATA%\\ValueDetectorPro\\logs\\YYYY-MM-DD\\
- app.log           — все INFO и выше
- errors.log        — только WARNING и ERROR
- bets_diag.log     — дневник ставок (пишет after_goal_engine)
- marathon_diag.log — аномалии Marathon

Разбивка по датам: удобно отдавать тестировщику папку за конкретный
день. Старые папки удаляются автоматически (retention 7 дней).

Работает и в .py, и в PyInstaller-сборке — путь всегда в APPDATA,
не рядом с .exe (там может быть Program Files, куда нет прав).
"""
import os
import shutil
from datetime import date
from typing import Optional

from ui.paths import get_app_data_dir


DEFAULT_RETENTION_DAYS = 7


def get_logs_root() -> str:
    """Корневая папка логов: %APPDATA%\\ValueDetectorPro\\logs"""
    d = os.path.join(get_app_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def get_day_dir(day: Optional[date] = None) -> str:
    """Папка логов за конкретный день."""
    day = day or date.today()
    d = os.path.join(get_logs_root(), day.isoformat())
    os.makedirs(d, exist_ok=True)
    return d


def get_log_path(name: str, day: Optional[date] = None) -> str:
    """Полный путь к файлу лога за день."""
    return os.path.join(get_day_dir(day), name)


def cleanup_old_logs(keep_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """
    Удаляет папки старше keep_days. Возвращает количество удалённых.
    Папки с именами не в формате YYYY-MM-DD игнорируются.
    """
    root = get_logs_root()
    today = date.today()
    removed = 0
    try:
        entries = os.listdir(root)
    except Exception:
        return 0

    for entry in entries:
        full = os.path.join(root, entry)
        if not os.path.isdir(full):
            continue
        try:
            d = date.fromisoformat(entry)
        except ValueError:
            continue
        if (today - d).days > keep_days:
            try:
                shutil.rmtree(full)
                removed += 1
            except Exception:
                pass
    return removed