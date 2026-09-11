# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Блок шифрования (оставляем None)
block_cipher = None

# ===== СКРЫТЫЕ ИМПОРТЫ =====
hiddenimports = [
    # Сторонние библиотеки
    'qasync',
    'aiohttp',
    'wmi',
    'pywintypes',
    'win32api',
    'win32com',
    'playwright',
    'playwright.async_api',
    'playwright._impl',
    'uvicorn',
    'fastapi',
    'pydantic',
    'httpx',
    'curl_cffi',
    'asyncio',
    'json',
    'sqlite3',
    'hashlib',
    'uuid',
    'requests',
    'PyQt5.QtWebSockets',
    'PyQt5.QtNetwork',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    # Модули проекта (на случай, если не подхватятся)
    'ui',
    'ui.after_goal',
    'ui.components',
    'ui.pages',
    'core',
    'parsers',
]

# ===== ФАЙЛЫ ДАННЫХ =====
# Все файлы, которые должны лежать рядом с исполняемым файлом
datas = [
]

# ===== АНАЛИЗ =====
a = Analysis(
    ['app.py'],                        # точка входа
    pathex=[],                         # дополнительные пути
    binaries=[],                       # динамические библиотеки
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],                      # кастомные хуки (если есть)
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],                       # что исключить (например, 'matplotlib')
    noarchive=False,
    optimize=0,
)

# ===== СБОРКА PYZ (архив .pyc) =====
pyz = PYZ(a.pure)

# ===== СОЗДАНИЕ EXE =====
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ValueDetector',              # имя выходного файла
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                          # сжатие (установите UPX, если нужно)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                     # без консоли (оконное приложение)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ===== КОЛЛЕКЦИЯ ВСЕХ ФАЙЛОВ (папка dist) =====
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ValueDetector',              # имя папки в dist
)