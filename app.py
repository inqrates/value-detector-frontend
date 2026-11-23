# app.py
import sys
import os
import asyncio
import tempfile

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QLockFile

from ui.styles import APP_STYLE, create_dark_palette
from ui.login_window import LoginWindow
from ui.main_window import MainWindow

import logging
from ui.log_paths import get_log_path, cleanup_old_logs

# ── Логи: консоль + файлы в %APPDATA%\ValueDetectorPro\logs\YYYY-MM-DD\ ──
# Тестировщик присылает папку за нужный день — там всё для отладки.
_FORMAT = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
)

# В windowed-сборке PyInstaller (console=False) sys.stdout = None.
# StreamHandler(None) выбросит AttributeError при первой записи —
# поэтому добавляем только если stdout реально живой.
_handlers = []
if sys.stdout is not None:
    _handlers.append(logging.StreamHandler(sys.stdout))

try:
    _app_handler = logging.FileHandler(
        get_log_path("app.log"), encoding="utf-8"
    )
    _app_handler.setLevel(logging.INFO)
    _app_handler.setFormatter(_FORMAT)
    _handlers.append(_app_handler)

    _err_handler = logging.FileHandler(
        get_log_path("errors.log"), encoding="utf-8"
    )
    _err_handler.setLevel(logging.WARNING)
    _err_handler.setFormatter(_FORMAT)
    _handlers.append(_err_handler)
except Exception as _e:
    print(f"[!] Не удалось открыть файл лога: {_e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=_handlers,
    force=True,
)

# Чистим старые папки логов (retention 7 дней)
try:
    _removed = cleanup_old_logs(keep_days=7)
    if _removed:
        logging.getLogger(__name__).info(
            f"🧹 Удалено старых папок логов: {_removed}"
        )
except Exception:
    pass

for _name in (
    'qasync', 'qasync._windows', 'qasync._windows._EventWorker',
    'qasync._QEventLoop',
    'urllib3', 'asyncio', 'playwright',
):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Наши модули — подробно, пока отлаживаем
# Наши модули — обычный уровень. DEBUG включаем только при отладке
# конкретной проблемы.
logging.getLogger('ui.after_goal_engine').setLevel(logging.INFO)
logging.getLogger('ui.after_goal.betcity').setLevel(logging.INFO)
logging.getLogger('ui.after_goal.ligastavok').setLevel(logging.INFO)

# ── Фильтр SPA-шума Marathon ──
# Оставляем полезное (listener готов, hook installed, SSE-хук),
# убираем весь JS-хлам (MARATHON_HOOK, REQ/RESP, mindbox, webpush).
class _MarathonNoiseFilter(logging.Filter):
    NOISE = (
        "MARATHON_HOOK",
        "MARATHON_XHR_SSE",
        "Marathon REQ:",
        "Marathon RESP:",
        "Marathon SSE hook",
        "Marathon MODELS_DUMP",
        "Marathon console[log]",
        "Marathon console[info]",
        "Marathon console[debug]",
        "Marathon console[warning]",
        "mindbox",
        "Banner not shown",
        "already closed",
        "webpush",
    )
    def filter(self, record):
        msg = record.getMessage()
        return not any(n in msg for n in self.NOISE)

logging.getLogger('ui.after_goal.marathon').addFilter(_MarathonNoiseFilter())

def main():

    # Глобальный перехватчик: любое необработанное исключение
    # попадёт в errors.log — иначе в console=False увидим только
    # системный попап без деталей.
    import traceback
    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("unhandled").error(
            "Необработанное исключение",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())
    app.setStyleSheet(APP_STYLE)

    # ---- Single-instance через QLockFile ----
    # Не даём запустить второй экземпляр, пока жив первый.
    # QLockFile сам определяет stale lock (умерший процесс).
    # ---- Компоненты: первый запуск ----
    from ui.first_run_dialog import ensure_camoufox_installed
    from PyQt5.QtWidgets import QMessageBox

    while True:
        if ensure_camoufox_installed():
            break
        reply = QMessageBox.question(
            None,
            "Компоненты не установлены",
            "Не удалось установить необходимые компоненты.\n\n"
            "Повторить попытку? (проверьте подключение к интернету)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            print("[X] Компоненты не установлены. Приложение не может работать без них.")
            sys.exit(1)
    lock_path = os.path.join(tempfile.gettempdir(), "value_detector_pro.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(0)  # 0 = считать stale немедленно при смерти процесса

    if not lock.tryLock(100):
        print("[!] Приложение уже запущено.")
        print("   Закройте старое окно (или снимите задачу в диспетчере) и попробуйте снова.")
        sys.exit(1)

    # ---- qasync: интеграция PyQt5 и asyncio (для Playwright) ----
    # Без qasync asyncio.create_task не заработает из Qt-слотов.
    # ВАЖНО: ставим loop ДО autostart AdsPower, иначе
    # asyncio.ensure_future привяжет задачу к мёртвому дефолтному loop.
    try:
        import qasync
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
    except ImportError:
        print("[X] Не установлен qasync — автоставки работать не будут.")
        print("   Установите командой: pip install qasync")
        loop = None


    # ---- Окно входа ----
    login_win = LoginWindow()
    main_holder = {"win": None}

    def on_login_success():
        w = MainWindow()
        w.show()
        main_holder["win"] = w

    login_win.login_success.connect(on_login_success)

    # ── Автологин через сохранённую сессию ──
    restored = False
    try:
        from ui.session_store import try_restore_session
        restored = try_restore_session()
    except Exception as e:
        print(f"[!] Ошибка восстановления сессии: {e}")

    if restored:
        print("[OK] Автовход по сохранённой сессии")
        on_login_success()
    else:
        login_win.show()

    # ---- Основной цикл ----
    if loop is not None:
        app.aboutToQuit.connect(lambda: loop.stop())
        try:
            loop.run_forever()
        except Exception as _e:
            logging.getLogger(__name__).warning(f"Loop error: {_e}")
        finally:
            # ── ФИКС 2026-10-29: НЕ используем 'with loop:' ──
            # 'with loop:' вызывает loop.close(), который ждёт
            # завершения ВСЕХ потоков (asyncio.to_thread/Playwright/
            # QWebSocket). Если хоть один висит — loop.close() виснет
            # навсегда, и os._exit(0) НИКОГДА не вызывается.
            # Поэтому мы выходим из run_forever() напрямую и сразу
            # идём в os._exit(0), который убьёт всё вместе с процессами.
            pass
    else:
        app.exec_()

    # ---- Форсированный выход ----
    # os._exit(0) убивает процесс мгновенно, игнорируя все
    # незакрытые потоки. Это ГАРАНТИРУЕТ, что процесс не останется
    # висеть в диспетчере задач.
    logging.getLogger(__name__).info("🛑 Форсированный выход (os._exit)")
    os._exit(0)



if __name__ == "__main__":
    main()