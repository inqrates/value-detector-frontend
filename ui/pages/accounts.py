"""
Страница «Аккаунты» — версия под Patchright (без AdsPower).
- Профиль = папка %APPDATA%/ValueDetectorPro/profiles/<id>
- «Открыть профиль» запускает браузер, чтобы залогиниться в БК вручную
- «Настройки профиля» — прокси + отпечаток (антидетект-панель)
ВАЖНО: ключ JSON остаётся 'ads_power_id' для совместимости
со strategies.py и dashboard.py — теперь это ID нашего профиля.
"""
import json
import os
import asyncio
import logging

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QFrame,
    QFormLayout, QComboBox, QLineEdit, QCheckBox, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor

from ui.paths import get_app_data_dir
from ui.log_bus import log_bus
from core.browser_launcher import PatchrightBrowser
from ui.profile_settings_dialog import ProfileSettingsDialog

logger = logging.getLogger(__name__)

BOOKMAKER_CHOICES = ["Fonbet", "Pari", "Winline", "Liga Stavok", "Leon",
                     "Marathon", "Sportbet", "Olimp", "Betcity", "Zenit"]

# Куда открывать вкладку при ручном открытии профиля (для логина в БК)
BK_START_URLS = {
    "fonbet":     "https://fon.bet/",
    "pari":       "https://pari.ru/",
    "winline":    "https://winline.ru/",
    "ligastavok": "https://www.ligastavok.ru/",
    "leon":       "https://leon.ru/",
    "marathon":   "https://new.marathonbet.ru/su/",
    "sportbet":   "https://sportbet.ru/",
    "olimp":      "https://www.olimp.bet/",
    "betcity":    "https://betcity.ru/ru/",
    "zenit":      "https://zenit.win/",
}


def _norm_bk(name: str) -> str:
    return (name or "").lower().replace(" ", "").replace("-", "").replace("_", "")


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


