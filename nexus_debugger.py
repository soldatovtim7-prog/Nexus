#!/usr/bin/env python3
# ============================================================
# NEXUS DEBUGGER — пошаговый отладчик для Code Studio
# ============================================================
# Возможности:
#   • Точки останова (клик по номеру строки / Ctrl+B)
#   • Step Over / Step Into / Step Out / Continue / Stop
#   • Live-preview переменных (locals + globals)
#   • Стек вызовов
#   • Вычисление выражений в контексте остановки
#   • Работает через subprocess (изолированный от GUI)
#   • Безопасность: не ломает sandbox, не даёт доступ к
#     реальным объектам — только JSON-сериализация
#
# Подключение в nexus.py:
#   import nexus_debugger
#   nexus_debugger.attach(app, globals())
#
# Использование:
#   Ctrl+Shift+D — открыть панель отладчика
#   F9 — toggle breakpoint на текущей строке
#   F5 — start/continue
#   F10 — step over
#   F11 — step into (внутри отладчика)
#   Shift+F11 — step out
#   Shift+F5 — stop
# ============================================================

from __future__ import annotations

import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════
# DEBUG PROTOCOL — обмен parent ↔ child через JSON-lines
# ═══════════════════════════════════════════════════════════
#
# Формат сообщений (одна строка JSON на сообщение):
#   Parent → Child:
#     {"cmd": "step"}         — один шаг
#     {"cmd": "continue"}     — до следующей точки останова
#     {"cmd": "step_out"}     — выйти из функции
#     {"cmd": "stop"}         — остановить
#     {"cmd": "eval", "expr": "x + 1"}
#   Child → Parent:
#     {"ev": "started", "line": 1}
#     {"ev": "stopped", "line": N, "file": "...",
#      "locals": {...}, "globals": {...}, "stack": [...]}
#     {"ev": "output", "text": "..."}
#     {"ev": "error", "text": "..."}
#     {"ev": "finished", "exit_code": 0}
#     {"ev": "eval_result", "value": "..."}
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# CHILD — код, который запускается в subprocess
# ═══════════════════════════════════════════════════════════

