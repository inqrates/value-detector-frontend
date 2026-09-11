# ui/components/ws_client.py
import json
import logging
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt5.QtWebSockets import QWebSocket
from PyQt5.QtNetwork import QAbstractSocket
from ui.config_loader import load_config

logger = logging.getLogger(__name__)


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

        self.socket = QWebSocket()
        self.socket.connected.connect(self.connected.emit)
        self.socket.disconnected.connect(self.disconnected.emit)
        self.socket.textMessageReceived.connect(self._on_text)

        self._reconnect_timer = QTimer()
        self._reconnect_timer.timeout.connect(self.connect)
        self._reconnect_timer.start(5000)

    def connect(self):
        if self.socket.state() != QAbstractSocket.ConnectedState:
            self.socket.open(QUrl(self.url))

    def disconnect(self):
        """Останавливает реконнект и закрывает сокет."""
        try:
            self._reconnect_timer.stop()
        except Exception:
            pass
        try:
            self.socket.close()
        except Exception:
            pass

    def _on_text(self, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            payload = data.get("payload", {})
            # print убран — слишком шумно. Раскомментируй при отладке:
            # logger.debug(f"WS ← {msg_type}")
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
        except Exception as e:
            logger.error(f"Ошибка обработки WebSocket: {e}")