class AccountsPage(QWidget):
    accounts_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.store = AccountStore()
        self.edit_index = -1
        # Профили, открытые вручную (если движок недоступен).
        # Обычно кладём wrapper в engine._browsers — тогда его
        # закроет engine.shutdown() при выходе из приложения.
        self._manual_browsers = {}
        self._opening = set()

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # ===== Левая часть: таблица аккаунтов =====
        left = QVBoxLayout()
        left_title = QLabel("Аккаунты букмекеров")
        left_title.setProperty("class", "sectionTitle")
        left.addWidget(left_title)

        self.acc_table = QTableWidget()
        self.acc_table.setColumnCount(5)
        self.acc_table.setHorizontalHeaderLabels(
            ["№", "Контора", "Логин", "Профиль", "Статус"])
        self.acc_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.acc_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.acc_table.verticalHeader().setVisible(False)
        self.acc_table.setEditTriggers(QAbstractItemView_NoEditTriggers())
        self.acc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.acc_table.setShowGrid(False)
        self.acc_table.setMinimumWidth(430)
        self.acc_table.itemDoubleClicked.connect(
            lambda item: self._load_to_form(item.row()))
        left.addWidget(self.acc_table)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("✏️ Изменить")
        edit_btn.setProperty("class", "ghostBtn")
        edit_btn.clicked.connect(
            lambda: self._load_to_form(self.acc_table.currentRow()))

        open_btn = QPushButton("🚀 Открыть профиль")
        open_btn.setProperty("class", "ghostBtn")
        open_btn.setToolTip("Открыть браузер профиля, чтобы залогиниться в БК.\n"
                            "Сессия сохранится в папке профиля.")
        open_btn.clicked.connect(self._open_profile)

        close_btn = QPushButton("⏹ Закрыть профиль")
        close_btn.setProperty("class", "ghostBtn")
        close_btn.clicked.connect(self._close_profile)

        settings_btn = QPushButton("⚙️ Настройки профиля")
        settings_btn.setProperty("class", "ghostBtn")
        settings_btn.setToolTip("Прокси, отпечаток, экран, WebRTC — как в антидетекте")
        settings_btn.clicked.connect(self._profile_settings)

        del_btn = QPushButton("🗑 Удалить")
        del_btn.setProperty("class", "dangerBtn")
        del_btn.clicked.connect(self._on_delete)

        btn_row.addWidget(edit_btn)
        btn_row.addWidget(open_btn)
        btn_row.addWidget(close_btn)
        btn_row.addWidget(settings_btn)
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
        self.profile_edit.setPlaceholderText("Например: betcity_1 (латиница, цифры, _)")
        form_layout.addRow("ID профиля:", self.profile_edit)

        hint = QLabel(
            "💡 ID профиля — любое имя латиницей. Браузер профиля хранится в\n"
            "%APPDATA%\\ValueDetectorPro\\profiles\\<ID>.\n\n"
            "После сохранения нажмите «🚀 Открыть профиль» и залогиньтесь\n"
            "в БК в открывшемся браузере — сессия сохранится навсегда.\n"
            "Прокси и отпечаток — в «⚙️ Настройки профиля»."
        )
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
            linked = bool(a.get("ads_power_id"))
            self.acc_table.setItem(row, 4, self._center(
                "🟢 Привязан" if linked else "🟡 Нет профиля",
                QColor("#42d78d") if linked else QColor("#f2c94c")))

    def _selected_account(self):
        row = self.acc_table.currentRow()
        if 0 <= row < len(self.store.accounts):
            return row, self.store.accounts[row]
        return -1, None

    # ---------- Действия ----------
    def _on_save(self):
        login = self.login_edit.text().strip()
        if not login:
            return

        acc = {
            "bk": self.bk_combo.currentText(),
            "login": login,
            "password": self.pass_edit.text(),
            # Ключ сохранён для совместимости со strategies/dashboard.
            # Теперь это ID папки профиля Patchright, не AdsPower.
            "ads_power_id": self.profile_edit.text().strip(),
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
        self.show_pass_cb.setChecked(False)

    # ---------- Профиль: открыть / закрыть / настройки ----------
    def _get_engine(self):
        return getattr(self.window(), "after_goal_engine", None)

    def _open_profile(self):
        row, acc = self._selected_account()
        if not acc:
            return
        pid = (acc.get("ads_power_id") or "").strip()
        if not pid:
            QMessageBox.warning(self, "Профиль", "Сначала укажи и сохрани ID профиля.")
            return
        if pid in self._opening:
            return
        self._opening.add(pid)
        log_bus.info("Браузер", f"Открываю профиль {pid}...")
        asyncio.ensure_future(self._open_profile_async(pid, acc.get("bk", "")))

    async def _open_profile_async(self, pid: str, bk: str):
        engine = self._get_engine()
        url = BK_START_URLS.get(_norm_bk(bk))
        try:
            wrapper = None
            fresh = False
            # Если профиль уже открыт (стратегией или вручную) — не плодим
            # второй браузер на той же папке (Chromium так нельзя).
            if engine is not None:
                w = engine._browsers.get(pid)
                if w and w.is_connected():
                    wrapper = w
            if wrapper is None:
                wrapper = PatchrightBrowser(profile_id=pid, headless=False)
                await wrapper.__aenter__()
                fresh = True
                if engine is not None:
                    # Кладём в реестр движка — он закроет браузер при выходе
                    engine._browsers[pid] = wrapper
                else:
                    self._manual_browsers[pid] = wrapper

            page = wrapper.page if fresh else await wrapper.new_page()
            if url:
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=30000)
                except Exception as e:
                    logger.warning(f"goto {url}: {e}")

            self._flash_status(True, pid)
            log_bus.success(
                "Браузер",
                f"Профиль {pid} открыт — залогинься в БК в окне браузера"
            )
        except Exception as e:
            logger.error(f"Открытие профиля {pid}: {e}", exc_info=True)
            log_bus.error("Браузер", f"Не удалось открыть профиль {pid}: {e}")
            self._flash_status(False, pid)
        finally:
            self._opening.discard(pid)

    def _close_profile(self):
        row, acc = self._selected_account()
        if not acc:
            return
        pid = (acc.get("ads_power_id") or "").strip()
        if not pid:
            return
        engine = self._get_engine()
        # Защита: не закрываем профиль, на котором работает мониторинг
        if engine is not None:
            active = any(p == pid for p in engine._monitoring_profiles.values())
            if active:
                QMessageBox.warning(
                    self, "Профиль",
                    "На этом профиле сейчас работает стратегия.\n"
                    "Сначала выключи стратегию, потом закрывай профиль."
                )
                return
        asyncio.ensure_future(self._close_profile_async(pid))

    async def _close_profile_async(self, pid: str):
        engine = self._get_engine()
        wrapper = None
        if engine is not None:
            wrapper = engine._browsers.pop(pid, None)
        if wrapper is None:
            wrapper = self._manual_browsers.pop(pid, None)
        if wrapper is None:
            log_bus.info("Браузер", f"Профиль {pid} не был открыт")
            return
        try:
            await wrapper.stop()
            log_bus.info("Браузер", f"Профиль {pid} закрыт")
        except Exception as e:
            logger.warning(f"stop({pid}): {e}")

    def _profile_settings(self):
        row, acc = self._selected_account()
        if not acc:
            return
        pid = (acc.get("ads_power_id") or "").strip()
        if not pid:
            QMessageBox.warning(
                self, "Настройки профиля",
                "Сначала укажи и сохрани ID профиля."
            )
            return
        dlg = ProfileSettingsDialog(pid, self)
        if dlg.exec_():
            log_bus.success(
                "Браузер",
                f"Настройки профиля {pid} сохранены "
                f"(применятся при следующем запуске профиля)"
            )

    def _flash_status(self, ok: bool, pid: str):
        for row, a in enumerate(self.store.accounts):
            if (a.get("ads_power_id") or "") == pid:
                self.acc_table.setItem(row, 4, self._center(
                    "🟢 Открыт" if ok else "🔴 Ошибка",
                    QColor("#42d78d") if ok else QColor("#eb5757")))
                break


def QAbstractItemView_NoEditTriggers():
    from PyQt5.QtWidgets import QAbstractItemView
    return QAbstractItemView.NoEditTriggers