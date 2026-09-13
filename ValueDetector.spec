# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files


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
    'uvicorn',
    'fastapi',
    'pydantic',
    'httpx',
    'curl_cffi',
    'requests',

    # Qt
    'PyQt5.QtWebSockets',
    'PyQt5.QtNetwork',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
]

# Playwright — самая капризная часть, нужны все подмодули и данные драйвера
hiddenimports += collect_submodules('playwright')
hiddenimports += collect_submodules('pyee')
hiddenimports += collect_submodules('greenlet')

# Все модули проекта — все подмодули каждого пакета
for pkg in ('ui', 'ui.after_goal', 'ui.components', 'ui.pages', 'core', 'parsers'):
    hiddenimports += collect_submodules(pkg)


# ===== ФАЙЛЫ ДАННЫХ =====
datas = []

# config.json (лежит в корне проекта, читается через sys._MEIPASS)
datas += [('config.json', '.')]

# Playwright driver (без этой папки браузер не запустится)
datas += collect_data_files('playwright')


# ===== АНАЛИЗ =====
a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Что точно не нужно — уменьшает размер
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'tkinter', 'PIL.ImageQt',
    ],
    noarchive=False,
    optimize=0,
)


pyz = PYZ(a.pure)


exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ValueDetector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # ← изменили на True — видно ошибки
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ValueDetector',
)