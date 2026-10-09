"""
NEXUS core — константы, пути, логгер, утилиты.

НЕ импортирует UI-зависимости (customtkinter, PIL, qrcode).
Это позволяет загружать core мгновенно и использовать его
в splash-окне до установки зависимостей.

Исправления относительно оригинала:
  ✅ simhash возвращает signed 64-bit (для SQLite INTEGER)
  ✅ simhash_similarity использует маску — корректно для
     signed и unsigned значений
  ✅ Полный набор путей и констант
"""
from __future__ import annotations

import atexit
import copy
import csv
import gc
import hashlib
import hmac
import locale
import logging
import logging.handlers
import math
import os
import platform
import queue
import random
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
import zipfile
from collections import Counter, OrderedDict, defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from functools import lru_cache, wraps
from pathlib import Path
from typing import (
    Any, Callable, Dict, Final, FrozenSet, Iterable, List,
    Optional, Set, Tuple, Union,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    import fcntl as _fcntl_mod
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


# ═══════════════════════════════════════════════════════════
# VERSION
# ═══════════════════════════════════════════════════════════
VERSION: Final[str] = "1.1.1"
VERSION_NAME: Final[str] = "CORE++ FIX"
VERSION_INFO: Final[Tuple[int, int, int]] = (1, 1, 1)
SCHEMA_VERSION: Final[int] = 3
APP_CHANNEL: Final[str] = "release"
BUILD: Final[str] = datetime.now().strftime("%Y%m%d")
BUILD_INFO: Final[Dict[str, Any]] = {
    "version": VERSION, "channel": APP_CHANNEL, "date": BUILD,
    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    "platform": sys.platform,
    "min_python": "3.10",
}
APP_NAME: Final[str] = "NEXUS"
APP_ICON: Final[str] = "🧠"
APP_AUTHOR: Final[str] = "NEXUS Team"
APP_YEAR: Final[int] = datetime.now().year
APP_TITLE: Final[str] = f"{APP_NAME} • {VERSION_NAME} • v{VERSION}"

_DEV: Final[bool] = os.environ.get("NEXUS_DEV", "") == "1"
_START_TIME: Final[float] = time.time()
_MIN_PYTHON: Final[Tuple[int, int]] = (3, 10)


def _is_child_process() -> bool:
    try:
        import multiprocessing as _mp
        return _mp.current_process().name != "MainProcess"
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# DIRECTORIES
# ═══════════════════════════════════════════════════════════
BASE_DIR: Final[Path] = Path(__file__).parent.parent.resolve()
DATA_DIR: Final[Path] = BASE_DIR / "nexus_data"
BACKUP_DIR: Final[Path] = BASE_DIR / "nexus_backups"
ATTACHMENTS_DIR: Final[Path] = BASE_DIR / "nexus_attachments"
PLUGINS_DIR: Final[Path] = BASE_DIR / "nexus_plugins"
TEMP_DIR: Final[Path] = Path(tempfile.gettempdir()) / "nexus_temp"
LOGS_DIR: Final[Path] = BASE_DIR / "nexus_logs"
QR_EXPORT_DIR: Final[Path] = BASE_DIR / "nexus_qr_exports"
EXPORT_DIR: Final[Path] = BASE_DIR / "nexus_exports"
CACHE_DIR: Final[Path] = BASE_DIR / "nexus_cache"
DRAFTS_DIR: Final[Path] = DATA_DIR / "drafts"
SNIPPETS_DIR: Final[Path] = DATA_DIR / "snippets"
QR_CACHE_DIR: Final[Path] = DATA_DIR / "qr_cache"
QR_LOGO_DIR: Final[Path] = DATA_DIR / "qr_logos"
HTML_REPORTS_DIR: Final[Path] = EXPORT_DIR / "html_reports"
VAULTS_DIR: Final[Path] = DATA_DIR / "vaults"
TRASH_DIR = DATA_DIR / "_trash"
SYNC_DIR: Final[Path] = DATA_DIR / "sync"
PASSWORDS_DIR: Final[Path] = DATA_DIR / "passwords"
API_LOGS_DIR: Final[Path] = LOGS_DIR / "api"
THEMES_DIR: Final[Path] = DATA_DIR / "themes"
MEMORY_DIR: Final[Path] = DATA_DIR / "memory"
SANDBOX_DIR: Final[Path] = TEMP_DIR / "sandbox"
RECOVERY_DIR: Final[Path] = DATA_DIR / "recovery"
SNIPPETS_DB_DIR: Final[Path] = DATA_DIR / "code_snippets"
TIMERS_DIR: Final[Path] = DATA_DIR / "timers"
AUDIT_DIR: Final[Path] = DATA_DIR / "audit"
FSRS_DIR: Final[Path] = DATA_DIR / "fsrs"
API_TOKENS_DIR: Final[Path] = DATA_DIR / "api_tokens"
PAGES_CACHE_DIR: Final[Path] = DATA_DIR / "pages_cache"
CODE_SNIPPETS_TEMPLATES_DIR: Final[Path] = (
    DATA_DIR / "code_templates")
INCREMENTAL_BACKUP_DIR: Final[Path] = BACKUP_DIR / "incremental"
SHAMIR_DIR: Final[Path] = DATA_DIR / "shamir"
DAILY_QUESTS_DIR: Final[Path] = DATA_DIR / "daily_quests"


def _mkdir_all() -> None:
    for _d in (
        DATA_DIR, BACKUP_DIR, ATTACHMENTS_DIR, PLUGINS_DIR,
        TEMP_DIR, LOGS_DIR, QR_EXPORT_DIR, EXPORT_DIR,
        CACHE_DIR, DRAFTS_DIR, SNIPPETS_DIR,
        QR_CACHE_DIR, QR_LOGO_DIR, HTML_REPORTS_DIR,
        VAULTS_DIR, SYNC_DIR, PASSWORDS_DIR, API_LOGS_DIR,
        THEMES_DIR, MEMORY_DIR, SANDBOX_DIR, RECOVERY_DIR,
        SNIPPETS_DB_DIR, TIMERS_DIR, AUDIT_DIR, FSRS_DIR,
        API_TOKENS_DIR, PAGES_CACHE_DIR,
        CODE_SNIPPETS_TEMPLATES_DIR, INCREMENTAL_BACKUP_DIR,
        SHAMIR_DIR, DAILY_QUESTS_DIR,
    ):
        try:
            _d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


if not _is_child_process():
    _mkdir_all()

DB_PATH: Final[Path] = DATA_DIR / "nexus.db"
CONFIG_PATH: Final[Path] = DATA_DIR / "config.json"
SESSION_TOKEN_PATH: Final[Path] = DATA_DIR / "session.token"
SESSION_KEY_PATH: Final[Path] = DATA_DIR / "session.key"
HOTKEYS_PATH: Final[Path] = DATA_DIR / "hotkeys.json"
VAULT_CONFIG: Final[Path] = DATA_DIR / "vault.json"
PASSWORDS_PATH: Final[Path] = PASSWORDS_DIR / "vault.enc"
MRU_PATH: Final[Path] = DATA_DIR / "mru.json"
FIRST_RUN_FLAG: Final[Path] = DATA_DIR / ".first_run_done"
MEMORY_CONTEXT_PATH: Final[Path] = MEMORY_DIR / "context.json"
RECOVERY_HASH_PATH: Final[Path] = RECOVERY_DIR / "recovery.hash"
PEPPER_PATH: Final[Path] = DATA_DIR / ".pepper"
CODE_DRAFT_PATH: Final[Path] = DATA_DIR / "code_draft.json"
CODE_DRAFT_LEGACY_PATH: Final[Path] = DATA_DIR / "code_draft.py"
QR_HISTORY_PATH: Final[Path] = DATA_DIR / "qr_history.json"
CODE_TEMPLATES_PATH: Final[Path] = (
    CODE_SNIPPETS_TEMPLATES_DIR / "templates.json")
DAILY_QUESTS_PATH: Final[Path] = DAILY_QUESTS_DIR / "quests.json"
HTTP_API_TOKENS_PATH: Final[Path] = API_TOKENS_DIR / "tokens.json"


# ═══════════════════════════════════════════════════════════
# UI CONSTANTS
# ═══════════════════════════════════════════════════════════
UI_PAD_OUTER: Final[int] = 20
UI_PAD_INNER: Final[int] = 12
UI_PAD_CARD: Final[int] = 14
UI_CARD_RADIUS: Final[int] = 8
UI_HEADER_HEIGHT: Final[int] = 64
UI_BUTTON_HEIGHT: Final[int] = 36
UI_BUTTON_WIDTH: Final[int] = 120
UI_SIDEBAR_WIDTH: Final[int] = 260
UI_STATUS_BAR_HEIGHT: Final[int] = 32
UI_TOAST_HEIGHT: Final[int] = 56
UI_FORM_ENTRY_HEIGHT: Final[int] = 38
UI_QR_PREVIEW_SIZE: Final[int] = 400
UI_TOAST_DEBOUNCE_MS: Final[int] = 800
UI_SEARCH_DEBOUNCE_MS: Final[int] = 250
UI_QR_DEBOUNCE_MS: Final[int] = 600
UI_CODE_AUTOSAVE_MS: Final[int] = 1500
UI_CODE_HIGHLIGHT_MS: Final[int] = 350
UI_HOME_WIDGET_MIN_H: Final[int] = 160
UI_HOME_WIDGET_MIN_W: Final[int] = 200
UI_QUICK_BTN_MIN_W: Final[int] = 90
UI_WRAP_WIDE: Final[int] = 1000
UI_WRAP_MED: Final[int] = 680
UI_SKELETON_ROWS: Final[int] = 4
UI_VIRTUAL_BATCH_SIZE: Final[int] = 50


# ═══════════════════════════════════════════════════════════
# LIMITS
# ═══════════════════════════════════════════════════════════
MAX_FILE_SIZE: Final[int] = 100 * 1024 * 1024
MAX_TAGS: Final[int] = 15
MAX_BACKUPS: Final[int] = 30
CACHE_TTL: Final[int] = 300
THREAD_POOL_SIZE: Final[int] = 16
PASSWORD_MIN_LENGTH: Final[int] = 8
QR_HISTORY_LIMIT: Final[int] = 200
QR_BATCH_LIMIT: Final[int] = 500
AUTO_BACKUP_INTERVAL: Final[int] = 300
LOGIN_RATE_LIMIT: Final[int] = 5
LOGIN_RATE_WINDOW: Final[int] = 300
DRAFT_SAVE_INTERVAL: Final[int] = 30
CODE_TIMEOUT: Final[int] = 10
CODE_HISTORY_LIMIT: Final[int] = 50
SNIPPET_LIMIT: Final[int] = 200
SESSION_TOKEN_TTL_DAYS: Final[int] = 90
SESSION_SLIDING_THRESHOLD: Final[int] = 24 * 3600
TOAST_STACK_MAX: Final[int] = 3
SESSION_REFRESH_INTERVAL_MS: Final[int] = 30 * 60 * 1000
ACTIVITY_THROTTLE_SEC: Final[float] = 1.0
STATUS_BAR_INTERVAL_MS: Final[int] = 5000
ACHIEVEMENT_BATCH_INTERVAL_MS: Final[int] = 30_000
NOTES_PAGE_SIZE: Final[int] = 20
MRU_MAX: Final[int] = 10
HTTP_RETRY_MAX: Final[int] = 3
HTTP_RETRY_BACKOFF: Final[float] = 0.5
CIRCUIT_BREAKER_THRESHOLD: Final[int] = 5
CIRCUIT_BREAKER_TIMEOUT: Final[int] = 300
SHUTDOWN_TIMEOUT: Final[float] = 5.0
SANDBOX_CPU_LIMIT: Final[int] = 5
SANDBOX_MEM_LIMIT: Final[int] = 256 * 1024 * 1024
SANDBOX_FSIZE_LIMIT: Final[int] = 10 * 1024 * 1024
SANDBOX_MAX_OUTPUT: Final[int] = 1 * 1024 * 1024
UNDO_WINDOW_SEC: Final[int] = 5
TAG_CLOUD_LIMIT: Final[int] = 20
NOTES_MERGE_THRESHOLD: Final[float] = 0.7
SIMHASH_BITS: Final[int] = 64
SIMHASH_BUCKETS: Final[int] = 4
MEMORY_HINT_MIN_CONFIDENCE: Final[float] = 0.55
MEMORY_HINT_MAX: Final[int] = 5
MEMORY_DECAY_DAYS: Final[int] = 30
MEMORY_DECAY_LOW_VIEWS: Final[int] = 2
MEMORY_PATTERN_WINDOW_DAYS: Final[int] = 14
MEMORY_CONTEXT_LOG_MAX: Final[int] = 500
DECAY_BASE_HALF_LIFE: Final[float] = 30.0
DECAY_FADE_THRESHOLD: Final[float] = 0.25
DECAY_FAVORITE_MULT: Final[float] = 3.0
DECAY_PINNED_MULT: Final[float] = 5.0
SESSION_ROLLING_WINDOW: Final[int] = 30
SESSION_ROLLING_DRIFT: Final[int] = 2
BLOCKCHAIN_MAX_EDIT_HISTORY: Final[int] = 20
RECOVERY_WORDS_COUNT: Final[int] = 12
AUDIT_MAX_ENTRIES: Final[int] = 10000
TIMER_FLUSH_INTERVAL: Final[int] = 60
WAL_CHECKPOINT_INTERVAL: Final[int] = 1000
PAGE_PRELOAD_DISTANCE: Final[int] = 1
SKELETON_DELAY_MS: Final[int] = 150
CODE_MULTI_TAB_MAX: Final[int] = 5
POMODORO_WORK_MIN: Final[int] = 25
POMODORO_SHORT_MIN: Final[int] = 5
POMODORO_LONG_MIN: Final[int] = 15
POMODORO_LONG_INTERVAL: Final[int] = 4
DAILY_QUESTS_COUNT: Final[int] = 3
AUTOLINK_MIN_SIM: Final[float] = 0.65
WEBHOOK_QUEUE_MAX: Final[int] = 1000
API_RATE_LIMIT_RPM: Final[int] = 120
API_MAX_BODY: Final[int] = 1024 * 1024
SHAMIR_PARTS: Final[int] = 3
SHAMIR_THRESHOLD: Final[int] = 2


# ═══════════════════════════════════════════════════════════
# LOGGER
# ═══════════════════════════════════════════════════════════
class _SensitiveFilter(logging.Filter):
    _PATTERNS = [
        (re.compile(
            r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^"\'\s]+)',
            re.IGNORECASE), r'\1=***'),
        (re.compile(
            r'(token|secret|api[_-]?key)["\']?\s*[:=]\s*["\']?([^"\'\s]+)',
            re.IGNORECASE), r'\1=***'),
        (re.compile(
            r'(recovery|mnemonic|seed)["\']?\s*[:=]\s*["\']?([^"\'\s]+)',
            re.IGNORECASE), r'\1=***'),
        (re.compile(r'(AESGCM\$[A-Za-z0-9+/=]+)',
                     re.IGNORECASE), r'***ENCRYPTED***'),
        (re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b'), r'***@***'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pat, repl in self._PATTERNS:
                msg = pat.sub(repl, msg)
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            return _json.dumps(payload, ensure_ascii=False,
                                default=str)
        except Exception:
            return _json.dumps({"ts": payload["ts"],
                                 "level": "ERROR",
                                 "msg": "log failed"})


class _Logger:
    _instance: Optional["_Logger"] = None
    _lock = threading.Lock()
    _metrics: Counter = Counter()

    def __new__(cls) -> "_Logger":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self._log = logging.getLogger("NEXUS")
        self._log.setLevel(
            logging.DEBUG if _DEV else logging.INFO)
        self._log.propagate = False
        if _is_child_process():
            self._log.addHandler(logging.NullHandler())
            return
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if self._log.handlers:
            return
        try:
            fh = logging.handlers.RotatingFileHandler(
                LOGS_DIR / "nexus.jsonl",
                maxBytes=50 * 1024 * 1024, backupCount=10,
                encoding="utf-8")
            fh.setFormatter(_JsonFormatter())
            fh.addFilter(_SensitiveFilter())
            self._log.addHandler(fh)
            hh = logging.handlers.RotatingFileHandler(
                LOGS_DIR / "nexus.log",
                maxBytes=10 * 1024 * 1024, backupCount=5,
                encoding="utf-8")
            hh.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(message)s"))
            hh.addFilter(_SensitiveFilter())
            self._log.addHandler(hh)
        except Exception:
            self._log.addHandler(logging.NullHandler())

    def _emit(self, level: str, msg: str) -> None:
        self._metrics[level] += 1
        with suppress(Exception):
            self._log.log(getattr(logging, level.upper()), msg)

    def info(self, m: str) -> None:
        self._emit("INFO", m)

    def error(self, m: str) -> None:
        self._emit("ERROR", m)

    def warning(self, m: str) -> None:
        self._emit("WARNING", m)

    def debug(self, m: str) -> None:
        self._emit("DEBUG", m)

    def exception(self, m: str) -> None:
        self._emit("ERROR", m)

    def metrics(self) -> Dict[str, int]:
        return dict(self._metrics)

    def flush(self) -> None:
        with suppress(Exception):
            for h in self._log.handlers:
                with suppress(Exception):
                    h.flush()


logger = _Logger()
if not _is_child_process():
    logger.info(f"🚀 NEXUS {VERSION} {VERSION_NAME} build {BUILD}")


# ═══════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════
def gen_id() -> str:
    return uuid.uuid4().hex


def gen_short_id(n: int = 8) -> str:
    return secrets.token_urlsafe(n)[:n]


def truncate(text: Optional[str], n: int = 100) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[:n - 1] + "…"


def fmt_size(size: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {u}"
        size /= 1024
    return f"{size:.1f} PB"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}с"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}м {s}с"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}ч {m}м"
    d, h = divmod(h, 24)
    return f"{d}д {h}ч"


def fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_dt(s: Optional[str],
           fmt: str = "%d.%m.%Y %H:%M") -> str:
    dt = parse_iso(s)
    return dt.strftime(fmt) if dt else (s[:16] if s else "")


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w\-. ]", "_", name)
    name = name.strip(". ")
    return name[:max_len] or "unnamed"


def sanitize_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))


