# app.py
import sys
import asyncio
from PyQt5.QtWidgets import QApplication
from qasync import QEventLoop

from ui.styles import create_dark_palette
from ui.main_window import MainWindow
from ui.login_window import LoginWindow


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())

    login = LoginWindow()

    def show_main():
        main_window = MainWindow()
        main_window.show()
        login.close()

    login.login_success.connect(show_main)
    login.show()

    with loop:
        sys.exit(loop.run_forever())