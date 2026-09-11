# ui/components/sidebar.py
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar
)
from PyQt5.QtCore import Qt, pyqtSignal

from ui.user_session import user_session


class Sidebar(QFrame):
    nav_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Бренд
        title = QLabel("Value Detector")
        title.setObjectName("sidebarTitle")
        subtitle = QLabel("BetMach")
        subtitle.setObjectName("sidebarSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Карточка подписки (теперь из реальных данных)
        sub_card = QFrame()
        sub_card.setObjectName("subscriptionCard")
        sub_layout = QVBoxLayout(sub_card)
        sub_layout.setContentsMargins(16, 14, 16, 14)

        sub_top = QHBoxLayout()
        sub_label = QLabel(f"ПОДПИСКА {user_session.tariff}")
        sub_label.setObjectName("subDaysLabel")
        sub_top.addWidget(sub_label)
        sub_top.addStretch()
        sub_layout.addLayout(sub_top)

        days_row = QHBoxLayout()
        days_val = QLabel(str(user_session.days_left))
        days_val.setObjectName("subDaysValue")
        days_of = QLabel(f"из {user_session.days_total} дней")
        days_of.setObjectName("subDaysLabel")
        days_of.setAlignment(Qt.AlignBottom)
        days_row.addWidget(days_val)
        days_row.addWidget(days_of)
        days_row.addStretch()
        days_row.setSpacing(6)
        sub_layout.addLayout(days_row)

        self.sub_progress = QProgressBar()
        self.sub_progress.setRange(0, user_session.days_total)
        self.sub_progress.setValue(user_session.days_left)
        self.sub_progress.setTextVisible(False)
        self.sub_progress.setFixedHeight(6)
        self.sub_progress.setStyleSheet("""
            QProgressBar { background: rgba(255,255,255,0.08); border-radius: 3px; border: none; }
            QProgressBar::chunk { background: #08a7c8; border-radius: 3px; }
        """)
        sub_layout.addWidget(self.sub_progress)

        layout.addWidget(sub_card)

        # Карточка пользователя
        user_card = QFrame()
        user_card.setObjectName("userCard")
        user_layout = QHBoxLayout(user_card)
        user_layout.setContentsMargins(14, 12, 14, 12)

        avatar = QLabel(user_session.login[0].upper())
        avatar.setFixedSize(38, 38)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet("""
            background: rgba(8,167,200,0.18);
            color: #21c1de;
            border-radius: 19px;
            font-weight: 800; font-size: 16px;
        """)
        user_layout.addWidget(avatar)

        user_info = QVBoxLayout()
        user_info.setSpacing(2)
        uname = QLabel(user_session.login)
        uname.setObjectName("userName")
        urole = QLabel("Аккаунт активен")
        urole.setObjectName("userRole")
        user_info.addWidget(uname)
        user_info.addWidget(urole)
        user_layout.addLayout(user_info)
        user_layout.addStretch()
        layout.addWidget(user_card)

        layout.addSpacing(8)
        nav_label = QLabel("НАВИГАЦИЯ")
        nav_label.setStyleSheet("color: rgba(199,214,223,0.42); font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        layout.addWidget(nav_label)

        # Навигационные кнопки (6 пунктов, без Сигналов)
        self.nav_buttons = []
        nav_items = [
            ("📊  Дашборд", 0),
            ("📋  Логи", 1),
            ("🧠  Стратегии", 2),
            ("👤  Аккаунты", 3),
            ("⚽  Виды спорта", 4),
            ("⚙️  Настройки", 5),
        ]
        for text, idx in nav_items:
            btn = QPushButton(text)
            btn.setProperty("class", "navBtn")
            btn.setStyleSheet(btn.styleSheet())
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, i=idx: self._on_nav(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        self.nav_buttons[0].setChecked(True)
        layout.addStretch()

        # Статус подключения
        self.status_frame = QFrame()
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(4, 4, 4, 4)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(9, 9)
        self.status_dot.setStyleSheet("""
            background: #42d78d; border-radius: 4.5px;
            border: 3px solid rgba(66,215,141,0.15);
        """)
        self.status_text = QLabel("Подключено к серверу")
        self.status_text.setStyleSheet("color: rgba(199,214,223,0.62); font-size: 11px; font-weight: 600;")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_text)
        status_layout.addStretch()
        layout.addWidget(self.status_frame)

    def _on_nav(self, idx):
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == idx)
        self.nav_changed.emit(idx)

    def set_connection(self, connected: bool):
        if connected:
            self.status_dot.setStyleSheet("""
                background: #42d78d; border-radius: 4.5px;
                border: 3px solid rgba(66,215,141,0.15);
            """)
            self.status_text.setText("Подключено к серверу")
        else:
            self.status_dot.setStyleSheet("""
                background: #eb5757; border-radius: 4.5px;
                border: 3px solid rgba(235,87,87,0.15);
            """)
            self.status_text.setText("Нет соединения")