# ui/components/ws_client.py
import json
import logging
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt5.QtWebSockets import QWebSocket
from PyQt5.QtNetwork import QAbstractSocket
from ui.config_loader import load_config

logger = logging.getLogger(__name__)


RECONNECT_MIN_MS = 1000
RECONNECT_MAX_MS = 30000
RECONNECT_FACTOR = 2
DISCONNECT_RETRY_DELAY_MS = 500


class SignalClient(QObject):
    signal_received = pyqtSignal(dict)
    advisor_received = pyqtSignal(dict)
    arbitrage_received = pyqtSignal(dict)
    value_received = pyqtSignal(dict)
    corridor_received = pyqtSignal(dict)
    missed_received = pyqtSignal(dict)
    preopen_received = pyqtSignal(dict)
    match_state_received = pyqtSignal(dict)   # ← НОВОЕ
    connected = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self, url: str = None):
        super().__init__()
        if url is None:
            config = load_config()
            url = config.get('ws_url', 'ws://localhost:8000/ws')
        self.url = url

        self._subscriptions: set = set()
        self._current_backoff = RECONNECT_MIN_MS
        self._manual_disconnect = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

        # ── heartbeat: uvicorn без server-ping, клиент сам пульсирует ──
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(20_000)
        self._heartbeat_timer.timeout.connect(self._send_ping)
        self._heartbeat_timer.start()

        self.socket = QWebSocket()
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_text)
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
        self._manual_disconnect = True
        try:
            self._reconnect_timer.stop()
        except Exception:
            pass
        try:
            self.socket.close()
        except Exception:
            pass

    def subscribe_match(self, key: str):
        if not key:
            return
        self._subscriptions.add(key)
        self._send({"type": "subscribe_match", "key": key})

    def unsubscribe_match(self, key: str):
        if not key:
            return
        self._subscriptions.discard(key)
        self._send({"type": "unsubscribe_match", "key": key})

    def _send(self, obj: dict):
        try:
            self.socket.sendTextMessage(json.dumps(obj, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"WS _send: {e}")

    # ---------- Обработчики QWebSocket ----------
    def _on_connected(self):
        self._current_backoff = RECONNECT_MIN_MS
        logger.info(f"✅ WS: подключено к {self.url}")

        # Подписка на каналы
        self._send({
            "type": "subscribe",
            "channels": [
                "signals", "arbitrage", "value", "corridor",
                "advisor", "health",
            ],
        })
        # Восстанавливаем подписки на матчи после реконнекта
        for key in list(self._subscriptions):
            self._send({"type": "subscribe_match", "key": key})

        self.connected.emit()

    def _on_disconnected(self):
        try:
            code = self.socket.closeCode()
            reason = self.socket.closeReason() or ""
        except Exception:
            code, reason = -1, ""
        logger.warning(
            f"⚠️ WS: отключено от {self.url} "
            f"(code={code}, reason={reason!r})"
        )
        self.disconnected.emit()

        if self._manual_disconnect:
            return

        self._reconnect_timer.start(DISCONNECT_RETRY_DELAY_MS)

    def _on_error(self, error_code):
        logger.debug(f"WS error code: {error_code}")

    def _send_ping(self):
        try:
            if self.socket.state() == QAbstractSocket.ConnectedState:
                self.socket.ping()
        except Exception:
            pass

    def _try_reconnect(self):
        if self._manual_disconnect:
            return

        try:
            state = self.socket.state()
            if state == QAbstractSocket.ConnectedState:
                return
            if state == QAbstractSocket.ConnectingState:
                return
        except Exception:
            pass

        logger.info(f"🔄 WS: попытка переподключения (backoff={self._current_backoff}мс)")
        self.connect()

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
            elif msg_type == "match_state":
                # Формат: {"type": "match_state", "key", "bks", "now"}
                self.match_state_received.emit(data)
            else:
                logger.debug(f"WS: неизвестный тип '{msg_type}'")
        except Exception as e:
            logger.error(f"Ошибка обработки WebSocket: {e}")