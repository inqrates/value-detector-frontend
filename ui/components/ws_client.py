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
    match_state_received = pyqtSignal(dict)
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

        # ── Fallback: если с JWT 2 раза подряд сервер отклонил (403)
        #    подключаемся без токена (обратная совместимость)
        self._use_token = True
        self._used_token_last = False
        self._failed_with_token = 0

        # ── heartbeat ──
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

        # ── SSL: доверяем нашему CA (certs/ca.pem) для wss:// ──
        # ВАЖНО: задаём конфиг ЯВНО на сокет. Глобальный
        # defaultConfiguration() не работает, если в процессе уже
        # загружен OpenSSL от curl_cffi (теряется наш CA).
        try:
            from ui.ssl_cert import build_qt_ssl_config
            _ssl_conf = build_qt_ssl_config()
            if _ssl_conf is not None:
                self.socket.setSslConfiguration(_ssl_conf)
        except Exception as e:
            logger.debug(f"WS: SSL CA не применён: {e}")

    def _get_ws_url_with_token(self) -> str:
        """
        Формирует URL с JWT-токеном: ws://host/ws?token=JWT
        Если токен просрочен — продлевает по refresh.
        Если токен не найден — возвращает без токена.
        """
        try:
            from ui.session_store import (
                get_saved_tokens, access_token_valid, refresh_access_token,
            )
            tokens = get_saved_tokens()
            if tokens:
                access_token, refresh_token = tokens
                if not access_token_valid(access_token):
                    # Продлеваем по refresh-токену
                    new = refresh_access_token()
                    if new:
                        access_token = new
                    else:
                        # Refresh не удался — fallback без токена
                        return self.url

                sep = "&" if "?" in self.url else "?"
                return f"{self.url}{sep}token={access_token}"
        except Exception as e:
            logger.debug(f"WS: не удалось получить JWT: {e}")
        return self.url

    # ---------- Публичный API ----------
    def connect(self):
        if self.socket.state() in (
            QAbstractSocket.ConnectedState,
            QAbstractSocket.ConnectingState,
        ):
            return
        self._manual_disconnect = False

        self._used_token_last = self._use_token
        ws_url = self._get_ws_url_with_token()
        logger.info(f"🔌 WS: подключение к {ws_url}")
        self.socket.open(QUrl(ws_url))

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
        # Успех с токеном → сбрасываем fallback
        self._failed_with_token = 0
        self._use_token = True
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
            f"(code={code}, reason={reason!r}, "
            f"err={self.socket.errorString()!r})"
        )
        self.disconnected.emit()

        if self._manual_disconnect:
            return

        # ── Fallback: если 2 раза подряд с токеном не удалось —
        #    переключаемся на подключение без токена
        if self._used_token_last:
            self._failed_with_token += 1
            if self._failed_with_token >= 2:
                self._use_token = False
                self._failed_with_token = 0
                self._used_token_last = False
                logger.warning(
                    "⚠️ WS: 2 неудачные попытки с JWT — "
                    "переключаемся на подключение без токена"
                )

        self._reconnect_timer.start(DISCONNECT_RETRY_DELAY_MS)

    def _on_error(self, error_code):
        try:
            detail = self.socket.errorString()
        except Exception:
            detail = ""
        logger.warning(f"WS error: code={int(error_code)} detail={detail!r}")

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