DEBUGGER_CHILD_TEMPLATE = r'''
import sys
import os
import json
import threading
import queue
import time
import traceback

# ── Безопасный stdout: только JSON-строки ────────────────
_REAL_STDOUT = sys.stdout
def emit(obj):
    try:
        _REAL_STDOUT.write(json.dumps(obj, default=str) + "\n")
        _REAL_STDOUT.flush()
    except Exception:
        pass

# ── Перехватываем stdout программы, шлём в parent ────────
class _OutputCapture:
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        if s:
            emit({"ev": "output", "text": str(s)})
    def flush(self):
        pass

sys.stdout = _OutputCapture("stdout")
sys.stderr = _OutputCapture("stderr")
sys.stdin = open(os.devnull, "r")

# ── Опасные модули заблокировать ─────────────────────────
_BLOCKED = {
    "os", "subprocess", "shutil", "socket", "urllib",
    "requests", "http", "ftplib", "smtplib", "telnetlib",
    "ctypes", "pickle", "marshal", "importlib", "gc",
    "inspect", "multiprocessing", "threading", "signal",
    "asyncio", "concurrent", "resource", "pty", "tty",
    "platform", "pwd", "grp", "crypt", "getpass",
    "webbrowser", "tkinter",
}
_orig_import = __builtins__.__import__ if isinstance(
    __builtins__, dict) else __builtins__.__import__

def _safe_import(name, *a, **kw):
    root = name.split(".")[0]
    if root in _BLOCKED:
        raise ImportError("import " + name + " blocked")
    return _orig_import(name, *a, **kw)

if isinstance(__builtins__, dict):
    __builtins__["__import__"] = _safe_import
else:
    __builtins__.__import__ = _safe_import


# ── Breakpoints и control flow ───────────────────────────
BREAKPOINTS = set()          # {line_number, ...}
STOP_NEXT = True             # остановиться на следующей строке?
STEP_MODE = "step"           # step | step_in | step_out | continue
STEP_DEPTH = 0               # для step_out
CURRENT_DEPTH = 0
STOPPED = threading.Event()
COMMANDS: "queue.Queue" = queue.Queue()
FINISHED = False
PROGRAM_GLOBALS = {"__name__": "__main__"}


def _serialize_value(v, depth=0, max_depth=3):
    """Безопасно превратить значение в JSON-friendly."""
    if depth > max_depth:
        return "..."
    if v is None or isinstance(v, (bool, int, float, str)):
        if isinstance(v, str) and len(v) > 500:
            return v[:500] + "…"
        return v
    if isinstance(v, (list, tuple)):
        return [_serialize_value(x, depth + 1) for x in v[:50]]
    if isinstance(v, dict):
        return {str(k): _serialize_value(v[k], depth + 1)
                for k in list(v.keys())[:50]}
    if isinstance(v, set):
        return list(v)[:50]
    if isinstance(v, (bytes, bytearray)):
        return f"<{type(v).__name__} len={len(v)}>"
    try:
        return repr(v)[:200]
    except Exception:
        return f"<{type(v).__name__}>"


def _gather_locals(frame):
    out = {}
    for k, v in frame.f_locals.items():
        if k.startswith("__"):
            continue
        try:
            out[k] = _serialize_value(v)
        except Exception:
            out[k] = "<err>"
    return out


def _gather_globals():
    out = {}
    for k, v in PROGRAM_GLOBALS.items():
        if k.startswith("__") and k != "__name__":
            continue
        if callable(v):
            out[k] = f"<{type(v).__name__}>"
            continue
        try:
            out[k] = _serialize_value(v, max_depth=2)
        except Exception:
            out[k] = "<err>"
    return out


def _gather_stack(frame):
    stack = []
    f = frame
    while f is not None:
        stack.append({
            "file": os.path.basename(f.f_code.co_filename),
            "line": f.f_lineno,
            "func": f.f_code.co_name,
        })
        f = f.f_back
    return stack


def _tracer(frame, event, arg):
    """Tracing function. Вызывается на каждый event."""
    global CURRENT_DEPTH, STEP_MODE, STOP_NEXT, STEP_DEPTH

    # Игнорируем внутренние модули
    filename = frame.f_code.co_filename
    if not filename.startswith("<user_code>"):
        return _tracer

    if event == "call":
        CURRENT_DEPTH += 1
        if STEP_MODE == "step_in":
            return _stop_at(frame, "step_in")
        if STEP_MODE == "step_out" and CURRENT_DEPTH > STEP_DEPTH:
            return _tracer
        return _tracer

    if event == "line":
        line = frame.f_lineno
        if line in BREAKPOINTS:
            return _stop_at(frame, "breakpoint")
        if STEP_MODE == "step":
            return _stop_at(frame, "step")
        if STEP_MODE == "step_out" and CURRENT_DEPTH <= STEP_DEPTH:
            return _stop_at(frame, "step_out")
        if STOP_NEXT:
            return _stop_at(frame, "start")
        return _tracer

    if event == "return":
        CURRENT_DEPTH = max(0, CURRENT_DEPTH - 1)
        return _tracer

    return _tracer


def _stop_at(frame, reason):
    """Останавливаем выполнение, шлём состояние в parent."""
    emit({
        "ev": "stopped",
        "reason": reason,
        "line": frame.f_lineno,
        "file": os.path.basename(frame.f_code.co_filename),
        "func": frame.f_code.co_name,
        "locals": _gather_locals(frame),
        "globals": _gather_globals(),
        "stack": _gather_stack(frame),
        "depth": CURRENT_DEPTH,
    })
    # Ждём команду
    while True:
        try:
            cmd = COMMANDS.get(timeout=0.2)
        except queue.Empty:
            continue
        _handle_command(cmd)
        if cmd.get("cmd") in ("step", "step_in",
                                "step_out", "continue",
                                "stop"):
            return _tracer


def _handle_command(cmd):
    global STEP_MODE, STOP_NEXT, STEP_DEPTH
    c = cmd.get("cmd", "")
    if c == "step":
        STEP_MODE = "step"
        STOP_NEXT = False
    elif c == "step_in":
        STEP_MODE = "step_in"
        STOP_NEXT = False
    elif c == "step_out":
        STEP_MODE = "step_out"
        STEP_DEPTH = CURRENT_DEPTH
        STOP_NEXT = False
    elif c == "continue":
        STEP_MODE = "continue"
        STOP_NEXT = False
    elif c == "stop":
        emit({"ev": "finished", "exit_code": 130})
        sys.exit(0)
    elif c == "add_bp":
        BREAKPOINTS.add(int(cmd.get("line", 0)))
    elif c == "remove_bp":
        BREAKPOINTS.discard(int(cmd.get("line", 0)))
    elif c == "eval":
        _eval_expr(cmd.get("expr", ""), cmd.get("frame_id"))


_LAST_FRAME = None
def _eval_expr(expr, frame_id=None):
    """Вычислить выражение в контексте последнего stop'а."""
    try:
        # Простейшая защита
        if any(bad in expr for bad in
               ("__", "import", "open", "eval", "exec")):
            emit({"ev": "eval_result",
                   "value": "⛔ запрещено"})
            return
        result = eval(expr, PROGRAM_GLOBALS, {})
        emit({"ev": "eval_result",
               "value": repr(result)[:500]})
    except Exception as e:
        emit({"ev": "eval_result",
               "value": f"❌ {type(e).__name__}: {e}"})


# ── Поток чтения команд ──────────────────────────────────
def _reader():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            cmd = json.loads(line)
            COMMANDS.put(cmd)
        except Exception:
            break


_stdin_thread = threading.Thread(target=_reader, daemon=True)


# ── Основной запуск ──────────────────────────────────────
def _main():
    # stdin переопределили — читаем команды напрямую
    real_stdin = os.fdopen(0, "r", closefd=False)

    def reader2():
        while True:
            try:
                line = real_stdin.readline()
                if not line:
                    break
                cmd = json.loads(line)
                COMMANDS.put(cmd)
            except Exception:
                break

    t = threading.Thread(target=reader2, daemon=True)
    t.start()

    # Настраиваем import-hook и запускаем
    global PROGRAM_GLOBALS
    PROGRAM_GLOBALS = {"__name__": "__main__"}

    emit({"ev": "started", "line": 1})

    USER_CODE = {user_code!r}

    # Записываем в файл для отображения трассы
    import tempfile as _tf
    tmpdir = _tf.mkdtemp(prefix="nexus_dbg_")
    user_file = os.path.join(tmpdir, "user_code.py")
    with open(user_file, "w", encoding="utf-8") as f:
        f.write(USER_CODE)

    # Компиляция с именем <user_code>
    code_obj = compile(USER_CODE, "<user_code>", "exec")

    sys.settrace(_tracer)
    try:
        exec(code_obj, PROGRAM_GLOBALS)
    except SystemExit:
        pass
    except Exception as e:
        emit({"ev": "error",
               "text": traceback.format_exc()})
    finally:
        sys.settrace(None)
        emit({"ev": "finished", "exit_code": 0})


if __name__ == "__main__":
    _main()
'''


