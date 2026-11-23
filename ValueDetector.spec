# -*- mode: python ; coding: utf-8 -*-

import sys
import os
import glob
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
    'win32crypt',          # DPAPI: импортируется лениво внутри ui.secure_key (.pyd)
    '_win32sysloader',
    'uvicorn',
    'fastapi',
    'pydantic',
    'httpx',
    'httpcore',
    'curl_cffi',
    'requests',
    'websockets',

    # Шифрование (cookies + session)
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.backends',
    'cryptography.fernet',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.primitives.ciphers.aead',
    'cryptography.hazmat.primitives.ciphers.algorithms',
    'cryptography.hazmat.primitives.ciphers.modes',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.ciphers.base',
    'cryptography.exceptions',

    # Qt
    'PyQt5.QtWebSockets',
    'PyQt5.QtNetwork',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
]

# Playwright — самая капризная часть, нужны все подмодули и данные драйвера
hiddenimports += collect_submodules('playwright')
hiddenimports += collect_submodules('camoufox')
hiddenimports += ['camoufox.fetcher', 'camoufox.async_api']
hiddenimports += collect_submodules('pyee')
hiddenimports += collect_submodules('greenlet')
hiddenimports += collect_submodules('websockets')
hiddenimports += collect_submodules('httpx')
hiddenimports += collect_submodules('httpcore')

# Все модули проекта — все подмодули каждого пакета
for pkg in ('ui', 'ui.after_goal', 'ui.components', 'ui.pages', 'core', 'parsers'):
    hiddenimports += collect_submodules(pkg)

# Явно — новые модули, импортируемые лениво
hiddenimports += [
    'core.fast_client',
    'core.fast_config',
    'core.names',             # единая нормализация имён (импорт из .pyd)
    'core.fast_cookie_collector',
    'core.browser_launcher',
    'core.profile_config',
    'core.fingerprint',
    'ui.fast_cookie_runner',
    'ui.secure_key',          # DPAPI-хранилище мастер-ключа
    'ui.ssl_cert',            # доверие к своему CA
    # ── Логи ──
    'ui.log_paths',
]

# ── PyArmor runtime (если сборка защищённая) ──
# build_protected.py кладёт pyarmor_runtime_<id>/ в корень staging.
# Здесь автоподхватываем: и как hiddenimport, и как данные.
_here = SPECPATH if 'SPECPATH' in globals() else os.path.dirname(os.path.abspath(SPEC))
_pyarmor_runtimes = glob.glob(os.path.join(_here, 'pyarmor_runtime_*'))
for _rt in _pyarmor_runtimes:
    hiddenimports.append(os.path.basename(_rt))

# ── Nuitka .pyd (защищённая сборка) ──
# build_protected.py пишет _pyd_manifest.txt со списком скомпилированных
# модулей: внутри .pyd импортов не видно, поэтому объявляем их явно.
_manifest = os.path.join(_here, '_pyd_manifest.txt')
if os.path.isfile(_manifest):
    with open(_manifest, encoding='utf-8') as _f:
        _pyd_mods = [ln.strip() for ln in _f if ln.strip()]
    hiddenimports += _pyd_mods
    print(f'[spec] Nuitka .pyd модулей: {len(_pyd_mods)}')


# ===== ФАЙЛЫ ДАННЫХ =====
datas = []

# Playwright driver (без этой папки браузер не запустится)
datas += collect_data_files('playwright')

# Camoufox: JSON/YAML-базы (пресеты отпечатков, шрифты, голоса, GeoIP).
# Без fingerprint-presets.json профиль останется без личности —
# отпечаток будет случайным при каждом запуске.
datas += collect_data_files('camoufox')

# PyArmor runtime — как данные (pyd-файл должен лежать рядом с кодом)
for _rt in _pyarmor_runtimes:
    datas.append((_rt, os.path.basename(_rt)))

# CA-сертификат для HTTPS по IP (certs/ca.pem вшивается в EXE)
_ca_file = os.path.join(_here, 'certs', 'ca.pem')
if os.path.isfile(_ca_file):
    datas.append((_ca_file, 'certs'))

# config.json больше не нужен — адрес зашит в ui/config_loader.py
# Если когда-нибудь понадобится — раскомментируйте:
# datas += [('config.json', '.')]


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
    optimize=2,                 # ← max optimization
)


pyz = PYZ(a.pure)

# ── Ключ шифрования байт-кода ──
# В PyInstaller 6.0+ ключ удалён.
# Для obfuscation используем: PyArmor или Cython для критических файлов.
pyz_key = None

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
    upx=False,                  # ← отключён: ложные срабатывания AV
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # windowed-режим
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
    upx=False,                  # ← отключён
    upx_exclude=[],
    name='ValueDetector',
)