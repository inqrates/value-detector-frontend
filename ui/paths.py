# ui/paths.py
import os
import sys

def get_app_data_dir():
    """Возвращает папку для хранения данных пользователя (%APPDATA%\ValueDetectorPro\)."""
    appdata = os.getenv('APPDATA') or os.path.expanduser('~/AppData/Roaming')
    data_dir = os.path.join(appdata, 'ValueDetectorPro')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_config():
    """Читает встроенный config.json из exe (через sys._MEIPASS)."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_path, 'config.json')
    import json
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)