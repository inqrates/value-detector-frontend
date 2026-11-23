# ui/ssl_cert.py
"""
Доверие к собственному CA-сертификату (self-signed, для HTTPS по IP).

ЗАЧЕМ:
  Let's Encrypt не выдаёт сертификаты на голый IP. Поэтому сервер
  использует собственный CA (certs/ca.pem на сервере, тот же файл
  вшит в клиент). Клиент доверяет ТОЛЬКО этому CA — MITM невозможен.

  ca.pem лежит в certs/ca.pem рядом с проектом / внутри EXE
  (PyInstaller datas → sys._MEIPASS).

Использование:
    from ui.ssl_cert import get_ca_path, build_requests_verify
    requests.post(url, verify=build_requests_verify())
"""
import os
import sys
import ssl
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CA_FILENAME = "ca.pem"

_cached_path: Optional[str] = None
_cached_ssl_context: Optional[ssl.SSLContext] = None


def _candidate_paths() -> list:
    """Возможные места, где может лежать ca.pem."""
    paths = []

    # 1) Внутри PyInstaller-сборки
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(os.path.join(meipass, "certs", _CA_FILENAME))
        paths.append(os.path.join(meipass, _CA_FILENAME))

    # 2) Рядом с EXE
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        paths.append(os.path.join(exe_dir, "certs", _CA_FILENAME))
        paths.append(os.path.join(exe_dir, "_internal", "certs", _CA_FILENAME))

    # 3) В корне проекта (запуск из исходников)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths.append(os.path.join(project_root, "certs", _CA_FILENAME))

    # 4) В %APPDATA% (можно положить вручную)
    try:
        from ui.paths import get_app_data_dir
        paths.append(os.path.join(get_app_data_dir(), "certs", _CA_FILENAME))
        paths.append(os.path.join(get_app_data_dir(), _CA_FILENAME))
    except Exception:
        pass

    return paths


def get_ca_path() -> Optional[str]:
    """
    Возвращает путь к ca.pem, если файл найден. Иначе None.
    """
    global _cached_path
    if _cached_path is not None:
        return _cached_path if _cached_path != "" else None

    for p in _candidate_paths():
        if os.path.isfile(p):
            _cached_path = p
            logger.info(f"SSL: используется CA {p}")
            return p

    _cached_path = ""
    logger.debug("SSL: ca.pem не найден — будет системный набор CA")
    return None


def build_requests_verify():
    """
    Для requests: возвращает путь к CA-файлу или True (системные CA).
    """
    ca = get_ca_path()
    return ca if ca else True


def get_ssl_context() -> Optional[ssl.SSLContext]:
    """
    Для aiohttp/websockets: SSLContext, доверяющий нашему CA.
    """
    global _cached_ssl_context
    if _cached_ssl_context is not None:
        return _cached_ssl_context

    ca = get_ca_path()
    if not ca:
        return None

    try:
        ctx = ssl.create_default_context(cafile=ca)
        # Наш сертификат подписан нашим CA — hostname check пройдёт по SAN=IP
        _cached_ssl_context = ctx
        return ctx
    except Exception as e:
        logger.warning(f"SSL: не удалось создать контекст из {ca}: {e}")
        return None


def build_qt_ssl_config():
    """
    Возвращает QSslConfiguration с НАШИМ CA (только он), либо None.

    ВАЖНО: применяется явно к сокету (ws.setSslConfiguration(conf)),
    а не через defaultConfiguration(). Причина: curl_cffi подгружает
    свой OpenSSL и глобальный дефолтный конфиг Qt теряет наш CA
    (ошибка QSslError::UnableToGetLocalIssuerCertificate).
    """
    ca = get_ca_path()
    if not ca:
        return None

    try:
        from PyQt5.QtNetwork import (
            QSslConfiguration, QSslCertificate, QSslSocket,
        )
        from PyQt5.QtCore import QByteArray

        with open(ca, "rb") as f:
            pem = f.read()

        certs = QSslCertificate.fromData(QByteArray(pem))
        if not certs:
            logger.warning("SSL: Qt не распознал ca.pem")
            return None

        conf = QSslConfiguration.defaultConfiguration()
        conf.setCaCertificates(certs)
        conf.setPeerVerifyMode(QSslSocket.VerifyPeer)
        logger.info(f"SSL: QSslConfiguration с нашим CA ({len(certs)} серт.)")
        return conf
    except Exception as e:
        logger.warning(f"SSL: не удалось собрать QSslConfiguration: {e}")
        return None


def apply_to_qt_websocket() -> bool:
    """
    Добавляет наш CA в глобальную QSslConfiguration.
    Оставлено для совместимости; для QWebSocket предпочтительнее
    build_qt_ssl_config() + socket.setSslConfiguration().
    """
    ca = get_ca_path()
    if not ca:
        return False

    try:
        from PyQt5.QtNetwork import QSslCertificate, QSslConfiguration
        from PyQt5.QtCore import QByteArray

        with open(ca, "rb") as f:
            pem = f.read()

        certs = QSslCertificate.fromData(QByteArray(pem))
        if not certs:
            logger.warning("SSL: Qt не распознал ca.pem")
            return False

        conf = QSslConfiguration.defaultConfiguration()
        existing = conf.caCertificates()
        conf.setCaCertificates(existing + certs)
        QSslConfiguration.setDefaultConfiguration(conf)

        logger.info(f"SSL: CA применён глобально ({len(certs)} серт.)")
        return True
    except Exception as e:
        logger.warning(f"SSL: не удалось применить CA к Qt: {e}")
        return False
