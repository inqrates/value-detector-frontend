# ui/login_window.py
import hashlib
import uuid
import winreg
import requests
import wmi
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame
)
from PyQt5.QtCore import Qt, QPoint, pyqtSignal

from ui.styles import APP_STYLE
from ui.user_session import user_session
from ui.config_loader import load_config   # <-- импорт


# ---- Функция получения HWID ----
def get_hwid() -> str:
    """Собирает уникальный идентификатор железа."""
    try:
        c = wmi.WMI()
        disk = c.Win32_LogicalDisk(DeviceID="C:")[0]
        volume_serial = disk.VolumeSerialNumber
        cpu = c.Win32_Processor()[0].ProcessorId
        mac = uuid.getnode()
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        product_id = winreg.QueryValueEx(key, "ProductId")[0]
        data = f"{volume_serial}{cpu}{mac}{product_id}".encode()
        return hashlib.sha256(data).hexdigest()
    except Exception:
        # fallback
        return hashlib.sha256(f"{uuid.getnode()}{volume_serial}".encode()).hexdigest()


class LoginWindow(QWidget):
    login_success = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Value Detector Pro – Вход")
        self.setFixedSize(420, 580)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setStyleSheet(APP_STYLE)

        self._drag_pos = None

        # ---- Основной макет ----
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 24, 32, 24)
        main_layout.setSpacing(14)

        # ---- Заголовок с крестиком ----
        title_bar = QFrame()
        title_bar.setObjectName("loginTitleBar")
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet("""
            QFrame#loginTitleBar {
                background: transparent;
                border: none;
            }
        """)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel("Value Detector Pro")
        title_label.setStyleSheet("""
            color: #f5fbff;
            font-size: 16px;
            font-weight: 800;
        """)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(245,249,252,0.6);
                font-size: 18px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #eb5757;
                color: #fff;
                border-radius: 4px;
            }
        """)
        close_btn.clicked.connect(self.close)
        title_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        # ---- Логотип ----
        logo = QLabel("⚡")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size: 44px; color: #08a7c8;")
        main_layout.addWidget(logo)

        # ---- Название и подзаголовок ----
        title = QLabel("Вход в систему")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #f5fbff; font-size: 20px; font-weight: 700;")
        main_layout.addWidget(title)

        subtitle = QLabel("Введите логин и пароль для доступа")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: rgba(199,214,223,0.62); font-size: 13px;")
        main_layout.addWidget(subtitle)

        main_layout.addSpacing(6)

        # ---- Поле логина ----
        login_label = QLabel("Логин")
        login_label.setStyleSheet("color: rgba(245,249,252,0.8); font-size: 13px; font-weight: 600;")
        main_layout.addWidget(login_label)

        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("Введите логин")
        self.login_edit.setFixedHeight(40)
        self.login_edit.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.075);
                border-radius: 8px;
                padding: 8px 14px;
                color: #f5fbff;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: rgba(8,167,200,0.56);
            }
        """)
        main_layout.addWidget(self.login_edit)

        # ---- Поле пароля ----
        pass_label = QLabel("Пароль")
        pass_label.setStyleSheet("color: rgba(245,249,252,0.8); font-size: 13px; font-weight: 600;")
        main_layout.addWidget(pass_label)

        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("Введите пароль")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setFixedHeight(40)
        self.pass_edit.setStyleSheet(self.login_edit.styleSheet())
        self.pass_edit.returnPressed.connect(self._on_login)
        main_layout.addWidget(self.pass_edit)

        main_layout.addSpacing(8)

        # ---- Кнопка входа ----
        self.login_btn = QPushButton("Войти")
        self.login_btn.setFixedHeight(46)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background: #08a7c8;
                border: none;
                border-radius: 8px;
                color: #fff;
                font-weight: 700;
                font-size: 15px;
            }
            QPushButton:hover {
                background: #21c1de;
            }
            QPushButton:pressed {
                background: #0790ab;
            }
        """)
        self.login_btn.clicked.connect(self._on_login)
        main_layout.addWidget(self.login_btn)

        # ---- Ошибка ----
        self.error_label = QLabel("")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setStyleSheet("color: #eb5757; font-size: 13px; font-weight: 600;")
        self.error_label.hide()
        main_layout.addWidget(self.error_label)

        main_layout.addStretch()

        # ---- Версия ----
        version = QLabel("v1.0.0")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("color: rgba(199,214,223,0.32); font-size: 11px;")
        main_layout.addWidget(version)

        # ---- Общий фон ----
        self.setStyleSheet(self.styleSheet() + """
            QWidget {
                background-color: #0b0d0f;
                border-radius: 12px;
            }
        """)

    # ---- Перетаскивание окна ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ---- Авторизация ----
    def _on_login(self):
        login = self.login_edit.text().strip()
        password = self.pass_edit.text().strip()

        if not login or not password:
            self.error_label.setText("Заполните все поля")
            self.error_label.show()
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("Проверка...")
        self.error_label.hide()

        try:
            config = load_config()
            backend_url = config.get('backend_url', 'http://localhost:8000')

            hwid = get_hwid()
            response = requests.post(
                f"{backend_url}/auth/login",
                json={"login": login, "password": password, "hwid": hwid},
                timeout=5
            )
            data = response.json()
            if data.get("success"):
                user_data = data.get("user", {})
                user_session.set_user(
                    login=user_data.get("login", login),
                    tariff=user_data.get("tariff", "Trial"),
                    days_left=user_data.get("days_left", 30)
                )
                self.error_label.hide()
                self.login_success.emit()
                self.close()
            else:
                self.error_label.setText(data.get("message", "Ошибка входа"))
                self.error_label.show()
        except requests.exceptions.ConnectionError:
            self.error_label.setText("Не удалось подключиться к серверу")
            self.error_label.show()
        except Exception as e:
            self.error_label.setText(f"Ошибка: {str(e)}")
            self.error_label.show()
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("Войти")