def safe_csv_cell(value: Any) -> str:
    s = str(value if value is not None else "")
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def copy_to_clipboard(text: str) -> bool:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        return False


def get_clipboard() -> Optional[str]:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        data = r.clipboard_get()
        r.destroy()
        return data
    except Exception:
        return None


def clipboard_image() -> Optional[Any]:
    """Требует PIL.ImageGrab. Импорт ленивый."""
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return None
    try:
        img = ImageGrab.grabclipboard()
        if img is None:
            return None
        if isinstance(img, list):
            if not img:
                return None
            return Image.open(img[0])
        if hasattr(img, "convert"):
            return img.convert("RGB")
        return None
    except Exception:
        return None


def fuzzy_match(query: str, text: str) -> Tuple[bool, float]:
    if not query:
        return True, 1.0
    q = query.lower()
    t = text.lower()
    if q in t:
        pos = t.index(q)
        score = 1.0 - (pos / max(1, len(t))) * 0.3
        score += (len(q) / max(1, len(t))) * 0.3
        return True, min(1.0, score)
    i, matches = 0, 0
    for ch in q:
        while i < len(t):
            if t[i] == ch:
                matches += 1
                i += 1
                break
            i += 1
        else:
            break
    if matches == len(q):
        return True, matches / max(1, len(t)) * 0.7
    return False, 0.0


