# ui/pages/dashboard.py
import json
import os
import asyncio
from datetime import date
from typing import Dict

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from ui.components.stat_card import StatCard
from ui.strategy_store import StrategyStore
from ui.paths import get_app_data_dir


def _center_item(text, color=None):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignCenter)
    if color:
        item.setForeground(color)
    return item


class DashboardPage(QWidget):
    def __init__(self, store: StrategyStore):
        super().__init__()
        self.store = store
        self.accounts = []
        self.advisor_stats = {}
        self.signal_count_today = 0

        # ── Балансы активных БК ──
        self._balances: Dict[str, float] = {}

        # ── Статистика ставок за сегодня ──
        self._turnover_today: float = 0.0
        self._bets_count_today: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # ---- Карточки статистики ----
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)

        self.card_bk = StatCard(0, "БК в работе", "statValueAccent")
        self.card_signals = StatCard(0, "Сигналов сегодня", "statValue")
        self.card_turnover = StatCard("0 ₽", "Оборот сегодня", "statValueGood")
        self.card_bets = StatCard(0, "Ставок сегодня", "statValue")
        self.card_tracked = StatCard(0, "БК отслеживается", "statValueWarn")

        for c in [self.card_bk, self.card_signals,
                  self.card_turnover, self.card_bets, self.card_tracked]:
            stats_row.addWidget(c, 1)   # ← stretch=1, растягиваем равномерно

        layout.addLayout(stats_row)

        # Строка со сбросом под карточками (выровнено вправо)
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 0, 0, 0)

        reset_btn = QPushButton("↻  Сбросить за сегодня")
        reset_btn.setToolTip("Обнулить оборот и счётчик ставок за сегодня")
        reset_btn.setProperty("class", "ghostBtn")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(32)
        reset_btn.clicked.connect(self._reset_bets_stats)
        reset_row.addStretch()
        reset_row.addWidget(reset_btn)
        layout.addLayout(reset_row)

        # ---- Таблица БК ----
        bk_title = QLabel("Букмекерские конторы в работе")
        bk_title.setProperty("class", "sectionTitle")
        layout.addWidget(bk_title)

        bk_card = QFrame()
        bk_card.setProperty("class", "sectionCard")
        bk_layout = QVBoxLayout(bk_card)
        bk_layout.setContentsMargins(0, 0, 0, 0)

        self.bk_table = QTableWidget()
        self.bk_table.setColumnCount(3)
        self.bk_table.setHorizontalHeaderLabels(["БК", "Баланс", "Профиль"])
        self.bk_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bk_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.bk_table.verticalHeader().setVisible(False)
        self.bk_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bk_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bk_table.setShowGrid(False)
        bk_layout.addWidget(self.bk_table)
        layout.addWidget(bk_card)

        # ---- Таблица стратегий ----
        st_title = QLabel("Активные стратегии")
        st_title.setProperty("class", "sectionTitle")
        layout.addWidget(st_title)

        st_card = QFrame()
        st_card.setProperty("class", "sectionCard")
        st_layout = QVBoxLayout(st_card)
        st_layout.setContentsMargins(0, 0, 0, 0)

        self.strategy_table = QTableWidget()
        self.strategy_table.setColumnCount(4)
        self.strategy_table.setHorizontalHeaderLabels(
            ["Стратегия", "Ставок", "Прибыль", "Статус"])
        self.strategy_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.strategy_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.strategy_table.verticalHeader().setVisible(False)
        self.strategy_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.strategy_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.strategy_table.setShowGrid(False)
        st_layout.addWidget(self.strategy_table)
        layout.addWidget(st_card)

        layout.addStretch()

        # ── Подписки на шины ──
        from ui.balance_bus import balance_bus
        balance_bus.balance_updated.connect(self._on_balance_update)
        balance_bus.balance_cleared.connect(self._on_balance_cleared)

        from ui.stats_bus import stats_bus
        stats_bus.bet_placed.connect(self._on_bet_placed)
        stats_bus.reset_today.connect(self._on_reset_today)

        self._load_bets_stats()
        self._update_bets_cards()

        self.load_accounts()
        self._fill_strategy_table()

    # ============================================================
    # Аккаунты и таблица БК
    # ============================================================
    def load_accounts(self):
        """Загружает аккаунты из accounts.json и обновляет «БК в работе»."""
        data_dir = get_app_data_dir()
        path = os.path.join(data_dir, "accounts.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self.accounts = json.load(f)
            else:
                self.accounts = []
        except Exception:
            self.accounts = []

        unique_bks = set(acc.get("bk", "") for acc in self.accounts if acc.get("bk"))
        self.card_bk.set_value(len(unique_bks))
        self._update_bk_table()

    def _update_bk_table(self):
        bk_map = {}
        for acc in self.accounts:
            bk_name = acc.get("bk", "")
            if not bk_name:
                continue
            bk_key = (bk_name or "").lower().replace(" ", "").replace("-", "")
            profile = acc.get("ads_power_id", "—")
            balance = self._balances.get(bk_key)
            if balance is None:
                balance_str = "—"
            else:
                if abs(balance - int(balance)) < 0.001:
                    balance_str = f"{int(balance):,} ₽".replace(",", " ")
                else:
                    balance_str = f"{balance:,.2f} ₽".replace(",", " ")

            bk_map[bk_name] = {
                "profile": profile,
                "balance": balance_str,
            }

        self.bk_table.setRowCount(len(bk_map))
        for row, (bk_name, data) in enumerate(bk_map.items()):
            self.bk_table.setItem(row, 0, _center_item(bk_name))
            self.bk_table.setItem(row, 1, _center_item(data["balance"]))
            self.bk_table.setItem(row, 2, _center_item(data["profile"]))

    # ============================================================
    # Балансы — приходят из balance_bus
    # ============================================================
    def _on_balance_update(self, bk: str, balance: float, currency: str):
        self._balances[(bk or "").lower()] = float(balance)
        self._update_bk_table()

    def _on_balance_cleared(self, bk: str):
        self._balances.pop((bk or "").lower(), None)
        self._update_bk_table()

    # ============================================================
    # Статистика ставок за сегодня
    # ============================================================
    def _bets_stats_path(self) -> str:
        return os.path.join(get_app_data_dir(), "bets_today.json")

    def _load_bets_stats(self):
        path = self._bets_stats_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == date.today().isoformat():
                self._turnover_today = float(data.get("turnover", 0.0))
                self._bets_count_today = int(data.get("bets_count", 0))
        except Exception:
            pass

    def _save_bets_stats(self):
        path = self._bets_stats_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "date": date.today().isoformat(),
                    "turnover": self._turnover_today,
                    "bets_count": self._bets_count_today,
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _update_bets_cards(self):
        t = self._turnover_today
        if t == int(t):
            self.card_turnover.set_value(f"{int(t):,} ₽".replace(",", " "))
        else:
            self.card_turnover.set_value(f"{t:,.2f} ₽".replace(",", " "))
        self.card_bets.set_value(self._bets_count_today)

    def _on_bet_placed(self, bk: str, amount: float, odd: float):
        self._turnover_today += float(amount)
        self._bets_count_today += 1
        self._save_bets_stats()
        self._update_bets_cards()

    def _on_reset_today(self):
        self._turnover_today = 0.0
        self._bets_count_today = 0
        self._save_bets_stats()
        self._update_bets_cards()

    def _reset_bets_stats(self):
        from ui.stats_bus import stats_bus
        stats_bus.reset()

    # ============================================================
    # Статистика советника
    # ============================================================
    def update_advisor_stats(self, stats: dict):
        self.advisor_stats = stats
        self.card_tracked.set_value(len(stats))
        self._update_bk_table()

    # ============================================================
    # Сигналы
    # ============================================================
    def update_signal_count(self, count):
        self.signal_count_today = count
        self.card_signals.set_value(count)

    # ============================================================
    # Таблица стратегий
    # ============================================================
    def _fill_strategy_table(self):
        strs = self.store.strategies
        self.strategy_table.setRowCount(len(strs))
        for row, st in enumerate(strs):
            enabled = st.get("enabled", False)
            self.strategy_table.setItem(
                row, 0, _center_item(st.get("name", "Без названия")))
            self.strategy_table.setItem(row, 1, _center_item("—"))
            self.strategy_table.setItem(row, 2, _center_item("— ₽"))

            status_text = "🟢 Активна" if enabled else "⏸ Пауза"
            status_color = QColor("#42d78d") if enabled else QColor("#f2c94c")
            self.strategy_table.setItem(
                row, 3, _center_item(status_text, status_color))

            btn = QPushButton("⏸ Пауза" if enabled else "▶ Включить")
            btn.setProperty("class", "ghostBtn")
            btn.clicked.connect(lambda checked, r=row: self._toggle_strategy(r))
            self.strategy_table.setCellWidget(row, 3, btn)

    def _toggle_strategy(self, row):
        st = self.store.strategies[row]
        new_state = not st.get("enabled", False)
        self.store.set_enabled(row, new_state)
        self.store.save()
        self._fill_strategy_table()

        if new_state:
            main = self.window()
            if hasattr(main, 'after_goal_engine'):
                asyncio.create_task(main.after_goal_engine.activate_strategy(st))
            else:
                print("⚠️ after_goal_engine не найден, пропускаем активацию профиля")