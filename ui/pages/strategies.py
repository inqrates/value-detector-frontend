# ui/pages/strategies.py
import json
import os
import asyncio
import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QLineEdit,
    QGroupBox, QFormLayout, QPushButton
)
from PyQt5.QtCore import Qt, pyqtSignal

from ui.components.switch import Switch
from ui.strategy_store import StrategyStore, SPORT_ANY, VALID_MARKETS
from ui.log_bus import log_bus

logger = logging.getLogger(__name__)


# --- Вид спорта: русский ключ → английский код ---
SPORT_CHOICES = {
    "Настольный теннис": "table_tennis",
    "Волейбол":          "volleyball",
    "Баскетбол":         "basketball",
    "Кибер":             "cyber_basketball",
    "Все виды":          SPORT_ANY,
}
SPORT_DISPLAY = {v: k for k, v in SPORT_CHOICES.items()}
SPORT_SHORT = {
    "table_tennis":     "🏓 НТ",
    "volleyball":       "🏐 Волейбол",
    "basketball":       "🏀 Баскетбол",
    "cyber_basketball": "🎮 КиберБаскетбол",
    SPORT_ANY:          "🌐 Все виды",
}

# --- Тип стратегии: русский ключ → английский код ---
TYPE_CHOICES = {
    "После гола": "After-goal",
    "Валуй":      "Value",
    "Вилка":      "Arbitrage",
    "Коридор":    "Corridor",
}
TYPE_DISPLAY = {v: k for k, v in TYPE_CHOICES.items()}

# --- Направление: русский ключ → английский код ---
DIRECTION_CHOICES = {
    "Лучший коэффициент": "best_odds",
    "Лидер фазы":         "leader",
    "Отстающий в фазе":   "laggard",
    "Как на быстрой БК":  "same_as_fast",
}
DIRECTION_DISPLAY = {v: k for k, v in DIRECTION_CHOICES.items()}

# --- Стороны Победителя и Фор: русский ключ → английский код ---
SIDES_WIN_CHOICES = {
    "Любая":     "both",
    "Только П1": "1",
    "Только П2": "2",
}
SIDES_WIN_DISPLAY = {v: k for k, v in SIDES_WIN_CHOICES.items()}

# --- Стороны Тотала: русский ключ → английский код ---
SIDES_TOTAL_CHOICES = {
    "Любая":         "both",
    "Только Больше": "over",
    "Только Меньше": "under",
}
SIDES_TOTAL_DISPLAY = {v: k for k, v in SIDES_TOTAL_CHOICES.items()}


def _make_field_grow(form):
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)


class StrategyCard(QFrame):
    clicked = pyqtSignal(int)
    toggled = pyqtSignal(int, bool)

    def __init__(self, index, strategy, parent=None):
        super().__init__(parent)
        self.index = index
        self.setFixedHeight(64)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QFrame { background: rgba(255,255,255,0.026);
                border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; }
            QFrame:hover { border-color: rgba(8,167,200,0.3); }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(strategy.get("name", "Стратегия"))
        name.setStyleSheet("font-weight: 700; font-size: 13px; color: #f5fbff;")

        bk = strategy.get('bk', '')
        profile = strategy.get('profile_id', '')
        sport_key = strategy.get('sport') or SPORT_ANY
        sport_label = SPORT_SHORT.get(sport_key, sport_key)

        type_raw = strategy.get('type', '') or ''
        type_label = TYPE_DISPLAY.get(type_raw, type_raw)

        profile_text = profile[:8] if profile else 'не выбран'
        meta = QLabel(
            f"{type_label} • {sport_label} • "
            f"{bk} (профиль: {profile_text})"
        )
        meta.setStyleSheet("color: rgba(199,214,223,0.52); font-size: 11px;")
        texts.addWidget(name)
        texts.addWidget(meta)
        layout.addLayout(texts, 1)

        self.switch = Switch(strategy.get("enabled", False))
        self.switch.toggled_state.connect(lambda on: self.toggled.emit(self.index, on))
        layout.addWidget(self.switch)

    def mousePressEvent(self, event):
        self.clicked.emit(self.index)
        super().mousePressEvent(event)


