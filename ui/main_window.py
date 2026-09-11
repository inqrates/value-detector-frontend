# ui/main_window.py
import os
import json
import asyncio
from datetime import date

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QStackedWidget, QSizeGrip
)
from PyQt5.QtCore import Qt

from ui.styles import APP_STYLE
from ui.components import Sidebar, TitleBar, SignalClient
from ui.pages import (
    DashboardPage, LogsPage, StrategiesPage,
    AccountsPage, SportsPage, SettingsPage
)
from ui.strategy_store import StrategyStore
from ui.after_goal_engine import AfterGoalEngine
from ui.log_bus import log_bus
from ui.paths import get_app_data_dir


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Value Bet Detector Pro")
        self.setGeometry(100, 100, 1560, 940)
        self.setMinimumSize(1280, 800)
        self.setWindowFlags(Qt.FramelessWindowHint)

        # ---- Статистика за сегодня ----
        self._signals_today = 0
        self._signal_match_ids = set()
        self._load_today_stats()

        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.nav_changed.connect(self._switch_page)
        body_layout.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(84)
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(28, 0, 28, 0)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.page_title = QLabel("Дашборд")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel("Обзор работающих контор и активных стратегий")
        self.page_subtitle.setObjectName("pageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        top_layout.addLayout(titles)
        top_layout.addStretch()

        live_frame = QFrame()
        live_frame.setFixedSize(118, 35)
        live_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.036);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
            }
        """)
        live_layout = QHBoxLayout(live_frame)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(7)
        live_layout.setAlignment(Qt.AlignCenter)

        live_dot = QLabel()
        live_dot.setFixedSize(8, 8)
        live_dot.setStyleSheet("background: #42d78d; border-radius: 4px;")
        live_text = QLabel("LIVE")
        live_text.setStyleSheet("color: rgba(245,249,252,0.94); font-weight: 700; font-size: 12px;")

        live_layout.addWidget(live_dot)
        live_layout.addWidget(live_text)
        top_layout.addWidget(live_frame, 0, Qt.AlignVCenter)

        right.addWidget(topbar)

        content_wrap = QWidget()
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(28, 22, 28, 22)

        self.stack = QStackedWidget()
        self.page_titles = [
            ("Дашборд", "Обзор работающих контор и активных стратегий"),
            ("Логи", "Журнал событий системы"),
            ("Стратегии", "Настройка и управление торговыми стратегиями"),
            ("Аккаунты", "Аккаунты букмекеров и профили AdsPower"),
            ("Виды спорта", "Парсинг по дисциплинам"),
            ("Настройки", "Общие настройки приложения"),
        ]
        self.strategy_store = StrategyStore()
        self.pages = [
            DashboardPage(self.strategy_store),
            LogsPage(),
            StrategiesPage(self.strategy_store),
            AccountsPage(),
            SportsPage(),
            SettingsPage(),
        ]
        for p in self.pages:
            self.stack.addWidget(p)

        self.pages[3].accounts_changed.connect(self._refresh_dashboard)
        self.pages[2].strategies_changed.connect(self._refresh_dashboard)
        self.pages[3].accounts_changed.connect(self._refresh_accounts_in_strategies)

        content_layout.addWidget(self.stack)
        right.addWidget(content_wrap)

        body_layout.addLayout(right)
        root.addWidget(body, 1)

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(18, 18)
        self._grip.setStyleSheet("QSizeGrip { background: transparent; }")
        self._position_grip()

        self.setStyleSheet(APP_STYLE)

        self.status_label = QLabel("🟢 Система работает")
        self.status_label.setStyleSheet("color: rgba(199,214,223,0.62); padding: 4px 12px;")
        self.statusBar().addPermanentWidget(self.status_label)

        self.after_goal_engine = AfterGoalEngine()

        # ---- Логи: подписываемся на шину ----
        log_bus.entry.connect(self._on_log_entry)

        # ---- WebSocket ----
        self.client = SignalClient()
        self.client.signal_received.connect(self._on_signal)
        self.client.advisor_received.connect(self._on_advisor)
        self.client.arbitrage_received.connect(self._on_arbitrage)
        self.client.value_received.connect(self._on_value)
        self.client.corridor_received.connect(self._on_corridor)
        self.client.connected.connect(lambda: self.sidebar.set_connection(True))
        self.client.disconnected.connect(lambda: self.sidebar.set_connection(False))
        self.client.connect()
        self.client.missed_received.connect(self._on_missed)
        self.client.preopen_received.connect(self._on_preopen)

        # ---- Стартовые сообщения ----
        log_bus.info("Система", "Приложение запущено")
        if self._signals_today > 0:
            self.pages[0].update_signal_count(self._signals_today)

    # ---------- Статистика "сегодня" ----------
    def _stats_path(self):
        return os.path.join(get_app_data_dir(), "stats_today.json")

    def _load_today_stats(self):
        path = self._stats_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == date.today().isoformat():
                self._signals_today = int(data.get("signals", 0))
                self._signal_match_ids = set(data.get("match_ids", []))
        except Exception:
            pass

    def _save_today_stats(self):
        path = self._stats_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "date": date.today().isoformat(),
                    "signals": self._signals_today,
                    "match_ids": list(self._signal_match_ids),
                }, f)
        except Exception:
            pass

    # ---------- Закрытие ----------
    def closeEvent(self, event):
        log_bus.info("Система", "Приложение закрывается")
        self._save_today_stats()
        try:
            self.client.disconnect()
        except Exception:
            pass
        try:
            for match_id in list(self.after_goal_engine._monitoring_tasks.keys()):
                asyncio.create_task(self.after_goal_engine.stop_monitoring(match_id))
        except Exception:
            pass
        super().closeEvent(event)

    # ---------- Логи ----------
    def _on_log_entry(self, entry: dict):
        self.pages[1].add_log(entry)

    # ---------- Обновления страниц ----------
    def _refresh_dashboard(self):
        self.pages[0].load_accounts()
        self.pages[0]._fill_strategy_table()

    def _refresh_accounts_in_strategies(self):
        if hasattr(self.pages[2], 'refresh_accounts'):
            self.pages[2].refresh_accounts()

    def resizeEvent(self, event):
        if hasattr(self, '_grip'):
            self._position_grip()
        super().resizeEvent(event)

    def _position_grip(self):
        self._grip.move(
            self.width() - self._grip.width(),
            self.height() - self._grip.height()
        )

    def _switch_page(self, idx):
        self.stack.setCurrentIndex(idx)
        title, subtitle = self.page_titles[idx]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)

    # ---------- Обработчики WebSocket ----------
    def _on_signal(self, payload):
        # 1) Всегда пытаемся преоткрыть матч, если стратегия подходит
        if self.strategy_store.is_signal_relevant(payload):
            strategy = self.strategy_store.get_matching_strategy(payload)
            if strategy:
                profile_id = strategy.get('profile_id')
                headless = strategy.get('headless', False)
                if profile_id:
                    asyncio.create_task(
                        self.after_goal_engine.preopen_match_with_profile(payload, profile_id, headless)
                    )
                else:
                    log_bus.warning(
                        "Стратегия",
                        f"'{strategy.get('name')}' — не указан profile_id для {payload.get('slow_bk')}"
                    )

        # 2) Логируем и считаем только НОВЫЕ сигналы
        if not payload.get("is_new", False):
            return

        self._log_signal(payload)

        match_id = payload.get('match_id')
        if match_id and match_id not in self._signal_match_ids:
            self._signal_match_ids.add(match_id)
            self._signals_today += 1
            self.pages[0].update_signal_count(self._signals_today)
            self._save_today_stats()

    def _log_signal(self, payload: dict):
        teams = payload.get('match_teams', ['', ''])
        fast_bk = payload.get('fast_bk', '?')
        slow_bk = payload.get('slow_bk', '?')
        log_bus.signal(
            source="Сигнал",
            message=f"{teams[0]} vs {teams[1]} · {fast_bk} → {slow_bk}",
            details={
                "teams": teams,
                "fast_bk": fast_bk,
                "slow_bk": slow_bk,
                # Быстрая БК
                "fast_score": payload.get('fast_score', payload.get('score', [0, 0])),
                "fast_sub_score": payload.get('fast_sub_score', payload.get('sub_score', [0, 0])),
                "fast_odds": payload.get('fast_odds', [0, 0]),
                # Медленная БК
                "slow_score": payload.get('slow_score', [0, 0]),
                "slow_sub_score": payload.get('slow_sub_score', [0, 0]),
                "slow_odds": payload.get('slow_odds', [0, 0]),
                # Прочее
                "delay": payload.get('delay', 0),
            },
        )

        # Троттлинг: логируем не чаще, чем раз в N секунд на один match_id
    _value_logged: dict = {}
    _arbitrage_logged: dict = {}
    _corridor_logged: dict = {}
    _LOG_THROTTLE_SEC = 30.0

    def _should_log(self, cache: dict, key, now: float) -> bool:
        last = cache.get(key, 0.0)
        if now - last < self._LOG_THROTTLE_SEC:
            return False
        cache[key] = now
        # Не даём кэшу расти бесконечно
        if len(cache) > 500:
            for k in list(cache.keys())[:250]:
                cache.pop(k, None)
        return True

    def _on_value(self, payload):
        import time
        key = (payload.get('match_id'), payload.get('bk'), payload.get('outcome'))
        if self._should_log(self._value_logged, key, time.time()):
            log_bus.info(
                "Валуй",
                f"{payload.get('player1')} vs {payload.get('player2')} · "
                f"{payload.get('bk')} · {payload.get('outcome')} @ {payload.get('odd')}"
            )
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_value_bet(payload, strategies))

    def _on_arbitrage(self, payload):
        import time
        key = (payload.get('match_id_p1'), payload.get('bk_p1'), payload.get('bk_p2'))
        if self._should_log(self._arbitrage_logged, key, time.time()):
            log_bus.info(
                "Вилка",
                f"{payload.get('player1')} vs {payload.get('player2')} · "
                f"{payload.get('bk_p1')} / {payload.get('bk_p2')} · "
                f"+{payload.get('profit_percent', 0):.2f}%"
            )
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_arbitrage_bet(payload, strategies))

    def _on_corridor(self, payload):
        import time
        key = (payload.get('match_id1'), payload.get('bk1'), payload.get('bk2'))
        if self._should_log(self._corridor_logged, key, time.time()):
            log_bus.info(
                "Коридор",
                f"{payload.get('player1')} vs {payload.get('player2')} · "
                f"{payload.get('bk1')} / {payload.get('bk2')}"
            )
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_corridor_bet(payload, strategies))

    def _on_advisor(self, payload):
        # Обновление дашборда статистикой активных БК
        self.pages[0].update_advisor_stats(payload)

    def _on_missed(self, payload):
        log_bus.info(
            "Рекомендация",
            f"{payload.get('player1')} vs {payload.get('player2')} · "
            f"{payload.get('message', '')}"
        )

    def _on_preopen(self, payload):
        strategy = self.strategy_store.get_matching_strategy(payload)
        if not strategy:
            return
        profile_id = strategy.get('profile_id')
        if not profile_id:
            return
        headless = strategy.get('headless', False)
        asyncio.create_task(
            self.after_goal_engine.preopen_match_with_profile(payload, profile_id, headless)
        )