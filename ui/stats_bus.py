# ui/stats_bus.py
"""
Шина статистики ставок. Engine пушит сюда, dashboard читает.

Сейчас — оборот (сумма принятых ставок) и счётчик ставок за сегодня.
Позже — можно добавить реальный расчёт выигрышей (см. задачу C).
"""
from PyQt5.QtCore import QObject, pyqtSignal


class StatsBus(QObject):
    bet_placed = pyqtSignal(str, float, float)   # bk, amount, odd
    reset_today = pyqtSignal()

    def on_bet_placed(self, bk: str, amount: float, odd: float):
        try:
            amount = float(amount)
            odd = float(odd or 0)
        except Exception:
            return
        if amount <= 0:
            return
        self.bet_placed.emit((bk or "").lower(), amount, odd)

    def reset(self):
        self.reset_today.emit()


stats_bus = StatsBus()