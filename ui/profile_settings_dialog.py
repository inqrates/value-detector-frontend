"""
Диалог настроек профиля.

Личность профиля — записанный отпечаток реального устройства (пресет
Camoufox). Она показана только для чтения: ручная правка UA/WebGL/экрана
приводит к рассогласованию полей, которое антифрод видит сразу.
Смена личности = кнопка «Новая личность» (другой реальный отпечаток).

Менять можно то, что не ломает согласованность:
  * прокси,
  * геолокация/язык/таймзона (по IP прокси — рекомендуется),
  * WebRTC.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QComboBox, QPushButton, QLabel,
    QDialogButtonBox, QScrollArea, QFrame,
    QApplication, QMessageBox, QWidget,
)
from PyQt5.QtCore import Qt

from core.profile_config import (
    load_profile_config,
    save_profile_config,
    regenerate_identity,
    used_identity_signatures,
)
from core.fingerprint import (
    GEO_AUTO, GEO_MANUAL, GEO_DEFAULT,
    OS_CHOICES, TIMEZONES, LOCALES,
    preset_summary, unique_preset_count, describe_geo_risk,
)

HINT_STYLE = "color: rgba(199,214,223,0.55); font-size: 10px;"
WARN_STYLE = "color: rgba(242,201,76,0.9); font-size: 10px;"
VALUE_STYLE = "color: rgba(245,249,252,0.95); font-size: 11px;"
MONO_STYLE = (
    "font-family: Consolas, 'Courier New', monospace; font-size: 10px;"
    "color: rgba(245,249,252,0.80);"
)


class ProfileSettingsDialog(QDialog):
    def __init__(self, profile_id: str, parent=None):
        super().__init__(parent)
        self.profile_id = profile_id
        self.cfg = load_profile_config(profile_id)

        self.setWindowTitle(f"Настройки профиля — {profile_id}")
        self.setModal(True)
        self.setSizeGripEnabled(True)

        w, h = 640, 720
        try:
            scr = QApplication.primaryScreen()
            if scr is not None:
                geo = scr.availableGeometry()
                w = min(w, geo.width() - 80)
                h = min(h, geo.height() - 80)
        except Exception:
            pass
        self.resize(max(560, w), max(420, h))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ══════════ Личность ══════════
        id_group = QGroupBox("Личность профиля (отпечаток устройства)")
        il = QVBoxLayout(id_group)
        il.setSpacing(8)

        self.identity_box = QFrame()
        self.identity_box.setStyleSheet(
            "QFrame { background: rgba(8,167,200,0.05);"
            "border: 1px solid rgba(33,193,222,0.25); border-radius: 5px; }"
        )
        self.identity_form = QFormLayout(self.identity_box)
        self.identity_form.setContentsMargins(12, 10, 12, 10)
        self.identity_form.setSpacing(5)
        il.addWidget(self.identity_box)

        hint = QLabel(
            "Отпечаток взят с реального устройства и одинаков при каждом запуске. "
            "Менять отдельные поля (UA, GPU, экран) нельзя: они должны совпадать "
            "друг с другом, иначе сайт увидит подмену."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(HINT_STYLE)
        il.addWidget(hint)

        regen_row = QHBoxLayout()
        self.regen_btn = QPushButton("🎲 Новая личность")
        self.regen_btn.setToolTip(
            "Выдать другой реальный отпечаток (другое устройство).\n"
            "Меняйте только при переезде на новый прокси — "
            "частая ротация на одном IP выглядит подозрительно."
        )
        self.regen_btn.clicked.connect(self._on_regenerate)
        regen_row.addWidget(self.regen_btn)

        self.available_lbl = QLabel("")
        self.available_lbl.setStyleSheet(HINT_STYLE)
        regen_row.addWidget(self.available_lbl, 1)
        il.addLayout(regen_row)
        root.addWidget(id_group)

        # ══════════ Прокси ══════════
        proxy_group = QGroupBox("Прокси")
        pf = QFormLayout(proxy_group)
        pf.setSpacing(6)
        proxy = self.cfg.get("proxy") or {}
        self.proxy_server = QLineEdit(proxy.get("server", ""))
        self.proxy_server.setPlaceholderText(
            "http://host:port или socks5://host:port (пусто = без прокси)"
        )
        pf.addRow("Сервер:", self.proxy_server)
        self.proxy_user = QLineEdit(proxy.get("username", ""))
        pf.addRow("Логин:", self.proxy_user)
        self.proxy_pass = QLineEdit(proxy.get("password", ""))
        self.proxy_pass.setEchoMode(QLineEdit.Password)
        pf.addRow("Пароль:", self.proxy_pass)
        root.addWidget(proxy_group)

        # ══════════ Сеть и гео ══════════
        geo_group = QGroupBox("Сеть и геолокация")
        gf = QFormLayout(geo_group)
        gf.setSpacing(6)
        fp = self.cfg.get("fingerprint") or {}

        self.geo_combo = QComboBox()
        self.geo_combo.addItem("По IP прокси — рекомендуется", GEO_AUTO)
        self.geo_combo.addItem("Задать вручную", GEO_MANUAL)
        self.geo_combo.addItem("Не переопределять", GEO_DEFAULT)
        self._set_data(self.geo_combo, fp.get("geo_mode", GEO_AUTO))
        self.geo_combo.currentIndexChanged.connect(self._geo_changed)
        gf.addRow("Геолокация:", self.geo_combo)

        self.tz_combo = QComboBox()
        self.tz_combo.setEditable(True)
        self.tz_combo.addItems(TIMEZONES)
        self.tz_combo.setCurrentText(fp.get("timezone") or "Europe/Moscow")
        gf.addRow("Часовой пояс:", self.tz_combo)

        self.locale_combo = QComboBox()
        self.locale_combo.setEditable(True)
        self.locale_combo.addItems(LOCALES)
        self.locale_combo.setCurrentText(fp.get("locale") or "ru-RU")
        gf.addRow("Язык:", self.locale_combo)

        self.geo_warn = QLabel("")
        self.geo_warn.setWordWrap(True)
        self.geo_warn.setStyleSheet(WARN_STYLE)
        gf.addRow("", self.geo_warn)

        self.webrtc_combo = QComboBox()
        self.webrtc_combo.addItem("Блокировать — рекомендуется", "block")
        self.webrtc_combo.addItem("Разрешить", "allow")
        self._set_data(self.webrtc_combo, fp.get("webrtc", "block"))
        gf.addRow("WebRTC:", self.webrtc_combo)

        webrtc_hint = QLabel(
            "WebRTC может раскрыть локальный IP в обход прокси. "
            "Блокировка не влияет на работу сайтов."
        )
        webrtc_hint.setWordWrap(True)
        webrtc_hint.setStyleSheet(HINT_STYLE)
        gf.addRow("", webrtc_hint)
        root.addWidget(geo_group)

        root.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ══════════ Нижняя панель ══════════
        bottom = QFrame()
        bottom.setObjectName("fpBottomBar")
        bottom.setStyleSheet(
            "QFrame#fpBottomBar { border-top: 1px solid rgba(255,255,255,0.10); }"
        )
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(10)

        note = QLabel("Применится при следующем запуске профиля")
        note.setStyleSheet(HINT_STYLE + "border: none;")
        bl.addWidget(note, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("💾 Сохранить")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        bl.addWidget(btns)
        outer.addWidget(bottom, 0)

        self._refresh_identity_view()
        self._geo_changed()

    # ---------- helpers ----------
    @staticmethod
    def _set_data(combo: QComboBox, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _refresh_identity_view(self):
        """Перерисовывает карточку личности из текущего конфига."""
        # Чистим старые строки
        while self.identity_form.rowCount():
            self.identity_form.removeRow(0)

        identity = self.cfg.get("identity") or {}
        preset = identity.get("preset")
        s = preset_summary(preset)

        if not preset:
            lbl = QLabel("⚠️ Личность не задана — отпечаток будет случайным")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(WARN_STYLE)
            self.identity_form.addRow(lbl)
            self.available_lbl.setText("")
            return

        os_label = dict(OS_CHOICES).get(identity.get("os", "windows"), "Windows")

        def add(title: str, value: str, mono: bool = False):
            v = QLabel(str(value))
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setStyleSheet(MONO_STYLE if mono else VALUE_STYLE)
            self.identity_form.addRow(f"{title}:", v)

        add("ОС", os_label)
        add("Экран", f"{s.get('screen')}  ({s.get('color_depth')} бит)")
        add("CPU", f"{s.get('cpu_cores')} ядер")
        add("GPU", s.get("gpu_renderer"), mono=True)
        add("Голоса", f"{s.get('voices')} шт.")
        add("User-Agent", s.get("user_agent"), mono=True)

        ua_note = QLabel(
            "Версию Firefox в UA движок подставляет свою — иначе UA "
            "расходился бы с реальным движком. Это правильное поведение."
        )
        ua_note.setWordWrap(True)
        ua_note.setStyleSheet(HINT_STYLE)
        self.identity_form.addRow("", ua_note)

        sig = QLabel(f"ID личности: {s.get('signature')}")
        sig.setStyleSheet(HINT_STYLE)
        self.identity_form.addRow(sig)

        used = used_identity_signatures(exclude_profile=self.profile_id)
        total = unique_preset_count(identity.get("os", "windows"))
        self.available_lbl.setText(
            f"Уникальных личностей для этой ОС: {total}, "
            f"занято другими профилями: {len(used)}"
        )

    def _geo_changed(self):
        mode = self.geo_combo.currentData()
        manual = mode == GEO_MANUAL
        self.tz_combo.setEnabled(manual)
        self.locale_combo.setEnabled(manual)

        has_proxy = bool(self.proxy_server.text().strip())
        risk = describe_geo_risk(mode, has_proxy)
        self.geo_warn.setText(risk or "")
        self.geo_warn.setVisible(bool(risk))

    def _on_regenerate(self):
        reply = QMessageBox.question(
            self, "Новая личность",
            "Выдать профилю другой отпечаток устройства?\n\n"
            "Делайте это только при смене прокси: частая ротация "
            "личности на одном IP выглядит как антидетект-браузер.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        identity = self.cfg.get("identity") or {}
        os_name = identity.get("os", "windows")
        preset = regenerate_identity(self.profile_id, os_name)
        if not preset:
            QMessageBox.warning(
                self, "Ошибка",
                "Не удалось получить новую личность: нет доступных пресетов."
            )
            return

        self.cfg = load_profile_config(self.profile_id)
        self._refresh_identity_view()

    # ---------- save ----------
    def _on_save(self):
        fp = self.cfg.setdefault("fingerprint", {})

        fp["geo_mode"] = self.geo_combo.currentData() or GEO_AUTO
        fp["timezone"] = self.tz_combo.currentText().strip()
        fp["locale"] = self.locale_combo.currentText().strip()
        fp["webrtc"] = self.webrtc_combo.currentData() or "block"

        self.cfg["proxy"] = {
            "server": self.proxy_server.text().strip(),
            "username": self.proxy_user.text().strip(),
            "password": self.proxy_pass.text(),
        }

        save_profile_config(self.profile_id, self.cfg)
        self.accept()
