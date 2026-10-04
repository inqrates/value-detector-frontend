# core/adspower_launcher.py
"""
Автозапуск AdsPower в headless-режиме.

Логика:
  1. Проверяем, отвечает ли API на localhost:50325.
  2. Если да — ничего не делаем, всё уже работает.
  3. Если нет — ищем AdsPower.exe в стандартных путях.
  4. Нашли — запускаем с --headless=true --api-key=... --api-port=...
  5. Ждём, пока API поднимется (до 30 сек).
  6. Если .exe не найден — возвращаем ошибку, приложение
     показывает диалог и просит указать путь вручную.
"""
import asyncio
import logging
import os
import subprocess
import sys
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

API_PORT = 50325
API_BASE = f"http://localhost:{API_PORT}"


# Стандартные пути для Windows
_WINDOWS_PATHS = [
    r"C:\Program Files\AdsPower Global\AdsPower Global.exe",
    r"C:\Program Files (x86)\AdsPower Global\AdsPower Global.exe",
    r"C:\Program Files\AdsPower\AdsPower.exe",
    r"C:\Program Files (x86)\AdsPower\AdsPower.exe",
    os.path.expandvars(
        r"%LOCALAPPDATA%\Programs\AdsPower Global\AdsPower Global.exe"
    ),
    os.path.expandvars(r"%LOCALAPPDATA%\AdsPower Global\AdsPower Global.exe"),
    os.path.expandvars(r"%APPDATA%\AdsPower Global\AdsPower Global.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\AdsPower Global\AdsPower Global.exe"),
    os.path.expandvars(
        r"%PROGRAMFILES(X86)%\AdsPower Global\AdsPower Global.exe"
    ),
]

# macOS
_MAC_PATHS = [
    "/Applications/AdsPower Global.app/Contents/MacOS/AdsPower Global",
    "/Applications/AdsPower.app/Contents/MacOS/AdsPower",
]

# Linux
_LINUX_PATHS = [
    "/usr/bin/adspower_global",
    "/usr/local/bin/adspower_global",
    "/opt/adspower/adspower_global",
]


def find_adspower_exe() -> Optional[str]:
    """Ищет AdsPower.exe в стандартных путях. Возвращает путь или None."""
    if sys.platform.startswith("win"):
        paths = _WINDOWS_PATHS
    elif sys.platform == "darwin":
        paths = _MAC_PATHS
    else:
        paths = _LINUX_PATHS

    for p in paths:
        if os.path.isfile(p):
            logger.info(f"[adspower_launcher] найден: {p}")
            return p

    return None


async def is_api_alive(timeout: float = 2.0) -> bool:
    """Проверяет, отвечает ли AdsPower API."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{API_BASE}/status", timeout=aiohttp.ClientTimeout(total=timeout)
            ) as r:
                return r.status == 200
    except Exception:
        return False


async def wait_for_api(timeout: float = 30.0,
                       progress_cb=None) -> bool:
    """Ждёт, пока API AdsPower поднимется."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if await is_api_alive(timeout=1.5):
            return True
        if progress_cb:
            elapsed = asyncio.get_event_loop().time() - start
            try:
                progress_cb(elapsed)
            except Exception:
                pass
        await asyncio.sleep(0.5)
    return False


def _build_command(exe_path: str, api_key: str, port: int) -> list:
    """Собирает команду запуска под текущую ОС."""
    if sys.platform == "darwin":
        return [
            exe_path,
            "--args",
            "--headless=true",
            f"--api-key={api_key}",
            f"--api-port={port}",
        ]
    # Windows и Linux
    return [
        exe_path,
        "--headless=true",
        f"--api-key={api_key}",
        f"--api-port={port}",
    ]


def launch_headless(exe_path: str, api_key: str, port: int = API_PORT) -> bool:
    """Запускает AdsPower в headless. Возвращает True, если процесс стартовал."""
    if not exe_path or not os.path.isfile(exe_path):
        logger.error(f"[adspower_launcher] .exe не найден: {exe_path}")
        return False

    cmd = _build_command(exe_path, api_key, port)
    logger.info(f"[adspower_launcher] запуск: {' '.join(cmd)}")

    try:
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        # Windows: без мелькания CMD
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True

        subprocess.Popen(cmd, **kwargs)
        return True
    except Exception as e:
        logger.error(f"[adspower_launcher] ошибка запуска: {e}")
        return False


async def ensure_adspower_running(
    api_key: str,
    exe_path: Optional[str] = None,
    progress_cb=None,
) -> dict:
    """
    Гарантирует, что AdsPower запущен и API доступен.

    Возвращает:
        {"ok": bool, "reason": str, "started": bool}
        started=True если мы сами запустили
    """
    # 1) Уже отвечает?
    if await is_api_alive():
        logger.info("[adspower_launcher] API уже отвечает")
        return {"ok": True, "reason": "already_running", "started": False}

    # 2) Определяем путь к .exe
    if not exe_path:
        exe_path = find_adspower_exe()

    if not exe_path:
        logger.warning("[adspower_launcher] .exe не найден в стандартных путях")
        return {"ok": False, "reason": "exe_not_found", "started": False}

    # 3) Запускаем
    if not launch_headless(exe_path, api_key):
        return {"ok": False, "reason": "launch_failed", "started": False}

    # 4) Ждём API
    if progress_cb:
        progress_cb("Запуск AdsPower (headless)...")

    ok = await wait_for_api(timeout=30.0, progress_cb=progress_cb)
    if not ok:
        return {"ok": False, "reason": "api_timeout", "started": True}

    logger.info("[adspower_launcher] AdsPower поднялся, API доступен")
    return {"ok": True, "reason": "started", "started": True}