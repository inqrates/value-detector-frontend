# ui/main_window.py
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QStackedWidget, QSizeGrip
)
from PyQt5.QtCore import Qt

from ui.styles import APP_STYLE
from ui.components import Sidebar, TitleBar, SignalClient
from ui.pages import (
    DashboardPage, AdvisorPage, StrategiesPage,
    AccountsPage, SportsPage, SettingsPage
)
from ui.strategy_store import StrategyStore
from ui.after_goal_engine import AfterGoalEngine
import asyncio


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Value Bet Detector Pro")
        self.setGeometry(100, 100, 1560, 940)
        self.setMinimumSize(1280, 800)
        self.setWindowFlags(Qt.FramelessWindowHint)

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
            ("Советник", "Лента событий и рекомендации"),
            ("Стратегии", "Настройка и управление торговыми стратегиями"),
            ("Аккаунты", "Аккаунты букмекеров и профили AdsPower"),
            ("Виды спорта", "Парсинг по дисциплинам"),
            ("Настройки", "Общие настройки приложения"),
        ]
        self.strategy_store = StrategyStore()
        self.pages = [
            DashboardPage(self.strategy_store),
            AdvisorPage(),
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

        # ---- Движок послегола ----
        self.after_goal_engine = AfterGoalEngine()

        # WebSocket
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

    # ---------- ИСПРАВЛЕНО: корректное завершение ----------
    def closeEvent(self, event):
        """Корректное завершение при закрытии окна."""
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
        # Обработку preopen делаем всегда (даже если signal не is_new) —
        # cooldown защищает от дублей
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
                    print("⚠️ В стратегии не указан profile_id, пропускаем")

        if not payload.get("is_new", False):
            return

        self.pages[1].add_event("signal", payload)
        signal_count = sum(1 for e in self.pages[1].events if e.get("type") == "Задержка")
        self.pages[0].update_signal_count(signal_count)

    def _on_value(self, payload):
        self.pages[1].add_event("value", payload)
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_value_bet(payload, strategies))

    def _on_arbitrage(self, payload):
        self.pages[1].add_event("arbitrage", payload)
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_arbitrage_bet(payload, strategies))

    def _on_corridor(self, payload):
        self.pages[1].add_event("corridor", payload)
        strategies = self.strategy_store.enabled_list()
        asyncio.create_task(self.after_goal_engine.place_corridor_bet(payload, strategies))

    def _on_advisor(self, payload):
        self.pages[0].update_advisor_stats(payload)

    def _on_missed(self, payload):
        self.pages[1].add_event("missed_opportunity", payload)

    def _on_preopen(self, payload):
        strategy = self.strategy_store.get_matching_strategy(payload)
        if not strategy:
            print(f"⚠️ preopen: нет подходящей стратегии для {payload.get('slow_bk')}")
            return

        profile_id = strategy.get('profile_id')
        headless = strategy.get('headless', False)

        if not profile_id:
            print(f"⚠️ preopen: в стратегии '{strategy.get('name')}' не указан profile_id")
            return

        asyncio.create_task(
            self.after_goal_engine.preopen_match_with_profile(
                payload, profile_id, headless
            )
        )