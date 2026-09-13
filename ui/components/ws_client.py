# ui/components/ws_client.py
import json
import logging
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt5.QtWebSockets import QWebSocket
from PyQt5.QtNetwork import QAbstractSocket
from ui.config_loader import load_config

logger = logging.getLogger(__name__)


# Экспоненциальный backoff: старт 1с, x2 каждый раз, потолок 30с
RECONNECT_MIN_MS = 1000
RECONNECT_MAX_MS = 30000
RECONNECT_FACTOR = 2
# Задержка после disconnected перед первой попыткой переподключения
DISCONNECT_RETRY_DELAY_MS = 500


class SignalClient(QObject):
    signal_received = pyqtSignal(dict)
    advisor_received = pyqtSignal(dict)
    arbitrage_received = pyqtSignal(dict)
    value_received = pyqtSignal(dict)
    corridor_received = pyqtSignal(dict)
    missed_received = pyqtSignal(dict)
    preopen_received = pyqtSignal(dict)
    connected = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self, url: str = None):
        super().__init__()
        if url is None:
            config = load_config()
            url = config.get('ws_url', 'ws://localhost:8000/ws')
        self.url = url

        self._current_backoff = RECONNECT_MIN_MS
        self._manual_disconnect = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

        self.socket = QWebSocket()
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_text)
        # Опционально: ловим ошибки сокета для отладки
        try:
            self.socket.error.connect(self._on_error)
        except Exception:
            pass

    # ---------- Публичный API ----------
    def connect(self):
        if self.socket.state() in (
            QAbstractSocket.ConnectedState,
            QAbstractSocket.ConnectingState,
        ):
            return
        self._manual_disconnect = False
        logger.info(f"🔌 WS: подключение к {self.url}")
        self.socket.open(QUrl(self.url))

    def disconnect(self):
        """Останавливает реконнект и закрывает сокет."""
        self._manual_disconnect = True
        try:
            self._reconnect_timer.stop()
        except Exception:
            pass
        try:
            self.socket.close()
        except Exception:
            pass

    # ---------- Обработчики QWebSocket ----------
    def _on_connected(self):
        # Успешное подключение — сбрасываем backoff
        self._current_backoff = RECONNECT_MIN_MS
        logger.info(f"✅ WS: подключено к {self.url}")
        self.connected.emit()

    def _on_disconnected(self):
        logger.warning(f"⚠️ WS: отключено от {self.url}")
        self.disconnected.emit()

        if self._manual_disconnect:
            return

        # Планируем переподключение с небольшой задержкой,
        # чтобы сокет успел корректно закрыть соединение
        self._reconnect_timer.start(DISCONNECT_RETRY_DELAY_MS)

    def _on_error(self, error_code):
        logger.debug(f"WS error code: {error_code}")

    def _try_reconnect(self):
        """Переподключаемся, если сокет не в ConnectedState/ConnectingState."""
        if self._manual_disconnect:
            return

        try:
            state = self.socket.state()
            if state == QAbstractSocket.ConnectedState:
                # Всё ещё подключены — реконнект не нужен
                return
            if state == QAbstractSocket.ConnectingState:
                # Уже в процессе подключения — не толкаемся
                return
        except Exception:
            pass

        logger.info(f"🔄 WS: попытка переподключения (backoff={self._current_backoff}мс)")
        self.connect()

        # Планируем следующую попытку, если не подключимся за это время
        # (если подключимся — _on_connected сбросит backoff)
        if not self._manual_disconnect:
            next_delay = min(self._current_backoff * RECONNECT_FACTOR, RECONNECT_MAX_MS)
            self._current_backoff = next_delay
            self._reconnect_timer.start(next_delay)

    # ---------- Обработка сообщений ----------
    def _on_text(self, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            payload = data.get("payload", {})

            if msg_type == "signal":
                self.signal_received.emit(payload)
            elif msg_type == "advisor":
                self.advisor_received.emit(payload)
            elif msg_type == "arbitrage":
                self.arbitrage_received.emit(payload)
            elif msg_type == "value":
                self.value_received.emit(payload)
            elif msg_type == "corridor":
                self.corridor_received.emit(payload)
            elif msg_type == "missed_opportunity":
                self.missed_received.emit(payload)
            elif msg_type == "preopen":
                self.preopen_received.emit(payload)
            else:
                # Неизвестный тип — логируем на DEBUG, чтобы не шуметь
                logger.debug(f"WS: неизвестный тип сообщения '{msg_type}'")
        except Exception as e:
            logger.error(f"Ошибка обработки WebSocket: {e}")