# ui/pages/strategies.py
import json
import os
import asyncio
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QLineEdit,
    QGroupBox, QFormLayout, QPushButton
)
from PyQt5.QtCore import Qt, pyqtSignal

from ui.components.switch import Switch
from ui.strategy_store import StrategyStore


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
        meta = QLabel(f"{strategy.get('type', '')} • {bk} (профиль: {profile[:8] if profile else 'не выбран'})")
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


def _make_field_grow(form):
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)


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

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        right_inner = QWidget()
        right_inner.setMinimumWidth(430)
        right = QVBoxLayout(right_inner)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)

        right_title = QLabel("Настройка стратегии")
        right_title.setProperty("class", "sectionTitle")
        right.addWidget(right_title)

        main_group = QGroupBox("Основные параметры")
        main_form = QFormLayout(main_group)
        main_form.setSpacing(10)
        _make_field_grow(main_form)

        self.name_edit = QLineEdit()
        main_form.addRow("Название:", self.name_edit)

        self.strategy_type = QComboBox()
        self.strategy_type.addItems(["After-goal", "Value", "Arbitrage", "Corridor"])
        main_form.addRow("Тип стратегии:", self.strategy_type)

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

        auto_group = QGroupBox("Автоматизация")
        auto_form = QFormLayout(auto_group)
        auto_form.setSpacing(10)
        _make_field_grow(auto_form)

        self.auto_bet = QCheckBox("Автоматическая ставка")
        auto_form.addRow(self.auto_bet)
        self.auto_confirm = QCheckBox("Подтверждение перед ставкой")
        self.auto_confirm.setChecked(True)
        auto_form.addRow(self.auto_confirm)
        self.stop_after_loss = QCheckBox("Стоп после серии убытков")
        auto_form.addRow(self.stop_after_loss)
        self.max_loss = QSpinBox()
        self.max_loss.setRange(1, 10)
        self.max_loss.setSuffix(" подряд")
        auto_form.addRow("Макс. убытков:", self.max_loss)

        # ---- Headless режим ----
        self.headless_checkbox = QCheckBox("Headless режим (без интерфейса)")
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
    # ui/pages/strategies.py (фрагмент)

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
        """Обновляет список аккаунтов из accounts.json и перезаполняет выпадающий список."""
        self.accounts = self._load_accounts()
        self.account_display_names = self._get_account_display_names()
        self.account_combo.clear()
        self.account_combo.addItems(self.account_display_names)
        # Восстановить выбор, если возможно
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


    # ---------- Методы управления карточками ----------
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
        self.strategy_type.setCurrentText(st.get("type", "After-goal"))

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
        self.bet_mode.setCurrentText(st.get("bet_mode", "Фиксированная ставка"))
        self.bet_size.setValue(st.get("bet_size", 100))
        self.min_odds.setValue(st.get("min_odds", 1.30))
        self.max_odds.setValue(st.get("max_odds", 5.0))
        self.auto_bet.setChecked(st.get("auto_bet", False))
        self.auto_confirm.setChecked(st.get("auto_confirm", True))
        self.stop_after_loss.setChecked(st.get("stop_after_loss", False))
        self.max_loss.setValue(st.get("max_loss", 3))
        self.headless_checkbox.setChecked(st.get("headless", False))

    def _on_save(self):
        if not (0 <= self.selected < len(self.store.strategies)):
            return
        st = self.store.strategies[self.selected]
        st["name"] = self.name_edit.text().strip() or st["name"]
        st["type"] = self.strategy_type.currentText()

        acc = self._get_account_by_index(self.account_combo.currentIndex())
        if acc:
            st["profile_id"] = acc.get("ads_power_id", "")
            st["bk"] = acc.get("bk", "")
        else:
            st["profile_id"] = ""
            st["bk"] = ""

        st["min_delay"] = self.min_delay.value()
        st["min_score_diff"] = self.min_score_diff.value()
        st["bet_mode"] = self.bet_mode.currentText()
        st["bet_size"] = self.bet_size.value()
        st["min_odds"] = self.min_odds.value()
        st["max_odds"] = self.max_odds.value()
        st["auto_bet"] = self.auto_bet.isChecked()
        st["auto_confirm"] = self.auto_confirm.isChecked()
        st["stop_after_loss"] = self.stop_after_loss.isChecked()
        st["max_loss"] = self.max_loss.value()
        st["headless"] = self.headless_checkbox.isChecked()
        self.store.save()
        self.strategies_changed.emit()
        self._rebuild_cards()