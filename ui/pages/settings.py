# ui/pages/settings.py
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QGroupBox, QFormLayout, QPushButton,
    QScrollArea, QFrame
)
from PyQt5.QtCore import Qt

class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(16)

        # Уведомления
        notif_group = QGroupBox("Уведомления")
        notif_form = QFormLayout(notif_group)
        self.sound = QCheckBox("Звук при сигнале")
        self.sound.setChecked(True)
        notif_form.addRow(self.sound)
        self.telegram = QCheckBox("Telegram уведомления")
        notif_form.addRow(self.telegram)
        layout.addWidget(notif_group)

        # Авто-ставка (глобальная)
        auto_group = QGroupBox("Автоматизация")
        auto_form = QFormLayout(auto_group)
        self.global_auto_bet = QCheckBox("Разрешить авто-ставки")
        auto_form.addRow(self.global_auto_bet)
        self.confirm_before = QCheckBox("Запрашивать подтверждение перед ставкой")
        self.confirm_before.setChecked(True)
        auto_form.addRow(self.confirm_before)
        layout.addWidget(auto_group)

        save_btn = QPushButton("💾 Сохранить")
        save_btn.setProperty("class", "primaryBtn")
        layout.addWidget(save_btn)

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)