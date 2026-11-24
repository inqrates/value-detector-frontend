# -*- coding: utf-8 -*-
"""
build_protected.py — защищённая сборка EXE без платных инструментов.

Схема (полностью бесплатно):
  1. Nuitka (Apache-2.0) компилирует критичные модули Python -> нативные .pyd
     (машинный код C, байткод Python внутри отсутствует).
  2. В staging-копии проекта исходники этих модулей заменяются на .pyd.
  3. PyInstaller собирает EXE из staging — критичных .py там уже нет.

Использование:
    venv\\Scripts\\python.exe build_protected.py              # полная сборка
    venv\\Scripts\\python.exe build_protected.py --force      # пересобрать все .pyd
    venv\\Scripts\\python.exe build_protected.py --skip-nuitka
    venv\\Scripts\\python.exe build_protected.py --only after_goal_engine
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = ROOT / "venv" / "Scripts" / "python.exe"
BUILD_PYD = ROOT / "build_pyd"
STAGING = ROOT / "build_staging"
DIST = ROOT / "dist"

# ── Модули, которые уходят в машинный код ────────────────────────────────
# Ядро: движок автоставок, сеть, авторизация, секреты
TARGETS = [
    "ui/after_goal_engine.py",
    "core/fast_client.py",
    "ui/login_window.py",
    "ui/session_store.py",
    "ui/secure_key.py",
    "ui/ssl_cert.py",
    "core/fast_config.py",
    "ui/strategy_store.py",
    # периферия: UI, браузер, профили
    "ui/main_window.py",
    "ui/config_loader.py",
    "ui/profile_settings_dialog.py",
    "ui/first_run_dialog.py",
    "ui/fast_cookie_runner.py",
    "core/browser_launcher.py",
    "core/fast_cookie_collector.py",
    "core/fingerprint.py",
    "core/profile_config.py",
]

# все .py из этих пакетов (кроме __init__.py — они остаются обычными)
for _pkg in ("ui/after_goal", "ui/components", "ui/pages"):
    for _p in sorted((ROOT / _pkg).glob("*.py")):
        if _p.name != "__init__.py":
            TARGETS.append(f"{_pkg}/{_p.name}")

# ── Что не копируем в staging ────────────────────────────────────────────
EXCLUDE_DIRS = {
    "venv", ".git", "__pycache__", "dist", "build_pyd", "build_staging",
    "build_obf", "build_src", "_nt", "_bt", "_bt_out", ".idea", ".vscode",
    "tests",
}
EXCLUDE_DIR_PREFIXES = ("build_",)
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".enc", ".spec.bak", "_tmp_pyz.pyz")
EXCLUDE_FILES = {"tools_check_names.py"}


def log(msg: str) -> None:
    print(msg, flush=True)


# ── Шаг 1: Nuitka ────────────────────────────────────────────────────────
def _pyd_name(rel: str) -> Path:
    """Отдельная папка вывода на модуль — чтобы не было коллизий имён."""
    stem = rel.replace("/", "_").replace("\\", "_").removesuffix(".py")
    return BUILD_PYD / stem


def compile_module(rel: str, force: bool) -> Path:
    src = ROOT / rel
    if not src.is_file():
        raise FileNotFoundError(f"нет исходника: {src}")

    out_dir = _pyd_name(rel)
    found = sorted(out_dir.glob("*.pyd")) if out_dir.is_dir() else []
    if found and not force and found[0].stat().st_mtime >= src.stat().st_mtime:
        log(f"  = {rel} (уже собран)")
        return found[0]

    cmd = [
        str(PY), "-m", "nuitka",
        "--module", str(src),
        "--nofollow-imports",          # только этот модуль, без втягивания соседей
        "--assume-yes-for-downloads",  # zig-компилятор скачивается один раз
        f"--output-dir={out_dir}",
        "--remove-output",
        "--quiet",
    ]
    log(f"  -> {rel}")
    t0 = time.time()
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        log(res.stdout[-4000:])
        log(res.stderr[-4000:])
        raise RuntimeError(f"Nuitka упал на {rel} (код {res.returncode})")

    pyds = sorted(out_dir.glob("*.pyd"))
    if not pyds:
        raise RuntimeError(f"Nuitka не создала .pyd для {rel}")
    log(f"     готово за {time.time() - t0:.1f}s: {pyds[0].name} "
        f"({pyds[0].stat().st_size / 1024:.0f} KB)")
    return pyds[0]


def compile_all(force: bool, only: list[str] | None) -> list[tuple[str, Path]]:
    BUILD_PYD.mkdir(exist_ok=True)
    targets = [t for t in TARGETS if not only or any(o in t for o in only)]
    log(f"[1/3] Nuitka: {len(targets)} модулей")
    result = []
    for rel in targets:
        result.append((rel, compile_module(rel, force)))
    return result


# ── Шаг 2: staging ───────────────────────────────────────────────────────
def _ignore(dirpath: str, names: list[str]) -> set[str]:
    skip = set()
    for n in names:
        full = Path(dirpath) / n
        if full.is_dir() and (n in EXCLUDE_DIRS or n.startswith(EXCLUDE_DIR_PREFIXES)):
            skip.add(n)
        elif full.is_file() and (n.endswith(EXCLUDE_SUFFIXES) or n in EXCLUDE_FILES):
            skip.add(n)
    return skip


def prepare_staging(compiled: list[tuple[str, Path]]) -> None:
    log("[2/3] staging: копируем проект и подменяем модули на .pyd")
    if STAGING.exists():
        shutil.rmtree(STAGING)
    shutil.copytree(ROOT, STAGING, ignore=_ignore)

    for rel, pyd in compiled:
        dst_dir = STAGING / Path(rel).parent
        dst_dir.mkdir(parents=True, exist_ok=True)
        py = STAGING / rel
        if py.exists():
            py.unlink()
        shutil.copy2(pyd, dst_dir / pyd.name)

    left = [rel for rel, _ in compiled if (STAGING / rel).exists()]
    if left:
        raise RuntimeError(f"в staging остались исходники: {left}")

    # Манифест: имена модулей, которые теперь .pyd.
    # PyInstaller не видит импорты внутри машинного кода, поэтому
    # ValueDetector.spec читает этот файл и добавляет их в hiddenimports.
    names = [rel.replace("\\", "/").removesuffix(".py").replace("/", ".")
             for rel, _ in compiled]
    (STAGING / "_pyd_manifest.txt").write_text("\n".join(names), encoding="utf-8")

    log(f"     подменено модулей: {len(compiled)}")


# ── Шаг 3: PyInstaller ───────────────────────────────────────────────────
def build_exe() -> Path:
    log("[3/3] PyInstaller")
    cmd = [str(PY), "-m", "PyInstaller", "--noconfirm", "--clean", "ValueDetector.spec"]
    res = subprocess.run(cmd, cwd=STAGING, capture_output=True, text=True)
    if res.returncode != 0:
        log(res.stdout[-6000:])
        log(res.stderr[-6000:])
        raise RuntimeError(f"PyInstaller упал (код {res.returncode})")

    src = STAGING / "dist" / "ValueDetector"
    if not src.is_dir():
        raise RuntimeError(f"нет результата: {src}")

    # Camoufox использует динамические импорты, которые PyInstaller не всегда
    # видит по графу зависимостей. Не выпускаем сборку, где остались только
    # metadata пакета без модуля, иначе EXE выдаст пользователю ложное
    # сообщение, что Camoufox не установлен.
    camoufox_api = src / "_internal" / "camoufox" / "async_api.py"
    if not camoufox_api.is_file():
        raise RuntimeError(
            "в сборку не попал camoufox/async_api.py; "
            "проверьте ValueDetector.spec и окружение сборки"
        )

    dst = DIST / "ValueDetector"
    if dst.exists():
        shutil.rmtree(dst)
    DIST.mkdir(exist_ok=True)
    shutil.move(str(src), str(dst))
    log(f"     готово: {dst}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="пересобрать все .pyd")
    ap.add_argument("--skip-nuitka", action="store_true", help="использовать готовые .pyd")
    ap.add_argument("--only", nargs="*", help="только модули, содержащие подстроку")
    args = ap.parse_args()

    if not PY.is_file():
        log(f"не найден python: {PY}")
        return 1

    if args.skip_nuitka:
        compiled = []
        for rel in TARGETS:
            pyds = sorted(_pyd_name(rel).glob("*.pyd"))
            if not pyds:
                log(f"нет .pyd для {rel} — сначала запустите без --skip-nuitka")
                return 1
            compiled.append((rel, pyds[0]))
        log(f"[1/3] Nuitka пропущена, готовых .pyd: {len(compiled)}")
    else:
        compiled = compile_all(args.force, args.only)

    prepare_staging(compiled)
    out = build_exe()

    exe = out / "ValueDetector.exe"
    log("")
    log(f"EXE: {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)" if exe.exists() else "EXE не найден")
    return 0


if __name__ == "__main__":
    sys.exit(main())
