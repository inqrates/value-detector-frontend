# ui/config_loader.py
import json
import os
import logging
from .paths import get_app_data_dir

logger = logging.getLogger(__name__)

# ── ЗАШИТЫЕ ДЕФАЛТЫ (hardcoded) ──
# При необходимости смены бэкенда — правим здесь и в APPDATA.
# Эти значения используются как fallback, если файл повреждён.
_EMBEDDED_BACKEND = "https://respectfully-smiling-hake.cloudpub.ru"
_EMBEDDED_WS = "wss://respectfully-smiling-hake.cloudpub.ru/ws"
_LOCALHOST_BACKEND = "http://localhost:8000"
_LOCALHOST_WS = "ws://localhost:8000/ws"


def load_config():
    """
    Загружает конфигурацию из %APPDATA%\\ValueDetectorPro\\config.json.
    Если файла нет – создаёт с HTTPS-дефолтами.
    
    БЕЗОПАСНОСТЬ: если в файле обнаружен HTTP-адрес не localhost,
    автоматически мигрирует на HTTPS (защита от MITM).
    """
    data_dir = get_app_data_dir()
    config_path = os.path.join(data_dir, 'config.json')
    
    # Если файла нет – создаём с HTTPS
    if not os.path.exists(config_path):
        default_config = {
            "backend_url": _EMBEDDED_BACKEND,
            "ws_url": _EMBEDDED_WS,
        }
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Не удалось создать config.json: {e}")
            return {
                "backend_url": _LOCALHOST_BACKEND,
                "ws_url": _LOCALHOST_WS,
            }
        logger.info("Создан config.json с HTTPS-адресом")
        return default_config

    # Если файл есть – читаем
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Ошибка чтения config.json: {e}")
        return {
            "backend_url": _EMBEDDED_BACKEND,
            "ws_url": _EMBEDDED_WS,
        }

    # ── БЕЗОПАСНОСТЬ: авто-миграция HTTP → HTTPS ──
    # Только для продакшен-домена cloudpub.ru. Для dev-адресов
    # (localhost, IP:порт) оставляем как есть, но предупреждаем.
    backend = data.get('backend_url', '')
    ws = data.get('ws_url', '')
    migrated = False

    if 'cloudpub.ru' in backend and backend.startswith('http://'):
        data['backend_url'] = backend.replace('http://', 'https://', 1)
        migrated = True
        logger.warning("config.json: backend_url мигрирован на HTTPS")

    if 'cloudpub.ru' in ws and ws.startswith('ws://'):
        data['ws_url'] = ws.replace('ws://', 'wss://', 1)
        migrated = True
        logger.warning("config.json: ws_url мигрирован на WSS")

    # Предупреждение для небезопасных адресов (не трогаем dev)
    if backend.startswith('http://') and 'localhost' not in backend:
        logger.warning(
            "config.json: backend работает по HTTP — трафик не шифруется!"
        )

    if migrated:
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("config.json: сохранена миграция на HTTPS")
        except Exception as e:
            logger.warning(f"Не удалось сохранить migrated config: {e}")
    
    return data
