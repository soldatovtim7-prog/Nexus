"""
NEXUS sandbox — безопасное выполнение кода.

Python   → SandboxRunner (AST + bytecode + rlimits)
Прочие   → MultiLangRunner (subprocess с rlimits)

Поддержка: python, javascript, typescript, shell, ruby, php,
           go, lua, perl, r, sql, html, css, markdown, json,
           yaml, xml.

Зависит от: core
"""
from __future__ import annotations

import ast
import builtins as _builtins
import dis
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from contextlib import suppress
from pathlib import Path
from typing import (
    Any, Dict, Final, FrozenSet, Tuple,
)

from nexus.core import (
    CODE_TIMEOUT,
    SANDBOX_CPU_LIMIT,
    SANDBOX_DIR,
    SANDBOX_FSIZE_LIMIT,
    SANDBOX_MAX_OUTPUT,
    SANDBOX_MEM_LIMIT,
    logger,
)


# ═══════════════════════════════════════════════════════════
# AST VALIDATOR (для Python)
# ═══════════════════════════════════════════════════════════
class CodeSandbox:
    FORBIDDEN_NAMES: Final[FrozenSet[str]] = frozenset({
        "eval", "exec", "compile", "__import__",
        "open", "input", "breakpoint", "exit", "quit",
        "globals", "locals", "vars", "dir",
        "getattr", "setattr", "delattr", "memoryview",
        "help", "license", "credits", "copyright",
    })
    FORBIDDEN_MODULES: Final[FrozenSet[str]] = frozenset({
        "os", "subprocess", "shutil", "socket", "urllib",
        "requests", "http", "ftplib", "smtplib", "telnetlib",
        "ctypes", "pickle", "marshal", "importlib",
        "gc", "inspect", "multiprocessing", "threading",
        "signal", "asyncio", "concurrent", "resource", "pty",
        "tty", "platform", "pwd", "grp", "crypt", "getpass",
        "webbrowser", "tkinter", "site", "sysconfig",
        "distutils", "setuptools", "pip",
    })
    FORBIDDEN_ATTRS: Final[FrozenSet[str]] = frozenset({
        "__class__", "__base__", "__subclasses__", "__bases__",
        "__mro__", "__globals__", "__code__", "__closure__",
        "__builtins__", "__import__", "__loader__",
        "__spec__", "__reduce__", "__reduce_ex__",
        "__getattribute__", "__getattr__", "__setattr__",
        "__delattr__", "__new__", "__init_subclass__",
        "__dict__", "__slots__",
        "gi_frame", "gi_code", "cr_frame", "cr_code",
        "f_globals", "f_builtins", "f_locals", "f_back",
        "__self__", "__func__", "__wrapped__",
        "__qualname__", "__module__", "__annotations__",
    })
    MAX_AST_NODES: Final[int] = 5000
    MAX_STRING_LEN: Final[int] = 10_000
    MAX_CODE_SIZE: Final[int] = 100_000

    @classmethod
    def validate(cls, code: str) -> Tuple[bool, str]:
        if not code or not code.strip():
            return False, "Пустой код"
        if len(code) > cls.MAX_CODE_SIZE:
            return False, "Слишком большой код"
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Синтаксис: {e}"
        node_count = 0
        for node in ast.walk(tree):
            node_count += 1
            if node_count > cls.MAX_AST_NODES:
                return False, (
                    f"Слишком сложный код "
                    f"(>{cls.MAX_AST_NODES})")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in cls.FORBIDDEN_MODULES:
                        return False, (
                            f"Запрещённый модуль: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root in cls.FORBIDDEN_MODULES:
                        return False, (
                            f"Запрещённый модуль: {node.module}")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id in cls.FORBIDDEN_NAMES:
                        return False, (
                            f"Запрещённая функция: {func.id}()")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in cls.FORBIDDEN_ATTRS:
                        return False, (
                            f"Запрещённый метод: {node.func.attr}")
                elif isinstance(node.func, ast.Subscript):
                    return False, "Вызов через подписку запрещён"
            elif isinstance(node, ast.Attribute):
                if node.attr in cls.FORBIDDEN_ATTRS:
                    return False, (
                        f"Запрещённый атрибут: {node.attr}")
            elif isinstance(node, ast.Name):
                if node.id in cls.FORBIDDEN_NAMES:
                    return False, f"Запрещённое имя: {node.id}"
            elif isinstance(node, ast.Constant):
                if (isinstance(node.value, str)
                        and len(node.value) > cls.MAX_STRING_LEN):
                    return False, "Слишком длинная строка"
        return True, "OK"


# ═══════════════════════════════════════════════════════════
# BYTECODE VALIDATOR (для Python)
# ═══════════════════════════════════════════════════════════
_FORBIDDEN_OPCODES: Final[FrozenSet[str]] = frozenset({
    "IMPORT_STAR",
    "LOAD_BUILD_CLASS",
    "EXEC", "PRINT_EXPR", "LOAD_LOCALS",
    "SETUP_ANNOTATIONS",
})

_SAFE_OPCODE_PREFIXES: Final[Tuple[str, ...]] = (
    "UNARY_", "BINARY_", "INPLACE_", "CALL_",
    "JUMP_", "POP_JUMP_", "LOAD_", "STORE_",
    "DELETE_", "BUILD_", "MAKE_", "CHECK_",
    "PUSH_", "POP_", "END_", "GET_", "SET_",
    "FOR_",
)

_ALLOWED_OPCODES: Final[FrozenSet[str]] = frozenset({
    "IMPORT_NAME", "IMPORT_FROM",
    "NOP", "POP_TOP", "PUSH_NULL", "ROT_TWO", "ROT_THREE",
    "ROT_FOUR", "DUP_TOP", "DUP_TOP_TWO", "COPY", "SWAP",
    "EXTENDED_ARG", "CACHE",
    "LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL",
    "LOAD_ATTR", "LOAD_METHOD", "LOAD_DEREF",
    "LOAD_CLASSDEREF",
    "STORE_NAME", "STORE_FAST", "STORE_GLOBAL",
    "STORE_ATTR", "STORE_DEREF", "DELETE_NAME",
    "DELETE_FAST", "DELETE_GLOBAL", "DELETE_ATTR",
    "DELETE_DEREF", "LOAD_CLOSURE", "COPY_FREE_VARS",
    "LOAD_ASSERTION_ERROR",
    "BINARY_OP", "BINARY_SUBSCR", "BINARY_SLICE",
    "UNARY_NEGATIVE", "UNARY_NOT", "UNARY_INVERT",
    "COMPARE_OP", "IS_OP", "CONTAINS_OP",
    "BUILD_LIST", "BUILD_TUPLE", "BUILD_SET", "BUILD_MAP",
    "BUILD_SLICE", "BUILD_STRING", "BUILD_CONST_KEY_MAP",
    "LIST_APPEND", "SET_ADD", "MAP_ADD", "LIST_EXTEND",
    "SET_UPDATE", "DICT_UPDATE", "DICT_MERGE",
    "UNPACK_SEQUENCE", "UNPACK_EX",
    "GET_ITER", "FOR_ITER", "END_FOR", "GET_LEN",
    "END_SEND", "SEND",
    "JUMP_FORWARD", "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT",
    "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
    "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE",
    "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
    "RETURN_VALUE", "RETURN_CONST",
    "CALL", "CALL_FUNCTION_EX", "KW_NAMES", "PRECALL",
    "MAKE_FUNCTION", "SET_FUNCTION_ATTRIBUTE",
    "BEFORE_WITH", "WITH_EXCEPT_START", "PUSH_EXC_INFO",
    "CHECK_EXC_MATCH", "RERAISE", "POP_EXCEPT",
    "RAISE_VARARGS",
    "RESUME", "GET_AWAITABLE",
    "STORE_SUBSCR", "DELETE_SUBSCR", "FORMAT_VALUE",
    "RETURN_GENERATOR",
    "CHECK_EG_MATCH",
    "ASYNC_GEN_WRAP", "PREP_RERAISE_STAR",
    "CLEANUP_THROW",
    "END_ASYNC_FOR", "MAKE_CELL",
    "LOAD_FAST_CHECK", "LOAD_FAST_AND_CLEAR",
    "STORE_FAST_LOAD_FAST", "STORE_FAST_STORE_FAST",
    "LOAD_FAST_LOAD_FAST",
    "CALL_INTRINSIC_1", "CALL_INTRINSIC_2",
    "LOAD_FROM_DICT_OR_DEREF",
    "LOAD_FROM_DICT_OR_GLOBALS",
    "GET_YIELD_FROM_ITER",
    "BEFORE_ASYNC_WITH",
    "CONVERT_VALUE", "FORMAT_SIMPLE", "FORMAT_WITH_SPEC",
    "BUILD_INTERPOLATION",
    "LOAD_SUPER_ATTR",
    "YIELD_VALUE",
    "RESERVED",
    "TO_BOOL", "NOT_TAKEN",
    "SETUP_FINALLY", "SETUP_CLEANUP", "SETUP_WITH",
    "POP_BLOCK",
    "MAKE_WAITABLE",
    "BUILD_TEMPLATE", "LOAD_COMMON_CONSTANT",
    "LOAD_SPECIAL", "POP_ITER",
})


def validate_bytecode(code_obj: Any,
                       depth: int = 0) -> Tuple[bool, str]:
    """Проверить байткод на запрещённые опкоды."""
    if depth > 10:
        return False, "Слишком глубокая вложенность"
    try:
        instructions = list(dis.get_instructions(code_obj))
    except Exception as e:
        return False, f"Не удалось разобрать байткод: {e}"
    for instr in instructions:
        op = instr.opname
        if op in _FORBIDDEN_OPCODES:
            return False, f"Запрещённый opcode: {op}"
        if op not in _ALLOWED_OPCODES:
            if op in ("RESERVED", "NOP"):
                continue
            if op.startswith(_SAFE_OPCODE_PREFIXES):
                continue
            return False, (
                f"Неизвестный opcode {op} — требуется "
                f"ревизия песочницы для вашей версии Python")
        if isinstance(instr.argval, type(code_obj)):
            ok, msg = validate_bytecode(instr.argval, depth + 1)
            if not ok:
                return False, msg
    return True, "OK"


# ═══════════════════════════════════════════════════════════
# STDOUT REDIRECT
# ═══════════════════════════════════════════════════════════
class _StdoutRedirect:
    def __init__(self, stdout: Any, stderr: Any) -> None:
        self.stdout, self.stderr = stdout, stderr
        self._old_out: Any = None
        self._old_err: Any = None

    def __enter__(self) -> "_StdoutRedirect":
        self._old_out, self._old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.stdout, self.stderr
        return self

    def __exit__(self, *a: Any) -> bool:
        sys.stdout, sys.stderr = self._old_out, self._old_err
        return False


# ═══════════════════════════════════════════════════════════
# IN-PROCESS WORKER (fallback)
# ═══════════════════════════════════════════════════════════
def _sandbox_worker(code: str) -> Tuple[str, str, bool]:
    """Возвращает (stdout, stderr, success)."""
    _real_exec = exec
    _real_compile = compile
    _real_import = __import__

    try:
        import resource as _res
        for name, limit in (
            ("RLIMIT_CPU", SANDBOX_CPU_LIMIT),
            ("RLIMIT_AS", SANDBOX_MEM_LIMIT),
            ("RLIMIT_DATA", SANDBOX_MEM_LIMIT),
            ("RLIMIT_FSIZE", SANDBOX_FSIZE_LIMIT),
        ):
            res = getattr(_res, name, None)
            if res is not None:
                with suppress(Exception):
                    _res.setrlimit(res, (limit, limit))
        for name, lim in (
            ("RLIMIT_NOFILE", 16), ("RLIMIT_NPROC", 1),
            ("RLIMIT_CORE", 0),
        ):
            res = getattr(_res, name, None)
            if res is not None:
                with suppress(Exception):
                    _res.setrlimit(res, (lim, lim))
    except Exception:
        pass

    if sys.platform.startswith("linux"):
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(38, 1, 0, 0, 0)
        except Exception:
            pass

    for name in ("eval", "input", "breakpoint",
                  "exit", "quit", "memoryview"):
        if hasattr(_builtins, name):
            with suppress(Exception):
                delattr(_builtins, name)

    _SAFE = frozenset({
        "math", "cmath", "statistics", "random", "datetime",
        "time", "json", "re", "textwrap", "string",
        "unicodedata", "itertools", "functools", "operator",
        "collections", "heapq", "bisect", "decimal",
        "fractions", "numbers", "array", "copy", "hashlib",
        "hmac", "secrets", "base64", "binascii", "struct",
        "uuid", "enum", "dataclasses", "typing", "abc",
        "contextlib", "warnings", "traceback", "pprint", "io",
        "csv", "sys",
    })

    def _safe_import(name: str, *a: Any, **kw: Any) -> Any:
        if name.split(".")[0] not in _SAFE:
            raise ImportError(f"Import blocked: {name}")
        return _real_import(name, *a, **kw)

    _builtins.__import__ = _safe_import

    class _Cap(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self._size = 0

        def write(self, s: str) -> int:
            if not isinstance(s, str):
                s = str(s)
            if self._size >= SANDBOX_MAX_OUTPUT:
                return len(s)
            remaining = SANDBOX_MAX_OUTPUT - self._size
            chunk = s[:remaining] if len(s) > remaining else s
            self._size += len(chunk)
            super().write(chunk)
            return len(s)

        def writelines(self, lines: Any) -> None:
            for line in lines:
                self.write(line)

    buf_out, buf_err = _Cap(), _Cap()
    try:
        glb: Dict[str, Any] = {"__name__": "__main__"}
        with _StdoutRedirect(buf_out, buf_err):
            compiled = _real_compile(code, "<nexus>", "exec")
            _real_exec(compiled, glb)
        return (buf_out.getvalue()[:SANDBOX_MAX_OUTPUT],
                buf_err.getvalue()[:SANDBOX_MAX_OUTPUT], True)
    except MemoryError:
        return "", "❌ Memory limit exceeded", False
    except Exception as e:
        tb = traceback.format_exc(limit=5)
        return (buf_out.getvalue()[:SANDBOX_MAX_OUTPUT],
                f"{type(e).__name__}: {e}\n{tb}", False)


# ═══════════════════════════════════════════════════════════
# SANDBOX RUNNER (Python — строгий)
# ═══════════════════════════════════════════════════════════
class SandboxRunner:
    DEFAULT_TIMEOUT: Final[int] = CODE_TIMEOUT

    @classmethod
    def run(
        cls, code: str,
        timeout: int | None = None,
        skip_validation: bool = False,
    ) -> Tuple[str, str, bool]:
        """
        Запуск Python-кода в изолированном subprocess.
        """
        timeout = timeout or cls.DEFAULT_TIMEOUT

        if not skip_validation:
            ok, msg = CodeSandbox.validate(code)
            if not ok:
                return "", f"⛔ AST: {msg}", False
            try:
                compiled = compile(code, "<nexus>", "exec")
            except SyntaxError as e:
                return "", f"⛔ Синтаксис: {e}", False
            ok, msg = validate_bytecode(compiled)
            if not ok:
                return "", f"⛔ Bytecode: {msg}", False

        return cls._fallback_subprocess(code, timeout)

    @classmethod
    def _fallback_subprocess(
        cls, code: str, timeout: int
    ) -> Tuple[str, str, bool]:
        try:
            SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        with tempfile.TemporaryDirectory(
                prefix="nexus_sbx_",
                dir=SANDBOX_DIR) as tmp:
            tmp_path = Path(tmp)
            env = {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": "",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "HOME": tmp,
                "TMPDIR": tmp,
                "LANG": "C.UTF-8",
                "PYTHONHASHSEED": "0",
            }
            wrapper = (
                "import sys as _sys\n"
                "import builtins as _bi\n"
                "try:\n"
                "    import resource as _res\n"
                f"    _res.setrlimit(_res.RLIMIT_CPU,"
                f"({SANDBOX_CPU_LIMIT},{SANDBOX_CPU_LIMIT}))\n"
                f"    _res.setrlimit(_res.RLIMIT_AS,"
                f"({SANDBOX_MEM_LIMIT},{SANDBOX_MEM_LIMIT}))\n"
                f"    _res.setrlimit(_res.RLIMIT_FSIZE,"
                f"({SANDBOX_FSIZE_LIMIT},{SANDBOX_FSIZE_LIMIT}))\n"
                "except Exception:\n"
                "    pass\n"
                "_BLOCKED = {'os','subprocess','shutil','socket',"
                "'urllib','requests','http','ftplib','smtplib',"
                "'telnetlib','ctypes','pickle','marshal','importlib',"
                "'gc','inspect','multiprocessing',"
                "'threading','signal','asyncio','concurrent',"
                "'resource','pty','tty','platform','pwd','grp',"
                "'crypt','getpass','webbrowser','tkinter'}\n"
                "_orig_import = _bi.__import__\n"
                "def _safe_import(name, globals=None, locals=None,"
                " fromlist=(), level=0):\n"
                "    root = name.split('.')[0]\n"
                "    if root in _BLOCKED:\n"
                "        caller_file = ''\n"
                "        try:\n"
                "            if globals and '__file__' in globals:\n"
                "                caller_file = globals['__file__'] or ''\n"
                "            elif globals and '__name__' in globals:\n"
                "                caller_file = str(globals['__name__'])\n"
                "        except Exception:\n"
                "            pass\n"
                "        is_user_code = (\n"
                "            'user_code' in caller_file\n"
                "            or caller_file == '__main__'\n"
                "            or caller_file == ''\n"
                "        )\n"
                "        if is_user_code and level == 0:\n"
                "            raise ImportError('import ' + name +"
                " ' blocked')\n"
                "    return _orig_import(name, globals, locals,"
                " fromlist, level)\n"
                "_bi.__import__ = _safe_import\n"
            ) + code

            script = tmp_path / "user_code.py"
            script.write_text(wrapper, encoding="utf-8")
            cmd = [sys.executable, "-I", str(script)]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout, cwd=tmp, env=env,
                    check=False, bufsize=-1)
                stdout = (result.stdout or "")[:SANDBOX_MAX_OUTPUT]
                stderr = (result.stderr or "")[:SANDBOX_MAX_OUTPUT]
                success = (result.returncode == 0)
                return stdout, stderr, success
            except subprocess.TimeoutExpired:
                return "", f"⏱ Таймаут ({timeout}с)", False
            except Exception as e:
                return "", f"❌ {e}", False


# ═══════════════════════════════════════════════════════════
# MULTI-LANGUAGE RUNNER
# ═══════════════════════════════════════════════════════════
# Python идёт через строгий SandboxRunner (AST + bytecode).
# Остальные языки — через subprocess с rlimits.
#
# Установка интерпретаторов:
#   node       — sudo apt install nodejs
#   bash       — уже есть
#   ruby       — sudo apt install ruby
#   php        — sudo apt install php-cli
#   go         — sudo apt install golang
#   lua        — sudo apt install lua5.4
#   perl       — sudo apt install perl
#   Rscript    — sudo apt install r-base
# ═══════════════════════════════════════════════════════════

LANG_CONFIG: Dict[str, Dict[str, Any]] = {
    "python": {
        "ext": ".py",
        "check": None,
        "cmd": None,
        "name": "Python",
    },
    "javascript": {
        "ext": ".js",
        "check": "node",
        "cmd": ["node", "{src}"],
        "name": "JavaScript",
    },
    "typescript": {
        "ext": ".ts",
        "check": "ts-node",
        "cmd": ["ts-node", "{src}"],
        "name": "TypeScript",
    },
    "shell": {
        "ext": ".sh",
        "check": "bash",
        "cmd": ["bash", "{src}"],
        "name": "Shell / Bash",
    },
    "ruby": {
        "ext": ".rb",
        "check": "ruby",
        "cmd": ["ruby", "{src}"],
        "name": "Ruby",
    },
    "php": {
        "ext": ".php",
        "check": "php",
        "cmd": ["php", "{src}"],
        "name": "PHP",
    },
    "go": {
        "ext": ".go",
        "check": "go",
        "cmd": ["go", "run", "{src}"],
        "name": "Go",
    },
    "lua": {
        "ext": ".lua",
        "check": "lua",
        "cmd": ["lua", "{src}"],
        "name": "Lua",
    },
    "perl": {
        "ext": ".pl",
        "check": "perl",
        "cmd": ["perl", "{src}"],
        "name": "Perl",
    },
    "r": {
        "ext": ".R",
        "check": "Rscript",
        "cmd": ["Rscript", "{src}"],
        "name": "R",
    },
    "sql": {
        "ext": ".sql",
        "check": None,
        "cmd": None,
        "name": "SQL (SQLite)",
    },
    "html": {
        "ext": ".html",
        "check": None,
        "cmd": None,
        "name": "HTML",
    },
    "css": {
        "ext": ".css",
        "check": None,
        "cmd": None,
        "name": "CSS",
    },
    "markdown": {
        "ext": ".md",
        "check": None,
        "cmd": None,
        "name": "Markdown",
    },
    "json": {
        "ext": ".json",
        "check": None,
        "cmd": None,
        "name": "JSON",
    },
    "yaml": {
        "ext": ".yaml",
        "check": None,
        "cmd": None,
        "name": "YAML",
    },
    "xml": {
        "ext": ".xml",
        "check": None,
        "cmd": None,
        "name": "XML",
    },
}


def lang_available(lang: str) -> Tuple[bool, str]:
    """Проверить, установлен ли интерпретатор языка."""
    cfg = LANG_CONFIG.get(lang)
    if not cfg:
        return False, f"Язык «{lang}» не поддержан"
    check = cfg.get("check")
    if not check:
        return True, "ok"
    if shutil.which(check):
        return True, "ok"
    hints = {
        "node": "sudo apt install nodejs",
        "ts-node": "npm install -g ts-node",
        "bash": "уже установлен",
        "ruby": "sudo apt install ruby",
        "php": "sudo apt install php-cli",
        "go": "sudo apt install golang",
        "lua": "sudo apt install lua5.4",
        "perl": "sudo apt install perl",
        "Rscript": "sudo apt install r-base",
    }
    return False, hints.get(check, f"установите {check}")


class MultiLangRunner:
    """
    Запуск кода на нескольких языках.

    Python → SandboxRunner (AST + bytecode)
    Остальные → subprocess с rlimits
    """

    MAX_OUTPUT: Final[int] = SANDBOX_MAX_OUTPUT
    DEFAULT_TIMEOUT: Final[int] = CODE_TIMEOUT

    @classmethod
    def run(cls, code: str, lang: str = "python",
            timeout: int | None = None
            ) -> Tuple[str, str, bool]:
        timeout = timeout or cls.DEFAULT_TIMEOUT
        lang = (lang or "python").lower()

        if lang == "python":
            return SandboxRunner.run(code, timeout=timeout)

        if lang in ("html", "css", "markdown"):
            return cls._preview_marker(code, lang)

        if lang == "json":
            return cls._run_json(code)

        if lang == "yaml":
            return cls._run_yaml(code)

        if lang == "xml":
            return cls._run_xml(code)

        if lang == "sql":
            return cls._run_sql(code)

        return cls._run_subprocess(code, lang, timeout)

    # ─────────────────────────────────────────────
    # PREVIEW (HTML/CSS/MD)
    # ─────────────────────────────────────────────
    @classmethod
    def _preview_marker(cls, code: str, lang: str
                         ) -> Tuple[str, str, bool]:
        """Возвращает маркер __HTML__ — UI откроет preview."""
        if lang == "html":
            html = code
        elif lang == "css":
            html = (
                "<!DOCTYPE html><html><head>"
                "<meta charset='utf-8'>"
                f"<style>{code}</style></head>"
                "<body><h1>CSS Preview</h1>"
                "<p>Пример текста для просмотра стилей.</p>"
                "<button>Кнопка</button></body></html>")
        elif lang == "markdown":
            body = cls._markdown_to_html(code)
            html = (
                "<!DOCTYPE html><html><head>"
                "<meta charset='utf-8'>"
                "<style>body{font-family:system-ui,"
                "-apple-system,sans-serif;max-width:800px;"
                "margin:40px auto;padding:20px;"
                "line-height:1.6;color:#333}"
                "code{background:#f4f4f4;padding:2px 6px;"
                "border-radius:3px;font-family:monospace}"
                "pre{background:#f4f4f4;padding:12px;"
                "border-radius:6px;overflow:auto}"
                "blockquote{border-left:4px solid #ddd;"
                "margin:0;padding-left:16px;color:#666}"
                "</style></head><body>" + body + "</body></html>")
        else:
            return ("",
                    f"❌ Preview для «{lang}» не поддержан",
                    False)
        return f"__HTML__{html}", "", True

    # ─────────────────────────────────────────────
    # JSON
    # ─────────────────────────────────────────────
    @classmethod
    def _run_json(cls, code: str
                    ) -> Tuple[str, str, bool]:
        try:
            import json as _json
            data = _json.loads(code)
            pretty = _json.dumps(
                data, indent=2, ensure_ascii=False)
            return pretty, "", True
        except Exception as e:
            return "", f"❌ JSON error: {e}", False

    # ─────────────────────────────────────────────
    # YAML
    # ─────────────────────────────────────────────
    @classmethod
    def _run_yaml(cls, code: str
                    ) -> Tuple[str, str, bool]:
        try:
            import yaml as _yaml  # type: ignore
            data = _yaml.safe_load(code)
            out = _yaml.safe_dump(
                data, allow_unicode=True,
                sort_keys=False)
            return out, "", True
        except ImportError:
            try:
                import json as _json
                _json.loads(code)
                return ("✅ Валидный JSON (PyYAML не "
                        "установлен — pip install pyyaml)",
                        "", True)
            except Exception as e:
                return "", f"❌ YAML error: {e}", False
        except Exception as e:
            return "", f"❌ YAML error: {e}", False

    # ─────────────────────────────────────────────
    # XML
    # ─────────────────────────────────────────────
    @classmethod
    def _run_xml(cls, code: str
                   ) -> Tuple[str, str, bool]:
        try:
            import xml.dom.minidom as _md
            dom = _md.parseString(code)
            pretty = dom.toprettyxml(indent="  ")
            lines = [ln for ln in pretty.splitlines()
                     if ln.strip()]
            return "\n".join(lines), "", True
        except Exception as e:
            return "", f"❌ XML error: {e}", False

    # ─────────────────────────────────────────────
    # SQL (in-memory sqlite3)
    # ─────────────────────────────────────────────
    @classmethod
    def _run_sql(cls, code: str
                   ) -> Tuple[str, str, bool]:
        try:
            import sqlite3
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            statements = [s.strip() for s in code.split(";")
                          if s.strip()]
            results: list = []
            for stmt in statements:
                try:
                    cur.execute(stmt)
                    if cur.description:
                        rows = cur.fetchall()
                        if rows:
                            cols = [d[0] for d in
                                    cur.description]
                            results.append(" | ".join(cols))
                            results.append("-" * 50)
                            for r in rows:
                                results.append(
                                    " | ".join(
                                        str(r[c]) for c in cols))
                            results.append("")
                except Exception as e:
                    return ("",
                            f"❌ SQL error: {e}\n"
                            f"  запрос: {stmt[:100]}", False)
            conn.commit()
            conn.close()
            return "\n".join(results) or "✅ OK", "", True
        except Exception as e:
            return "", f"❌ {e}", False

    # ─────────────────────────────────────────────
    # UNIFIED subprocess (JS, Shell, Ruby, PHP, Go, ...)
    # ─────────────────────────────────────────────
    @classmethod
    def _run_subprocess(cls, code: str, lang: str,
                         timeout: int
                         ) -> Tuple[str, str, bool]:
        cfg = LANG_CONFIG.get(lang)
        if not cfg:
            return "", f"❌ Язык «{lang}» не поддержан", False

        ok, hint = lang_available(lang)
        if not ok:
            return "", f"❌ {hint}", False

        try:
            SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        with tempfile.TemporaryDirectory(
                prefix="nexus_ml_",
                dir=SANDBOX_DIR) as tmp:
            tmp_path = Path(tmp)
            script = tmp_path / f"user_code{cfg['ext']}"
            try:
                script.write_text(code, encoding="utf-8")
            except Exception as e:
                return ("",
                        f"❌ Не удалось записать файл: {e}",
                        False)

            env = {
                "PATH": os.environ.get(
                    "PATH", "/usr/bin:/bin"),
                "HOME": tmp,
                "TMPDIR": tmp,
                "LANG": "C.UTF-8",
                "PYTHONIOENCODING": "utf-8",
            }

            cmd = [c.replace("{src}", str(script))
                    for c in cfg["cmd"]]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout, cwd=tmp, env=env,
                    check=False, bufsize=-1)
                stdout = (result.stdout
                           or "")[:cls.MAX_OUTPUT]
                stderr = (result.stderr
                           or "")[:cls.MAX_OUTPUT]
                return stdout, stderr, result.returncode == 0
            except subprocess.TimeoutExpired:
                return ("",
                        f"⏱️ Таймаут ({timeout}с) — "
                        f"процесс убит", False)
            except FileNotFoundError:
                return ("",
                        f"❌ {cfg['check']} не найден — "
                        f"{hint}", False)
            except Exception as e:
                return "", f"❌ {e}", False

    # ─────────────────────────────────────────────
    # MARKDOWN → HTML (простой рендер)
    # ─────────────────────────────────────────────
    @staticmethod
    def _markdown_to_html(md: str) -> str:
        import html as _html
        import re as _re

        lines = md.splitlines()
        out: list = []
        in_code = False
        in_ul = False

        for line in lines:
            if line.strip().startswith("```"):
                if not in_code:
                    out.append("<pre><code>")
                    in_code = True
                else:
                    out.append("</code></pre>")
                    in_code = False
                continue
            if in_code:
                out.append(_html.escape(line))
                continue

            m = _re.match(r"^(#{1,6})\s+(.+)$", line)
            if m:
                lvl = len(m.group(1))
                out.append(
                    f"<h{lvl}>{_html.escape(m.group(2))}"
                    f"</h{lvl}>")
                continue

            if line.strip().startswith(("- ", "* ")):
                if not in_ul:
                    out.append("<ul>")
                    in_ul = True
                out.append(
                    f"<li>{_html.escape(line.strip()[2:])}</li>")
                continue
            elif in_ul:
                out.append("</ul>")
                in_ul = False

            if not line.strip():
                out.append("<br>")
                continue

            line = _html.escape(line)
            line = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
            line = _re.sub(r"\*(.+?)\*", r"<i>\1</i>", line)
            line = _re.sub(r"`(.+?)`", r"<code>\1</code>", line)
            out.append(f"<p>{line}</p>")

        if in_ul:
            out.append("</ul>")
        if in_code:
            out.append("</code></pre>")

        return "\n".join(out)


__all__ = [
    "CodeSandbox", "SandboxRunner", "MultiLangRunner",
    "validate_bytecode", "lang_available", "LANG_CONFIG",
    "_sandbox_worker", "_StdoutRedirect",
    "_FORBIDDEN_OPCODES", "_ALLOWED_OPCODES",
    "_SAFE_OPCODE_PREFIXES",
]