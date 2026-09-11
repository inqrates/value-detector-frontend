"""Тумблер ВКЛ/ВЫКЛ."""
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtCore import pyqtSignal, Qt


class Switch(QPushButton):
    toggled_state = pyqtSignal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(46, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self._on_toggled)
        self._apply()

    def _on_toggled(self, on):
        self._apply()
        self.toggled_state.emit(on)

    def _apply(self):
        if self.isChecked():
            self.setText("ON")
            self.setStyleSheet("""
                QPushButton { background: rgba(8,167,200,0.85); border-radius: 11px;
                    border: none; color: #fff; font-weight: 800; font-size: 10px; }
                QPushButton:hover { background: #21c1de; }
            """)
        else:
            self.setText("OFF")
            self.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.10); border-radius: 11px;
                    border: none; color: rgba(245,249,252,0.45); font-weight: 800; font-size: 10px; }
                QPushButton:hover { background: rgba(255,255,255,0.16); }
            """)