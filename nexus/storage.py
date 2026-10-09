"""
NEXUS storage — Store, TTLCache, MRU, Database.

Зависит от: nexus.core

Исправления:
  ✅ Schema version 4 — полная миграция для старых БД
  ✅ Автоповтор миграции при обнаружении отсутствующих колонок
  ✅ db.execute: BEGIN/COMMIT вокруг SELECT — свежий snapshot
     в thread-local соединениях (фикс 401 в HTTP API)
  ✅ users, notes, tasks, user_stats — все поля
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import Counter, OrderedDict, defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (
    Any, Callable, Dict, Final, Iterable, List, Optional,
    Set, Tuple,
)

from nexus.core import (
    API_TOKENS_DIR,
    BACKUP_DIR,
    BASE_DIR,
    CACHE_TTL,
    DATA_DIR,
    DB_PATH,
    DECAY_FAVORITE_MULT,
    DECAY_PINNED_MULT,
    HTTP_RETRY_BACKOFF,
    HTTP_RETRY_MAX,
    INCREMENTAL_BACKUP_DIR,
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW,
    MAX_BACKUPS,
    MRU_MAX,
    MRU_PATH,
    SHUTDOWN_TIMEOUT,
    THREAD_POOL_SIZE,
    TOAST_STACK_MAX,
    UI_TOAST_DEBOUNCE_MS,
    WAL_CHECKPOINT_INTERVAL,
    gen_id,
    logger,
    now_iso,
    on_shutdown,
    parse_iso,
    truncate,
)

import urllib.error
import urllib.request


# ═══════════════════════════════════════════════════════════
# STORE
# ═══════════════════════════════════════════════════════════
class Store:
    _lock = threading.RLock()
    _CACHE_TTL = 60
    _BAK_THRESHOLD = 1_000_000

    @classmethod
    def _path(cls, name: str) -> Path:
        safe = name.replace("\\", "/").strip("/")
        if ".." in safe.split("/"):
            raise ValueError(f"Unsafe store name: {name}")
        return DATA_DIR / f"{safe}.json"

    @classmethod
    def load(cls, name: str, default: Any = None) -> Any:
        import copy as _copy
        ck = f"store:{name}"
        cached = cache.get(ck)
        if cached is not None:
            try:
                return _copy.deepcopy(cached)
            except Exception:
                return cached
        try:
            path = cls._path(name)
        except ValueError:
            return default if default is not None else []
        if not path.exists():
            return default if default is not None else []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cache.set(ck, data, cls._CACHE_TTL)
            return data
        except Exception:
            return default if default is not None else []

    @classmethod
    def save(cls, name: str, data: Any) -> bool:
        with cls._lock:
            try:
                path = cls._path(name)
            except ValueError:
                return False
            with suppress(Exception):
                if (path.exists()
                        and path.stat().st_size
                        < cls._BAK_THRESHOLD):
                    bk = BACKUP_DIR / f"{path.stem}.bak.json"
                    shutil.copy2(path, bk)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False,
                              indent=2)
                    f.flush()
                    with suppress(Exception):
                        os.fsync(f.fileno())
                tmp.replace(path)
            except Exception as e:
                logger.error(f"Store.save {name}: {e}")
                return False
            cache.set(f"store:{name}", data, cls._CACHE_TTL)
            return True

    @classmethod
    def delete(cls, name: str) -> bool:
        with cls._lock:
            try:
                path = cls._path(name)
                if path.exists():
                    path.unlink()
                cache.delete(f"store:{name}")
                return True
            except Exception:
                return False


# ═══════════════════════════════════════════════════════════
# TTL CACHE
# ═══════════════════════════════════════════════════════════
class TTLCache:
    def __init__(self, max_size: int = 2000,
                 default_ttl: int = CACHE_TTL) -> None:
        self._data: "OrderedDict[str, Tuple[Any, float, float]]" = (
            OrderedDict())
        self._lock = threading.RLock()
        self._max = max(16, max_size)
        self._default_ttl = max(1, default_ttl)
        self._stats: Counter = Counter()
        self._last_gc = time.monotonic()

    def set(self, key: str, value: Any,
            ttl: Optional[int] = None) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            eff = (self._default_ttl
                    if ttl is None else max(1, ttl))
            self._data[key] = (value, time.monotonic(), eff)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
                self._stats["evicted"] += 1
            self._maybe_gc()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._maybe_gc()
            item = self._data.get(key)
            if item is None:
                self._stats["miss"] += 1
                return default
            value, ts, ttl = item
            if time.monotonic() - ts > ttl:
                del self._data[key]
                self._stats["expired"] += 1
                return default
            self._data.move_to_end(key)
            self._stats["hit"] += 1
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self, pattern: Optional[str] = None) -> int:
        with self._lock:
            if pattern is None:
                n = len(self._data)
                self._data.clear()
                return n
            rx = re.compile(pattern.replace("*", ".*"))
            keys = [k for k in self._data if rx.match(k)]
            for k in keys:
                del self._data[k]
            return len(keys)

    def _maybe_gc(self) -> None:
        now = time.monotonic()
        if now - self._last_gc < 30:
            return
        self._last_gc = now
        expired = [k for k, (_, ts, ttl) in self._data.items()
                   if now - ts > ttl]
        for k in expired:
            del self._data[k]
            self._stats["expired"] += 1

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def close(self) -> None:
        with self._lock:
            self._data.clear()


cache = TTLCache()


# ═══════════════════════════════════════════════════════════
# MRU
# ═══════════════════════════════════════════════════════════
class MRU:
    def __init__(self, path: Path,
                 max_size: int = MRU_MAX) -> None:
        self._items: List[str] = []
        self._lock = threading.RLock()
        self._max = max(1, max_size)
        self._path = path
        self._load()

    def _load(self) -> None:
        with suppress(Exception):
            if self._path.exists():
                with open(self._path, "r",
                          encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._items = [str(x)
                                    for x in data][:self._max]

    def _save(self) -> None:
        with suppress(Exception):
            tmp = self._path.with_suffix(
                self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False)
            tmp.replace(self._path)

    def push(self, item: str) -> None:
        if not item:
            return
        with self._lock:
            if item in self._items:
                self._items.remove(item)
            self._items.insert(0, item)
            del self._items[self._max:]
            self._save()

    def get_all(self) -> List[str]:
        with self._lock:
            return list(self._items)


mru = MRU(MRU_PATH)


# ═══════════════════════════════════════════════════════════
# SAFE EXECUTOR
# ═══════════════════════════════════════════════════════════
class SafeExecutor:
    def __init__(self,
                 max_workers: int = THREAD_POOL_SIZE) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="nexus")
        self._lock = threading.Lock()
        self._closing = False
        self._active = 0

    def submit(self, fn: Callable, *args: Any,
               **kwargs: Any) -> Optional[Future]:
        with self._lock:
            if self._closing:
                return None
            self._active += 1
        try:
            return self._pool.submit(
                self._wrapped, fn, args, kwargs)
        except RuntimeError:
            with self._lock:
                self._active = max(0, self._active - 1)
            return None

    @staticmethod
    def _wrapped(fn: Callable, args: Tuple[Any, ...],
                  kwargs: Dict[str, Any]) -> Any:
        return fn(*args, **kwargs)

    def submit_safe(self, fn: Callable, *args: Any,
                     **kwargs: Any) -> Optional[Future]:
        def _run() -> None:
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Executor "
                    f"{getattr(fn, '__name__', fn)}: {e}")
            finally:
                with self._lock:
                    self._active = max(0, self._active - 1)
        with self._lock:
            if self._closing:
                return None
            self._active += 1
        try:
            return self._pool.submit(_run)
        except RuntimeError:
            with self._lock:
                self._active = max(0, self._active - 1)
            return None

    def active_count(self) -> int:
        with self._lock:
            return self._active

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        try:
            self._pool.shutdown(
                wait=wait, cancel_futures=True)
        except TypeError:
            self._pool.shutdown(wait=wait)


executor = SafeExecutor()


# ═══════════════════════════════════════════════════════════
# TOAST DEDUP
# ═══════════════════════════════════════════════════════════
class ToastDedup:
    def __init__(self) -> None:
        self._recent: Dict[str, float] = {}
        self._lock = threading.RLock()

    def should_show(self, msg: str,
                     debounce_ms: int = UI_TOAST_DEBOUNCE_MS
                     ) -> bool:
        key = msg.strip()[:120]
        now = time.time()
        window = debounce_ms / 1000.0
        with self._lock:
            last = self._recent.get(key)
            if last is not None and (now - last) < window:
                return False
            self._recent[key] = now
            if len(self._recent) > 64:
                cutoff = now - 5.0
                self._recent = {
                    k: v for k, v in self._recent.items()
                    if v > cutoff}
            return True


toast_dedup = ToastDedup()


# ═══════════════════════════════════════════════════════════
# EVENT BUS
# ═══════════════════════════════════════════════════════════
class EventBus:
    _instance: Optional["EventBus"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "EventBus":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._subs: Dict[
                    str, List[Callable]] = defaultdict(list)
                cls._instance._rlock = threading.RLock()
            return cls._instance

    def subscribe(self, event: str,
                   handler: Callable) -> None:
        with self._rlock:
            if handler not in self._subs[event]:
                self._subs[event].append(handler)

    def unsubscribe(self, event: str,
                     handler: Callable) -> None:
        with self._rlock:
            if (event in self._subs
                    and handler in self._subs[event]):
                self._subs[event].remove(handler)

    def emit(self, event: str, **kwargs: Any) -> None:
        with self._rlock:
            handlers = list(self._subs.get(event, []))
        for h in handlers:
            try:
                h(**kwargs)
            except Exception as e:
                logger.error(f"EventBus [{event}]: {e}")

    def emit_async(self, event: str, **kwargs: Any) -> None:
        with self._rlock:
            handlers = list(self._subs.get(event, []))
        for h in handlers:
            executor.submit_safe(
                self._safe_call, h, event, kwargs)

    @staticmethod
    def _safe_call(handler: Callable, event: str,
                    kwargs: Dict[str, Any]) -> None:
        try:
            handler(**kwargs)
        except Exception as e:
            logger.error(f"EventBus.async [{event}]: {e}")


event_bus = EventBus()


# ═══════════════════════════════════════════════════════════
# SECURE STRING
# ═══════════════════════════════════════════════════════════
class SecureString:
    __slots__ = ("_buf", "_lock", "_destroyed")

    def __init__(self, value: str) -> None:
        self._buf = bytearray(value.encode("utf-8"))
        self._lock = threading.Lock()
        self._destroyed = False

    def use(self) -> str:
        with self._lock:
            if self._destroyed:
                raise RuntimeError("SecureString destroyed")
            return self._buf.decode("utf-8")

    @contextmanager
    def open(self) -> Iterable[str]:
        s = self.use()
        try:
            yield s
        finally:
            del s

    def destroy(self) -> None:
        with self._lock:
            if self._destroyed:
                return
            for i in range(len(self._buf)):
                self._buf[i] = 0
            self._destroyed = True

    def __del__(self) -> None:
        with suppress(Exception):
            self.destroy()

    def __bool__(self) -> bool:
        with self._lock:
            return not self._destroyed and len(self._buf) > 0


# ═══════════════════════════════════════════════════════════
# UNDO STACK
# ═══════════════════════════════════════════════════════════
class UndoStack:
    def __init__(self, max_items: int = 20) -> None:
        self._items: deque = deque(maxlen=max_items)
        self._lock = threading.RLock()

    def push(self, undo_fn: Callable, label: str,
             ttl: int = 5) -> None:
        with self._lock:
            self._items.append({
                "fn": undo_fn, "label": label,
                "expires": time.time() + ttl})

    def pop_valid(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            while self._items:
                item = self._items.pop()
                if time.time() < item["expires"]:
                    return item
            return None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


undo_stack = UndoStack()


# ═══════════════════════════════════════════════════════════
# TIMER POOL
# ═══════════════════════════════════════════════════════════
class TimerPool:
    def __init__(self, widget: Any) -> None:
        self._widget = widget
        self._timers: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._closed = False

    def schedule(self, name: str, ms: int, fn: Callable,
                 *args: Any) -> Optional[str]:
        with self._lock:
            if self._closed:
                return None
            old = self._timers.pop(name, None)
        if old:
            with suppress(Exception):
                self._widget.after_cancel(old)
        try:
            holder: Dict[str, Any] = {}

            def runner() -> None:
                with self._lock:
                    self._timers.pop(name, None)
                if self._closed:
                    return
                with suppress(Exception):
                    fn(*args)

            holder["id"] = self._widget.after(ms, runner)
            with self._lock:
                self._timers[name] = holder["id"]
            return holder["id"]
        except Exception:
            return None

    def cancel(self, name: str) -> None:
        with self._lock:
            tid = self._timers.pop(name, None)
        if tid:
            with suppress(Exception):
                self._widget.after_cancel(tid)

    def cancel_all(self) -> None:
        with self._lock:
            self._closed = True
            tids = list(self._timers.values())
            self._timers.clear()
        for tid in tids:
            with suppress(Exception):
                self._widget.after_cancel(tid)


# ═══════════════════════════════════════════════════════════
# HTTP с retry
# ═══════════════════════════════════════════════════════════
def http_with_retry(
    url: str, data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST", timeout: int = 5,
    max_response: int = 65536,
) -> Tuple[bool, Optional[int], Optional[str]]:
    import random
    last_error: Optional[str] = None
    for attempt in range(HTTP_RETRY_MAX):
        try:
            req = urllib.request.Request(
                url, data=data, headers=headers or {},
                method=method)
            with urllib.request.urlopen(
                    req, timeout=timeout) as resp:
                with suppress(Exception):
                    resp.read(max_response)
                return True, resp.status, None
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            if 400 <= e.code < 500:
                return False, e.code, last_error
        except Exception as e:
            last_error = str(e)
        if attempt < HTTP_RETRY_MAX - 1:
            base = min(
                HTTP_RETRY_BACKOFF * (2 ** attempt), 10.0)
            time.sleep(base * random.uniform(0.5, 1.5))
    return False, None, last_error


# ═══════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════
class CircuitBreaker:
    def __init__(self, threshold: int = 5,
                 timeout: int = 300) -> None:
        self._failures: Dict[str, int] = {}
        self._opened_at: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._threshold = threshold
        self._timeout = timeout

    def is_open(self, key: str) -> bool:
        with self._lock:
            if key not in self._opened_at:
                return False
            if time.time() - self._opened_at[key] > \
                    self._timeout:
                self._opened_at.pop(key, None)
                self._failures.pop(key, None)
                return False
            return True

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key] = \
                self._failures.get(key, 0) + 1
            if self._failures[key] >= self._threshold:
                if key not in self._opened_at:
                    logger.warning(
                        f"Circuit breaker OPEN: {key}")
                self._opened_at[key] = time.time()


circuit_breaker = CircuitBreaker()


# ═══════════════════════════════════════════════════════════
# ОБЯЗАТЕЛЬНЫЕ КОЛОНКИ ПО ТАБЛИЦАМ
# ═══════════════════════════════════════════════════════════
_REQUIRED_COLUMNS: Final[Dict[str, List[Tuple[str, str]]]] = {
    "users": [
        ("username", "TEXT"), ("email", "TEXT"),
        ("password", "TEXT"), ("avatar", "TEXT"),
        ("bio", "TEXT"), ("created_at", "TEXT"),
        ("last_active", "TEXT"), ("preferences", "TEXT"),
        ("is_admin", "INTEGER"),
        ("recovery_phrase_hash", "TEXT"),
        ("recovery_phrase_created", "TEXT"),
    ],
    "notes": [
        ("id", "TEXT"), ("title", "TEXT"),
        ("content", "TEXT"), ("author", "TEXT"),
        ("tags", "TEXT"), ("category", "TEXT"),
        ("created_at", "TEXT"), ("updated_at", "TEXT"),
        ("favorite", "INTEGER"), ("views", "INTEGER"),
        ("pinned", "INTEGER"), ("status", "TEXT"),
        ("last_viewed", "TEXT"), ("simhash", "INTEGER"),
        ("decay_score", "REAL"), ("resurrected_at", "TEXT"),
        ("encrypted", "INTEGER"), ("parent_id", "TEXT"),
        ("links_count", "INTEGER"), ("deleted_at", "TEXT"),
        ("autolinks", "TEXT"),
    ],
    "tasks": [
        ("id", "TEXT"), ("title", "TEXT"),
        ("description", "TEXT"), ("priority", "TEXT"),
        ("status", "TEXT"), ("author", "TEXT"),
        ("tags", "TEXT"), ("category", "TEXT"),
        ("deadline", "TEXT"), ("created_at", "TEXT"),
        ("updated_at", "TEXT"), ("done_at", "TEXT"),
        ("favorite", "INTEGER"), ("progress", "INTEGER"),
        ("time_spent", "INTEGER"), ("parent_id", "TEXT"),
        ("repeat", "TEXT"), ("timer_started", "TEXT"),
        ("deleted_at", "TEXT"),
    ],
    "subtasks": [
        ("id", "TEXT"), ("parent_task_id", "TEXT"),
        ("title", "TEXT"), ("done", "INTEGER"),
        ("order_idx", "INTEGER"), ("created_at", "TEXT"),
        ("done_at", "TEXT"),
    ],
    "forum_posts": [
        ("id", "TEXT"), ("title", "TEXT"),
        ("content", "TEXT"), ("author", "TEXT"),
        ("tags", "TEXT"), ("category", "TEXT"),
        ("created_at", "TEXT"), ("updated_at", "TEXT"),
        ("favorite", "INTEGER"), ("views", "INTEGER"),
        ("likes", "INTEGER"), ("liked_by", "TEXT"),
        ("pinned", "INTEGER"), ("comments", "TEXT"),
        ("status", "TEXT"),
    ],
    "code_history": [
        ("id", "TEXT"), ("user", "TEXT"), ("code", "TEXT"),
        ("output", "TEXT"), ("success", "INTEGER"),
        ("duration", "REAL"), ("created_at", "TEXT"),
        ("stderr", "TEXT"),
    ],
    "sessions": [
        ("token", "TEXT"), ("username", "TEXT"),
        ("created_at", "TEXT"), ("expires_at", "TEXT"),
        ("ip_address", "TEXT"), ("user_agent", "TEXT"),
        ("machine_id", "TEXT"),
    ],
    "user_stats": [
        ("user", "TEXT"), ("level", "INTEGER"),
        ("xp", "INTEGER"), ("xp_next", "INTEGER"),
        ("tasks_completed", "INTEGER"),
        ("notes_created", "INTEGER"),
        ("posts_created", "INTEGER"),
        ("blocks_created", "INTEGER"),
        ("blocks_edited", "INTEGER"),
        ("blocks_deleted", "INTEGER"),
        ("qr_generated", "INTEGER"),
        ("code_executed", "INTEGER"),
        ("snippets_saved", "INTEGER"),
        ("streak", "INTEGER"), ("last_visit", "TEXT"),
        ("attachments_added", "INTEGER"),
        ("likes_received", "INTEGER"),
        ("comments_count", "INTEGER"),
        ("total_time", "INTEGER"),
        ("sessions_count", "INTEGER"),
        ("memory_hints_shown", "INTEGER"),
        ("notes_resurrected", "INTEGER"),
        ("subtasks_completed", "INTEGER"),
        ("timers_started", "INTEGER"),
        ("blocks_signed", "INTEGER"),
        ("quests_completed", "INTEGER"),
        ("pomodoro_sessions", "INTEGER"),
        ("clipboard_scans", "INTEGER"),
        ("code_compiles", "INTEGER"),
    ],
    "qr_history": [
        ("id", "TEXT"), ("user", "TEXT"), ("data", "TEXT"),
        ("style", "TEXT"), ("size", "INTEGER"),
        ("hash", "TEXT"), ("created_at", "TEXT"),
        ("mode", "TEXT"),
    ],
    "api_tokens": [
        ("id", "TEXT"), ("user", "TEXT"),
        ("token_hash", "TEXT"), ("name", "TEXT"),
        ("scopes", "TEXT"), ("created_at", "TEXT"),
        ("expires_at", "TEXT"), ("last_used", "TEXT"),
        ("revoked", "INTEGER"),
    ],
    "page_cache": [
        ("page", "TEXT"), ("rendered_at", "TEXT"),
        ("hits", "INTEGER"), ("hits_lifetime", "INTEGER"),
    ],
}


# ═══════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════
SCHEMA_VERSION: Final[int] = 4


class Database:
    _instance: Optional["Database"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "Database":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._fts_ready: bool = False
        self._checkpoint_counter: int = 0
        self._checkpoint_lock = threading.Lock()
        self._initialized: bool = False

    def initialize(self, *, with_fts: bool = True,
                    with_migrations: bool = True) -> None:
        if self._initialized:
            return
        self._init_tables()
        self._create_indexes()
        if with_fts:
            self._init_fts()
        if with_migrations:
            self._apply_migrations()
        self._initialized = True
        logger.info(
            f"Database ready: {DB_PATH} "
            f"(schema v{self.schema_version()})")

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(
                str(DB_PATH), check_same_thread=False,
                timeout=30)
            c.row_factory = sqlite3.Row
            for pragma in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA cache_size=-64000",
                "PRAGMA foreign_keys=ON",
                "PRAGMA temp_store=MEMORY",
                "PRAGMA busy_timeout=30000",
                "PRAGMA wal_autocheckpoint=1000",
            ):
                with suppress(sqlite3.Error):
                    c.execute(pragma)
            self._local.conn = c
        return c

    def _init_tables(self) -> None:
        schemas = [
            """CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                avatar TEXT DEFAULT '🧠',
                bio TEXT DEFAULT '',
                created_at TEXT,
                last_active TEXT,
                preferences TEXT DEFAULT '{}',
                is_admin INTEGER DEFAULT 0,
                recovery_phrase_hash TEXT DEFAULT '',
                recovery_phrase_created TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                author TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                category TEXT DEFAULT 'Общее',
                created_at TEXT, updated_at TEXT,
                favorite INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                last_viewed TEXT,
                simhash INTEGER DEFAULT 0,
                decay_score REAL DEFAULT 1.0,
                resurrected_at TEXT,
                encrypted INTEGER DEFAULT 0,
                parent_id TEXT DEFAULT '',
                links_count INTEGER DEFAULT 0,
                deleted_at TEXT DEFAULT '',
                autolinks TEXT DEFAULT '[]')""",
            """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'pending',
                author TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                category TEXT DEFAULT 'Общее',
                deadline TEXT, created_at TEXT,
                updated_at TEXT, done_at TEXT,
                favorite INTEGER DEFAULT 0,
                progress INTEGER DEFAULT 0,
                time_spent INTEGER DEFAULT 0,
                parent_id TEXT DEFAULT '',
                repeat TEXT DEFAULT '',
                timer_started TEXT DEFAULT '',
                deleted_at TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS subtasks (
                id TEXT PRIMARY KEY,
                parent_task_id TEXT NOT NULL,
                title TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                order_idx INTEGER DEFAULT 0,
                created_at TEXT, done_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS forum_posts (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                author TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                category TEXT DEFAULT 'Общее',
                created_at TEXT, updated_at TEXT,
                favorite INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                liked_by TEXT DEFAULT '[]',
                pinned INTEGER DEFAULT 0,
                comments TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active')""",
            """CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                owner TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                type TEXT DEFAULT 'file',
                ref_type TEXT, ref_id TEXT,
                created_at TEXT,
                downloads INTEGER DEFAULT 0,
                description TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS achievements (
                id TEXT PRIMARY KEY,
                user TEXT NOT NULL,
                achievement TEXT NOT NULL,
                unlocked_at TEXT,
                UNIQUE(user, achievement))""",
            """CREATE TABLE IF NOT EXISTS user_stats (
                user TEXT PRIMARY KEY,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                xp_next INTEGER DEFAULT 100,
                tasks_completed INTEGER DEFAULT 0,
                notes_created INTEGER DEFAULT 0,
                posts_created INTEGER DEFAULT 0,
                blocks_created INTEGER DEFAULT 0,
                blocks_edited INTEGER DEFAULT 0,
                blocks_deleted INTEGER DEFAULT 0,
                qr_generated INTEGER DEFAULT 0,
                code_executed INTEGER DEFAULT 0,
                snippets_saved INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_visit TEXT,
                attachments_added INTEGER DEFAULT 0,
                likes_received INTEGER DEFAULT 0,
                comments_count INTEGER DEFAULT 0,
                total_time INTEGER DEFAULT 0,
                sessions_count INTEGER DEFAULT 0,
                memory_hints_shown INTEGER DEFAULT 0,
                notes_resurrected INTEGER DEFAULT 0,
                subtasks_completed INTEGER DEFAULT 0,
                timers_started INTEGER DEFAULT 0,
                blocks_signed INTEGER DEFAULT 0,
                quests_completed INTEGER DEFAULT 0,
                pomodoro_sessions INTEGER DEFAULT 0,
                clipboard_scans INTEGER DEFAULT 0,
                code_compiles INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS qr_history (
                id TEXT PRIMARY KEY,
                user TEXT NOT NULL,
                data TEXT, style TEXT, size INTEGER,
                hash TEXT, created_at TEXT,
                mode TEXT DEFAULT 'text')""",
            """CREATE TABLE IF NOT EXISTS code_history (
                id TEXT PRIMARY KEY,
                user TEXT NOT NULL,
                code TEXT, output TEXT,
                success INTEGER, duration REAL,
                created_at TEXT,
                stderr TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT, expires_at TEXT,
                ip_address TEXT, user_agent TEXT,
                machine_id TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS rolling_sessions (
                username TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL,
                created_at TEXT, last_used TEXT)""",
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT, action TEXT, details TEXT,
                created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS audit_trail (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                diff TEXT DEFAULT '{}',
                user TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT, ip TEXT, success INTEGER,
                created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS daily_activity (
                user TEXT NOT NULL, day TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user, day))""",
            """CREATE TABLE IF NOT EXISTS hourly_activity (
                user TEXT NOT NULL, day TEXT NOT NULL,
                hour INTEGER NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user, day, hour))""",
            """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY,
                user TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                name TEXT DEFAULT '',
                scopes TEXT DEFAULT '[]',
                created_at TEXT, expires_at TEXT,
                last_used TEXT,
                revoked INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS page_cache (
                page TEXT PRIMARY KEY,
                rendered_at TEXT,
                hits INTEGER DEFAULT 0,
                hits_lifetime INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS webhook_queue (
                id TEXT PRIMARY KEY,
                webhook_id TEXT NOT NULL,
                url TEXT NOT NULL,
                event TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                attempts INTEGER DEFAULT 0,
                next_try_at TEXT,
                last_error TEXT DEFAULT '',
                created_at TEXT,
                delivered_at TEXT DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS daily_quests (
                user TEXT NOT NULL, day TEXT NOT NULL,
                quest_id TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                done_at TEXT DEFAULT '',
                PRIMARY KEY (user, day, quest_id))""",
            """CREATE TABLE IF NOT EXISTS qr_dynamic (
                id TEXT PRIMARY KEY,
                user TEXT NOT NULL,
                target TEXT NOT NULL,
                created_at TEXT,
                hits INTEGER DEFAULT 0,
                last_hit TEXT DEFAULT '')""",
        ]
        with self._write_lock:
            cur = self.conn.cursor()
            for sql in schemas:
                try:
                    cur.execute(sql)
                except sqlite3.Error as e:
                    logger.error(f"CREATE TABLE: {e}")
            self.conn.commit()
        with self._write_lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO schema_version "
                "VALUES (?, ?)", (1, now_iso()))
            self.conn.commit()

    def _create_indexes(self) -> None:
        idx = [
            "CREATE INDEX IF NOT EXISTS idx_notes_author ON notes(author)",
            "CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status)",
            "CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notes_deleted ON notes(deleted_at)",
            "CREATE INDEX IF NOT EXISTS idx_notes_decay ON notes(decay_score)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_author ON tasks(author)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at)",
            "CREATE INDEX IF NOT EXISTS idx_subtasks_parent ON subtasks(parent_task_id)",
            "CREATE INDEX IF NOT EXISTS idx_ach_user ON achievements(user)",
            "CREATE INDEX IF NOT EXISTS idx_daily_user ON daily_activity(user)",
            "CREATE INDEX IF NOT EXISTS idx_hourly_user ON hourly_activity(user)",
            "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user)",
            "CREATE INDEX IF NOT EXISTS idx_audit_trail_entity ON audit_trail(entity_type, entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_qr_user ON qr_history(user)",
            "CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user)",
            "CREATE INDEX IF NOT EXISTS idx_webhook_queue_next ON webhook_queue(next_try_at)",
            "CREATE INDEX IF NOT EXISTS idx_quests_user_day ON daily_quests(user, day)",
            "CREATE INDEX IF NOT EXISTS idx_qr_dyn_user ON qr_dynamic(user)",
        ]
        with self._write_lock:
            cur = self.conn.cursor()
            for sql in idx:
                try:
                    cur.execute(sql)
                except sqlite3.Error as e:
                    logger.debug(f"CREATE INDEX: {e}")
            self.conn.commit()

    def _init_fts(self) -> None:
        try:
            with self._write_lock:
                cur = self.conn.cursor()
                cur.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                    "USING fts5(title, content, tags, "
                    "content='notes', content_rowid='rowid', "
                    "tokenize='unicode61 remove_diacritics 2')")
                for trig in (
                    """CREATE TRIGGER IF NOT EXISTS notes_ai
                       AFTER INSERT ON notes BEGIN
                       INSERT INTO notes_fts(rowid, title, content, tags)
                       VALUES (new.rowid, new.title, new.content, new.tags);
                       END""",
                    """CREATE TRIGGER IF NOT EXISTS notes_ad
                       AFTER DELETE ON notes BEGIN
                       INSERT INTO notes_fts(notes_fts, rowid, title, content, tags)
                       VALUES ('delete', old.rowid, old.title, old.content, old.tags);
                       END""",
                    """CREATE TRIGGER IF NOT EXISTS notes_au
                       AFTER UPDATE ON notes BEGIN
                       INSERT INTO notes_fts(notes_fts, rowid, title, content, tags)
                       VALUES ('delete', old.rowid, old.title, old.content, old.tags);
                       INSERT INTO notes_fts(rowid, title, content, tags)
                       VALUES (new.rowid, new.title, new.content, new.tags);
                       END""",
                ):
                    try:
                        cur.execute(trig)
                    except sqlite3.Error as e:
                        logger.debug(f"FTS trigger: {e}")
                row = cur.execute(
                    "SELECT COUNT(*) FROM notes_fts").fetchone()
                if row and row[0] == 0:
                    cnt = cur.execute(
                        "SELECT COUNT(*) FROM notes").fetchone()
                    if cnt and cnt[0] > 0:
                        cur.execute(
                            "INSERT INTO notes_fts(notes_fts) "
                            "VALUES('rebuild')")
                self.conn.commit()
                self._fts_ready = True
                logger.info("FTS5 initialized")
        except Exception as e:
            logger.warning(
                f"FTS5 недоступен, fallback на LIKE: {e}")
            self._fts_ready = False

    # ─────────────────────────────────────────────────
    # MIGRATIONS
    # ─────────────────────────────────────────────────
    def _find_missing_columns(self) -> Dict[str, List[Tuple[str, str]]]:
        missing: Dict[str, List[Tuple[str, str]]] = {}
        for table, required_cols in _REQUIRED_COLUMNS.items():
            try:
                rows = self.conn.execute(
                    f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    continue
                existing = {r[1] for r in rows}
                table_missing = [
                    (col, typ) for col, typ in required_cols
                    if col not in existing
                ]
                if table_missing:
                    missing[table] = table_missing
            except sqlite3.Error as e:
                logger.debug(f"PRAGMA {table}: {e}")
        return missing

    def _apply_migrations(self) -> None:
        try:
            current = self.schema_version()
            target = SCHEMA_VERSION
            logger.info(
                f"Schema check: current v{current}, "
                f"target v{target}")

            if current < target:
                for version in range(current + 1, target + 1):
                    fn = getattr(
                        self, f"_migrate_to_v{version}", None)
                    if fn is None:
                        logger.debug(
                            f"Migration v{version} "
                            f"не найдена — skip")
                        continue
                    logger.info(
                        f"Applying migration v{version}")
                    try:
                        fn()
                        with self._write_lock:
                            cur = self.conn.cursor()
                            cur.execute(
                                "INSERT OR REPLACE INTO "
                                "schema_version VALUES (?, ?)",
                                (version, now_iso()))
                            self.conn.commit()
                        logger.info(
                            f"Migration v{version} OK")
                    except Exception as e:
                        logger.error(
                            f"Migration v{version} "
                            f"failed: {e}")

            missing = self._find_missing_columns()
            if missing:
                logger.warning(
                    f"Обнаружены отсутствующие колонки "
                    f"в {len(missing)} таблицах — "
                    f"форсирую миграцию v4")
                for table, cols in missing.items():
                    logger.warning(
                        f"  {table}: {[c for c,_ in cols]}")
                self._migrate_to_v4(force=True)
            else:
                logger.info("Все колонки на месте ✅")

            logger.info("Migrations complete ✅")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def _migrate_to_v2(self) -> None:
        self._add_missing_columns([
            ("notes", "autolinks", "TEXT DEFAULT '[]'"),
            ("notes", "decay_score", "REAL DEFAULT 1.0"),
            ("code_history", "stderr", "TEXT DEFAULT ''"),
            ("sessions", "machine_id", "TEXT DEFAULT ''"),
            ("user_stats", "quests_completed",
             "INTEGER DEFAULT 0"),
            ("user_stats", "pomodoro_sessions",
             "INTEGER DEFAULT 0"),
            ("user_stats", "clipboard_scans",
             "INTEGER DEFAULT 0"),
        ])

    def _migrate_to_v3(self) -> None:
        self._add_missing_columns([
            ("page_cache", "hits_lifetime",
             "INTEGER DEFAULT 0"),
            ("user_stats", "code_compiles",
             "INTEGER DEFAULT 0"),
        ])

    def _migrate_to_v4(self, force: bool = False) -> None:
        columns: List[Tuple[str, str, str]] = []
        for table, cols in _REQUIRED_COLUMNS.items():
            for col, typ in cols:
                spec = self._default_spec(col, typ)
                columns.append((table, col, spec))

        extra = [
            ("notes", "favorite", "INTEGER DEFAULT 0"),
            ("notes", "views", "INTEGER DEFAULT 0"),
            ("notes", "pinned", "INTEGER DEFAULT 0"),
            ("tasks", "favorite", "INTEGER DEFAULT 0"),
            ("tasks", "progress", "INTEGER DEFAULT 0"),
            ("tasks", "repeat", "TEXT DEFAULT ''"),
            ("tasks", "timer_started", "TEXT DEFAULT ''"),
            ("user_stats", "sessions_count",
             "INTEGER DEFAULT 0"),
            ("user_stats", "comments_count",
             "INTEGER DEFAULT 0"),
            ("user_stats", "total_time", "INTEGER DEFAULT 0"),
            ("user_stats", "memory_hints_shown",
             "INTEGER DEFAULT 0"),
            ("user_stats", "blocks_edited",
             "INTEGER DEFAULT 0"),
            ("user_stats", "likes_received",
             "INTEGER DEFAULT 0"),
        ]
        columns.extend(extra)
        self._add_missing_columns(columns)

    @staticmethod
    def _default_spec(col: str, typ: str) -> str:
        defaults: Dict[str, str] = {
            "level": "1", "xp": "0", "xp_next": "100",
            "decay_score": "1.0", "progress": "0",
            "size": "0", "hits": "0", "hits_lifetime": "0",
            "revoked": "0", "is_admin": "0", "favorite": "0",
            "views": "0", "pinned": "0", "encrypted": "0",
            "links_count": "0", "downloads": "0",
            "order_idx": "0", "simhash": "0", "done": "0",
            "success": "0", "duration": "0.0",
            "attempts": "0", "count": "0", "streak": "0",
            "sessions_count": "0", "comments_count": "0",
            "total_time": "0", "memory_hints_shown": "0",
            "notes_resurrected": "0",
            "subtasks_completed": "0",
            "timers_started": "0", "blocks_signed": "0",
            "quests_completed": "0",
            "pomodoro_sessions": "0",
            "clipboard_scans": "0",
            "code_compiles": "0",
            "tasks_completed": "0", "notes_created": "0",
            "posts_created": "0", "blocks_created": "0",
            "blocks_edited": "0", "blocks_deleted": "0",
            "qr_generated": "0", "code_executed": "0",
            "snippets_saved": "0",
            "attachments_added": "0",
            "likes_received": "0",
        }
        if typ.startswith("INTEGER"):
            default = defaults.get(col, "0")
            return f"INTEGER DEFAULT {default}"
        if typ.startswith("REAL"):
            default = defaults.get(col, "0.0")
            return f"REAL DEFAULT {default}"
        if col == "status":
            return "TEXT DEFAULT 'active'"
        if col == "priority":
            return "TEXT DEFAULT 'normal'"
        if col == "category":
            return "TEXT DEFAULT 'Общее'"
        if col in ("tags", "liked_by", "comments", "autolinks"):
            return "TEXT DEFAULT '[]'"
        if col in ("preferences", "scopes", "payload", "diff"):
            return "TEXT DEFAULT '{}'"
        if col == "mode":
            return "TEXT DEFAULT 'text'"
        if col == "avatar":
            return "TEXT DEFAULT '🧠'"
        return "TEXT DEFAULT ''"

    def _add_missing_columns(
        self, columns: List[Tuple[str, str, str]]
    ) -> None:
        with self._write_lock:
            cur = self.conn.cursor()
            added = 0
            skipped = 0
            for table, col, spec in columns:
                try:
                    exists = cur.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name=?",
                        (table,)).fetchone()
                    if not exists:
                        skipped += 1
                        continue
                    existing = cur.execute(
                        f"PRAGMA table_info({table})").fetchall()
                    cols = {r[1] for r in existing}
                    if col in cols:
                        skipped += 1
                        continue
                    cur.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {col} {spec}")
                    logger.info(
                        f"Migration: +{table}.{col} {spec}")
                    added += 1
                except sqlite3.Error as e:
                    logger.error(
                        f"Migration {table}.{col}: {e}")
                    skipped += 1
            self.conn.commit()
            if added:
                logger.info(
                    f"Migration: added={added}, "
                    f"skipped={skipped}")

    def schema_version(self) -> int:
        try:
            rows = self.execute(
                "SELECT MAX(version) AS v FROM schema_version")
            if rows and rows[0]["v"] is not None:
                return int(rows[0]["v"])
        except Exception:
            pass
        return 0

    def rebuild_fts(self) -> bool:
        if not self._fts_ready:
            return False
        try:
            with self._write_lock:
                self.conn.execute(
                    "INSERT INTO notes_fts(notes_fts) "
                    "VALUES('rebuild')")
                self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"rebuild_fts: {e}")
            return False

    def search_notes_fts(
        self, query: str,
        author: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        if not self._fts_ready:
            return self._search_notes_like(query, author, limit)
        safe_q = re.sub(r'[^\w\sа-яА-ЯёЁ]', " ", query)
        terms = [w for w in safe_q.split() if len(w) >= 2]
        if not terms:
            return []
        match_expr = " ".join(f'"{t}"*' for t in terms)
        try:
            if author:
                rows = self.execute(
                    "SELECT n.*, bm25(notes_fts) AS rank "
                    "FROM notes_fts "
                    "JOIN notes n ON n.rowid = notes_fts.rowid "
                    "WHERE notes_fts MATCH ? "
                    "  AND n.status != 'deleted' "
                    "  AND n.deleted_at = '' "
                    "  AND n.author = ? "
                    "ORDER BY rank LIMIT ?",
                    (match_expr, author, int(limit)))
            else:
                rows = self.execute(
                    "SELECT n.*, bm25(notes_fts) AS rank "
                    "FROM notes_fts "
                    "JOIN notes n ON n.rowid = notes_fts.rowid "
                    "WHERE notes_fts MATCH ? "
                    "  AND n.status != 'deleted' "
                    "  AND n.deleted_at = '' "
                    "ORDER BY rank LIMIT ?",
                    (match_expr, int(limit)))
            return [dict(r) for r in (rows or [])]
        except Exception as e:
            logger.debug(f"FTS search: {e}")
            return self._search_notes_like(query, author, limit)

    def _search_notes_like(
        self, query: str, author: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        like = f"%{query.strip()}%"
        try:
            if author:
                rows = self.execute(
                    "SELECT * FROM notes WHERE author = ? "
                    "  AND deleted_at = '' "
                    "  AND status != 'deleted' "
                    "  AND (LOWER(title) LIKE LOWER(?) OR "
                    "       LOWER(content) LIKE LOWER(?)) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (author, like, like, int(limit)))
            else:
                rows = self.execute(
                    "SELECT * FROM notes WHERE deleted_at = '' "
                    "  AND status != 'deleted' "
                    "  AND (LOWER(title) LIKE LOWER(?) OR "
                    "       LOWER(content) LIKE LOWER(?)) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (like, like, int(limit)))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    def health_check(self) -> Tuple[bool, str]:
        try:
            rows = self.execute("PRAGMA integrity_check")
            if not rows:
                return False, "no response"
            result = rows[0][0] if rows else "unknown"
            return (True, "OK") if result == "ok" \
                else (False, str(result))
        except Exception as e:
            return False, str(e)

    def _maybe_checkpoint(self, is_write: bool = False) -> None:
        if not is_write:
            return
        with self._checkpoint_lock:
            self._checkpoint_counter += 1
            if self._checkpoint_counter < WAL_CHECKPOINT_INTERVAL:
                return
            self._checkpoint_counter = 0
        try:
            with self._write_lock:
                self.conn.execute(
                    "PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    def execute(self, sql: str, params: tuple = (),
                retries: int = 3) -> Optional[List[sqlite3.Row]]:
        """
        Универсальный execute.

        FIX v4.1: BEGIN/COMMIT вокруг SELECT — форсирует
        свежий read snapshot в thread-local соединении.
        Без этого HTTP-поток может не видеть коммиты
        из main thread (даже в WAL mode), из-за чего
        API возвращал 401 Unauthorized.
        """
        is_select = sql.lstrip().upper().startswith("SELECT")
        is_pragma = sql.lstrip().upper().startswith("PRAGMA")
        for attempt in range(retries):
            try:
                cur = self.conn.cursor()
                # FIX: свежий snapshot для SELECT
                if is_select:
                    with suppress(sqlite3.OperationalError):
                        cur.execute("BEGIN")
                cur.execute(sql, params)
                if is_select or is_pragma:
                    rows = cur.fetchall()
                    if is_select:
                        with suppress(sqlite3.OperationalError):
                            cur.execute("COMMIT")
                    return rows
                self._maybe_checkpoint(is_write=True)
                return []
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() \
                        and attempt < retries - 1:
                    time.sleep(0.05 * (2 ** attempt))
                    continue
                logger.error(
                    f"DB error: {e} | SQL: {sql[:120]}")
                return None
            except Exception as e:
                logger.error(
                    f"DB error: {e} | SQL: {sql[:120]}")
                return None
        return None

    @contextmanager
    def transaction(self) -> Iterable[sqlite3.Cursor]:
        self._write_lock.acquire()
        try:
            cur = self.conn.cursor()
            yield cur
            self.conn.commit()
        except Exception:
            with suppress(Exception):
                self.conn.rollback()
            raise
        finally:
            self._write_lock.release()

    def backup(self) -> Optional[Path]:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = BACKUP_DIR / f"nexus_{ts}.db"
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            with self._write_lock:
                with suppress(Exception):
                    self.conn.execute(
                        "PRAGMA wal_checkpoint(FULL)")
                src = sqlite3.connect(str(DB_PATH))
                dc = sqlite3.connect(str(dst))
                try:
                    with dc:
                        src.backup(dc)
                finally:
                    src.close()
                    dc.close()
            backups = sorted(
                BACKUP_DIR.glob("nexus_*.db"),
                key=lambda p: p.stat().st_mtime)
            for old in backups[:-MAX_BACKUPS]:
                with suppress(Exception):
                    old.unlink()
            return dst
        except Exception as e:
            logger.error(f"Backup: {e}")
            return None

    def incremental_backup(self) -> Optional[Path]:
        try:
            INCREMENTAL_BACKUP_DIR.mkdir(
                parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = INCREMENTAL_BACKUP_DIR / f"nexus_{ts}.sql"
            with self._write_lock:
                dump_lines: List[str] = []
                cur = self.conn.cursor()
                tables = [r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'").fetchall()]
                for t in tables:
                    rows = cur.execute(
                        f"SELECT * FROM {t}").fetchall()
                    for r in rows:
                        vals = []
                        for v in r:
                            if v is None:
                                vals.append("NULL")
                            elif isinstance(v, (int, float)):
                                vals.append(str(v))
                            else:
                                esc = str(v).replace(
                                    "'", "''")
                                vals.append(f"'{esc}'")
                        dump_lines.append(
                            f"INSERT INTO {t} VALUES "
                            f"({','.join(vals)});")
            dst.write_text(
                f"-- NEXUS incremental backup {ts}\n"
                + "\n".join(dump_lines), encoding="utf-8")
            files = sorted(
                INCREMENTAL_BACKUP_DIR.glob("nexus_*.sql"),
                key=lambda p: p.stat().st_mtime)
            for old in files[:-10]:
                with suppress(Exception):
                    old.unlink()
            return dst
        except Exception as e:
            logger.error(f"incremental_backup: {e}")
            return None

    def audit(self, user: Optional[str], action: str,
              details: str = "") -> None:
        try:
            self.execute(
                "INSERT INTO audit_log (user, action, details, "
                "created_at) VALUES (?, ?, ?, ?)",
                (user or "anonymous", action,
                 truncate(details, 500), now_iso()))
            day = (datetime.now(timezone.utc)
                    .date().isoformat())
            hour = datetime.now(timezone.utc).hour
            if user:
                existing = self.execute(
                    "SELECT count FROM daily_activity "
                    "WHERE user = ? AND day = ?", (user, day))
                if existing:
                    self.execute(
                        "UPDATE daily_activity "
                        "SET count = count + 1 "
                        "WHERE user = ? AND day = ?",
                        (user, day))
                else:
                    self.execute(
                        "INSERT INTO daily_activity "
                        "(user, day, count) VALUES (?, ?, 1)",
                        (user, day))
                existing_h = self.execute(
                    "SELECT count FROM hourly_activity "
                    "WHERE user = ? AND day = ? AND hour = ?",
                    (user, day, hour))
                if existing_h:
                    self.execute(
                        "UPDATE hourly_activity "
                        "SET count = count + 1 "
                        "WHERE user = ? AND day = ? AND hour = ?",
                        (user, day, hour))
                else:
                    self.execute(
                        "INSERT INTO hourly_activity "
                        "(user, day, hour, count) "
                        "VALUES (?, ?, ?, 1)",
                        (user, day, hour))
        except Exception:
            pass

    def check_rate_limit(
        self, username: str
    ) -> Tuple[bool, int]:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(
                      seconds=LOGIN_RATE_WINDOW)).isoformat()
        rows = self.execute(
            "SELECT COUNT(*) as c FROM login_attempts "
            "WHERE username = ? AND success = 0 "
            "  AND created_at > ?",
            (username, cutoff))
        count = rows[0]["c"] if rows else 0
        if count >= LOGIN_RATE_LIMIT:
            return False, LOGIN_RATE_WINDOW
        return True, 0

    def record_login_attempt(self, username: str,
                              success: bool) -> None:
        with suppress(Exception):
            self.execute(
                "INSERT INTO login_attempts "
                "(username, ip, success, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, "local", 1 if success else 0,
                 now_iso()))

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c:
            with suppress(Exception):
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
            self._local.conn = None

    def stats(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        try:
            for tbl in ("users", "notes", "tasks",
                         "forum_posts", "attachments",
                         "audit_log", "sessions"):
                rows = self.execute(
                    f"SELECT COUNT(*) AS c FROM {tbl}")
                result[tbl] = rows[0]["c"] if rows else 0
            if DB_PATH.exists():
                result["db_size"] = DB_PATH.stat().st_size
                wal = DB_PATH.with_suffix(".db-wal")
                if wal.exists():
                    result["wal_size"] = wal.stat().st_size
        except Exception:
            pass
        return result

    def fts_available(self) -> bool:
        return self._fts_ready


db = Database()


# ═══════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════
def _self_test() -> bool:
    print("Storage self-test...")
    try:
        print("  init...")
        db.initialize()
        print(f"  ✅ schema v{db.schema_version()}")
        print(f"  ✅ FTS5: {db.fts_available()}")

        missing = db._find_missing_columns()
        if missing:
            print("  ❌ ОТСУТСТВУЮТ КОЛОНКИ:")
            for table, cols in missing.items():
                print(f"     {table}: {[c for c, _ in cols]}")
            return False
        print("  ✅ все колонки на месте")

        print("  INSERT notes...")
        db.execute(
            "DELETE FROM notes WHERE id = ?", ("_selftest_n",))
        db.execute(
            "INSERT INTO notes "
            "(id, title, content, author, tags, category, "
            " created_at, updated_at, simhash, decay_score, "
            " autolinks, deleted_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("_selftest_n", "t", "c", "_selftest",
             "[]", "Общее", now_iso(), now_iso(), 0,
             1.0, "[]", "", "active"))
        rows = db.execute(
            "SELECT id FROM notes WHERE id = ?",
            ("_selftest_n",))
        assert rows, "INSERT notes не сработал"
        print("  ✅ INSERT notes OK")

        print("  INSERT tasks...")
        db.execute(
            "DELETE FROM tasks WHERE id = ?", ("_selftest_t",))
        db.execute(
            "INSERT INTO tasks "
            "(id, title, description, priority, status, "
            " author, tags, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("_selftest_t", "t", "", "normal", "pending",
             "_selftest", "[]", "Общее",
             now_iso(), now_iso()))
        rows = db.execute(
            "SELECT id FROM tasks WHERE id = ?",
            ("_selftest_t",))
        assert rows, "INSERT tasks не сработал"
        print("  ✅ INSERT tasks OK")

        db.execute("DELETE FROM notes WHERE id = ?",
                   ("_selftest_n",))
        db.execute("DELETE FROM tasks WHERE id = ?",
                   ("_selftest_t",))
        print("  ✅ cleanup")
        print("✅ PASS")
        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ FAIL: {e}")
        return False


__all__ = [
    "Store", "TTLCache", "cache", "MRU", "mru",
    "SafeExecutor", "executor", "ToastDedup", "toast_dedup",
    "EventBus", "event_bus", "SecureString", "UndoStack",
    "undo_stack", "TimerPool", "http_with_retry",
    "CircuitBreaker", "circuit_breaker",
    "Database", "db", "SCHEMA_VERSION",
    "_REQUIRED_COLUMNS",
]


if __name__ == "__main__":
    import sys
    ok = _self_test()
    sys.exit(0 if ok else 1)