# ui/pages/logs.py
import html
import logging

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QLineEdit, QPushButton, QTextEdit,
    QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor

logger = logging.getLogger(__name__)

MAX_ENTRIES = 800

LEVEL_STYLE = {
    "info":    ("●", "#21c1de", "ИНФО"),
    "success": ("✓", "#42d78d", "OK"),
    "warning": ("⚠", "#f2c94c", "ВНИМАНИЕ"),
    "error":   ("✕", "#eb5757", "ОШИБКА"),
    "signal":  ("●", "#ff8c42", "СИГНАЛ"),
}


class LogsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.entries = []
        self._level_filter = "Все"
        self._search_text = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ---- Панель управления ----
        top = QFrame()
        top.setProperty("class", "sectionCard")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(12)

        top_layout.addWidget(QLabel("Фильтр:"))

        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            "Все",
            "Только сигналы",
            "Только ошибки",
            "Только успехи",
            "AdsPower",
            "Стратегии",
            "Ставки",
        ])
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        top_layout.addWidget(self.filter_combo)

        top_layout.addSpacing(16)
        top_layout.addWidget(QLabel("Поиск:"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Найти в логах...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        top_layout.addWidget(self.search_edit, 1)

        clear_btn = QPushButton("🗑 Очистить")
        clear_btn.setProperty("class", "ghostBtn")
        clear_btn.clicked.connect(self.clear_logs)
        top_layout.addWidget(clear_btn)

        layout.addWidget(top)

        # ---- Окно логов ----
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QTextEdit {
                background-color: #0d1014;
                color: #cfdae2;
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 12px;
                padding: 12px;
            }
        """)
        self.log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.log_view, 1)

        self.status = QLabel("Записей: 0")
        self.status.setStyleSheet("color: rgba(199,214,223,0.52); font-size: 11px;")
        layout.addWidget(self.status)

    # ---------- Публичный API ----------
    def add_log(self, entry: dict):
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
            self._rerender()
            return

        if not self._matches_filter(entry):
            self._update_status()
            return

        self._append_html(self._render_entry(entry))
        self._update_status()

    def clear_logs(self):
        self.entries.clear()
        self.log_view.clear()
        self._update_status()

    # ---------- Фильтры ----------
    def _on_filter_changed(self, text: str):
        self._level_filter = text
        self._rerender()

    def _on_search_changed(self, text: str):
        self._search_text = text.strip().lower()
        self._rerender()

    def _matches_filter(self, entry: dict) -> bool:
        f = self._level_filter
        if f == "Только сигналы" and entry["level"] != "signal":
            return False
        if f == "Только ошибки" and entry["level"] != "error":
            return False
        if f == "Только успехи" and entry["level"] != "success":
            return False
        if f == "AdsPower" and entry["source"] != "AdsPower":
            return False
        if f == "Стратегии" and entry["source"] != "Стратегия":
            return False
        if f == "Ставки" and entry["source"] != "Ставка":
            return False

        if self._search_text:
            haystack = (entry["message"] + " " + entry["source"]).lower()
            if self._search_text not in haystack:
                return False
        return True

    # ---------- Рендер ----------
    def _rerender(self):
        self.log_view.clear()
        for entry in self.entries:
            if self._matches_filter(entry):
                self._append_html(self._render_entry(entry))
        self._update_status()

    def _append_html(self, html_text: str):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)
        self.log_view.insertHtml(html_text + "<br>")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _render_entry(self, e: dict) -> str:
        time_str = f'<span style="color:#5a6b7a;">[{e["time"]}]</span>'
        icon, color, label = LEVEL_STYLE.get(e["level"], ("•", "#999", "?"))
        level_span = f'<span style="color:{color};font-weight:bold;">{icon} {label}</span>'
        source_span = f'<span style="color:#7fa1b7;">[{html.escape(e["source"])}]</span>'
        msg = html.escape(e["message"])

        if e["level"] == "signal":
            return self._render_signal(e, time_str, level_span, source_span)

        return f'{time_str} {level_span} {source_span} <span style="color:#e7eef4;">{msg}</span>'

    def _render_signal(self, e: dict, time_str: str, level_span: str, source_span: str) -> str:
        d = e.get("details", {})
        teams = d.get("teams", ["", ""])
        fast_bk = d.get("fast_bk", "?")
        slow_bk = d.get("slow_bk", "?")
        delay = d.get("delay", 0)

        fast_score = d.get("fast_score", [0, 0])
        fast_sub = d.get("fast_sub_score", [0, 0])
        fast_odds = d.get("fast_odds", [0, 0])

        slow_score = d.get("slow_score", [0, 0])
        slow_sub = d.get("slow_sub_score", [0, 0])
        slow_odds = d.get("slow_odds", [0, 0])

        # Подсветка: у кого счёт выше — зелёный, кто отстаёт — красный
        fast_set = fast_score[0] + fast_score[1]
        slow_set = slow_score[0] + slow_score[1]
        fast_leads = fast_set > slow_set or (fast_set == slow_set and (fast_sub[0] + fast_sub[1]) > (slow_sub[0] + slow_sub[1]))
        slow_leads = slow_set > fast_set or (slow_set == fast_set and (slow_sub[0] + slow_sub[1]) > (fast_sub[0] + fast_sub[1]))

        fast_score_color = "#42d78d" if fast_leads else ("#eb5757" if slow_leads else "#cfdae2")
        slow_score_color = "#42d78d" if slow_leads else ("#eb5757" if fast_leads else "#cfdae2")

        # Разница в сетах
        diff_sets = fast_set - slow_set
        diff_sub = (fast_sub[0] + fast_sub[1]) - (slow_sub[0] + slow_sub[1])
        if diff_sets != 0:
            diff_str = f"{'+' if diff_sets > 0 else ''}{diff_sets} сет"
        else:
            diff_str = f"{'+' if diff_sub > 0 else ''}{diff_sub} очк"

        lines = []
        # Заголовок
        lines.append(f'{time_str} {level_span} {source_span}')
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#ffffff;font-weight:bold;">'
            f'{html.escape(str(teams[0]))} vs {html.escape(str(teams[1]))}</span>'
            f' &nbsp;<span style="color:#5a6b7a;">·</span>&nbsp; '
            f'<span style="color:#f2c94c;">задержка {delay}с</span>'
        )

        # Быстрая БК (впереди)
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#42d78d;font-weight:bold;">⚡ {html.escape(str(fast_bk))}</span>'
            f' <span style="color:#5a6b7a;">(впереди)</span> '
            f'<span style="color:{fast_score_color};font-weight:bold;">'
            f'счёт {fast_score[0]}:{fast_score[1]}</span>'
            f' <span style="color:#8ea3b3;">(сет {fast_sub[0]}:{fast_sub[1]})</span>'
            f' <span style="color:#5a6b7a;">·</span> '
            f'<span style="color:#cfdae2;">П1 {float(fast_odds[0]):.2f} / П2 {float(fast_odds[1]):.2f}</span>'
        )

        # Медленная БК (отстаёт)
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#eb5757;font-weight:bold;">🐢 {html.escape(str(slow_bk))}</span>'
            f' <span style="color:#5a6b7a;">(отстаёт)</span> '
            f'<span style="color:{slow_score_color};font-weight:bold;">'
            f'счёт {slow_score[0]}:{slow_score[1]}</span>'
            f' <span style="color:#8ea3b3;">(сет {slow_sub[0]}:{slow_sub[1]})</span>'
            f' <span style="color:#5a6b7a;">·</span> '
            f'<span style="color:#cfdae2;">П1 {float(slow_odds[0]):.2f} / П2 {float(slow_odds[1]):.2f}</span>'
        )

        # Итоговая разница
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#5a6b7a;">Разница: </span>'
            f'<span style="color:#f2c94c;font-weight:bold;">{diff_str}</span>'
        )

        return "<br>".join(lines)

    def _update_status(self):
        total = len(self.entries)
        shown = sum(1 for e in self.entries if self._matches_filter(e))
        self.status.setText(f"Записей: {total} (показано: {shown})")