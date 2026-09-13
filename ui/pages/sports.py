"""Страница «Виды спорта»."""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QGridLayout
)


# (иконка, название, активен, инфо)
SPORTS = [
    ("🏓", "Настольный теннис", True,  "9 БК • активен"),
    ("🏐", "Волейбол",          True,  "9 БК • активен"),
    ("🏀", "Баскетбол",         True,  "9 БК • активен"),
    ("🎮", "Кибербаскетбол",    True,  "8 БК • активен"),
    ("⚽", "Футбол",            False, "Скоро"),
    ("🎾", "Большой теннис",    False, "Скоро"),
    ("🏒", "Хоккей",            False, "Скоро"),
]


class SportsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = QLabel("Виды спорта")
        title.setProperty("class", "sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Парсинг работает по всем активным видам сразу. "
            "Сигналы, фильтры и автоставки учитывают вид спорта автоматически."
        )
        subtitle.setStyleSheet("color: rgba(199,214,223,0.58); font-size: 13px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(14)

        for i, (icon, name, active, info) in enumerate(SPORTS):
            card = QFrame()
            card.setProperty("class", "sectionCard")
            card.setFixedHeight(120)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(6)

            icon_label = QLabel(icon)
            icon_label.setStyleSheet("font-size: 26px;")
            card_layout.addWidget(icon_label)

            name_label = QLabel(name)
            name_label.setStyleSheet(
                "font-weight: 700; font-size: 14px; color: #f5fbff;"
            )
            card_layout.addWidget(name_label)

            info_label = QLabel(info)
            if active:
                info_label.setStyleSheet(
                    "color: #42d78d; font-size: 11px; font-weight: 600;"
                )
            else:
                info_label.setStyleSheet(
                    "color: rgba(199,214,223,0.42); font-size: 11px; font-weight: 600;"
                )
            card_layout.addWidget(info_label)

            card_layout.addStretch()

            grid.addWidget(card, i // 4, i % 4)

        layout.addLayout(grid)
        layout.addStretch()