class StrategiesPage(QWidget):
    strategies_changed = pyqtSignal()

    def __init__(self, store: StrategyStore):
        super().__init__()
        self.store = store
        self.selected = 0
        self.accounts = self._load_accounts()
        self.account_display_names = self._get_account_display_names()

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # ============ Левая колонка: карточки ============
        left = QVBoxLayout()
        left_title = QLabel("Стратегии")
        left_title.setProperty("class", "sectionTitle")
        left.addWidget(left_title)

        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.cards_scroll.setWidget(self.cards_container)
        left.addWidget(self.cards_scroll, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ Создать")
        add_btn.setProperty("class", "primaryBtn")
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("🗑 Удалить")
        del_btn.setProperty("class", "dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        layout.addLayout(left, 1)

        # ============ Правая колонка: форма ============
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        right_inner = QWidget()
        right_inner.setMinimumWidth(470)
        right = QVBoxLayout(right_inner)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)

        right_title = QLabel("Настройка стратегии")
        right_title.setProperty("class", "sectionTitle")
        right.addWidget(right_title)

        # ---- Группа: Основные параметры ----
        main_group = QGroupBox("Основные параметры")
        main_form = QFormLayout(main_group)
        main_form.setSpacing(10)
        _make_field_grow(main_form)

        self.name_edit = QLineEdit()
        main_form.addRow("Название:", self.name_edit)

        self.strategy_type = QComboBox()
        self.strategy_type.addItems(list(TYPE_CHOICES.keys()))  # русские ключи
        main_form.addRow("Тип стратегии:", self.strategy_type)

        self.sport_combo = QComboBox()
        self.sport_combo.addItems(list(SPORT_CHOICES.keys()))
        main_form.addRow("Вид спорта:", self.sport_combo)

        self.account_combo = QComboBox()
        self.account_combo.addItems(self.account_display_names)
        main_form.addRow("Аккаунт для ставок:", self.account_combo)

        self.min_delay = QDoubleSpinBox()
        self.min_delay.setRange(0.5, 30.0)
        self.min_delay.setSuffix(" сек")
        main_form.addRow("Мин. задержка:", self.min_delay)

        self.min_score_diff = QSpinBox()
        self.min_score_diff.setRange(1, 11)
        self.min_score_diff.setSuffix(" очк.")
        main_form.addRow("Мин. разница в счёте:", self.min_score_diff)
        right.addWidget(main_group)

        # ---- Группа: Что ставить ----
        what_group = QGroupBox("Что ставить")
        what_form = QFormLayout(what_group)
        what_form.setSpacing(10)
        _make_field_grow(what_form)

        markets_row = QHBoxLayout()
        self.cb_winner = QCheckBox("Победитель")
        self.cb_total = QCheckBox("Тотал")
        self.cb_handicap = QCheckBox("Фора")
        for cb in (self.cb_winner, self.cb_total, self.cb_handicap):
            markets_row.addWidget(cb)
        markets_row.addStretch()
        what_form.addRow("Рынки:", markets_row)

        self.direction_combo = QComboBox()
        self.direction_combo.addItems(list(DIRECTION_CHOICES.keys()))  # русские ключи
        what_form.addRow("Направление (Победитель):", self.direction_combo)

        self.winner_sides_combo = QComboBox()
        self.winner_sides_combo.addItems(list(SIDES_WIN_CHOICES.keys()))
        what_form.addRow("Стороны Победителя:", self.winner_sides_combo)

        self.total_sides_combo = QComboBox()
        self.total_sides_combo.addItems(list(SIDES_TOTAL_CHOICES.keys()))
        what_form.addRow("Стороны Тотала:", self.total_sides_combo)

        self.handicap_sides_combo = QComboBox()
        self.handicap_sides_combo.addItems(list(SIDES_WIN_CHOICES.keys()))
        what_form.addRow("Стороны Фор:", self.handicap_sides_combo)

        hint = QLabel(
            "💡 «Направление» применяется только к рынку Победителя. "
            "«Лучший коэффициент» — без фильтра, выбирается самый вкусный "
            "из подтверждённых."
        )
        hint.setProperty("class", "hintLabel")
        hint.setWordWrap(True)
        what_form.addRow("", hint)
        right.addWidget(what_group)

        # ---- Группа: Банкролл ----
        bank_group = QGroupBox("Управление банкроллом")
        bank_form = QFormLayout(bank_group)
        bank_form.setSpacing(10)
        _make_field_grow(bank_form)

        self.bet_mode = QComboBox()
        self.bet_mode.addItems(["Фиксированная ставка", "% от банка", "Критерий Келли"])
        bank_form.addRow("Режим ставки:", self.bet_mode)

        self.bet_size = QDoubleSpinBox()
        self.bet_size.setRange(10, 100000)
        self.bet_size.setSuffix(" ₽")
        bank_form.addRow("Размер ставки:", self.bet_size)

        self.min_odds = QDoubleSpinBox()
        self.min_odds.setRange(1.01, 20.0)
        self.min_odds.setSingleStep(0.05)
        bank_form.addRow("Мин. коэффициент:", self.min_odds)

        self.max_odds = QDoubleSpinBox()
        self.max_odds.setRange(1.01, 50.0)
        self.max_odds.setSingleStep(0.05)
        bank_form.addRow("Макс. коэффициент:", self.max_odds)
        right.addWidget(bank_group)

        # ---- Группа: Автоматизация ----
        auto_group = QGroupBox("Автоматизация")
        auto_form = QFormLayout(auto_group)
        auto_form.setSpacing(10)
        _make_field_grow(auto_form)

        self.auto_bet = QCheckBox("Автоматическая ставка")
        auto_form.addRow(self.auto_bet)
        self.auto_confirm = QCheckBox("Подтверждение перед ставкой")
        self.auto_confirm.setChecked(True)
        auto_form.addRow(self.auto_confirm)

        self.verify_seconds = QDoubleSpinBox()
        self.verify_seconds.setRange(0.0, 30.0)
        self.verify_seconds.setSingleStep(0.5)
        self.verify_seconds.setSuffix(" сек")
        auto_form.addRow("Пауза после сигнала:", self.verify_seconds)

        self.max_bets_per_match = QSpinBox()
        self.max_bets_per_match.setRange(1, 100)
        auto_form.addRow("Макс. ставок на матч:", self.max_bets_per_match)

        self.max_bets_per_phase = QSpinBox()
        self.max_bets_per_phase.setRange(1, 100)
        auto_form.addRow("Макс. ставок на фазу:", self.max_bets_per_phase)

        self.ignore_repeats = QCheckBox("Игнорировать повторные сигналы")
        auto_form.addRow(self.ignore_repeats)

        self.stop_after_loss = QCheckBox("Стоп после серии убытков")
        auto_form.addRow(self.stop_after_loss)
        self.max_loss = QSpinBox()
        self.max_loss.setRange(1, 10)
        self.max_loss.setSuffix(" подряд")
        auto_form.addRow("Макс. убытков:", self.max_loss)

        self.headless_checkbox = QCheckBox("Скрытый режим (без окна браузера)")
        self.headless_checkbox.setChecked(False)
        auto_form.addRow(self.headless_checkbox)

        right.addWidget(auto_group)

        control_row = QHBoxLayout()
        save_btn = QPushButton("💾 Сохранить")
        save_btn.setProperty("class", "primaryBtn")
        save_btn.clicked.connect(self._on_save)
        control_row.addWidget(save_btn)
        control_row.addStretch()
        right.addLayout(control_row)

        right.addStretch()
        right_scroll.setWidget(right_inner)
        layout.addWidget(right_scroll, 2)

        self._rebuild_cards()
        self._load_form(0)

    # ---------- Загрузка аккаунтов ----------
    def _load_accounts(self):
        from ui.paths import get_app_data_dir
        data_dir = get_app_data_dir()
        path = os.path.join(data_dir, "accounts.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def refresh_accounts(self):
        self.accounts = self._load_accounts()
        self.account_display_names = self._get_account_display_names()
        self.account_combo.clear()
        self.account_combo.addItems(self.account_display_names)
        if 0 <= self.selected < len(self.store.strategies):
            st = self.store.strategies[self.selected]
            profile_id = st.get('profile_id', '')
            if profile_id:
                for i, acc in enumerate(self.accounts):
                    if acc.get('ads_power_id') == profile_id:
                        self.account_combo.setCurrentIndex(i)
                        break

    def _get_account_display_names(self):
        names = []
        for acc in self.accounts:
            bk = acc.get('bk', 'Неизвестно')
            login = acc.get('login', '')
            profile = acc.get('ads_power_id', '')
            display = f"{bk} ({login})" if login else f"{bk} (ID: {profile})"
            names.append(display)
        return names

    def _get_account_by_index(self, index):
        if 0 <= index < len(self.accounts):
            return self.accounts[index]
        return None

    # ---------- Карточки ----------
    def _rebuild_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, st in enumerate(self.store.strategies):
            card = StrategyCard(i, st)
            card.clicked.connect(self._load_form)
            card.toggled.connect(self._on_switch)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch()

    def _on_switch(self, index, on):
        self.store.set_enabled(index, on)
        self.store.save()
        self.strategies_changed.emit()

        st = self.store.strategies[index]
        name = st.get('name', '?')
        log_bus.info("Стратегия", f"'{name}' — {'включена' if on else 'выключена'}")

        if on:
            main = self.window()
            if hasattr(main, 'after_goal_engine'):
                if st.get('profile_id'):
                    asyncio.create_task(
                        main.after_goal_engine.activate_strategy(st)
                    )
                    logger.info(f"Стратегия '{name}' включена, запускаем AdsPower")
                else:
                    logger.warning(f"Стратегия '{name}' без profile_id")
                    log_bus.warning("Стратегия", f"'{name}' — не указан profile_id")

    def _on_add(self):
        idx = self.store.add()
        self.store.save()
        self.strategies_changed.emit()
        self._rebuild_cards()
        self._load_form(idx)

    def _on_delete(self):
        if not self.store.strategies:
            return
        self.store.remove(self.selected)
        self.store.save()
        self.strategies_changed.emit()
        self._rebuild_cards()
        self._load_form(min(self.selected, max(0, len(self.store.strategies) - 1)))

    def _load_form(self, index):
        if not (0 <= index < len(self.store.strategies)):
            return
        self.selected = index
        st = self.store.strategies[index]
        self.name_edit.setText(st.get("name", ""))

        # ---- Тип стратегии ----
        type_raw = st.get("type", "After-goal")
        self.strategy_type.setCurrentText(TYPE_DISPLAY.get(type_raw, "После гола"))

        # ---- Вид спорта ----
        sport_key = st.get("sport") or SPORT_ANY
        self.sport_combo.setCurrentText(SPORT_DISPLAY.get(sport_key, "Все виды"))

        # ---- Аккаунт ----
        profile_id = st.get('profile_id', '')
        if profile_id:
            for i, acc in enumerate(self.accounts):
                if acc.get('ads_power_id') == profile_id:
                    self.account_combo.setCurrentIndex(i)
                    break
            else:
                self.account_combo.setCurrentIndex(0)
        else:
            self.account_combo.setCurrentIndex(0)

        self.min_delay.setValue(st.get("min_delay", 2.0))
        self.min_score_diff.setValue(st.get("min_score_diff", 2))

        # ---- Рынки ----
        markets = st.get("markets_enabled") or ["winner", "total", "handicap"]
        self.cb_winner.setChecked("winner" in markets)
        self.cb_total.setChecked("total" in markets)
        self.cb_handicap.setChecked("handicap" in markets)

        # ---- Направление ----
        direction = st.get("bet_direction", "best_odds")
        self.direction_combo.setCurrentText(
            DIRECTION_DISPLAY.get(direction, "Лучший коэффициент"))

        # ---- Стороны ----
        self.winner_sides_combo.setCurrentText(
            SIDES_WIN_DISPLAY.get(st.get("winner_sides", "both"), "Любая"))
        self.total_sides_combo.setCurrentText(
            SIDES_TOTAL_DISPLAY.get(st.get("total_sides", "both"), "Любая"))
        self.handicap_sides_combo.setCurrentText(
            SIDES_WIN_DISPLAY.get(st.get("handicap_sides", "both"), "Любая"))

        # ---- Банкролл ----
        self.bet_mode.setCurrentText(st.get("bet_mode", "Фиксированная ставка"))
        self.bet_size.setValue(st.get("bet_size", 100))
        self.min_odds.setValue(st.get("min_odds", 1.30))
        self.max_odds.setValue(st.get("max_odds", 5.0))

        # ---- Автоматизация ----
        self.auto_bet.setChecked(st.get("auto_bet", False))
        self.auto_confirm.setChecked(st.get("auto_confirm", True))
        self.verify_seconds.setValue(st.get("verify_seconds", 3.0))
        self.max_bets_per_match.setValue(st.get("max_bets_per_match", 1))
        self.max_bets_per_phase.setValue(st.get("max_bets_per_phase", 1))
        self.ignore_repeats.setChecked(st.get("ignore_repeats", False))
        self.stop_after_loss.setChecked(st.get("stop_after_loss", False))
        self.max_loss.setValue(st.get("max_loss", 3))
        self.headless_checkbox.setChecked(st.get("headless", False))

    def _on_save(self):
        if not (0 <= self.selected < len(self.store.strategies)):
            return

        # ---- Рынки: минимум один ----
        markets = []
        if self.cb_winner.isChecked():
            markets.append("winner")
        if self.cb_total.isChecked():
            markets.append("total")
        if self.cb_handicap.isChecked():
            markets.append("handicap")
        if not markets:
            log_bus.warning("Стратегия", "Нужно выбрать хотя бы один рынок")
            return

        st = self.store.strategies[self.selected]
        st["name"] = self.name_edit.text().strip() or st["name"]

        # ---- Тип стратегии: русский ключ → английский код ----
        st["type"] = TYPE_CHOICES.get(
            self.strategy_type.currentText(), "After-goal")

        # ---- Вид спорта ----
        st["sport"] = SPORT_CHOICES.get(
            self.sport_combo.currentText(), SPORT_ANY)

        # ---- Аккаунт ----
        acc = self._get_account_by_index(self.account_combo.currentIndex())
        if acc:
            st["profile_id"] = acc.get("ads_power_id", "")
            st["bk"] = acc.get("bk", "")
        else:
            st["profile_id"] = ""
            st["bk"] = ""

        st["min_delay"] = self.min_delay.value()
        st["min_score_diff"] = self.min_score_diff.value()

        # ---- Что ставить: русский ключ → английский код ----
        st["markets_enabled"] = markets
        st["bet_direction"] = DIRECTION_CHOICES.get(
            self.direction_combo.currentText(), "best_odds")
        st["winner_sides"] = SIDES_WIN_CHOICES.get(
            self.winner_sides_combo.currentText(), "both")
        st["total_sides"] = SIDES_TOTAL_CHOICES.get(
            self.total_sides_combo.currentText(), "both")
        st["handicap_sides"] = SIDES_WIN_CHOICES.get(
            self.handicap_sides_combo.currentText(), "both")

        # ---- Банкролл ----
        st["bet_mode"] = self.bet_mode.currentText()
        st["bet_size"] = self.bet_size.value()
        st["min_odds"] = self.min_odds.value()
        st["max_odds"] = self.max_odds.value()

        # ---- Автоматизация ----
        st["auto_bet"] = self.auto_bet.isChecked()
        st["auto_confirm"] = self.auto_confirm.isChecked()
        st["verify_seconds"] = self.verify_seconds.value()
        st["max_bets_per_match"] = self.max_bets_per_match.value()
        st["max_bets_per_phase"] = self.max_bets_per_phase.value()
        st["ignore_repeats"] = self.ignore_repeats.isChecked()
        st["stop_after_loss"] = self.stop_after_loss.isChecked()
        st["max_loss"] = self.max_loss.value()
        st["headless"] = self.headless_checkbox.isChecked()

        if "market_type" in st:
            del st["market_type"]

        self.store.save()
        self.strategies_changed.emit()
        self._rebuild_cards()