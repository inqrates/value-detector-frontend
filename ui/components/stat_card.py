"""Карточка статистики для дашборда."""
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel


class StatCard(QFrame):
    def __init__(self, value, label, color_class="statValue"):
        super().__init__()
        self.setProperty("class", "statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        self.value_label = QLabel(str(value))
        self.value_label.setProperty("class", color_class)
        self.label = QLabel(label)
        self.label.setProperty("class", "statLabel")

        layout.addWidget(self.value_label)
        layout.addWidget(self.label)

    def set_value(self, v):
        self.value_label.setText(str(v))