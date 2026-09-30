# ui/balance_bus.py
from PyQt5.QtCore import QObject, pyqtSignal


class BalanceBus(QObject):
    """Шина балансов БК. Handler'ы пушат сюда, dashboard читает."""

    balance_updated = pyqtSignal(str, float, str)   # bk, balance, currency
    balance_cleared = pyqtSignal(str)               # bk

    def update(self, bk: str, balance, currency: str = "RUB"):
        if bk is None or balance is None:
            return
        try:
            val = float(balance)
        except Exception:
            return
        self.balance_updated.emit((bk or "").lower(), val, currency or "RUB")

    def clear(self, bk: str):
        self.balance_cleared.emit((bk or "").lower())


balance_bus = BalanceBus()