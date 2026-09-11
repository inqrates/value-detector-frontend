"""Стили и палитра приложения."""
from PyQt5.QtGui import QPalette, QColor


APP_STYLE = """
/* ---------- Глобальные ---------- */
QMainWindow, QWidget#centralWidget {
    background-color: #0b0d0f;
    color: rgba(245,249,252,0.94);
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}
QWidget {
    color: rgba(245,249,252,0.94);
    font-family: "Segoe UI", Arial, sans-serif;
}

/* ---------- SIDEBAR ---------- */
QFrame#sidebar {
    background-color: #0f1113;
    border-right: 1px solid rgba(255,255,255,0.075);
}
QLabel#sidebarTitle {
    color: #f5fbff;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0px;
}
QLabel#sidebarSubtitle {
    color: rgba(199,214,223,0.62);
    font-size: 11px;
    font-weight: 500;
}
QFrame#subscriptionCard {
    background-color: rgba(8,167,200,0.08);
    border: 1px solid rgba(8,167,200,0.24);
    border-radius: 10px;
}
QLabel#subDaysValue {
    color: #21c1de;
    font-size: 28px;
    font-weight: 800;
}
QLabel#subDaysLabel {
    color: rgba(199,214,223,0.62);
    font-size: 11px;
    font-weight: 600;
}
QFrame#userCard {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 10px;
}
QLabel#userName {
    color: rgba(245,249,252,0.94);
    font-size: 14px;
    font-weight: 700;
}
QLabel#userRole {
    color: rgba(199,214,223,0.62);
    font-size: 11px;
    font-weight: 500;
}

/* ---------- Навигация ---------- */
QPushButton.navBtn {
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: rgba(245,249,252,0.86);
    font-size: 13px;
    font-weight: 600;
    text-align: left;
    padding: 10px 14px;
}
QPushButton.navBtn:hover {
    background-color: rgba(255,255,255,0.075);
    color: #fff;
}
QPushButton.navBtn:checked {
    background-color: rgba(8,167,200,0.13);
    color: #21c1de;
    border: 1px solid rgba(8,167,200,0.22);
}

/* ---------- Topbar ---------- */
QFrame#topbar {
    background-color: rgba(255,255,255,0.025);
    border-bottom: 1px solid rgba(255,255,255,0.075);
}
QLabel#pageTitle {
    color: #f5fbff;
    font-size: 22px;
    font-weight: 700;
}
QLabel#pageSubtitle {
    color: rgba(199,214,223,0.58);
    font-size: 13px;
}

/* ---------- Карточки статистики ---------- */
QFrame.statCard {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 10px;
}
QLabel.statValue {
    color: #f5fbff;
    font-size: 26px;
    font-weight: 800;
}
QLabel.statLabel {
    color: rgba(199,214,223,0.62);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}
QLabel.statValueGood { color: #42d78d; font-size: 26px; font-weight: 800; }
QLabel.statValueAccent { color: #21c1de; font-size: 26px; font-weight: 800; }
QLabel.statValueWarn { color: #f2c94c; font-size: 26px; font-weight: 800; }

/* ---------- Секции ---------- */
QLabel.sectionTitle {
    color: #f5fbff;
    font-size: 15px;
    font-weight: 700;
}
QFrame.sectionCard {
    background-color: rgba(255,255,255,0.026);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
}

/* ---------- Таблицы ---------- */
QTableWidget {
    background-color: #101418;
    alternate-background-color: #14181c;
    border: 1px solid rgba(255,255,255,0.06);
    color: #eff7fb;
    gridline-color: rgba(255,255,255,0.05);
    font-size: 13px;
}
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected {
    background-color: rgba(8,167,200,0.12);
    color: #fff;
}
QHeaderView {
    background-color: #101418;
    border: none;
}
QHeaderView::section {
    background-color: #171c21;
    color: #dfe9ef;
    padding: 8px;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    font-weight: 700;
    font-size: 12px;
}
QTableCornerButton::section {
    background-color: #171c21;
    border: none;
}
QListWidget {
    background-color: #101418;
    border: 1px solid rgba(255,255,255,0.06);
    color: #eff7fb;
}

/* ---------- Кнопки ---------- */
QPushButton.primaryBtn {
    background-color: #08a7c8;
    border: none;
    border-radius: 8px;
    padding: 10px 18px;
    color: #fff;
    font-weight: 700;
    font-size: 13px;
}
QPushButton.primaryBtn:hover { background-color: #21c1de; }
QPushButton.primaryBtn:pressed { background-color: #0790ab; }

QPushButton.ghostBtn {
    background-color: rgba(255,255,255,0.036);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
    padding: 9px 16px;
    color: rgba(245,249,252,0.82);
    font-weight: 600;
    font-size: 13px;
}
QPushButton.ghostBtn:hover {
    background-color: rgba(255,255,255,0.062);
    border-color: rgba(255,255,255,0.13);
    color: #fff;
}
QPushButton.dangerBtn {
    background-color: rgba(255,100,120,0.10);
    border: 1px solid rgba(255,100,120,0.22);
    border-radius: 8px;
    padding: 9px 16px;
    color: #ff6478;
    font-weight: 600;
}
QPushButton.dangerBtn:hover { background-color: rgba(255,100,120,0.18); }

/* ---------- Поля ввода ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: rgba(255,255,255,0.045);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 8px;
    padding: 8px 12px;
    color: rgba(245,249,252,0.94);
    selection-background-color: rgba(8,167,200,0.4);
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: rgba(8,167,200,0.56);
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #16191c;
    color: rgba(245,249,252,0.94);
    border: 1px solid rgba(255,255,255,0.075);
    selection-background-color: rgba(8,167,200,0.2);
}

QCheckBox { spacing: 8px; color: rgba(245,249,252,0.94); }
QCheckBox::indicator {
    width: 18px; height: 18px;
    border-radius: 4px;
    border: 1px solid rgba(255,255,255,0.2);
    background: rgba(255,255,255,0.045);
}
QCheckBox::indicator:checked {
    background: #08a7c8;
    border-color: #08a7c8;
}

QGroupBox {
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 10px;
    margin-top: 14px;
    padding: 18px 14px 14px 14px;
    background-color: rgba(255,255,255,0.02);
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: rgba(245,249,252,0.94);
    font-weight: 700;
}

QTextEdit {
    background-color: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 8px;
    color: rgba(239,247,251,0.84);
}

QSlider::groove:horizontal {
    background: rgba(255,255,255,0.075);
    height: 5px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #08a7c8;
    width: 16px; margin: -6px 0;
    border-radius: 8px;
}

QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.15);
    border-radius: 5px; min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QTabWidget::pane { border: none; }

QLabel.hintLabel {
    color: rgba(199,214,223,0.52);
    font-size: 11px;
    font-style: italic;
}
"""


def create_dark_palette() -> QPalette:
    """Создаёт тёмную палитру для всего приложения."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#0b0d0f"))
    p.setColor(QPalette.WindowText, QColor("#f5f9fc"))
    p.setColor(QPalette.Base, QColor("#101418"))
    p.setColor(QPalette.AlternateBase, QColor("#14181c"))
    p.setColor(QPalette.ToolTipBase, QColor("#14181c"))
    p.setColor(QPalette.ToolTipText, QColor("#f5f9fc"))
    p.setColor(QPalette.Text, QColor("#eff7fb"))
    p.setColor(QPalette.Button, QColor("#171c21"))
    p.setColor(QPalette.ButtonText, QColor("#f5f9fc"))
    p.setColor(QPalette.BrightText, QColor("#21c1de"))
    p.setColor(QPalette.Link, QColor("#08a7c8"))
    p.setColor(QPalette.Highlight, QColor("#08a7c8"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, QColor("#91a2b0"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#91a2b0"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#91a2b0"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#91a2b0"))
    return p