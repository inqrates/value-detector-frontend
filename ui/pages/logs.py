# ui/pages/logs.py
import html
import logging
from collections import deque

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QLineEdit, QPushButton, QTextEdit,
    QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextCursor

logger = logging.getLogger(__name__)

MAX_ENTRIES = 400          # меньше, чтобы UI не давился
FLUSH_INTERVAL_MS = 250    # батчинг: раз в 250 мс рендерим пачку

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
        self.entries = deque(maxlen=MAX_ENTRIES)
        self._pending = []          # буфер для батчинга
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
        # Отключаем лишние пересчёты при вставке
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.log_view, 1)

        self.status = QLabel("Записей: 0")
        self.status.setStyleSheet("color: rgba(199,214,223,0.52); font-size: 11px;")
        layout.addWidget(self.status)

        # ---- Батчинг: рендерим раз в 250 мс, а не на каждое событие ----
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()

    # ---------- Публичный API ----------
    def add_log(self, entry: dict):
        """Кладём в очередь, реальный рендер произойдёт в _flush_pending."""
        self.entries.append(entry)
        self._pending.append(entry)

    def clear_logs(self):
        self.entries.clear()
        self._pending.clear()
        self.log_view.clear()
        self._update_status()

    # ---------- Батчинг ----------
    def _flush_pending(self):
        if not self._pending:
            return

        # Если пользователь листает вверх — не дёргаем автоскролл,
        # но всё равно добавляем записи (пусть копятся)
        scrollbar = self.log_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 5

        # Берём пачку и очищаем очередь
        batch = self._pending
        self._pending = []

        # Фильтруем по текущему фильтру/поиску
        to_append = [e for e in batch if self._matches_filter(e)]
        if not to_append:
            self._update_status()
            return

        # Рендерим пачкой через один insertHtml — это в 10 раз быстрее
        html_chunk = "<br>".join(self._render_entry(e) for e in to_append) + "<br>"

        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)
        self.log_view.insertHtml(html_chunk)

        # Обрезаем QTextEdit, если он разросся (защита от memory leak)
        self._trim_if_needed()

        # Автоскролл — только если пользователь был внизу
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

        self._update_status()

    def _trim_if_needed(self):
        """Если в QTextEdit накопилось больше MAX_ENTRIES + 100 блоков — перерендерим из self.entries."""
        doc = self.log_view.document()
        if doc.blockCount() < MAX_ENTRIES + 100:
            return
        # Полный rerender из deque — это дешевле, чем держать гигантский QTextEdit
        self._rerender()

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
        """Полный пересбор QTextEdit одним setHtml() — намного быстрее, чем цикл insertHtml."""
        # Останавливаем приём, чтобы не пересекаться
        self._pending.clear()

        html_parts = []
        for entry in self.entries:
            if self._matches_filter(entry):
                html_parts.append(self._render_entry(entry))

        # Собираем один HTML — конвертируем <br> в блоки
        doc_html = (
            '<html><body style="color:#cfdae2; font-family:Consolas,monospace; '
            'font-size:12px; background-color:#0d1014;">'
            + "<br>".join(html_parts)
            + "</body></html>"
        )
        self.log_view.setHtml(doc_html)

        # Прокрутка вниз
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        self._update_status()

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

        fast_set = fast_score[0] + fast_score[1]
        slow_set = slow_score[0] + slow_score[1]
        fast_leads = fast_set > slow_set or (fast_set == slow_set and (fast_sub[0] + fast_sub[1]) > (slow_sub[0] + slow_sub[1]))
        slow_leads = slow_set > fast_set or (slow_set == fast_set and (slow_sub[0] + slow_sub[1]) > (fast_sub[0] + fast_sub[1]))

        fast_score_color = "#42d78d" if fast_leads else ("#eb5757" if slow_leads else "#cfdae2")
        slow_score_color = "#42d78d" if slow_leads else ("#eb5757" if fast_leads else "#cfdae2")

        diff_sets = fast_set - slow_set
        diff_sub = (fast_sub[0] + fast_sub[1]) - (slow_sub[0] + slow_sub[1])
        if diff_sets != 0:
            diff_str = f"{'+' if diff_sets > 0 else ''}{diff_sets} сет"
        else:
            diff_str = f"{'+' if diff_sub > 0 else ''}{diff_sub} очк"

        lines = []
        lines.append(f'{time_str} {level_span} {source_span}')
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#ffffff;font-weight:bold;">'
            f'{html.escape(str(teams[0]))} vs {html.escape(str(teams[1]))}</span>'
            f' &nbsp;<span style="color:#5a6b7a;">·</span>&nbsp; '
            f'<span style="color:#f2c94c;">задержка {delay}с</span>'
        )
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#42d78d;font-weight:bold;">⚡ {html.escape(str(fast_bk))}</span>'
            f' <span style="color:#5a6b7a;">(впереди)</span> '
            f'<span style="color:{fast_score_color};font-weight:bold;">'
            f'счёт {fast_score[0]}:{fast_score[1]}</span>'
            f' <span style="color:#8ea3b3;">(сет {fast_sub[0]}:{fast_sub[1]})</span>'
            f' <span style="color:#5a6b7a;">·</span> '
            f'<span style="color:#cfdae2;">П1 {float(fast_odds[0]):.2f} / П2 {float(fast_odds[1]):.2f}</span>'
        )
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#eb5757;font-weight:bold;">🐢 {html.escape(str(slow_bk))}</span>'
            f' <span style="color:#5a6b7a;">(отстаёт)</span> '
            f'<span style="color:{slow_score_color};font-weight:bold;">'
            f'счёт {slow_score[0]}:{slow_score[1]}</span>'
            f' <span style="color:#8ea3b3;">(сет {slow_sub[0]}:{slow_sub[1]})</span>'
            f' <span style="color:#5a6b7a;">·</span> '
            f'<span style="color:#cfdae2;">П1 {float(slow_odds[0]):.2f} / П2 {float(slow_odds[1]):.2f}</span>'
        )
        lines.append(
            f'&nbsp;&nbsp;&nbsp;<span style="color:#5a6b7a;">Разница: </span>'
            f'<span style="color:#f2c94c;font-weight:bold;">{diff_str}</span>'
        )
        return "<br>".join(lines)

    def _update_status(self):
        total = len(self.entries)
        # Не считаем показанные на каждое обновление — дёшево
        self.status.setText(f"Записей: {total}")