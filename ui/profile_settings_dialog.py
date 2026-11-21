"""
Диалог настроек профиля: браузер, прокси, отпечаток.
Контент в скролле, кнопки всегда видны, окно растягивается.
"""
import re
import uuid

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QComboBox, QCheckBox, QPushButton, QLabel,
    QDialogButtonBox, QDoubleSpinBox, QScrollArea, QFrame,
    QApplication, QMessageBox,
)
from PyQt5.QtCore import Qt

from core.profile_config import load_profile_config, save_profile_config
from core.fingerprint import (
    WEBGL_PROFILES, SCREEN_RESOLUTIONS, TIMEZONES,
)

WARN_STYLE = "color: rgba(242,201,76,0.9); font-size: 10px;"
HINT_STYLE = "color: rgba(199,214,223,0.55); font-size: 10px;"


class ProfileSettingsDialog(QDialog):
    def __init__(self, profile_id: str, parent=None):
        super().__init__(parent)
        self.profile_id = profile_id
        self.cfg = load_profile_config(profile_id)
        fp = self.cfg.get("fingerprint", {})
        proxy = self.cfg.get("proxy", {})

        self.setWindowTitle(f"Настройки профиля — {profile_id}")
        self.setMinimumSize(540, 380)
        self.setSizeGripEnabled(True)
        self.setModal(True)

        w, h = 680, 760
        try:
            scr = QApplication.primaryScreen()
            if scr is not None:
                geo = scr.availableGeometry()
                w = min(w, geo.width() - 80)
                h = min(h, geo.height() - 80)
        except Exception:
            pass
        self.resize(max(540, w), max(380, h))

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

        # ══════════ Главное правило ══════════
        banner = QLabel(
            "⚠️ Любое отклонение от «Реальный / auto / system» включает JS-подмену.\n"
            "Она ловится через Function.toString() и через Worker (у Worker свой navigator).\n"
            "Максимальная чистота = всё реальное. Оверрайды — только осознанно."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background: rgba(242,201,76,0.08); border-left: 3px solid #f2c94c;"
            "padding: 8px 10px; border-radius: 4px;" + WARN_STYLE
        )
        root.addWidget(banner)

        # ══════════ Браузер ══════════
        br_group = QGroupBox("Браузер")
        bf = QFormLayout(br_group)
        bf.setSpacing(6)
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("Авто: Chrome → Edge → Chromium", "auto")
        self.channel_combo.addItem("Google Chrome (обновляется сам)", "chrome")
        self.channel_combo.addItem("Microsoft Edge (обновляется сам)", "msedge")
        self.channel_combo.addItem("Встроенный Chromium (версия фиксирована)", "chromium")
        self._set_data(self.channel_combo, self.cfg.get("browser_channel", "auto"))
        bf.addRow("Движок:", self.channel_combo)

        try:
            from core.browser_versions import describe_browsers
            info = describe_browsers()
            ver_label = QLabel(info["text"])
            ver_label.setStyleSheet(
                WARN_STYLE if info["stale"] or not (info["chrome"] or info["edge"])
                else HINT_STYLE
            )
        except Exception as e:
            ver_label = QLabel(f"Версии недоступны: {e}")
            ver_label.setStyleSheet(HINT_STYLE)
        ver_label.setWordWrap(True)
        bf.addRow("Версии:", ver_label)
        root.addWidget(br_group)

        # ══════════ Прокси ══════════
        proxy_group = QGroupBox("Прокси")
        pf = QFormLayout(proxy_group)
        pf.setSpacing(6)
        self.proxy_server = QLineEdit(proxy.get("server", ""))
        self.proxy_server.setPlaceholderText(
            "http://host:port или socks5://host:port (пусто = без прокси)")
        pf.addRow("Сервер:", self.proxy_server)
        self.proxy_user = QLineEdit(proxy.get("username", ""))
        pf.addRow("Логин:", self.proxy_user)
        self.proxy_pass = QLineEdit(proxy.get("password", ""))
        self.proxy_pass.setEchoMode(QLineEdit.Password)
        pf.addRow("Пароль:", self.proxy_pass)
        root.addWidget(proxy_group)

        # ══════════ Отпечаток ══════════
        fp_group = QGroupBox("Отпечаток (личность профиля)")
        ff = QFormLayout(fp_group)
        ff.setSpacing(6)

        seed_row = QHBoxLayout()
        self.seed_edit = QLineEdit(str(fp.get("seed", "")))
        self.seed_edit.setReadOnly(True)
        seed_row.addWidget(self.seed_edit, 1)
        regen_btn = QPushButton("🎲 Новый")
        regen_btn.setToolTip("Новая личность. Смена отпечатка на том же IP "
                             "выглядит как ротация антидетекта (temporal_drift).")
        regen_btn.clicked.connect(self._regen_seed)
        seed_row.addWidget(regen_btn)
        ff.addRow("Seed:", seed_row)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("Windows", "windows")
        self.platform_combo.addItem("macOS", "macos")
        self._set_data(self.platform_combo, fp.get("platform", "windows"))
        ff.addRow("Платформа:", self.platform_combo)

        self.ua_edit = QLineEdit(fp.get("user_agent", "") or "")
        self.ua_edit.setPlaceholderText("Пусто = реальный UA (рекомендуется)")
        self.ua_edit.setToolTip("Ручной UA расходится с sec-ch-ua — антифрод это ловит.")
        ff.addRow("User-Agent:", self.ua_edit)

        # ── Часовой пояс: auto по IP ──
        self.tz_combo = QComboBox()
        self.tz_combo.setEditable(True)
        self.tz_combo.addItems(TIMEZONES)
        self.tz_combo.setCurrentText(str(fp.get("timezone", "system")))
        self.tz_combo.setToolTip(
            "auto — определить по IP (прокси, а без прокси — по своему IP)\n"
            "system — как в операционной системе\n"
            "или выбери конкретную зону"
        )
        ff.addRow("Часовой пояс:", self.tz_combo)
        tz_hint = QLabel(
            "auto = по IP. Без прокси определится по твоему реальному IP. "
            "Таймзона должна совпадать с геолокацией IP — иначе противоречие."
        )
        tz_hint.setWordWrap(True)
        tz_hint.setStyleSheet(HINT_STYLE)
        ff.addRow("", tz_hint)

        self.locale_combo = QComboBox()
        self.locale_combo.setEditable(True)
        self.locale_combo.addItems([
            "system", "auto", "ru-RU", "en-US", "en-GB",
            "uk-UA", "de-DE", "tr-TR",
        ])
        self.locale_combo.setCurrentText(str(fp.get("locale", "system")))
        self.locale_combo.setToolTip("system = системный язык (рекомендуется)")
        ff.addRow("Язык:", self.locale_combo)

        # ── Геолокация ──
        geo_row = QHBoxLayout()
        self.geo_combo = QComboBox()
        self.geo_combo.addItem("Не трогать (default)", "default")
        self.geo_combo.addItem("По IP (auto)", "auto")
        self.geo_combo.addItem("Запретить (deny)", "deny")
        self.geo_combo.addItem("Вручную", "coords")
        geo_row.addWidget(self.geo_combo, 1)
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(4)
        self.lat_spin.setPrefix("lat ")
        geo_row.addWidget(self.lat_spin)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.lon_spin.setPrefix("lon ")
        geo_row.addWidget(self.lon_spin)
        ff.addRow("Геолокация:", geo_row)

        geo_val = fp.get("geolocation", "default")
        if isinstance(geo_val, dict):
            self._set_data(self.geo_combo, "coords")
            self.lat_spin.setValue(float(geo_val.get("lat") or 0))
            self.lon_spin.setValue(float(geo_val.get("lon") or 0))
        else:
            self._set_data(self.geo_combo, geo_val)
        self.geo_combo.currentIndexChanged.connect(self._geo_changed)
        self._geo_changed()

        # ── Экран: список частых разрешений ──
        self.screen_combo = QComboBox()
        self.screen_combo.setEditable(True)
        self.screen_combo.addItem("auto — реальный экран (рекомендуется)", None)
        for w_, h_, label in SCREEN_RESOLUTIONS:
            self.screen_combo.addItem(label, (w_, h_))
        scr_cfg = fp.get("screen", "auto")
        if isinstance(scr_cfg, dict) and scr_cfg.get("width"):
            target = f"{scr_cfg['width']}x{scr_cfg['height']}"
            found = False
            for i in range(self.screen_combo.count()):
                d = self.screen_combo.itemData(i)
                if d and f"{d[0]}x{d[1]}" == target:
                    self.screen_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                self.screen_combo.setCurrentText(target)
        else:
            self.screen_combo.setCurrentIndex(0)
        self.screen_combo.setToolTip(
            "auto = реальный экран и окно.\n"
            "Ручной размер задаётся нативно через CDP (без JS-подмены)."
        )
        ff.addRow("Экран:", self.screen_combo)

        # ── WebGL: расширенный список ──
        self.webgl_combo = QComboBox()
        self.webgl_combo.addItem("Реальный GPU (рекомендуется)", None)
        for p in WEBGL_PROFILES:
            r = p["renderer"]
            short = r.split(",")[1].strip() if "," in r else r
            short = short.replace(" Direct3D11 vs_5_0 ps_5_0", "")
            self.webgl_combo.addItem(short, p)
        wgl = fp.get("webgl", "auto")
        if isinstance(wgl, dict):
            for i in range(self.webgl_combo.count()):
                d = self.webgl_combo.itemData(i)
                if isinstance(d, dict) and d.get("renderer") == wgl.get("renderer"):
                    self.webgl_combo.setCurrentIndex(i)
                    break
        ff.addRow("WebGL:", self.webgl_combo)
        wgl_hint = QLabel(
            "⚠️ Ручной GPU = JS-подмена getParameter + противоречие с WebGPU "
            "(WebGPU подменить нельзя, он покажет твою реальную карту)."
        )
        wgl_hint.setWordWrap(True)
        wgl_hint.setStyleSheet(WARN_STYLE)
        ff.addRow("", wgl_hint)
        root.addWidget(fp_group)

        # ══════════ Шум ══════════
        noise_group = QGroupBox("Шум (JS-подмена — детектируется антифродом)")
        nf = QFormLayout(noise_group)
        nf.setSpacing(6)
        self.canvas_combo = self._noise_combo(fp.get("canvas", "real"))
        nf.addRow("Canvas:", self.canvas_combo)
        self.audio_combo = self._noise_combo(fp.get("audio", "real"))
        nf.addRow("AudioContext:", self.audio_combo)
        self.rects_combo = self._noise_combo(fp.get("client_rects", "real"))
        nf.addRow("ClientRects:", self.rects_combo)
        self.voices_combo = self._noise_combo(fp.get("speech_voices", "real"))
        nf.addRow("SpeechVoices:", self.voices_combo)
        root.addWidget(noise_group)

        # ══════════ Железо и сеть ══════════
        hw_group = QGroupBox("Железо и сеть")
        hf = QFormLayout(hw_group)
        hf.setSpacing(6)

        self.cpu_combo = QComboBox()
        self.cpu_combo.addItem("Реальное (рекомендуется)", "auto")
        for c in (2, 4, 6, 8, 10, 12, 16, 20, 24, 32):
            self.cpu_combo.addItem(f"{c} ядер", c)
        self._set_data(self.cpu_combo, fp.get("cpu_cores", "auto"))
        hf.addRow("CPU:", self.cpu_combo)

        self.ram_combo = QComboBox()
        self.ram_combo.addItem("Реальная (рекомендуется)", "auto")
        for r in (2, 4, 8, 16, 32, 64):
            self.ram_combo.addItem(f"{r} GB", r)
        self._set_data(self.ram_combo, fp.get("ram_gb", "auto"))
        hf.addRow("RAM:", self.ram_combo)

        hw_warn = QLabel(
            "🚨 CPU и RAM нельзя подменить чисто: Web Worker имеет свой navigator "
            "и покажет РЕАЛЬНЫЕ значения. Чекер увидит main≠worker и пометит браузер "
            "как антидетект. Оставляй «Реальное»."
        )
        hw_warn.setWordWrap(True)
        hw_warn.setStyleSheet(
            "background: rgba(235,87,87,0.08); border-left: 3px solid #eb5757;"
            "padding: 6px 8px; border-radius: 4px; color: rgba(235,87,87,0.95);"
            "font-size: 10px;"
        )
        hf.addRow("", hw_warn)

        self.webrtc_combo = QComboBox()
        self.webrtc_combo.addItem("Только через прокси", "proxy_only")
        self.webrtc_combo.addItem("Реальный", "real")
        self._set_data(self.webrtc_combo, fp.get("webrtc", "proxy_only"))
        self.webrtc_combo.setToolTip(
            "Без прокси политика не применяется — иначе 0 ICE-кандидатов, "
            "что антифрод считает признаком антидетекта.")
        hf.addRow("WebRTC:", self.webrtc_combo)

        self.dnt_combo = QComboBox()
        self.dnt_combo.addItem("По умолчанию", "default")
        self.dnt_combo.addItem("Включён (1)", "1")
        self.dnt_combo.addItem("Выключен (0)", "0")
        self._set_data(self.dnt_combo, fp.get("dnt", "default"))
        hf.addRow("Do Not Track:", self.dnt_combo)

        self.hw_accel_cb = QCheckBox("Аппаратное ускорение (GPU)")
        self.hw_accel_cb.setChecked(bool(fp.get("hardware_accel", True)))
        hf.addRow("", self.hw_accel_cb)
        root.addWidget(hw_group)

        note = QLabel(
            "MAC-адрес и имя устройства сайты не читают — подмена не нужна.\n"
            "TLS-отпечаток у браузера как у настоящего Chrome — не трогаем."
        )
        note.setWordWrap(True)
        note.setStyleSheet(HINT_STYLE)
        root.addWidget(note)

        root.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ══════════ Нижняя панель ══════════
        bottom = QFrame()
        bottom.setObjectName("fpBottomBar")
        bottom.setStyleSheet(
            "QFrame#fpBottomBar { border-top: 1px solid rgba(255,255,255,0.10); }")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(10)

        reset_btn = QPushButton("↺ Всё реальное")
        reset_btn.setToolTip("Сбросить все оверрайды в «Реальный/auto/system» "
                             "— максимальная чистота отпечатка")
        reset_btn.clicked.connect(self._reset_to_real)
        bl.addWidget(reset_btn)

        hint = QLabel("Применится при следующем запуске профиля")
        hint.setStyleSheet(HINT_STYLE + "border: none;")
        bl.addWidget(hint, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("💾 Сохранить")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        bl.addWidget(btns)
        outer.addWidget(bottom, 0)

    # ---------- helpers ----------
    @staticmethod
    def _set_data(combo: QComboBox, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    @staticmethod
    def _noise_combo(current: str) -> QComboBox:
        c = QComboBox()
        c.addItem("Реальный (рекомендуется)", "real")
        c.addItem("Шум (noise)", "noise")
        for i in range(c.count()):
            if c.itemData(i) == current:
                c.setCurrentIndex(i)
        return c

    def _geo_changed(self):
        on = self.geo_combo.currentData() == "coords"
        self.lat_spin.setEnabled(on)
        self.lon_spin.setEnabled(on)

    def _regen_seed(self):
        self.seed_edit.setText(str(uuid.uuid4().int % 90000 + 10000))

    def _reset_to_real(self):
        self.ua_edit.setText("")
        self.tz_combo.setCurrentText("system")
        self.locale_combo.setCurrentText("system")
        self._set_data(self.geo_combo, "default")
        self._geo_changed()
        self.screen_combo.setCurrentIndex(0)
        self.webgl_combo.setCurrentIndex(0)
        for c in (self.canvas_combo, self.audio_combo,
                  self.rects_combo, self.voices_combo):
            c.setCurrentIndex(0)
        self.cpu_combo.setCurrentIndex(0)
        self.ram_combo.setCurrentIndex(0)
        self._set_data(self.dnt_combo, "default")
        self.hw_accel_cb.setChecked(True)
        QMessageBox.information(
            self, "Сброшено",
            "Все оверрайды сброшены в «Реальный/auto».\n"
            "Нажми «Сохранить», затем закрой и открой профиль заново."
        )

    def _resolve_screen(self):
        txt = self.screen_combo.currentText().strip()
        idx = self.screen_combo.findText(txt)
        if idx >= 0:
            data = self.screen_combo.itemData(idx)
            if data:
                return {"width": int(data[0]), "height": int(data[1])}
            return "auto"
        m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", txt, re.IGNORECASE)
        if m:
            return {"width": int(m.group(1)), "height": int(m.group(2))}
        return "auto"

    # ---------- save ----------
    def _on_save(self):
        fp = self.cfg.setdefault("fingerprint", {})
        try:
            fp["seed"] = int(self.seed_edit.text())
        except ValueError:
            fp["seed"] = uuid.uuid4().int % 90000 + 10000
        fp["platform"] = self.platform_combo.currentData()
        fp["user_agent"] = self.ua_edit.text().strip()
        fp["timezone"] = self.tz_combo.currentText().strip() or "system"
        fp["locale"] = self.locale_combo.currentText().strip() or "system"

        geo_mode = self.geo_combo.currentData()
        if geo_mode == "coords":
            fp["geolocation"] = {"lat": self.lat_spin.value(),
                                 "lon": self.lon_spin.value()}
        else:
            fp["geolocation"] = geo_mode

        fp["screen"] = self._resolve_screen()
        fp["webgl"] = self.webgl_combo.currentData() or "auto"
        fp["canvas"] = self.canvas_combo.currentData()
        fp["audio"] = self.audio_combo.currentData()
        fp["client_rects"] = self.rects_combo.currentData()
        fp["speech_voices"] = self.voices_combo.currentData()
        fp["cpu_cores"] = self.cpu_combo.currentData()
        fp["ram_gb"] = self.ram_combo.currentData()
        fp["webrtc"] = self.webrtc_combo.currentData()
        fp["dnt"] = self.dnt_combo.currentData()
        fp["hardware_accel"] = self.hw_accel_cb.isChecked()

        self.cfg["browser_channel"] = self.channel_combo.currentData() or "auto"
        self.cfg["proxy"] = {
            "server": self.proxy_server.text().strip(),
            "username": self.proxy_user.text().strip(),
            "password": self.proxy_pass.text(),
        }

        save_profile_config(self.profile_id, self.cfg)
        self.accept()