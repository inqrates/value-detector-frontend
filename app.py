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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
)

for _name in (
    'qasync', 'qasync._windows', 'qasync._windows._EventWorker',
    'qasync._QEventLoop',
    'urllib3', 'asyncio', 'playwright',
):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Наши модули — подробно, пока отлаживаем
logging.getLogger('ui.after_goal_engine').setLevel(logging.DEBUG)
logging.getLogger('ui.after_goal.betcity').setLevel(logging.DEBUG)
#logging.getLogger('ui.after_goal.olimp').setLevel(logging.DEBUG)
logging.getLogger('ui.after_goal.ligastavok').setLevel(logging.DEBUG)


def main():

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())
    app.setStyleSheet(APP_STYLE)

    # ---- Single-instance через QLockFile ----
    # Не даём запустить второй экземпляр, пока жив первый.
    # QLockFile сам определяет stale lock (умерший процесс).
    lock_path = os.path.join(tempfile.gettempdir(), "value_detector_pro.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(0)  # 0 = считать stale немедленно при смерти процесса

    if not lock.tryLock(100):
        print("⚠️ Приложение уже запущено.")
        print("   Закройте старое окно (или снимите задачу в диспетчере) и попробуйте снова.")
        sys.exit(1)

    # ---- qasync: интеграция PyQt5 и asyncio (для Playwright) ----
    # Без qasync asyncio.create_task не заработает из Qt-слотов.
    try:
        import qasync
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
    except ImportError:
        print("❌ Не установлен qasync — автоставки работать не будут.")
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
        print(f"⚠️ Ошибка восстановления сессии: {e}")

    if restored:
        print("🔓 Автовход по сохранённой сессии")
        on_login_success()
    else:
        login_win.show()

    # ---- Основной цикл ----
    if loop is not None:
        app.aboutToQuit.connect(lambda: loop.stop())
        with loop:
            loop.run_forever()
    else:
        app.exec_()

    # ---- Форсированный выход ----
    # После выхода из event loop любой оставшийся поток (Playwright,
    # QWebSocket, AdsPower) убьётся вместе с процессом.
    os._exit(0)


if __name__ == "__main__":
    main()