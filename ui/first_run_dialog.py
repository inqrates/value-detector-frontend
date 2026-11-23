"""
Окно первого запуска: скачивает Camoufox (патченный Firefox) при необходимости.
Показывается один раз — путь к браузеру сохраняется в кэше Camoufox.
"""
import os
import sys
import socket
import logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
    QHBoxLayout, QFrame,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

logger = logging.getLogger(__name__)


def _has_internet(timeout: float = 4.0) -> bool:
    """Проверяет, есть ли подключение к интернету (по DNS/TCP)."""
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 53), timeout=timeout)
            return True
        except OSError:
            continue
    return False


def _camoufox_browser_dir() -> str:
    """Путь, куда Camoufox кладёт скачанный Firefox."""
    base = os.environ.get("CAMOUFOX_BROWSER_PATH")
    if base:
        return base
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            local = os.path.expanduser("~")
        return os.path.join(local, "camoufox")
    return os.path.expanduser("~/.camoufox")


def _is_camoufox_installed() -> bool:
    d = _camoufox_browser_dir()
    if not os.path.isdir(d):
        return False
    # Ищем camoufox.exe / firefox.exe и проверяем, что > 1 МБ (не битый)
    for root, dirs, files in os.walk(d):
        for f in files:
            if f.lower() in ("camoufox.exe", "firefox.exe"):
                path = os.path.join(root, f)
                if os.path.getsize(path) > 1024 * 1024:
                    return True
    return False


class _FetchWorker(QThread):
    """Фоновый поток: скачивает Camoufox и шлёт прогресс в UI."""
    progress = pyqtSignal(str)      # текстовый статус
    done = pyqtSignal(bool, str)    # (success, message)

    def run(self):
        try:
            self.progress.emit("Инициализация...")
            if not _has_internet():
                self.done.emit(
                    False,
                    "Нет подключения к интернету. "
                    "Проверьте сеть и попробуйте снова.",
                )
                return
            # Camoufox сам скачает браузер при первом импорте AsyncCamoufox,
            # но мы форсируем через fetch с колбэком.
            try:
                from camoufox.fetcher import fetch_browser  # type: ignore[import]
                # fetch_browser(progress_cb=...) — если есть в твоей версии
                fetch_browser(progress_cb=lambda msg: self.progress.emit(msg))
            except ImportError:
                # Fallback: просто импортируем — компоненты скачаются сами
                self.progress.emit("Скачивание компонентов...")
                from camoufox import AsyncCamoufox  # noqa: F401
                self.progress.emit("Проверка компонентов...")
            self.done.emit(True, "Компоненты успешно установлены")
        except Exception as e:
            logger.error(f"Camoufox fetch error: {e}", exc_info=True)
            self.done.emit(False, f"Ошибка установки компонентов: {e}")


class FirstRunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Первый запуск — установка компонентов")
        self.setFixedSize(480, 280)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 24, 24, 24)

        # Заголовок
        title = QLabel("⚙️ Установка компонентов")
        title.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #f5fbff;"
        )
        root.addWidget(title)

        # Описание
        desc = QLabel(
            "Приложению требуются дополнительные компоненты для работы.\n\n"
            "Это разовая процедура. Компоненты сохранятся локально\n"
            "и не будут скачиваться повторно."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            "color: rgba(199,214,223,0.75); font-size: 12px;"
        )
        root.addWidget(desc)

        # Прогресс
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # бесконечный (indeterminate)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,0.08);
                border: none; border-radius: 4px;
            }
            QProgressBar::chunk {
                background: #21c1de; border-radius: 4px;
            }
        """)
        root.addWidget(self.progress_bar)

        # Статус
        self.status_label = QLabel("Проверка компонентов...")
        self.status_label.setStyleSheet(
            "color: rgba(199,214,223,0.6); font-size: 11px;"
        )
        root.addWidget(self.status_label)

        root.addStretch()

        # Кнопки (изначально скрыты, появятся при ошибке)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.retry_btn = QPushButton("Повторить")
        self.retry_btn.setProperty("class", "primaryBtn")
        self.retry_btn.setVisible(False)
        self.retry_btn.clicked.connect(self._on_retry)
        btn_row.addWidget(self.retry_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        # Запускаем worker
        self._worker = _FetchWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, msg: str):
        self.status_label.setText(msg)

    def _on_done(self, success: bool, message: str):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(message)
        if success:
            self.status_label.setStyleSheet(
                "color: #42d78d; font-size: 12px; font-weight: 600;"
            )
            # Автозакрытие через 1.5 сек
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1500, self.accept)
        else:
            self.status_label.setStyleSheet(
                "color: #eb5757; font-size: 12px; font-weight: 600;"
            )
            self.retry_btn.setVisible(True)
            self.close_btn.setVisible(True)

    def _on_retry(self):
        self.retry_btn.setVisible(False)
        self.close_btn.setVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.status_label.setText("Повторная попытка...")
        self.status_label.setStyleSheet(
            "color: rgba(199,214,223,0.6); font-size: 11px;"
        )
        self._worker = _FetchWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()


def ensure_camoufox_installed(parent=None) -> bool:
    """
    Главная точка входа. Вызывается из app.py ДО создания MainWindow.
    Возвращает True, если браузер готов к работе.
    """
    if _is_camoufox_installed():
        logger.info("[first_run] Camoufox уже установлен — пропускаем")
        return True

    logger.info("[first_run] Camoufox не найден — показываем окно установки")
    dlg = FirstRunDialog(parent)
    result = dlg.exec_()
    return result == QDialog.Accepted and _is_camoufox_installed()