def extract_tags(text: str, limit: int = MAX_TAGS) -> List[str]:
    return re.findall(r"#[\wа-яА-ЯёЁ]+", text or "")[:limit]


def detect_system_theme() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Themes\Personalize")
            value, _ = winreg.QueryValueEx(
                key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value > 0 else "dark"
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["defaults", "read", "-g",
                     "AppleInterfaceStyle"],
                    capture_output=True, text=True, timeout=2)
                return "dark" if "Dark" in result.stdout \
                    else "light"
            except Exception:
                return "light"
        try:
            result = subprocess.run(
                ["gsettings", "get",
                 "org.gnome.desktop.interface",
                 "color-scheme"],
                capture_output=True, text=True, timeout=2)
            if "dark" in result.stdout.lower():
                return "dark"
        except Exception:
            pass
    except Exception:
        pass
    return "dark"


def get_host_info() -> str:
    try:
        hostname = socket.gethostname()
        user = (os.environ.get("USERNAME")
                or os.environ.get("USER") or "unknown")
        system = f"{platform.system()} {platform.release()}"
        return f"{user}@{hostname} ({system})"
    except Exception:
        return "unknown"


def sound_beep() -> None:
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["afplay",
                 "/System/Library/Sounds/Glass.aiff"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        else:
            for cmd in (
                ["paplay",
                 "/usr/share/sounds/freedesktop/stereo/message.oga"],
                ["aplay",
                 "/usr/share/sounds/alsa/Front_Center.wav"],
            ):
                try:
                    subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                    break
                except Exception:
                    continue
    except Exception:
        pass


def beep_async() -> None:
    threading.Thread(target=sound_beep, daemon=True).start()


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    try:
        h = h.lstrip("#")
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        if len(h) != 6:
            return (0, 0, 0)
        return (int(h[0:2], 16), int(h[2:4], 16),
                int(h[4:6], 16))
    except Exception:
        return (0, 0, 0)


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def luminance(hex_color: str) -> float:
    r, g, b = hex_to_rgb(hex_color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def pick_contrast(hex_color: str, light: str = "#ffffff",
                   dark: str = "#000000") -> str:
    return dark if luminance(hex_color) > 0.55 else light


def mix_colors(c1: str, c2: str, ratio: float = 0.5) -> str:
    try:
        ratio = max(0.0, min(1.0, float(ratio)))
    except Exception:
        ratio = 0.5
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex((
        int(r1 + (r2 - r1) * ratio),
        int(g1 + (g2 - g1) * ratio),
        int(b1 + (b2 - b1) * ratio),
    ))


_FILE_ICONS: Final[Dict[str, str]] = {
    ".txt": "📄", ".pdf": "📕", ".doc": "📘", ".docx": "📘",
    ".xls": "📊", ".xlsx": "📊", ".ppt": "📙", ".pptx": "📙",
    ".jpg": "🖼️", ".jpeg": "🖼️", ".png": "🖼️", ".gif": "🖼️",
    ".webp": "🖼️", ".svg": "📐", ".ico": "🖼️",
    ".mp3": "🎵", ".wav": "🎵", ".ogg": "🎵", ".flac": "🎵",
    ".mp4": "🎬", ".avi": "🎬", ".mkv": "🎬", ".mov": "🎬",
    ".zip": "📦", ".rar": "📦", ".7z": "📦", ".tar": "📦",
    ".exe": "⚙️", ".msi": "⚙️", ".deb": "⚙️", ".rpm": "⚙️",
    ".py": "🐍", ".js": "📜", ".ts": "📜", ".html": "🌐",
    ".css": "🎨", ".json": "📋", ".xml": "📋",
    ".yaml": "📋", ".yml": "📋", ".csv": "📊", ".md": "📝",
    ".sh": "💻", ".bat": "💻", ".log": "📋", ".ini": "⚙️",
    ".conf": "⚙️", ".db": "🗄️", ".sql": "🗄️",
    ".epub": "📚", ".mobi": "📚",
}


def file_icon(name: str) -> str:
    return _FILE_ICONS.get(
        os.path.splitext(name)[1].lower(), "📎")


_QUOTES: Final[Dict[str, List[str]]] = {
    "code": [
        "Код — поэзия, которую понимает машина ✨",
        "Каждая строка кода — шаг в будущее 🚀",
        "Программирование — это искусство 🎨",
        "Простота — высшая форма изящества 💎",
        "Хороший код — это код, который легко удалить 🧹",
        "Сначала заставь работать, потом — правильно, "
        "потом — быстро ⚡"],
    "innovation": [
        "Технологии служат человеку 🌟",
        "Человек + Машина = Эволюция 🚀",
        "Будущее создаётся сегодня 🛠️"],
    "motivation": [
        "Ты сможешь всё! 💪", "Верь в себя! 🌟",
        "Каждый день — новый шанс! 🚀",
        "Ты сильнее, чем думаешь 🔥"],
    "memory": [
        "Забывать — тоже искусство 🌱",
        "Связи важнее фактов 🔗",
        "Помнить — значит жить дважды ✨"],
    "nexus": [
        "NEXUS — твоя цифровая вселенная! 🌀",
        "Один узел — бесконечность связей ♾️"],
    "philosophy": [
        "Знание — сила, применение — мудрость 🧠",
        "Меняй себя — мир изменится 🌍",
        "Путь важнее цели 🛤️"],
    "qr": [
        "QR — мост между мирами 🌉",
        "Один квадрат — тысяча слов 🔲",
        "Сканируй — открывай — живи 📱"],
}
_QUOTES_FLAT: Final[List[str]] = [
    q for c in _QUOTES.values() for q in c]


def quote(category: Optional[str] = None) -> str:
    if category and category in _QUOTES:
        return random.choice(_QUOTES[category])
    return random.choice(_QUOTES_FLAT)


# ── Валидаторы ────────────────────────────────────────────
def validate_email(email: str) -> bool:
    return bool(re.match(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
        email or ""))


def validate_username(u: str) -> Tuple[bool, str]:
    if not u or len(u) < 3:
        return False, "Имя минимум 3 символа"
    if len(u) > 30:
        return False, "Имя максимум 30 символов"
    if not re.match(r'^[a-zA-Zа-яА-Я0-9_\-]+$', u,
                     re.UNICODE):
        return False, "Недопустимые символы"
    return True, "OK"


def validate_password(p: str) -> Tuple[bool, str]:
    if not p or len(p) < PASSWORD_MIN_LENGTH:
        return False, (f"Пароль минимум "
                        f"{PASSWORD_MIN_LENGTH} символов")
    if not re.search(r"[A-ZА-Я]", p):
        return False, "Нужна заглавная буква"
    if not re.search(r"[a-zа-я]", p):
        return False, "Нужна строчная буква"
    if not re.search(r"\d", p):
        return False, "Нужна цифра"
    if not re.search(
            r'[!@#$%^&*(),.?":{}|<>_\-+=;\[\]\\/`~]', p):
        return False, "Нужен спецсимвол"
    return True, "OK"


def password_strength(p: str) -> Tuple[int, str]:
    if not p:
        return 0, "пусто"
    score = 0
    ln = len(p)
    score += min(40, ln * 4)
    kinds = 0
    if re.search(r"[a-z]", p):
        kinds += 1
    if re.search(r"[A-Z]", p):
        kinds += 1
    if re.search(r"\d", p):
        kinds += 1
    if re.search(r"[^\w]", p, re.UNICODE):
        kinds += 1
    score += kinds * 10
    if len(set(p)) >= ln * 0.6:
        score += 10
    for bad in ("password", "123456", "qwerty", "admin",
                "letmein", "welcome", "12345678"):
        if bad in p.lower():
            score -= 30
    if re.match(r"^(.)\1+$", p):
        score -= 40
    score = max(0, min(100, score))
    if score < 30:
        label = "очень слабый"
    elif score < 50:
        label = "слабый"
    elif score < 70:
        label = "средний"
    elif score < 85:
        label = "сильный"
    else:
        label = "очень сильный"
    return score, label


def safe_join(base: Path, *parts: str) -> Optional[Path]:
    try:
        candidate = (base / Path(*parts)).resolve()
        base_r = base.resolve()
        candidate.relative_to(base_r)
        return candidate
    except (ValueError, OSError):
        return None


def retry(times: int = 3, delay: float = 0.1,
          backoff: float = 2.0,
          exceptions: Tuple[type, ...] = (Exception,)):
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            d = delay
            last: Optional[Exception] = None
            for attempt in range(times):
                try:
                    return fn(*a, **kw)
                except exceptions as e:
                    last = e
                    if attempt < times - 1:
                        time.sleep(d)
                        d *= backoff
            if last:
                raise last
            return None
        return wrapper
    return deco


def sha256_hex(data: Union[str, bytes]) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def humanize_relative(iso_ts: Optional[str]) -> str:
    dt = parse_iso(iso_ts)
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин назад"
    if secs < 86400:
        return f"{secs // 3600} ч назад"
    if secs < 604800:
        return f"{secs // 86400} д назад"
    return dt.strftime("%d.%m.%Y")


# ═══════════════════════════════════════════════════════════
# SIMHASH
# ═══════════════════════════════════════════════════════════
def _simhash_tokens(text: str) -> Iterable[str]:
    return re.findall(r"\w{3,}", (text or "").lower())


def _simhash_hash_token(tok: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(tok.encode(),
                         digest_size=8).digest(),
        "big", signed=False)


def simhash(text: str, bits: int = SIMHASH_BITS) -> int:
    """
    Вычисляет 64-битный simhash текста.

    FIX: результат — signed 64-bit, чтобы корректно
    сохраняться в SQLite INTEGER (диапазон
    -2^63 .. 2^63-1).

    Раньше возвращалось unsigned значение (0..2^64-1),
    и при попытке вставить его в БД SQLite падал с
    OverflowError — из-за этого NoteManager.create
    возвращал None, хотя TaskManager работал.

    XOR и bit_count работают одинаково на signed и
    unsigned, поэтому simhash_similarity не страдает.
    """
    if not text:
        return 0
    v = [0] * bits
    for tok, cnt in Counter(_simhash_tokens(text)).items():
        h = _simhash_hash_token(tok)
        for i in range(bits):
            v[i] += cnt if (h >> i) & 1 else -cnt
    out = 0
    for i, x in enumerate(v):
        if x > 0:
            out |= 1 << i
    # Приведение к signed 64-bit для SQLite
    if out >= (1 << 63):
        out -= (1 << 64)
    return out


def simhash_similarity(a: int, b: int,
                        bits: int = SIMHASH_BITS) -> float:
    """
    Сходство двух simhash (0..1).

    FIX: работает корректно и для signed, и для unsigned —
    приводим оба к unsigned через маску перед XOR.

    Без маски: если a (signed, отрицательное) XOR b
    (unsigned) — bit_count() может посчитать неверно
    для питоновских int разной длины.
    """
    mask = (1 << bits) - 1
    a_u = a & mask
    b_u = b & mask
    return 1.0 - ((a_u ^ b_u).bit_count() / bits)


# ═══════════════════════════════════════════════════════════
# SHUTDOWN HOOKS
# ═══════════════════════════════════════════════════════════
_shutdown_handlers: List[Callable] = []
_shutdown_done = False
_shutdown_lock = threading.Lock()


def on_shutdown(fn: Callable) -> None:
    with _shutdown_lock:
        if fn not in _shutdown_handlers:
            _shutdown_handlers.append(fn)


def _run_shutdown() -> None:
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True
        handlers = list(_shutdown_handlers)
    for fn in handlers:
        try:
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            t.join(timeout=SHUTDOWN_TIMEOUT)
        except Exception as e:
            logger.error(f"Shutdown: {e}")


atexit.register(_run_shutdown)


def _signal_handler(signum: int, frame: Any) -> None:
    with suppress(Exception):
        _run_shutdown()
    os._exit(0)


for _sig in (signal.SIGINT, signal.SIGTERM):
    with suppress(Exception):
        signal.signal(_sig, _signal_handler)


# ═══════════════════════════════════════════════════════════
# SAFE EXCEPTION HOOKS
# ═══════════════════════════════════════════════════════════
_original_excepthook = sys.excepthook
_unhandled_errors: List[str] = []
_unhandled_lock = threading.Lock()
_MAX_UNHANDLED: Final[int] = 100


def _write_error(tb_text: str) -> None:
    try:
        sys.stderr.write("\n" + "=" * 60 + "\n")
        sys.stderr.write("❌ NEXUS — НЕОБРАБОТАННАЯ ОШИБКА\n")
        sys.stderr.write("=" * 60 + "\n")
        sys.stderr.write(tb_text)
        sys.stderr.write("=" * 60 + "\n\n")
        sys.stderr.flush()
    except Exception:
        pass
    with suppress(Exception):
        logger.error(f"Unhandled:\n{tb_text}")
    with _unhandled_lock:
        _unhandled_errors.append(tb_text)
        if len(_unhandled_errors) > _MAX_UNHANDLED:
            del _unhandled_errors[:-_MAX_UNHANDLED]


def _graceful_excepthook(exc_type, exc_value,
                          exc_tb) -> None:
    if exc_type is SystemExit \
            or exc_type is KeyboardInterrupt:
        with suppress(Exception):
            _original_excepthook(
                exc_type, exc_value, exc_tb)
        return
    tb_text = ""
    try:
        tb_text = "".join(
            traceback.format_exception(
                exc_type, exc_value, exc_tb))
    except Exception:
        tb_text = f"{exc_type.__name__}: {exc_value}"
    _write_error(tb_text)
    try:
        import tkinter as tk
        if tk._default_root is not None:
            from tkinter import messagebox
            messagebox.showerror(
                "NEXUS — Ошибка",
                f"Произошла ошибка:\n\n{exc_value}\n\n"
                f"Подробнее: nexus_logs/")
    except Exception:
        pass


sys.excepthook = _graceful_excepthook


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    _graceful_excepthook(args.exc_type, args.exc_value,
                          args.exc_traceback)


try:
    threading.excepthook = _thread_excepthook
except Exception:
    pass


def _tk_callback_excepthook(exc, val, tb) -> None:
    if isinstance(val, SystemExit):
        return
    _graceful_excepthook(type(val), val, tb)


def get_unhandled_errors() -> List[str]:
    with _unhandled_lock:
        return list(_unhandled_errors)