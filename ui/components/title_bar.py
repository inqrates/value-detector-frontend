"""Кастомный title bar в цвет приложения."""
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt, QPoint, QEvent


class TitleBar(QFrame):
    HEIGHT = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("titleBar")
        self.setStyleSheet("""
            QFrame#titleBar {
                background-color: #0b0d0f;
                border-bottom: 1px solid rgba(255,255,255,0.075);
            }
            QLabel#titleText {
                color: rgba(245,249,252,0.94);
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#titleLogo { color: #21c1de; font-size: 16px; }
            QPushButton#winBtn {
                background: transparent; border: none;
                color: rgba(245,249,252,0.72);
                font-size: 18px; font-weight: bold;
                min-width: 48px; height: 40px;
            }
            QPushButton#winBtn:hover {
                background: rgba(255,255,255,0.075); color: #fff;
            }
            QPushButton#closeBtn {
                background: transparent; border: none;
                color: rgba(245,249,252,0.72);
                font-size: 18px; font-weight: bold;
                min-width: 48px; height: 40px;
            }
            QPushButton#closeBtn:hover { background: #eb5757; color: #fff; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(10)

        logo = QLabel("⚡")
        logo.setObjectName("titleLogo")
        layout.addWidget(logo)

        title = QLabel("Value Detector Pro")
        title.setObjectName("titleText")
        layout.addWidget(title)
        layout.addStretch()

        self.btn_min = QPushButton("─")
        self.btn_min.setObjectName("winBtn")
        self.btn_min.setFixedSize(46, self.HEIGHT)
        self.btn_min.clicked.connect(self._minimize)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton("❐")
        self.btn_max.setObjectName("winBtn")
        self.btn_max.setFixedSize(46, self.HEIGHT)
        self.btn_max.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setFixedSize(46, self.HEIGHT)
        self.btn_close.clicked.connect(self._close)
        layout.addWidget(self.btn_close)

        self._drag_pos = None

    def _minimize(self):
        self.parent_window.showMinimized()

    def _toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.btn_max.setText("□")
        else:
            self.parent_window.showMaximized()
            self.btn_max.setText("❐")

    def _close(self):
        self.parent_window.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            if self.parent_window.isMaximized():
                self.parent_window.showNormal()
                self.btn_max.setText("□")
                self._drag_pos = QPoint(
                    int(event.globalPos().x() * 0.5),
                    int(self.HEIGHT / 2)
                )
            self.parent_window.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self._toggle_maximize()