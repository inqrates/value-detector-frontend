# ui/pages/accounts.py
import json
import os
import sys
import urllib.request
import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QFrame,
    QFormLayout, QComboBox, QLineEdit, QCheckBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from ui.paths import get_app_data_dir

logger = logging.getLogger(__name__)
ADSPOWER_API_URL = "http://localhost:50325"

BOOKMAKER_CHOICES = ["Fonbet", "Winline", "Liga Stavok", "Leon",
                     "BetBoom", "Pari", "Marathon", "Sportbet", "Olimp", "Betcity", "Zenit"]

class AccountStore:
    def __init__(self):
        data_dir = get_app_data_dir()
        self.path = os.path.join(data_dir, "accounts.json")
        self.accounts = []
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.accounts = json.load(f)
        except Exception:
            self.accounts = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.accounts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Не удалось сохранить аккаунты: {e}")

# ... остальной код AccountsPage без изменений (он использует self.store)


class AccountsPage(QWidget):
    accounts_changed = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.store = AccountStore()
        self.edit_index = -1

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # ===== Левая часть: таблица аккаунтов =====
        left = QVBoxLayout()
        left_title = QLabel("Аккаунты букмекеров")
        left_title.setProperty("class", "sectionTitle")
        left.addWidget(left_title)

        self.acc_table = QTableWidget()
        self.acc_table.setColumnCount(6)
        self.acc_table.setHorizontalHeaderLabels(
            ["№", "Контора", "Логин", "Профиль AdsPower", "API Key", "Статус"])
        self.acc_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.acc_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.acc_table.verticalHeader().setVisible(False)
        self.acc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.acc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.acc_table.setShowGrid(False)
        self.acc_table.setMinimumWidth(430)
        self.acc_table.itemDoubleClicked.connect(lambda item: self._load_to_form(item.row()))
        left.addWidget(self.acc_table)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("✏️ Изменить")
        edit_btn.setProperty("class", "ghostBtn")
        edit_btn.clicked.connect(lambda: self._load_to_form(self.acc_table.currentRow()))
        launch_btn = QPushButton("🚀 Запустить в AdsPower")
        launch_btn.setProperty("class", "ghostBtn")
        launch_btn.clicked.connect(self._launch_adspower)
        del_btn = QPushButton("🗑 Удалить")
        del_btn.setProperty("class", "dangerBtn")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(launch_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        layout.addLayout(left, 2)

        # ===== Правая часть: форма добавления =====
        right = QVBoxLayout()
        form_title = QLabel("Добавить аккаунт")
        form_title.setProperty("class", "sectionTitle")
        right.addWidget(form_title)

        form_card = QFrame()
        form_card.setProperty("class", "sectionCard")
        form_layout = QFormLayout(form_card)
        form_layout.setSpacing(12)
        form_layout.setContentsMargins(18, 18, 18, 18)
        form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.bk_combo = QComboBox()
        self.bk_combo.addItems(BOOKMAKER_CHOICES)
        form_layout.addRow("Контора:", self.bk_combo)

        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("Логин или e-mail")
        form_layout.addRow("Логин:", self.login_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("Пароль от аккаунта")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form_layout.addRow("Пароль:", self.pass_edit)

        self.show_pass_cb = QCheckBox("Показать пароль")
        self.show_pass_cb.toggled.connect(
            lambda on: self.pass_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        form_layout.addRow("", self.show_pass_cb)

        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("Например: k1fdmfr4")
        form_layout.addRow("Профиль AdsPower:", self.profile_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("Ключ API AdsPower (если требуется)")
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        form_layout.addRow("API Key:", self.api_key_edit)

        hint = QLabel("💡 ID профиля копируется из приложения AdsPower —\n"
                      "колонка «Profile ID» в списке профилей.\n"
                      "API Key нужен, если AdsPower требует авторизации.")
        hint.setProperty("class", "hintLabel")
        hint.setWordWrap(True)
        form_layout.addRow("", hint)

        right.addWidget(form_card)

        ctrl = QHBoxLayout()
        save_btn = QPushButton("💾 Сохранить аккаунт")
        save_btn.setProperty("class", "primaryBtn")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("❌ Отмена")
        cancel_btn.setProperty("class", "ghostBtn")
        cancel_btn.clicked.connect(self._reset_form)
        ctrl.addWidget(save_btn)
        ctrl.addWidget(cancel_btn)
        ctrl.addStretch()
        right.addLayout(ctrl)

        right.addStretch()
        layout.addLayout(right, 1)

        self._refresh_table()

    # ---------- Таблица ----------
    @staticmethod
    def _center(text, color=None):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if color:
            item.setForeground(color)
        return item

    def _refresh_table(self):
        accs = self.store.accounts
        self.acc_table.setRowCount(len(accs))
        for row, a in enumerate(accs):
            self.acc_table.setItem(row, 0, self._center(str(row + 1)))
            self.acc_table.setItem(row, 1, self._center(a.get("bk", "")))
            self.acc_table.setItem(row, 2, self._center(a.get("login", "")))
            self.acc_table.setItem(row, 3, self._center(a.get("ads_power_id") or "—"))
            api_key = a.get("api_key", "")
            display_key = "••••" if api_key else ""
            self.acc_table.setItem(row, 4, self._center(display_key))
            linked = bool(a.get("ads_power_id"))
            self.acc_table.setItem(row, 5, self._center(
                "🟢 Привязан" if linked else "🟡 Нет профиля",
                QColor("#42d78d") if linked else QColor("#f2c94c")))

    # ---------- Действия ----------
    def _on_save(self):
        login = self.login_edit.text().strip()
        if not login:
            return

        acc = {
            "bk": self.bk_combo.currentText(),
            "login": login,
            "password": self.pass_edit.text(),
            "ads_power_id": self.profile_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
        }

        if 0 <= self.edit_index < len(self.store.accounts):
            self.store.accounts[self.edit_index] = acc
        else:
            self.store.accounts.append(acc)

        self.store.save()
        self.accounts_changed.emit()
        self._reset_form()
        self._refresh_table()

    def _load_to_form(self, row):
        if not (0 <= row < len(self.store.accounts)):
            return
        a = self.store.accounts[row]
        self.edit_index = row
        self.bk_combo.setCurrentText(a.get("bk", "Fonbet"))
        self.login_edit.setText(a.get("login", ""))
        self.pass_edit.setText(a.get("password", ""))
        self.profile_edit.setText(a.get("ads_power_id", ""))
        self.api_key_edit.setText(a.get("api_key", ""))

    def _on_delete(self):
        row = self.acc_table.currentRow()
        if 0 <= row < len(self.store.accounts):
            self.store.accounts.pop(row)
            self.store.save()
            self.accounts_changed.emit()
            self._reset_form()
            self._refresh_table()

    def _reset_form(self):
        self.edit_index = -1
        self.login_edit.clear()
        self.pass_edit.clear()
        self.profile_edit.clear()
        self.api_key_edit.clear()
        self.show_pass_cb.setChecked(False)

    def _launch_adspower(self):
        row = self.acc_table.currentRow()
        if not (0 <= row < len(self.store.accounts)):
            return
        acc = self.store.accounts[row]
        pid = acc.get("ads_power_id", "")
        api_key = acc.get("api_key", "")
        if not pid:
            return
        try:
            url = f"http://localhost:50325/api/v2/browser-profile/start"
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            data = json.dumps({"profile_id": pid}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=10) as r:
                response = json.load(r)
                ok = response.get("code") == 0
                self._flash_status(ok, pid)
        except Exception as e:
            print(f"❌ Ошибка запуска AdsPower: {e}")
            logger.error(f"AdsPower ошибка: {e}")
            self._flash_status(False, pid)

    def _flash_status(self, ok, pid):
        row = self.acc_table.currentRow()
        if row < 0:
            return
        item = self._center(
            "🟢 Запущен" if ok else "🔴 AdsPower не отвечает",
            QColor("#42d78d") if ok else QColor("#eb5757"))
        self.acc_table.setItem(row, 5, item)