# ═══════════════════════════════════════════════════════════
# DEBUG SESSION — управление subprocess'ом
# ═══════════════════════════════════════════════════════════

class DebugSession:
    """
    Управляет subprocess'ом отладки.
    Публичный API:
        session = DebugSession(user_code, breakpoints)
        session.start(on_event_callback)
        session.step()
        session.step_into()
        session.step_out()
        session.continue_()
        session.stop()
        session.evaluate("x + 1")
        session.add_breakpoint(line)
        session.remove_breakpoint(line)
        session.is_alive()
    """

    def __init__(self, user_code: str,
                 breakpoints: Optional[List[int]] = None,
                 timeout: int = 60) -> None:
        self.user_code = user_code
        self.breakpoints = set(breakpoints or [])
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._on_event: Optional[Any] = None
        self._lock = threading.RLock()
        self._alive = False
        self._tmp_script: Optional[Path] = None
        self._ready = threading.Event()
        self._start_time = 0.0

    # ── Lifecycle ────────────────────────────────────────
    def start(self, on_event: Any) -> bool:
        """Запустить отладку. on_event(ev_dict) на каждое событие."""
        with self._lock:
            if self.proc is not None:
                return False
            self._on_event = on_event
            try:
                self._tmp_script = self._write_script()
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "HOME": tempfile.gettempdir(),
                    "LANG": "C.UTF-8",
                }
                cmd = [
                    sys.executable, "-u", "-I",
                    str(self._tmp_script),
                ]
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                    cwd=tempfile.gettempdir(),
                )
                self._alive = True
                self._start_time = time.time()
                self._reader_thread = threading.Thread(
                    target=self._read_loop, daemon=True,
                    name="nexus-debugger-reader")
                self._reader_thread.start()
                return True
            except Exception as e:
                self._emit({"ev": "error",
                             "text": f"start failed: {e}"})
                self._alive = False
                return False

    def stop(self) -> None:
        with self._lock:
            if self.proc is None:
                return
            self._send({"cmd": "stop"})
            with suppress(Exception):
                self.proc.terminate()
            with suppress(Exception):
                self.proc.wait(timeout=3)
            with suppress(Exception):
                if self.proc.stdin:
                    self.proc.stdin.close()
            self._alive = False
            self.proc = None
            if self._tmp_script and self._tmp_script.exists():
                with suppress(Exception):
                    self._tmp_script.unlink()

    def is_alive(self) -> bool:
        return (self._alive and self.proc is not None
                and self.proc.poll() is None)

    # ── Commands ─────────────────────────────────────────
    def step(self) -> None:
        self._send({"cmd": "step"})

    def step_into(self) -> None:
        self._send({"cmd": "step_in"})

    def step_out(self) -> None:
        self._send({"cmd": "step_out"})

    def continue_(self) -> None:
        self._send({"cmd": "continue"})

    def evaluate(self, expr: str) -> None:
        self._send({"cmd": "eval", "expr": expr})

    def add_breakpoint(self, line: int) -> None:
        self.breakpoints.add(int(line))
        self._send({"cmd": "add_bp", "line": int(line)})

    def remove_breakpoint(self, line: int) -> None:
        self.breakpoints.discard(int(line))
        self._send({"cmd": "remove_bp", "line": int(line)})

    def toggle_breakpoint(self, line: int) -> bool:
        if int(line) in self.breakpoints:
            self.remove_breakpoint(int(line))
            return False
        self.add_breakpoint(int(line))
        return True

    # ── Internals ────────────────────────────────────────
    def _write_script(self) -> Path:
        fd, path = tempfile.mkstemp(
            prefix="nexus_dbg_", suffix=".py",
            dir=tempfile.gettempdir())
        os.close(fd)
        tmp = Path(path)
        script = DEBUGGER_CHILD_TEMPLATE.replace(
            "{user_code!r}", repr(self.user_code))
        # Инжектим начальные breakpoints
        if self.breakpoints:
            bp_init = ("\nBREAKPOINTS.update(%r)\n"
                        % sorted(self.breakpoints))
            script = script.replace(
                "if __name__ == \"__main__\":",
                bp_init + "if __name__ == \"__main__\":")
        tmp.write_text(script, encoding="utf-8")
        return tmp

    def _send(self, cmd: Dict[str, Any]) -> None:
        with self._lock:
            if self.proc is None or self.proc.stdin is None:
                return
            try:
                line = json.dumps(cmd, ensure_ascii=False)
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
            except Exception:
                self._alive = False

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    # Не JSON — считаем как output
                    ev = {"ev": "output", "text": line + "\n"}
                self._emit(ev)
                if ev.get("ev") in ("finished", "error"):
                    self._alive = False
                    break
        except Exception:
            pass
        finally:
            self._alive = False
            self._emit({"ev": "closed"})

    def _emit(self, ev: Dict[str, Any]) -> None:
        cb = self._on_event
        if cb:
            try:
                cb(ev)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
