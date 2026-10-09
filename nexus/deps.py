"""
Проверка и установка зависимостей.
ВАЖНО: не импортирует customtkinter на уровне модуля.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from typing import Callable, Dict, Optional

_DEPS_VERIFIED: Dict[str, bool] = {}
_VERIFY_LOCK = threading.RLock()

# ── Обязательные ─────────────────────────────────────
REQUIRED: Dict[str, str] = {
    "customtkinter": "customtkinter>=5.2.0",
    "PIL": "Pillow>=10.0.0",
    "qrcode": "qrcode[pil]>=7.4.0",
    "cryptography": "cryptography>=41.0.0",
}

# ── Опциональные ─────────────────────────────────────
OPTIONAL: Dict[str, str] = {
    "argon2": "argon2-cffi>=23.1.0",
    "matplotlib": "matplotlib>=3.7.0",
    "pyzbar": "pyzbar>=0.1.9",
    "pyotp": "pyotp>=2.9.0",
    "cairosvg": "cairosvg>=2.7.0",
}


def check_module(mod: str) -> bool:
    """Проверить, доступен ли модуль. Кэшируется."""
    with _VERIFY_LOCK:
        if mod in _DEPS_VERIFIED:
            return _DEPS_VERIFIED[mod]
    try:
        if "." in mod:
            parent = mod.split(".", 1)[0]
            try:
                __import__(parent)
                importlib.import_module(mod)
                ok = True
            except Exception:
                ok = False
        else:
            ok = importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        ok = False
    with _VERIFY_LOCK:
        _DEPS_VERIFIED[mod] = ok
    return ok


def invalidate_cache(mod: Optional[str] = None) -> None:
    """Сбросить кэш проверки."""
    with _VERIFY_LOCK:
        if mod is None:
            _DEPS_VERIFIED.clear()
        else:
            _DEPS_VERIFIED.pop(mod, None)


def pip_install(pkg: str, timeout: int = 180,
                 progress: Optional[Callable[[str], None]] = None
                 ) -> bool:
    """Установить пакет через pip. Устойчиво к таймаутам."""
    if progress:
        progress(f"📦 pip install {pkg} …")
    for attempt in range(2):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg,
                 "--quiet", "--disable-pip-version-check",
                 "--no-input"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            if progress:
                progress(f"⏱️ Таймаут {pkg} "
                         f"(попытка {attempt + 1}/2)")
        except Exception as e:
            if progress:
                progress(f"❌ {pkg}: {e}")
            return False
    return False


def ensure_deps(
    progress: Optional[Callable[[str], None]] = None,
    *, install: bool = True,
) -> Dict[str, bool]:
    """
    Проверить зависимости. Опционально — установить
    недостающие. Возвращает dict {module: ok}.

    ВЫЗЫВАТЬ В ФОНОВОМ ПОТОКЕ — иначе заблокирует UI.
    """
    result: Dict[str, bool] = {}

    # 1) Проверка + установка обязательных
    to_install = []
    for mod, pkg in REQUIRED.items():
        if not check_module(mod):
            to_install.append((mod, pkg))

    if to_install and install:
        if progress:
            progress(f"Устанавливаю {len(to_install)} пакет(ов)…")
        for mod, pkg in to_install:
            pip_install(pkg, progress=progress)
            invalidate_cache(mod)

    # 2) Финальная проверка обязательных
    missing_required = []
    for mod in REQUIRED:
        ok = check_module(mod)
        result[mod] = ok
        if not ok:
            missing_required.append(mod)

    if missing_required:
        raise RuntimeError(
            f"Обязательные модули недоступны: "
            f"{', '.join(missing_required)}")

    # 3) Опциональные — просто отмечаем
    for mod in OPTIONAL:
        result[mod] = check_module(mod)

    return result


def deps_summary() -> tuple[list[str], list[str]]:
    """(installed, missing) для обоих списков."""
    installed, missing = [], []
    for mod in list(REQUIRED) + list(OPTIONAL):
        (installed if check_module(mod) else missing).append(mod)
    return installed, missing