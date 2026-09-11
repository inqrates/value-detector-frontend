# ui/pages/advisor.py
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QComboBox, QLineEdit
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from datetime import datetime

from ui.components.stat_card import StatCard


def _center(text, color=None):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignCenter)
    if color:
        item.setForeground(color)
    return item


class AdvisorPage(QWidget):
    def __init__(self):
        super().__init__()
        self.events = []  # список для отображения
        self._event_cache = {}  # ключ: (match_id, type), значение: последнее событие
        self.filter_type = "Все"

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Карточки статистики
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.card_total = StatCard(0, "Всего событий", "statValue")
        self.card_signals = StatCard(0, "Задержки", "statValueWarn")
        self.card_arbitrage = StatCard(0, "Вилки", "statValueAccent")
        self.card_value = StatCard(0, "Валуи", "statValueGood")
        self.card_corridor = StatCard(0, "Коридоры", "statValue")
        for c in (self.card_total, self.card_signals, self.card_arbitrage,
                  self.card_value, self.card_corridor):
            cards_row.addWidget(c)
        layout.addLayout(cards_row)

        # Фильтры
        filter_card = QFrame()
        filter_card.setProperty("class", "sectionCard")
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(14, 12, 14, 12)
        filter_layout.setSpacing(12)

        filter_layout.addWidget(QLabel("Тип:"))
        self.type_filter = QComboBox()
        self.type_filter.addItems(["Все", "Задержка", "Вилка", "Валуй", "Коридор", "Рекомендация"])
        self.type_filter.currentTextChanged.connect(self._refresh)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addSpacing(16)
        filter_layout.addWidget(QLabel("Поиск:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Игроки...")
        self.search.textChanged.connect(self._refresh)
        filter_layout.addWidget(self.search)

        filter_layout.addStretch()
        layout.addWidget(filter_card)

        # Таблица событий
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Тип", "Матч", "Детали", "БК", "Значение", "Время"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setShowGrid(False)
        layout.addWidget(self.table)

        layout.addStretch()

    def add_event(self, event_type: str, payload: dict):
        """
        Добавляет событие в ленту.
        - Для signal используется is_new для фильтрации.
        - Для arbitrage, value, corridor обновляет существующую запись по match_id.
        """
        now = datetime.now().strftime("%H:%M:%S")

        if event_type == "signal":
            # Показываем только новые задержки (is_new == True)
            if not payload.get("is_new", False):
                return
            row = {
                "type": "Задержка",
                "match": f"{payload['match_teams'][0]} vs {payload['match_teams'][1]}",
                "details": f"Сет: {payload['score'][0]}:{payload['score'][1]} | Счет: {payload['sub_score'][0]}:{payload['sub_score'][1]}",
                "bk": f"{payload['fast_bk']} → {payload['slow_bk']}",
                "value": f"Задержка {payload['delay']:.1f}с",
                "time": now,
                "color": QColor("#f2c94c"),
                "match_id": payload.get('match_id', '')
            }
            # Для сигналов добавляем новую запись (так как они новые)
            self.events.insert(0, row)
            self._refresh()
            return

        # Для остальных типов используем кэш по match_id + event_type
        match_id = payload.get('match_id') or payload.get('match_id_p1') or payload.get('match_id1')
        if not match_id:
            # Если match_id нет, просто добавляем (редкий случай)
            self._add_raw_event(event_type, payload, now)
            return

        cache_key = (match_id, event_type)
        # Формируем строку события
        row = self._create_event_row(event_type, payload, now)
        if not row:
            return

        # Обновляем кэш
        self._event_cache[cache_key] = row

        # Перестраиваем список events из кэша (сортируем по времени)
        self._rebuild_events_from_cache()
        self._refresh()

    def _create_event_row(self, event_type, payload, time_str):
        """Создаёт словарь события для разных типов."""
        if event_type == "arbitrage":
            return {
                "type": "Вилка",
                "match": f"{payload['player1']} vs {payload['player2']}",
                "details": f"П1={payload['best_p1']:.2f} / П2={payload['best_p2']:.2f}",
                "bk": f"{payload['bk_p1']} / {payload['bk_p2']}",
                "value": f"Прибыль {payload['profit_percent']:.2f}%",
                "time": time_str,
                "color": QColor("#21c1de"),
                "match_id": payload.get('match_id_p1', '')
            }
        elif event_type == "value":
            return {
                "type": "Валуй",
                "match": f"{payload['player1']} vs {payload['player2']}",
                "details": f"{payload['outcome']} {payload['odd']:.2f}",
                "bk": payload['bk'],
                "value": f"Выше среднего в {payload['ratio']:.2f}x",
                "time": time_str,
                "color": QColor("#42d78d"),
                "match_id": payload.get('match_id', '')
            }
        elif event_type == "corridor":
            return {
                "type": "Коридор",
                "match": f"{payload['player1']} vs {payload['player2']}",
                "details": f"Линии {payload['line1']:.1f} / {payload['line2']:.1f}",
                "bk": f"{payload['bk1']} / {payload['bk2']}",
                "value": f"profit {payload['profit']:.2f}",
                "time": time_str,
                "color": QColor("#ff8c42"),
                "match_id": payload.get('match_id1', '')
            }
        elif event_type == "missed_opportunity":
            return {
                "type": "Рекомендация",
                "match": f"{payload.get('player1', '')} vs {payload.get('player2', '')}",
                "details": payload.get('message', ''),
                "bk": payload.get('bk', ''),
                "value": payload.get('recommendation', ''),
                "time": time_str,
                "color": QColor("#9b59b6"),
                "match_id": payload.get('match_id', '')
            }
        return None

    def _add_raw_event(self, event_type, payload, time_str):
        """Добавляет событие без кэширования (fallback)."""
        row = self._create_event_row(event_type, payload, time_str)
        if row:
            self.events.append(row)
            self._refresh()

    def _rebuild_events_from_cache(self):
        """Перестраивает список events из кэша (сортировка по времени)."""
        # Берем все записи из кэша и сортируем по времени (новые сверху)
        self.events = list(self._event_cache.values())
        # Сортировка по времени (предполагаем, что время в формате HH:MM:SS)
        self.events.sort(key=lambda x: x["time"], reverse=True)

    def _refresh(self):
        filter_type = self.type_filter.currentText()
        search_text = self.search.text().strip().lower()

        filtered = []
        for ev in self.events:
            if filter_type != "Все" and ev["type"] != filter_type:
                continue
            if search_text and search_text not in ev["match"].lower():
                continue
            filtered.append(ev)

        self.table.setRowCount(len(filtered))
        for r, ev in enumerate(filtered):
            self.table.setItem(r, 0, _center(ev["type"], ev.get("color")))
            self.table.setItem(r, 1, _center(ev["match"]))
            self.table.setItem(r, 2, _center(ev["details"]))
            self.table.setItem(r, 3, _center(ev["bk"]))
            self.table.setItem(r, 4, _center(ev["value"], QColor("#f2c94c")))
            self.table.setItem(r, 5, _center(ev["time"]))

        # Обновляем карточки
        total = len(self.events)
        signals = sum(1 for e in self.events if e["type"] == "Задержка")
        arb = sum(1 for e in self.events if e["type"] == "Вилка")
        val = sum(1 for e in self.events if e["type"] == "Валуй")
        corr = sum(1 for e in self.events if e["type"] == "Коридор")

        self.card_total.set_value(total)
        self.card_signals.set_value(signals)
        self.card_arbitrage.set_value(arb)
        self.card_value.set_value(val)
        self.card_corridor.set_value(corr)

    # Для обратной совместимости
    def add_opportunity(self, opp_type, payload):
        self.add_event(opp_type, payload)

    def update_stats(self, payload):
        pass