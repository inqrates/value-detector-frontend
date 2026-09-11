# ui/config_loader.py
import json
import os
import sys
from .paths import get_app_data_dir

def load_config():
    """
    Загружает конфигурацию из %APPDATA%\ValueDetectorPro\config.json.
    Если файла нет – создаёт его с дефолтными настройками и возвращает их.
    """
    data_dir = get_app_data_dir()
    config_path = os.path.join(data_dir, 'config.json')
    
    # Если файла нет – создаём
    if not os.path.exists(config_path):
        default_config = {
        "backend_url": "https://respectfully-smiling-hake.cloudpub.ru",
        "ws_url": "wss://respectfully-smiling-hake.cloudpub.ru/ws"
        }
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Не удалось создать config.json: {e}")
            # Если не удалось создать – возвращаем дефолт (локальный)
            return {
                "backend_url": "http://localhost:8000",
                "ws_url": "ws://localhost:8000/ws"
            }
        return default_config

    # Если файл есть – читаем
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения config.json: {e}")
        # Возвращаем дефолт
        return {
            "backend_url": "http://localhost:8000",
            "ws_url": "ws://localhost:8000/ws"
        }