# UI — ПАНЕЛЬ ОТЛАДЧИКА
# ═══════════════════════════════════════════════════════════

class DebuggerUI:
    """Floating-панель отладчика. Прикрепляется к Code Studio."""

    def __init__(self, app: Any, ns: Dict[str, Any]) -> None:
        self.app = app
        self.ns = ns
        self.session: Optional[DebugSession] = None
        self.win: Optional[Any] = None
        self.breakpoints: set = set()

        # UI-элементы (создаются в build)
        self.var_tree: Optional[Any] = None
        self.output_box: Optional[Any] = None
        self.status_lbl: Optional[Any] = None
        self.stack_box: Optional[Any] = None
        self.eval_entry: Optional[Any] = None
        self.line_indicator: Optional[Any] = None

        # Работа с редактором
        self._editor: Optional[Any] = None
        self._editor_bind_id: Optional[str] = None

    # ── Публичный API ────────────────────────────────────
    def open(self) -> None:
        """Открыть/сфокусировать окно отладчика."""
        CTkToplevel = self.ns.get("CTkToplevel")
        if CTkToplevel is None:
            return
        if self.win and self.win.winfo_exists():
            self.win.focus()
            return
        self._build_window()

    def close(self) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None
        if self.win and self.win.winfo_exists():
            with suppress(Exception):
                self.win.destroy()
        self.win = None

    def bind_to_editor(self, editor: Any) -> None:
        """Привязать горячие клавиши к текущему редактору."""
        if editor is None:
            return
        try:
            if not editor.winfo_exists():
                return
        except Exception:
            return
        self._editor = editor
        # Клик по номеру строки → toggle breakpoint
        try:
            editor.bind("<Button-1>",
                         self._on_editor_click, add="+")
            editor.bind("<F9>",
                         self._on_toggle_bp_here, add="+")
            editor.bind("<Control-b>",
                         self._on_toggle_bp_here, add="+")
        except Exception:
            pass

    def toggle_breakpoint_at_cursor(self) -> None:
        self._on_toggle_bp_here(None)

    def start_debug(self) -> None:
        """Запустить отладку текущего кода."""
        editor = self._get_current_editor()
        if editor is None:
            self._toast("❌ Редактор недоступен", "error")
            return
        try:
            code = editor.get("1.0", "end-1c")
        except Exception:
            return
        if not code.strip():
            self._toast("❌ Пустой код", "warning")
            return

        # Собираем breakpoints из редактора (метки "bp")
        bps = self._collect_breakpoints(editor)

        if self.session is not None:
            self.session.stop()
        self.session = DebugSession(code, list(bps))
        ok = self.session.start(self._on_debug_event)
        if not ok:
            self._toast("❌ Не удалось запустить", "error")
            return
        self._toast("🐛 Отладка запущена", "success", 2000)

    # ── UI build ─────────────────────────────────────────
    def _build_window(self) -> None:
        CTkToplevel = self.ns["CTkToplevel"]
        CTkFrame = self.ns["CTkFrame"]
        CTkLabel = self.ns["CTkLabel"]
        CTkButton = self.ns["CTkButton"]
        CTkTextbox = self.ns["CTkTextbox"]
        CTkEntry = self.ns["CTkEntry"]
        CTkFont = self.ns["CTkFont"]
        theme = self.ns["theme_manager"]

        win = CTkToplevel(self.app)
        self.win = win
        win.title("🐛 NEXUS Debugger")
        win.geometry("900x680")
        win.configure(fg_color=theme.get("bg"))
        win.transient(self.app)
        win.protocol("WM_DELETE_WINDOW", self.close)

        # Заголовок
        header = CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 4))
        CTkLabel(header, text="🐛 ОТЛАДЧИК",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme.get("primary")
                 ).pack(side="left")
        self.status_lbl = CTkLabel(
            header, text="⏹ Остановлен",
            font=CTkFont(size=11, slant="italic"),
            text_color=theme.get("text_dim"))
        self.status_lbl.pack(side="right")

        # Панель кнопок управления
        ctrl = CTkFrame(win, fg_color=theme.get("bg2"),
                         corner_radius=8)
        ctrl.pack(fill="x", padx=15, pady=(4, 8))
        inner = CTkFrame(ctrl, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        for txt, color, cmd, tooltip in [
            ("▶ Запустить", theme.get("success"),
             self.start_debug, "F5"),
            ("⏭ Шаг", theme.get("primary"),
             self._do_step, "F10"),
            ("⏬ Войти", theme.get("accent"),
             self._do_step_into, "F11"),
            ("⏫ Выйти", theme.get("gradient2"),
             self._do_step_out, "Shift+F11"),
            ("⏩ Далее", theme.get("warning"),
             self._do_continue, "F5 (в паузе)"),
            ("⏹ Стоп", theme.get("error"),
             self._do_stop, "Shift+F5"),
            ("🔴 Точка", theme.get("secondary"),
             self.toggle_breakpoint_at_cursor, "F9"),
        ]:
            CTkButton(inner, text=txt, height=34, width=110,
                       font=CTkFont(size=10, weight="bold"),
                       fg_color=color,
                       text_color=theme.get("bg"),
                       command=cmd).pack(side="left", padx=2)

        # Основная область: слева переменные + стек,
        # справа вывод
        main = CTkFrame(win, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=15,
                   pady=(0, 8))
        main.grid_columnconfigure(0, weight=2, uniform="dbg")
        main.grid_columnconfigure(1, weight=3, uniform="dbg")
        main.grid_rowconfigure(0, weight=1)

        # ЛЕВО: переменные
        left = CTkFrame(main, fg_color=theme.get("bg2"),
                         corner_radius=8, border_width=1,
                         border_color=theme.get("card_border"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        CTkLabel(left, text="📦 ПЕРЕМЕННЫЕ",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=theme.get("primary"),
                 anchor="w").grid(row=0, column=0, sticky="ew",
                                    padx=10, pady=(10, 4))

        self.var_tree = _TreeWidget(
            left, self.ns, on_select=self._on_var_select)
        self.var_tree.grid(row=1, column=0, sticky="nsew",
                             padx=10, pady=(0, 10))

        # ПРАВО: стек + вывод + eval
        right = CTkFrame(main, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(3, weight=2)

        # Стек вызовов
        stack_frame = CTkFrame(
            right, fg_color=theme.get("bg2"),
            corner_radius=8, border_width=1,
            border_color=theme.get("card_border"))
        stack_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        CTkLabel(stack_frame, text="📚 СТЕК ВЫЗОВОВ",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme.get("accent"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(6, 2))
        self.stack_box = CTkTextbox(
            stack_frame, height=80,
            font=CTkFont(family="Courier", size=10),
            fg_color=theme.get("bg3"),
            text_color=theme.get("text"),
            wrap="none")
        self.stack_box.pack(fill="x", padx=10, pady=(0, 8))
        self.stack_box.configure(state="disabled")

        # Eval
        eval_frame = CTkFrame(
            right, fg_color=theme.get("bg2"),
            corner_radius=8, border_width=1,
            border_color=theme.get("card_border"))
        eval_frame.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        CTkLabel(eval_frame, text="🔬 ВЫРАЖЕНИЕ",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme.get("accent"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(6, 2))
        eval_row = CTkFrame(eval_frame, fg_color="transparent")
        eval_row.pack(fill="x", padx=10, pady=(0, 8))
        self.eval_entry = CTkEntry(
            eval_row, placeholder_text="например: x + y",
            font=CTkFont(family="Courier", size=11),
            height=32,
            fg_color=theme.get("bg3"),
            border_color=theme.get("card_border"),
            text_color=theme.get("text"))
        self.eval_entry.pack(side="left", fill="x", expand=True,
                                padx=(0, 6))
        CTkButton(eval_row, text="▶", width=40, height=32,
                  font=CTkFont(size=12, weight="bold"),
                  fg_color=theme.get("accent"),
                  text_color=theme.get("bg"),
                  command=self._do_eval).pack(side="right")
        self.eval_entry.bind(
            "<Return>", lambda e: self._do_eval())

        # Вывод
        out_frame = CTkFrame(
            right, fg_color=theme.get("bg2"),
            corner_radius=8, border_width=1,
            border_color=theme.get("card_border"))
        out_frame.grid(row=3, column=0, sticky="nsew")
        CTkLabel(out_frame, text="📤 ВЫВОД ПРОГРАММЫ",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme.get("secondary"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(6, 2))
        self.output_box = CTkTextbox(
            out_frame,
            font=CTkFont(family="Courier", size=10),
            fg_color=theme.get("bg3"),
            text_color=theme.get("text"),
            wrap="word")
        self.output_box.pack(fill="both", expand=True, padx=10,
                               pady=(0, 8))

        # Подвал
        footer = CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=15, pady=(0, 12))
        CTkLabel(footer,
                 text="F5 — Запустить/Далее · F10 — Шаг · "
                      "F11 — Войти · Shift+F11 — Выйти · "
                      "F9 — Точка останова",
                 font=CTkFont(size=9),
                 text_color=theme.get("text_dim")
                 ).pack(side="left")

        # Горячие клавиши
        win.bind("<F5>", lambda e: self._do_f5())
        win.bind("<F10>", lambda e: self._do_step())
        win.bind("<F11>", lambda e: self._do_step_into())
        win.bind("<Shift-F11>", lambda e: self._do_step_out())
        win.bind("<Shift-F5>", lambda e: self._do_stop())
        win.bind("<F9>", lambda e: self.toggle_breakpoint_at_cursor())

    # ── Editor helpers ───────────────────────────────────
    def _get_current_editor(self) -> Optional[Any]:
        """Возвращает текущий _code_widget."""
        editor = getattr(self.app, "_code_widget", None)
        if editor is None:
            return None
        try:
            if not editor.winfo_exists():
                return None
        except Exception:
            return None
        return editor

    def _collect_breakpoints(self, editor: Any) -> List[int]:
        """Найти строки с меткой "bp"."""
        bps: List[int] = []
        try:
            ranges = editor.tag_ranges("bp")
            for i in range(0, len(ranges), 2):
                start = ranges[i]
                line = int(str(start).split(".")[0])
                bps.append(line)
        except Exception:
            pass
        return bps

    def _on_editor_click(self, event: Any) -> None:
        """Ctrl+Клик по строке → toggle breakpoint."""
        if not (event.state & 0x4):  # Ctrl
            return
        editor = self._editor or self._get_current_editor()
        if editor is None:
            return
        try:
            idx = editor.index(f"@{event.x},{event.y}")
            line = int(str(idx).split(".")[0])
        except Exception:
            return
        self._toggle_bp_line(editor, line)

    def _on_toggle_bp_here(self, event: Any) -> str:
        editor = self._editor or self._get_current_editor()
        if editor is None:
            return "break"
        try:
            cursor = editor.index("insert")
            line = int(str(cursor).split(".")[0])
        except Exception:
            return "break"
        self._toggle_bp_line(editor, line)
        return "break"

    def _toggle_bp_line(self, editor: Any, line: int) -> None:
        """Переключить точку на строке."""
        start = f"{line}.0"
        end = f"{line}.end"
        has = False
        try:
            has = "bp" in editor.tag_names(start)
        except Exception:
            pass
        try:
            if has:
                editor.tag_remove("bp", start, end)
                self.breakpoints.discard(line)
                if self.session:
                    self.session.remove_breakpoint(line)
            else:
                editor.tag_add("bp", start, end)
                editor.tag_config(
                    "bp", background="#7a0000",
                    foreground="#ffb0b0")
                self.breakpoints.add(line)
                if self.session:
                    self.session.add_breakpoint(line)
        except Exception:
            pass

    # ── Управление ──────────────────────────────────────
    def _do_f5(self) -> None:
        if (self.session is not None
                and self.session.is_alive()):
            self._do_continue()
        else:
            self.start_debug()

    def _do_step(self) -> None:
        if self.session and self.session.is_alive():
            self.session.step()
            self._set_status("⏭ Шаг...")

    def _do_step_into(self) -> None:
        if self.session and self.session.is_alive():
            self.session.step_into()
            self._set_status("⏬ Вход...")

    def _do_step_out(self) -> None:
        if self.session and self.session.is_alive():
            self.session.step_out()
            self._set_status("⏫ Выход...")

    def _do_continue(self) -> None:
        if self.session and self.session.is_alive():
            self.session.continue_()
            self._set_status("▶ Выполняется...")

    def _do_stop(self) -> None:
        if self.session:
            self.session.stop()
        self._set_status("⏹ Остановлен")
        self._clear_ui()

    def _do_eval(self) -> None:
        if not (self.session and self.session.is_alive()):
            self._toast("Отладчик не запущен", "warning")
            return
        expr = (self.eval_entry.get() or "").strip()
        if not expr:
            return
        self.session.evaluate(expr)

    # ── Event handler ────────────────────────────────────
    def _on_debug_event(self, ev: Dict[str, Any]) -> None:
        """Вызывается из reader-thread. Прокидываем в UI-поток."""
        if self.app and hasattr(self.app, "_safe_after"):
            self.app._safe_after(0, self._handle_event, ev)
        else:
            self._handle_event(ev)

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("ev", "")
        if kind == "started":
            self._set_status("▶ Выполняется...")
        elif kind == "stopped":
            self._on_stopped(ev)
        elif kind == "output":
            self._append_output(ev.get("text", ""))
        elif kind == "error":
            text = ev.get("text", "")
            self._append_output("\n❌ " + text)
            self._set_status("❌ Ошибка")
        elif kind == "eval_result":
            self._append_output(
                "\n🔬 → " + ev.get("value", ""))
        elif kind == "finished":
            code = ev.get("exit_code", 0)
            self._set_status(f"✅ Завершено (код {code})"
                              if code == 0
                              else f"⏹ Стоп (код {code})")
        elif kind == "closed":
            self._set_status("⏹ Отладчик закрыт")

    def _on_stopped(self, ev: Dict[str, Any]) -> None:
        line = ev.get("line", 0)
        reason = ev.get("reason", "")
        func = ev.get("func", "")
        self._set_status(
            f"⏸ Строка {line} в {func}() — {reason}")
        # Обновляем переменные
        locals_ = ev.get("locals", {}) or {}
        globals_ = ev.get("globals", {}) or {}
        variables: Dict[str, Any] = {}
        for k, v in globals_.items():
            variables[f"🌐 {k}"] = v
        for k, v in locals_.items():
            variables[f"📍 {k}"] = v
        self.var_tree.update(variables)
        # Стек
        stack = ev.get("stack", []) or []
        self._update_stack(stack)
        # Подсветка текущей строки в редакторе
        self._highlight_current_line(line)

    def _update_stack(self, stack: List[Dict[str, Any]]) -> None:
        try:
            if not self.stack_box.winfo_exists():
                return
            self.stack_box.configure(state="normal")
            self.stack_box.delete("1.0", "end")
            for i, f in enumerate(stack):
                prefix = "▶ " if i == 0 else "  "
                self.stack_box.insert(
                    "end",
                    f"{prefix}{f.get('func', '?')}  "
                    f"({f.get('file', '?')}:{f.get('line', 0)})\n")
            self.stack_box.configure(state="disabled")
        except Exception:
            pass

    def _highlight_current_line(self, line: int) -> None:
        editor = self._get_current_editor()
        if editor is None:
            return
        try:
            editor.tag_remove("dbg_line", "1.0", "end")
            editor.tag_add("dbg_line", f"{line}.0", f"{line}.end")
            editor.tag_config(
                "dbg_line", background="#1a3a5a",
                foreground="#ffffff")
            # Прокрутить к строке
            editor.see(f"{line}.0")
        except Exception:
            pass

    def _append_output(self, text: str) -> None:
        try:
            if not self.output_box.winfo_exists():
                return
            self.output_box.configure(state="normal")
            self.output_box.insert("end", text)
            self.output_box.see("end")
            self.output_box.configure(state="disabled")
        except Exception:
            pass

    def _clear_ui(self) -> None:
        try:
            if self.var_tree:
                self.var_tree.update({})
            if self.stack_box and self.stack_box.winfo_exists():
                self.stack_box.configure(state="normal")
                self.stack_box.delete("1.0", "end")
                self.stack_box.configure(state="disabled")
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        with suppress(Exception):
            if self.status_lbl and self.status_lbl.winfo_exists():
                self.status_lbl.configure(text=text)

    def _on_var_select(self, name: str, value: Any) -> None:
        """Клик по переменной — вывод её значения в eval."""
        with suppress(Exception):
            if self.eval_entry and self.eval_entry.winfo_exists():
                clean = name.replace("🌐 ", "").replace("📍 ", "")
                self.eval_entry.delete(0, "end")
                self.eval_entry.insert(0, clean)

    def _toast(self, msg: str, typ: str = "info",
                duration: int = 3000) -> None:
        with suppress(Exception):
            self.app._show_toast(msg, typ, duration)


# ═══════════════════════════════════════════════════════════
# ПРОСТОЙ ДЕРЕВО-ВИДЖЕТ (без ttk.Treeview — свой)
# ═══════════════════════════════════════════════════════════

class _TreeWidget:
    """Минимальный tree-widget на CTkScrollableFrame."""

    def __init__(self, parent: Any, ns: Dict[str, Any],
                  on_select: Optional[Any] = None) -> None:
        self.ns = ns
        self.on_select = on_select
        CTkScrollableFrame = ns["CTkScrollableFrame"]
        self._frame = CTkScrollableFrame(
            parent, fg_color=ns["theme_manager"].get("bg3"))
        self._items: List[Dict[str, Any]] = []

    def grid(self, **kw: Any) -> None:
        self._frame.grid(**kw)

    def pack(self, **kw: Any) -> None:
        self._frame.pack(**kw)

    def update(self, variables: Dict[str, Any]) -> None:
        self._items.clear()
        theme = self.ns["theme_manager"]
        CTkFrame = self.ns["CTkFrame"]
        CTkLabel = self.ns["CTkLabel"]
        CTkFont = self.ns["CTkFont"]
        for w in self._frame.winfo_children():
            with suppress(Exception):
                w.destroy()
        for name, value in variables.items():
            row = CTkFrame(self._frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=1)
            # Имя
            name_lbl = CTkLabel(
                row, text=str(name)[:30],
                font=CTkFont(family="Courier", size=10,
                              weight="bold"),
                text_color=theme.get("primary"),
                anchor="w", width=160)
            name_lbl.pack(side="left", padx=2)
            # Значение (одна строка)
            val_repr = _short_repr(value)
            val_lbl = CTkLabel(
                row, text=val_repr,
                font=CTkFont(family="Courier", size=10),
                text_color=theme.get("text"),
                anchor="w", justify="left")
            val_lbl.pack(side="left", fill="x", expand=True,
                          padx=2)
            # Клик
            for w in (row, name_lbl, val_lbl):
                w.bind(
                    "<Button-1>",
                    lambda e, n=name, v=value:
                        self._click(n, v))
            self._items.append({"name": name, "value": value})

    def _click(self, name: str, value: Any) -> None:
        if self.on_select:
            with suppress(Exception):
                self.on_select(name, value)


def _short_repr(v: Any, max_len: int = 60) -> str:
    try:
        s = repr(v)
    except Exception:
        s = f"<{type(v).__name__}>"
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


# ═══════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ
# ═══════════════════════════════════════════════════════════

_DEBUGGER_UI: Optional[DebuggerUI] = None


def attach(app: Any, ns: Dict[str, Any]) -> None:
    """Навесить на app методы debugger_*."""
    global _DEBUGGER_UI
    _DEBUGGER_UI = DebuggerUI(app, ns)
    app.debugger_open = _DEBUGGER_UI.open
    app.debugger_close = _DEBUGGER_UI.close
    app.debugger_start = _DEBUGGER_UI.start_debug
    app.debugger_toggle_bp = _DEBUGGER_UI.toggle_breakpoint_at_cursor
    app.debugger_bind_editor = _DEBUGGER_UI.bind_to_editor
    app.debugger_session = lambda: _DEBUGGER_UI.session
    print("✅ Debugger: attached")


def get_ui() -> Optional[DebuggerUI]:
    return _DEBUGGER_UI


__all__ = ["attach", "DebugSession", "DebuggerUI",
           "get_ui"]