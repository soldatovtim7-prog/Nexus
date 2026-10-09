#!/usr/bin/env python3
"""
nexus_doctor_v2.py — БЕЗОПАСНАЯ диагностика NEXUS.

Отличия от v1:
  ✅ НИЧЕГО не удаляет и не меняет — только читает
  ✅ Сканирует ТОЛЬКО указанную папку (nexus/), не sys.path
  ✅ Учитывает классы (не путает методы разных классов)
  ✅ Учитывает пути файлов
  ✅ Не считает дубликатом метод в разных классах
  ✅ Показывает "что и где", но не трогает код

Использование:
  python3 nexus_doctor_v2.py             # диагностика nexus/
  python3 nexus_doctor_v2.py --verbose   # подробно
  python3 nexus_doctor_v2.py --out report.txt   # сохранить в файл
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


# ═══════════════════════════════════════════════════════════
# ЦВЕТА
# ═══════════════════════════════════════════════════════════
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"


def _c(text: str, color: str, *,
        bold: bool = False, dim: bool = False) -> str:
    if not sys.stdout.isatty():
        return text
    prefix = ""
    if bold:
        prefix += C.BOLD
    if dim:
        prefix += C.DIM
    return f"{prefix}{color}{text}{C.RESET}"


# ═══════════════════════════════════════════════════════════
# ПРОВЕРКИ
# ═══════════════════════════════════════════════════════════

class Diagnostics:
    def __init__(self) -> None:
        self.syntax_errors: List[Tuple[Path, str]] = []
        self.duplicates: Dict[str, List[Tuple[Path, int]]] = (
            defaultdict(list))
        self.stubs: List[Tuple[Path, str, int]] = []
        self.empty_methods: List[Tuple[Path, str, int]] = []
        self.long_methods: List[Tuple[Path, str, int, int]] = []
        self.todos: List[Tuple[Path, int, str]] = []
        self.unused_imports: Dict[Path, List[Tuple[str, int]]] = (
            defaultdict(list))

    def print_summary(self, verbose: bool = False) -> None:
        print()
        print(_c("═" * 62, C.CYAN, bold=True))
        print(_c("  📊 ИТОГОВЫЙ ОТЧЁТ", C.CYAN, bold=True))
        print(_c("═" * 62, C.CYAN, bold=True))
        print()

        # Синтаксис
        n_syn = len(self.syntax_errors)
        icon = "✅" if n_syn == 0 else "❌"
        color = C.GREEN if n_syn == 0 else C.RED
        print(f"  {icon} Синтаксические ошибки: "
              f"{_c(str(n_syn), color, bold=True)}")

        # Дубликаты
        n_dup = sum(len(v) for v in self.duplicates.values())
        icon = "✅" if n_dup == 0 else "⚠️"
        color = C.GREEN if n_dup == 0 else C.YELLOW
        print(f"  {icon} Дубликаты методов: "
              f"{_c(str(n_dup), color, bold=True)} "
              f"({len(self.duplicates)} классов)")

        # Stub'ы
        n_stub = len(self.stubs)
        icon = "✅" if n_stub == 0 else "🚧"
        color = C.GREEN if n_stub == 0 else C.MAGENTA
        print(f"  {icon} Stub-методы: "
              f"{_c(str(n_stub), color, bold=True)}")

        # Пустые методы
        n_empty = len(self.empty_methods)
        icon = "✅" if n_empty == 0 else "⚠️"
        color = C.GREEN if n_empty == 0 else C.YELLOW
        print(f"  {icon} Пустые (pass) методы: "
              f"{_c(str(n_empty), color, bold=True)}")

        # Длинные методы
        n_long = len(self.long_methods)
        icon = "✅" if n_long == 0 else "📏"
        color = C.GREEN if n_long == 0 else C.BLUE
        print(f"  {icon} Очень длинные (>100 строк): "
              f"{_c(str(n_long), color, bold=True)}")

        # TODO/FIXME
        n_todo = len(self.todos)
        icon = "✅" if n_todo == 0 else "📝"
        color = C.GREEN if n_todo == 0 else C.YELLOW
        print(f"  {icon} TODO/FIXME/HACK: "
              f"{_c(str(n_todo), color, bold=True)}")

        print()
        print(_c("═" * 62, C.CYAN, bold=True))
        print()

        # Детали
        if self.syntax_errors:
            print(_c("❌ СИНТАКСИЧЕСКИЕ ОШИБКИ",
                     C.RED, bold=True))
            print("-" * 62)
            for path, msg in self.syntax_errors:
                print(f"  {_c(path.name, C.RED)}: {msg}")
            print()

        if self.duplicates:
            print(_c("⚠️  ДУБЛИКАТЫ МЕТОДОВ",
                     C.YELLOW, bold=True))
            print("-" * 62)
            for key, occurrences in sorted(
                    self.duplicates.items()):
                print(f"  {_c(key, C.YELLOW)}:")
                for path, lineno in occurrences:
                    print(f"    • {path.name}:{lineno}")
            print()

        if self.stubs:
            print(_c("🚧 STUB-МЕТОДЫ (заглушки)",
                     C.MAGENTA, bold=True))
            print("-" * 62)
            for path, name, lineno in self.stubs:
                print(f"  {path.name}:{lineno} → "
                      f"{_c(name, C.MAGENTA)}")
            print()

        if self.empty_methods:
            print(_c("⚠️  ПУСТЫЕ МЕТОДЫ (pass)",
                     C.YELLOW, bold=True))
            print("-" * 62)
            for path, name, lineno in self.empty_methods:
                print(f"  {path.name}:{lineno} → {name}")
            print()

        if self.long_methods:
            print(_c("📏 ОЧЕНЬ ДЛИННЫЕ МЕТОДЫ",
                     C.BLUE, bold=True))
            print("-" * 62)
            for path, name, start, length in self.long_methods:
                print(f"  {path.name}:{start} → "
                      f"{name} ({length} строк)")
            print()

        if self.todos and verbose:
            print(_c("📝 TODO / FIXME / HACK",
                     C.YELLOW, bold=True))
            print("-" * 62)
            by_file: Dict[Path, List[Tuple[int, str]]] = (
                defaultdict(list))
            for path, lineno, text in self.todos:
                by_file[path].append((lineno, text))
            for path, items in by_file.items():
                print(f"  {path.name}:")
                for lineno, text in items:
                    print(f"    {lineno}: {text}")
            print()


# ═══════════════════════════════════════════════════════════
# АНАЛИЗ ФАЙЛА
# ═══════════════════════════════════════════════════════════

def analyze_file(path: Path,
                  diagnostics: Diagnostics) -> None:
    """Анализирует один файл."""
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        diagnostics.syntax_errors.append(
            (path, f"Не могу прочитать: {e}"))
        return

    # Синтаксис
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        diagnostics.syntax_errors.append(
            (path, f"Line {e.lineno}: {e.msg}"))
        return

    # Рекурсивный обход
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            _check_class(node, path, diagnostics)
        elif isinstance(node, ast.FunctionDef):
            # Модульные функции — тоже проверяем длину и pass
            _check_method(node, path, diagnostics,
                          class_name=None)

    # TODO/FIXME/HACK в комментариях и строках
    _find_todos(src, path, diagnostics)

    # Неиспользуемые импорты
    _find_unused_imports(tree, path, diagnostics)


def _check_class(cls_node: ast.ClassDef, path: Path,
                  diagnostics: Diagnostics) -> None:
    """Анализ методов внутри класса."""
    methods: Dict[str, List[ast.FunctionDef]] = defaultdict(list)

    for item in cls_node.body:
        if isinstance(item, ast.FunctionDef):
            methods[item.name].append(item)
            _check_method(item, path, diagnostics,
                          class_name=cls_node.name)

    # Дубликаты внутри одного класса
    for name, defs in methods.items():
        if len(defs) > 1:
            key = f"{cls_node.name}.{name}"
            for d in defs:
                diagnostics.duplicates[key].append(
                    (path, d.lineno))


def _check_method(node: ast.FunctionDef, path: Path,
                   diagnostics: Diagnostics,
                   class_name: str | None) -> None:
    """Анализ одного метода."""
    name = node.name
    if class_name:
        full_name = f"{class_name}.{name}"
    else:
        full_name = name

    # Stub
    body_src = ast.unparse(node)
    if "_stub_dialog" in body_src:
        diagnostics.stubs.append((path, full_name, node.lineno))

    # Пустой метод (только pass или ...)
    if len(node.body) == 1:
        only = node.body[0]
        if isinstance(only, ast.Pass):
            diagnostics.empty_methods.append(
                (path, full_name, node.lineno))
        elif (isinstance(only, ast.Expr)
                and isinstance(only.value, ast.Constant)
                and only.value.value is Ellipsis):
            diagnostics.empty_methods.append(
                (path, full_name, node.lineno))

    # Длина
    if hasattr(node, "end_lineno") and node.end_lineno:
        length = node.end_lineno - node.lineno + 1
        if length > 100:
            diagnostics.long_methods.append(
                (path, full_name, node.lineno, length))


def _find_todos(src: str, path: Path,
                 diagnostics: Diagnostics) -> None:
    """Ищет TODO/FIXME/HACK/XXX в комментариях."""
    markers = ("TODO", "FIXME", "HACK", "XXX")
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            for m in markers:
                if m in stripped:
                    diagnostics.todos.append(
                        (path, i, stripped[:80]))
                    break


def _find_unused_imports(tree: ast.Module, path: Path,
                          diagnostics: Diagnostics) -> None:
    """Находит неиспользуемые импорты (эвристика)."""
    imported: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno

    used: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)

    # Игнорируем импорты, начинающиеся с _
    # и известные side-effect импорты
    ignore_prefixes = ("_", "customtkinter", "PIL")
    for name, lineno in imported.items():
        if any(name.startswith(p) for p in ignore_prefixes):
            continue
        if name not in used:
            diagnostics.unused_imports[path].append(
                (name, lineno))


# ═══════════════════════════════════════════════════════════
# ГЛАВНАЯ
# ═══════════════════════════════════════════════════════════

def find_files(base: Path) -> List[Path]:
    """Находит только NEXUS-файлы (в папке nexus/ и корне)."""
    files: List[Path] = []

    # Пакет
    nexus_dir = base / "nexus"
    if nexus_dir.exists():
        files.extend(sorted(nexus_dir.glob("*.py")))

    # PRO-модули в корне
    for name in ("nexus_qr_pro.py", "nexus_code_pro.py",
                  "nexus_debugger.py", "nexus_ctk_patch.py"):
        p = base / name
        if p.exists():
            files.append(p)

    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEXUS Doctor v2 — безопасная диагностика")
    parser.add_argument("--path", type=str, default=".",
                        help="Корень проекта")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Подробный вывод")
    parser.add_argument("--out", type=str,
                        help="Сохранить отчёт в файл")
    args = parser.parse_args()

    base = Path(args.path).resolve()

    print()
    print(_c("╔" + "═" * 60 + "╗", C.CYAN, bold=True))
    print(_c("║  🩺 NEXUS DOCTOR v2 — БЕЗОПАСНАЯ ДИАГНОСТИКА" +
             " " * 13 + "║", C.CYAN, bold=True))
    print(_c("╚" + "═" * 60 + "╝", C.CYAN, bold=True))
    print()
    print(f"  📂 Проект: {base}")
    print(f"  🕐 Время:  "
          f"{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"  🛡️  Режим:  ТОЛЬКО ЧТЕНИЕ")
    print()

    # Файлы
    files = find_files(base)
    if not files:
        print(_c("  ❌ Не найдено файлов NEXUS", C.RED))
        return 1

    print(f"  📁 Файлов: {len(files)}")
    print()

    # Анализ
    print(_c("🔍 АНАЛИЗ", C.BLUE, bold=True))
    print("-" * 62)

    diagnostics = Diagnostics()
    for f in files:
        try:
            rel = f.relative_to(base)
        except ValueError:
            rel = f
        print(f"  📄 {rel}")
        analyze_file(f, diagnostics)

    print()
    diagnostics.print_summary(verbose=args.verbose)

    # Вердикт
    total_issues = (
        len(diagnostics.syntax_errors) +
        sum(len(v) for v in diagnostics.duplicates.values()) +
        len(diagnostics.stubs) +
        len(diagnostics.empty_methods)
    )

    if diagnostics.syntax_errors:
        print(_c("❌ ЕСТЬ СИНТАКСИЧЕСКИЕ ОШИБКИ — "
                 "проект может не запуститься!",
                 C.RED, bold=True))
        return 1

    if total_issues == 0:
        print(_c("✅ ПРОЕКТ ЗДОРОВ!", C.GREEN, bold=True))
        print()
        return 0

    print(_c(f"⚠️  Найдено проблем: {total_issues}",
             C.YELLOW, bold=True))
    print(_c("   Это ТОЛЬКО диагностика. Ничего не изменено.",
             C.DIM))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())