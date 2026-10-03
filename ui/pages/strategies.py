# ui/pages/strategies.py
import json
import os
import asyncio
import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QLineEdit,
    QGroupBox, QFormLayout, QPushButton, QRadioButton,
)
from PyQt5.QtCore import Qt, pyqtSignal

from ui.components.switch import Switch
from ui.strategy_store import (
    StrategyStore, SPORT_ANY, ALL_MARKETS,
    missing_markets_for_strategy,
    recommended_thresholds_for_sport,
)
from ui.log_bus import log_bus

logger = logging.getLogger(__name__)


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

TYPE_CHOICES = {
    "Послегол": "After-goal",
}
TYPE_DISPLAY = {v: k for k, v in TYPE_CHOICES.items()}
TYPE_DISPLAY_FULL = {
    "After-goal": "Послегол",
}

# Понятные названия для рынков — для warning'ов
MARKET_DISPLAY = {
    "winner_1": "Победитель партии П1",
    "winner_2": "Победитель партии П2",
    "total_over": "Тотал партии Больше",
    "total_under": "Тотал партии Меньше",
    "handicap_1": "Фора партии 1",
    "handicap_2": "Фора партии 2",
    "it1_over": "ИТ1 Больше",
    "it1_under": "ИТ1 Меньше",
    "it2_over": "ИТ2 Больше",
    "it2_under": "ИТ2 Меньше",
    "odd": "Чёт/Нечёт",
    "race": "Гонка внутри сета",
    "point": "Следующее очко",
}


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
        type_label = TYPE_DISPLAY_FULL.get(type_raw, type_raw)

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
        self.switch.toggled_state.connect(
            lambda on: self.toggled.emit(self.index, on)
        )
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

        # ============ Левая колонка ============
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

        # ============ Правая колонка ============
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        right_inner = QWidget()
        right_inner.setMinimumWidth(520)
        right = QVBoxLayout(right_inner)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)

        right_title = QLabel("Настройка стратегии")
        right_title.setProperty("class", "sectionTitle")
        right.addWidget(right_title)

        # ---- Основные параметры ----
        main_group = QGroupBox("Основные параметры")
        main_form = QFormLayout(main_group)
        main_form.setSpacing(10)
        _make_field_grow(main_form)

        self.name_edit = QLineEdit()
        main_form.addRow("Название:", self.name_edit)

        self.strategy_type = QComboBox()
        self.strategy_type.addItems(list(TYPE_CHOICES.keys()))
        main_form.addRow("Тип стратегии:", self.strategy_type)

        self.sport_combo = QComboBox()
        self.sport_combo.addItems(list(SPORT_CHOICES.keys()))
        self.sport_combo.currentTextChanged.connect(self._update_threshold_hint)
        main_form.addRow("Вид спорта:", self.sport_combo)

        self.account_combo = QComboBox()
        self.account_combo.addItems(self.account_display_names)
        main_form.addRow("Аккаунт для ставок:", self.account_combo)

        self.min_delay = QDoubleSpinBox()
        self.min_delay.setRange(0.5, 30.0)
        self.min_delay.setSuffix(" сек")
        main_form.addRow("Мин. задержка:", self.min_delay)

        right.addWidget(main_group)

        # ============================================================
        # ---- Что ставить ----
        # ============================================================
        what_group = QGroupBox("Что ставить")
        what_layout = QVBoxLayout(what_group)
        what_layout.setSpacing(12)

        # ── Способ выбора ──
        mode_label = QLabel("Способ выбора рынков:")
        mode_label.setStyleSheet(
            "color: rgba(245,249,252,0.85); font-size: 12px; font-weight: 600;")
        what_layout.addWidget(mode_label)

        self.market_mode_auto = QRadioButton(
            "Автоматически — бот сам выберет лучший доступный рынок")
        self.market_mode_manual = QRadioButton(
            "Вручную — я сам отмечу, на что ставить")
        self.market_mode_auto.setChecked(True)

        mode_col = QVBoxLayout()
        mode_col.setContentsMargins(10, 0, 0, 0)
        mode_col.setSpacing(4)
        mode_col.addWidget(self.market_mode_auto)
        mode_col.addWidget(self.market_mode_manual)
        what_layout.addLayout(mode_col)

        # ── Критерий авто ──
        self.auto_criterion_group = QWidget()
        auto_form = QVBoxLayout(self.auto_criterion_group)
        auto_form.setContentsMargins(30, 6, 0, 0)
        auto_form.setSpacing(4)

        auto_label = QLabel("Критерий выбора лучшего варианта:")
        auto_label.setStyleSheet(
            "color: rgba(199,214,223,0.7); font-size: 11px; font-weight: 600;")
        auto_form.addWidget(auto_label)

        self.crit_reliable = QRadioButton(
            "Самый надёжный исход (победитель → тотал → фора)")
        self.crit_max_odds = QRadioButton(
            "Самый высокий коэффициент")
        self.crit_all = QRadioButton(
            "Ставить всё подтверждённое (несколько ставок за сигнал)")
        self.crit_reliable.setChecked(True)

        for w in (self.crit_reliable, self.crit_max_odds, self.crit_all):
            auto_form.addWidget(w)

        hint_auto = QLabel(
            "«Самый надёжный» — приоритет победителю партии, потом тоталу, "
            "потом форе.\n"
            "«Самый высокий коэф.» — из подтверждённых исходов берётся "
            "максимальный коэффициент.\n"
            "«Всё подтверждённое» — пока работает как «надёжный» (одна "
            "ставка за сигнал). Мульти-ставки — в следующем обновлении."
        )
        hint_auto.setWordWrap(True)
        hint_auto.setStyleSheet(
            "color: rgba(199,214,223,0.5); font-size: 10px; font-style: italic; "
            "padding-left: 4px;")
        auto_form.addWidget(hint_auto)

        what_layout.addWidget(self.auto_criterion_group)

        # ── Ручной режим ──
        self.manual_group = QWidget()
        manual_form = QVBoxLayout(self.manual_group)
        manual_form.setContentsMargins(30, 6, 0, 0)
        manual_form.setSpacing(8)

        manual_hint = QLabel(
            "Отметьте рынки, на которые ставить. "
            "Бот будет ставить только на выбранное:")
        manual_hint.setStyleSheet(
            "color: rgba(199,214,223,0.7); font-size: 11px; font-weight: 600;")
        manual_hint.setWordWrap(True)
        manual_form.addWidget(manual_hint)

        self.cb_markets = {}

        def make_market_row(label, items):
            row = QHBoxLayout()
            row.setSpacing(10)

            lbl = QLabel(label)
            lbl.setMinimumWidth(155)
            lbl.setStyleSheet(
                "color: rgba(245,249,252,0.82); font-size: 12px;")
            row.addWidget(lbl)

            result = {}
            for code, text in items:
                cb = QCheckBox(text)
                cb.setChecked(True)
                row.addWidget(cb)
                result[code] = cb

            row.addStretch()
            manual_form.addLayout(row)
            return result

        self.cb_markets.update(make_market_row(
            "Победитель партии:",
            [("winner_1", "П1"), ("winner_2", "П2")],
        ))
        self.cb_markets.update(make_market_row(
            "Тотал партии:",
            [("total_over", "Больше"), ("total_under", "Меньше")],
        ))
        self.cb_markets.update(make_market_row(
            "Фора в партии:",
            [("handicap_1", "Фора 1"), ("handicap_2", "Фора 2")],
        ))
        self.cb_markets.update(make_market_row(
            "Индивидуальный тотал:",
            [("it1_over", "ИТ1 Б"), ("it1_under", "ИТ1 М"),
             ("it2_over", "ИТ2 Б"), ("it2_under", "ИТ2 М")],
        ))
        self.cb_markets.update(make_market_row(
            "Дополнительно:",
            [("odd", "Чёт/Нечёт"), ("race", "Гонка"), ("point", "Очко")],
        ))

        what_layout.addWidget(self.manual_group)

        # ── Пороги отставания ──
        thresholds_sep = QFrame()
        thresholds_sep.setFrameShape(QFrame.HLine)
        thresholds_sep.setStyleSheet(
            "color: rgba(255,255,255,0.08); background: rgba(255,255,255,0.08); "
            "max-height: 1px;")
        what_layout.addWidget(thresholds_sep)

        thr_label = QLabel("Пороги отставания (очки):")
        thr_label.setStyleSheet(
            "color: rgba(245,249,252,0.85); font-size: 12px; font-weight: 600;")
        what_layout.addWidget(thr_label)

        thr_hint = QLabel(
            "Минимальное отставание fast БК от slow, при котором исход "
            "считается подтверждённым.")
        thr_hint.setStyleSheet(
            "color: rgba(199,214,223,0.5); font-size: 10px; font-style: italic;")
        thr_hint.setWordWrap(True)
        what_layout.addWidget(thr_hint)

        # ── Рекомендуемые пороги по спорту ──
        self.thr_recommend_label = QLabel("")
        self.thr_recommend_label.setWordWrap(True)
        self.thr_recommend_label.setStyleSheet(
            "color: #21c1de; font-size: 10px; font-weight: 600; "
            "padding: 4px 0;")
        what_layout.addWidget(self.thr_recommend_label)

        thr_row = QHBoxLayout()
        thr_row.setSpacing(14)

        def make_threshold(label, default):
            wrap = QWidget()
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: rgba(245,249,252,0.82); font-size: 12px;")
            h.addWidget(lbl)
            spin = QSpinBox()
            spin.setRange(1, 30)
            spin.setValue(default)
            spin.setSuffix(" очк.")
            spin.setFixedWidth(85)
            h.addWidget(spin)
            return wrap, spin

        w1, self.spin_winner_thr = make_threshold("Победитель:", 2)
        w2, self.spin_total_thr = make_threshold("Тотал:", 1)
        w3, self.spin_handicap_thr = make_threshold("Фора:", 3)
        w4, self.spin_it_thr = make_threshold("ИТ:", 2)
        thr_row.addWidget(w1)
        thr_row.addWidget(w2)
        thr_row.addWidget(w3)
        thr_row.addWidget(w4)
        thr_row.addStretch()
        what_layout.addLayout(thr_row)

        # ── RACE (гонка внутри сета) ──
        race_sep = QFrame()
        race_sep.setFrameShape(QFrame.HLine)
        race_sep.setStyleSheet(
            "color: rgba(255,255,255,0.08); background: rgba(255,255,255,0.08); "
            "max-height: 1px;")
        what_layout.addWidget(race_sep)

        self.cb_race_enabled = QCheckBox(
            "Учитывать гонку внутри сета (RACE)")
        self.cb_race_enabled.setToolTip(
            "Работает только у БК, которые дают рынки RACE в лайв-ленте.\n"
            "Сейчас это только LigaStavok."
        )
        what_layout.addWidget(self.cb_race_enabled)

        race_hint = QLabel(
            "RACE — это отрезок внутри сета (до 3, 5, 7 или 10 очков). "
            "Букмекер даёт по ним отдельные тоталы и форы.\n"
            "У LigaStavok — есть. У Marathon — нет. У остальных — проверяйте."
        )
        race_hint.setStyleSheet(
            "color: rgba(199,214,223,0.5); font-size: 10px; font-style: italic;")
        race_hint.setWordWrap(True)
        what_layout.addWidget(race_hint)

        # ── Обновление видимости ──
        def _update_market_mode():
            is_auto = self.market_mode_auto.isChecked()
            self.auto_criterion_group.setVisible(is_auto)
            self.manual_group.setVisible(not is_auto)

        self.market_mode_auto.toggled.connect(lambda _: _update_market_mode())
        _update_market_mode()

        right.addWidget(what_group)

        # ---- Банкролл / ставка ----
        bank_group = QGroupBox("Ставка")
        bank_form = QFormLayout(bank_group)
        bank_form.setSpacing(10)
        _make_field_grow(bank_form)

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

        # ---- Автоматизация ----
        auto_group = QGroupBox("Автоматизация")
        auto_form = QFormLayout(auto_group)
        auto_form.setSpacing(10)
        _make_field_grow(auto_form)

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

        self.max_bets_per_session = QSpinBox()
        self.max_bets_per_session.setRange(0, 10000)
        self.max_bets_per_session.setSpecialValueText("без лимита")
        self.max_bets_per_session.setSuffix(" ставок")
        self.max_bets_per_session.setToolTip(
            "Остановить работу после N успешно принятых ставок за сессию.\n"
            "0 = без лимита. Сбрасывается при перезапуске приложения."
        )
        auto_form.addRow("Лимит ставок за сессию:", self.max_bets_per_session)

        self.ignore_repeats = QCheckBox("Игнорировать повторные сигналы")
        auto_form.addRow(self.ignore_repeats)

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
        self._update_threshold_hint()

    # ---------- Аккаунты ----------
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

    # ---------- Подсказка по порогам ----------
    def _update_threshold_hint(self):
        """Обновляет label с рекомендованными порогами по спорту."""
        sport_key = SPORT_CHOICES.get(self.sport_combo.currentText(), SPORT_ANY)
        if sport_key == SPORT_ANY:
            self.thr_recommend_label.setText(
                "💡 Для «Все виды» пороги применяются ко всем спортам. "
                "Для точной настройки создайте отдельные стратегии "
                "под каждый вид."
            )
            return

        rec = recommended_thresholds_for_sport(sport_key)
        self.thr_recommend_label.setText(
            f"💡 Рекомендуем для этого вида: "
            f"Победитель={rec['winner']}, Тотал={rec['total']}, "
            f"Фора={rec['handicap']}, ИТ={rec['it']}"
        )

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
        if not (0 <= index < len(self.store.strategies)):
            return
        st = self.store.strategies[index]
        current = bool(st.get('enabled', False))
        on = bool(on)
        if current == on:
            return

        self.store.set_enabled(index, on)
        self.store.save()
        self.strategies_changed.emit()

        name = st.get('name', '?')
        log_bus.info("Стратегия", f"'{name}' — {'включена' if on else 'выключена'}")

        main = self.window()
        if not hasattr(main, 'after_goal_engine'):
            return

        engine = main.after_goal_engine
        profile_id = st.get('profile_id')

        if on:
            if profile_id:
                asyncio.create_task(engine.activate_strategy(st))
        else:
            if profile_id:
                asyncio.create_task(engine.deactivate_profile(profile_id, name))
                logger.info(f"Стратегия '{name}' выключена, закрываем профиль")
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

        # основные
        self.name_edit.setText(st.get("name", ""))
        type_raw = st.get("type", "After-goal")
        self.strategy_type.setCurrentText(TYPE_DISPLAY.get(type_raw, "Послегол"))
        sport_key = st.get("sport") or SPORT_ANY
        self.sport_combo.setCurrentText(SPORT_DISPLAY.get(sport_key, "Все виды"))

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

        # ── Режим рынков ──
        mode = st.get("market_mode", "auto")
        if mode == "manual":
            self.market_mode_manual.setChecked(True)
        else:
            self.market_mode_auto.setChecked(True)

        criterion = st.get("auto_criterion", "reliable")
        if criterion == "max_odds":
            self.crit_max_odds.setChecked(True)
        elif criterion == "all_confirmed":
            self.crit_all.setChecked(True)
        else:
            self.crit_reliable.setChecked(True)

        manual = st.get("manual_markets") or []
        for code, cb in self.cb_markets.items():
            cb.setChecked(code in manual)

        self.cb_race_enabled.setChecked(
            bool(st.get("race_enabled", False))
        )

        thresholds = st.get("market_thresholds") or {}
        self.spin_winner_thr.setValue(int(thresholds.get("winner", 2)))
        self.spin_total_thr.setValue(int(thresholds.get("total", 1)))
        self.spin_handicap_thr.setValue(int(thresholds.get("handicap", 3)))
        self.spin_it_thr.setValue(int(thresholds.get("it", 2)))

        # ставка
        self.bet_size.setValue(st.get("bet_size", 100))
        self.min_odds.setValue(st.get("min_odds", 1.30))
        self.max_odds.setValue(st.get("max_odds", 5.0))

        # автоматизация
        self.verify_seconds.setValue(st.get("verify_seconds", 3.0))
        self.max_bets_per_match.setValue(st.get("max_bets_per_match", 1))
        self.max_bets_per_phase.setValue(st.get("max_bets_per_phase", 1))
        self.max_bets_per_session.setValue(st.get("max_bets_per_session", 0))
        self.ignore_repeats.setChecked(st.get("ignore_repeats", False))
        self.headless_checkbox.setChecked(st.get("headless", False))

        self._update_threshold_hint()

    def _on_save(self):
        if not (0 <= self.selected < len(self.store.strategies)):
            return

        # Валидация: в ручном режиме нужен хотя бы один рынок
        if self.market_mode_manual.isChecked():
            manual_check = [
                code for code, cb in self.cb_markets.items() if cb.isChecked()
            ]
            if not manual_check:
                log_bus.warning(
                    "Стратегия",
                    "В ручном режиме нужно выбрать хотя бы один рынок",
                )
                return

        st = self.store.strategies[self.selected]
        st["name"] = self.name_edit.text().strip() or st["name"]
        st["type"] = TYPE_CHOICES.get(self.strategy_type.currentText(), "After-goal")
        st["sport"] = SPORT_CHOICES.get(self.sport_combo.currentText(), SPORT_ANY)

        acc = self._get_account_by_index(self.account_combo.currentIndex())
        if acc:
            st["profile_id"] = acc.get("ads_power_id", "")
            st["bk"] = acc.get("bk", "")
        else:
            st["profile_id"] = ""
            st["bk"] = ""

        st["min_delay"] = self.min_delay.value()

        st["market_thresholds"] = {
            "winner":   self.spin_winner_thr.value(),
            "total":    self.spin_total_thr.value(),
            "handicap": self.spin_handicap_thr.value(),
            "it":       self.spin_it_thr.value(),
        }
        # min_score_diff — legacy fallback, используется только в
        # _get_min_threshold при auto-режиме и в дефолтах.
        # Считаем минимум по ВСЕМ четырём базовым рынкам, чтобы
        # не блокировать стратегии «только ИТ» (см. _MARKET_CODE_TO_BASE).
        st["min_score_diff"] = min(
            st["market_thresholds"]["winner"],
            st["market_thresholds"]["total"],
            st["market_thresholds"]["handicap"],
            st["market_thresholds"]["it"],
        )

        # ── Рынки ──
        st["market_mode"] = (
            "manual" if self.market_mode_manual.isChecked() else "auto"
        )

        if self.crit_max_odds.isChecked():
            st["auto_criterion"] = "max_odds"
        elif self.crit_all.isChecked():
            st["auto_criterion"] = "all_confirmed"
        else:
            st["auto_criterion"] = "reliable"

        manual = [code for code, cb in self.cb_markets.items() if cb.isChecked()]
        if not manual:
            manual = ["winner_1", "winner_2"]
        st["manual_markets"] = manual

        st["race_enabled"] = self.cb_race_enabled.isChecked()

        # ставка
        st["bet_size"] = self.bet_size.value()
        st["min_odds"] = self.min_odds.value()
        st["max_odds"] = self.max_odds.value()

        # автоматизация
        st["verify_seconds"] = self.verify_seconds.value()
        st["max_bets_per_match"] = self.max_bets_per_match.value()
        st["max_bets_per_phase"] = self.max_bets_per_phase.value()
        st["max_bets_per_session"] = self.max_bets_per_session.value()
        st["ignore_repeats"] = self.ignore_repeats.isChecked()
        st["headless"] = self.headless_checkbox.isChecked()

        self.store.save()
        self.strategies_changed.emit()
        self._rebuild_cards()

        # ── Проверка рынков против возможностей БК ──
        try:
            missing = missing_markets_for_strategy(st)
            if missing:
                pretty = ", ".join(
                    MARKET_DISPLAY.get(code, code) for code in missing
                )
                log_bus.warning(
                    "Стратегия",
                    f"«{st.get('name')}» ({st.get('bk') or '—'}): "
                    f"рынки не поддерживаются БК и будут пропущены: {pretty}"
                )
        except Exception as e:
            logger.warning(f"capability check on save: {e}", exc_info=True)