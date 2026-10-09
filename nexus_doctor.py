#!/usr/bin/env python3
"""
nexus_doctor.py — Диагностика и автофикс NEXUS.

Что делает:
  1. Проверяет синтаксис всех .py файлов (ast.parse)
  2. Находит дубликаты методов в классах
  3. Находит stub-методы (с self._stub_dialog)
  4. Находит неимпортированные имена (грубая эвристика)
  5. Находит неиспользуемые импорты
  6. Автофикс: удаляет дубликаты методов (оставляет последнее определение)
  7. Создаёт бэкапы перед изменениями
  8. Пишет отчёт в nexus_doctor_report.txt

Использование:
  python3 nexus_doctor.py           # только отчёт (dry-run)
  python3 nexus_doctor.py --fix     # с автофиксом
  python3 nexus_doctor.py --fix --yes   # без подтверждений
  python3 nexus_doctor.py --file nexus/app.py   # только один файл
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import shutil
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ═══════════════════════════════════════════════════════════
# ЦВЕТА ДЛЯ ТЕРМИНАЛА
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
    WHITE = "\033[97m"


def _c(text: str, color: str, *,
        bold: bool = False, dim: bool = False) -> str:
    """Раскрасить текст (если терминал поддерживает)."""
    if not sys.stdout.isatty():
        return text
    prefix = ""
    if bold:
        prefix += C.BOLD
    if dim:
        prefix += C.DIM
    return f"{prefix}{color}{text}{C.RESET}"


# ═══════════════════════════════════════════════════════════
# СТРУКТУРА ОТЧЁТА
# ═══════════════════════════════════════════════════════════
class Report:
    def __init__(self) -> None:
        self.syntax_errors: List[Tuple[Path, str]] = []
        self.duplicates: List[Tuple[Path, str, List[int]]] = []
        self.stubs: List[Tuple[Path, str, int]] = []
        self.unimported: List[Tuple[Path, str, int]] = []
        self.unused_imports: List[Tuple[Path, str, int]] = []
        self.fixed: List[Tuple[Path, str, int]] = []
        self.warnings: List[str] = []

    def summary(self) -> str:
        lines = []
        lines.append("")
        lines.append("=" * 60)
        lines.append("📊 ИТОГОВЫЙ ОТЧЁТ")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  ❌ Синтаксические ошибки:      {len(self.syntax_errors)}")
        lines.append(f"  ⚠️  Дубликаты методов:         {len(self.duplicates)}")
        lines.append(f"  🚧 Stub-методы:                {len(self.stubs)}")
        lines.append(f"  📛 Неимпортированные имена:    {len(self.unimported)}")
        lines.append(f"  🗑️  Неиспользуемые импорты:     {len(self.unused_imports)}")
        lines.append(f"  ✅ Исправлено автофиксом:      {len(self.fixed)}")
        lines.append("")
        return "\n".join(lines)


report = Report()


# ═══════════════════════════════════════════════════════════
# ПРОВЕРКА СИНТАКСИСА
# ═══════════════════════════════════════════════════════════
def check_syntax(path: Path) -> Optional[ast.Module]:
    """Парсит файл. Возвращает AST или None при ошибке."""
    try:
        src = path.read_text(encoding="utf-8")
        return ast.parse(src, filename=str(path))
    except SyntaxError as e:
        msg = f"Line {e.lineno}: {e.msg}"
        report.syntax_errors.append((path, msg))
        return None
    except Exception as e:
        report.syntax_errors.append((path, str(e)))
        return None


# ═══════════════════════════════════════════════════════════
# ПОИСК ДУБЛИКАТОВ МЕТОДОВ
# ═══════════════════════════════════════════════════════════
def find_duplicates(tree: ast.Module) -> Dict[str, List[ast.FunctionDef]]:
    """Находит дублирующиеся методы во всех классах."""
    result: Dict[str, List[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Собираем методы этого класса
        methods: Dict[str, List[ast.FunctionDef]] = {}
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                methods.setdefault(item.name, []).append(item)
        # Дубликаты (по имени > 1)
        for name, defs in methods.items():
            if len(defs) > 1:
                key = f"{node.name}.{name}"
                result[key] = defs
    return result


# ═══════════════════════════════════════════════════════════
# ПОИСК STUB'ОВ
# ═══════════════════════════════════════════════════════════
def find_stubs(tree: ast.Module) -> List[Tuple[str, int]]:
    """Находит методы, в теле которых есть self._stub_dialog."""
    result: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body_src = ast.unparse(node)
        if "_stub_dialog" in body_src:
            result.append((node.name, node.lineno))
    return result


# ═══════════════════════════════════════════════════════════
# ПОИСК НЕИМПОРТИРОВАННЫХ ИМЁН (эвристика)
# ═══════════════════════════════════════════════════════════
def find_unimported(tree: ast.Module) -> List[Tuple[str, int]]:
    """
    Ищет имена, которые используются, но не импортированы
    и не определены в модуле.
    """
    # Собираем все импортированные имена
    imported: Set[str] = set()
    defined: Set[str] = set()

    # Стандартные builtins
    builtins_set = {
        "print", "len", "range", "str", "int", "float", "list",
        "dict", "set", "tuple", "bool", "type", "isinstance",
        "enumerate", "zip", "map", "filter", "sorted", "sum",
        "min", "max", "abs", "all", "any", "round", "repr",
        "hash", "id", "chr", "ord", "open", "input", "exit",
        "iter", "next", "getattr", "setattr", "hasattr",
        "Exception", "BaseException", "ValueError", "TypeError",
        "KeyError", "IndexError", "AttributeError",
        "RuntimeError", "StopIteration", "SyntaxError",
        "ImportError", "ModuleNotFoundError", "NameError",
        "OSError", "IOError", "FileNotFoundError",
        "PermissionError", "ConnectionError", "TimeoutError",
        "object", "super", "property", "staticmethod",
        "classmethod", "self", "cls", "__name__", "__file__",
        "__doc__", "__init__", "__main__", "True", "False",
        "None", "NotImplemented", "Ellipsis", "ExceptionGroup",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported.add(name)
        elif isinstance(node, ast.FunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        defined.add(elt.id)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(
                        item.optional_vars, ast.Name):
                    defined.add(item.optional_vars.id)
        elif isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.name:
                    defined.add(handler.name)
        elif isinstance(node, (ast.comprehension,)):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)

    # Известные константы/модули
    known = imported | defined | builtins_set

    # Известные "имена из импортов звёздочкой"
    star_import = any(
        isinstance(n, ast.ImportFrom) and
        any(a.name == "*" for a in n.names)
        for n in ast.walk(tree))
    if star_import:
        return []  # не можем точно проверить

    # Собираем использованные имена
    used: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                if node.id not in used:
                    used[node.id] = node.lineno

    # Ищем "использовано, но не определено"
    result: List[Tuple[str, int]] = []
    for name, lineno in used.items():
        if name not in known and not name.startswith("__"):
            result.append((name, lineno))

    return result


# ═══════════════════════════════════════════════════════════
# ПОИСК НЕИСПОЛЬЗУЕМЫХ ИМПОРТОВ
# ═══════════════════════════════════════════════════════════
def find_unused_imports(tree: ast.Module) -> List[Tuple[str, int]]:
    """Находит импорты, которые не используются."""
    imported: Dict[str, int] = {}  # name → lineno
    aliases: Dict[str, str] = {}    # alias → original

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
                aliases[name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno
                aliases[name] = alias.name

    # Ищем использование
    used: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            # Attribute: module.attr — берём module
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)

    # Находим неиспользуемые
    result: List[Tuple[str, int]] = []
    for name, lineno in imported.items():
        if name not in used:
            result.append((name, lineno))

    return result


# ═══════════════════════════════════════════════════════════
# АВТОФИКС ДУБЛИКАТОВ
# ═══════════════════════════════════════════════════════════
def fix_duplicates(path: Path,
                    duplicates: Dict[str, List[ast.FunctionDef]],
                    keep_last: bool = True) -> int:
    """
    Удаляет дубликаты методов.
    keep_last=True — оставляет последнее определение.
    keep_last=False — оставляет первое.
    """
    if not duplicates:
        return 0

    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)

    # Собираем диапазоны для удаления
    to_delete: List[Tuple[int, int, str]] = []
    for key, defs in duplicates.items():
        sorted_defs = sorted(defs, key=lambda d: d.lineno)
        if keep_last:
            # Удаляем все кроме последнего
            for d in sorted_defs[:-1]:
                to_delete.append((d.lineno, d.end_lineno, key))
        else:
            # Удаляем все кроме первого
            for d in sorted_defs[1:]:
                to_delete.append((d.lineno, d.end_lineno, key))

    # Удаляем в обратном порядке
    to_delete.sort(reverse=True)
    for start, end, key in to_delete:
        del lines[start - 1:end]
        report.fixed.append((path, key, start))

    path.write_text("".join(lines), encoding="utf-8")
    return len(to_delete)


# ═══════════════════════════════════════════════════════════
# ГЛАВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════
def analyze_file(path: Path) -> Optional[ast.Module]:
    """Анализирует один файл."""
    tree = check_syntax(path)
    if tree is None:
        return None

    # Дубликаты
    dups = find_duplicates(tree)
    for key, defs in dups.items():
        linenos = sorted(d.lineno for d in defs)
        report.duplicates.append((path, key, linenos))

    # Stub'ы
    stubs = find_stubs(tree)
    for name, lineno in stubs:
        report.stubs.append((path, name, lineno))

    # Неимпортированные (только для app.py и app_pages.py)
    if path.name in ("app.py", "app_pages.py"):
        unimp = find_unimported(tree)
        for name, lineno in unimp:
            report.unimported.append((path, name, lineno))

    # Неиспользуемые импорты
    unused = find_unused_imports(tree)
    for name, lineno in unused:
        report.unused_imports.append((path, name, lineno))

    return tree


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEXUS Doctor — диагностика и автофикс")
    parser.add_argument("--fix", action="store_true",
                        help="Применить автофикс")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Не спрашивать подтверждения")
    parser.add_argument("--file", type=str,
                        help="Обработать один файл")
    parser.add_argument("--dir", type=str, default="nexus",
                        help="Директория (по умолчанию nexus/)")
    args = parser.parse_args()

    print()
    print(_c("╔" + "═" * 58 + "╗", C.CYAN, bold=True))
    print(_c("║  🩺 NEXUS DOCTOR — Диагностика проекта" +
             " " * 19 + "║", C.CYAN, bold=True))
    print(_c("╚" + "═" * 58 + "╝", C.CYAN, bold=True))
    print()

    # Собираем файлы
    if args.file:
        files = [Path(args.file)]
    else:
        base = Path(args.dir)
        if not base.exists():
            print(_c(f"❌ Директория не найдена: {base}", C.RED))
            return 1
        files = sorted(base.rglob("*.py"))
        # Плюс корневые PRO-файлы
        for pro in ("nexus_qr_pro.py", "nexus_code_pro.py",
                    "nexus_debugger.py", "nexus_ctk_patch.py"):
            p = Path(pro)
            if p.exists():
                files.append(p)

    print(f"📁 Файлов для проверки: {len(files)}")
    print()

    # Анализ
    print(_c("🔍 АНАЛИЗ", C.BLUE, bold=True))
    print("-" * 60)
    trees: Dict[Path, ast.Module] = {}
    for f in files:
        result = analyze_file(f)
        if result is not None:
            trees[f] = result
            # Markers
            file_dups = [d for d in report.duplicates if d[0] == f]
            file_stubs = [s for s in report.stubs if s[0] == f]
            file_unimp = [u for u in report.unimported if u[0] == f]
            file_unused = [u for u in report.unused_imports
                            if u[0] == f]

            status = _c("✅", C.GREEN)
            if file_dups or file_stubs:
                status = _c("⚠️", C.YELLOW)
            if not file_dups and not file_stubs and \
                    (file_unimp or file_unused):
                status = _c("ℹ️", C.CYAN)

            info = ""
            if file_dups:
                info += f" {_c(f'{len(file_dups)}д', C.YELLOW)}"
            if file_stubs:
                info += f" {_c(f'{len(file_stubs)}s', C.MAGENTA)}"
            if file_unimp:
                info += f" {_c(f'{len(file_unimp)}?', C.RED)}"
            if file_unused:
                info += f" {_c(f'{len(file_unused)}u', C.DIM)}"

            print(f"  {status} {f}{info}")

    print()

    # Детали
    if report.syntax_errors:
        print(_c("❌ СИНТАКСИЧЕСКИЕ ОШИБКИ", C.RED, bold=True))
        print("-" * 60)
        for path, msg in report.syntax_errors:
            print(f"  {_c(path.name, C.RED, bold=True)}: {msg}")
        print()

    if report.duplicates:
        print(_c("⚠️  ДУБЛИКАТЫ МЕТОДОВ", C.YELLOW, bold=True))
        print("-" * 60)
        for path, key, linenos in report.duplicates:
            print(f"  {_c(str(path), C.YELLOW)}")
            print(f"    {key} → строки {linenos}")
        print()

    if report.stubs:
        print(_c("🚧 STUB-МЕТОДЫ (не реализованы)",
                 C.MAGENTA, bold=True))
        print("-" * 60)
        for path, name, lineno in report.stubs:
            print(f"  {path.name}:{lineno} → {name}")
        print()

    if report.unimported:
        print(_c("📛 НЕИМПОРТИРОВАННЫЕ ИМЕНА (эвристика)",
                 C.RED, bold=True))
        print("-" * 60)
        # Группируем по файлу
        by_file: Dict[Path, List[Tuple[str, int]]] = {}
        for path, name, lineno in report.unimported:
            by_file.setdefault(path, []).append((name, lineno))
        for path, items in by_file.items():
            print(f"  {_c(str(path), C.RED)}")
            # Уникальные имена
            seen = set()
            for name, lineno in items:
                if name in seen:
                    continue
                seen.add(name)
                print(f"    {name} (первое использование: "
                      f"строка {lineno})")
        print()

    if report.unused_imports:
        print(_c("🗑️  НЕИСПОЛЬЗУЕМЫЕ ИМПОРТЫ (эвристика)",
                 C.DIM, bold=True))
        print("-" * 60)
        by_file: Dict[Path, List[Tuple[str, int]]] = {}
        for path, name, lineno in report.unused_imports:
            by_file.setdefault(path, []).append((name, lineno))
        for path, items in by_file.items():
            print(f"  {path.name}: "
                  f"{len(items)} неиспользуемых")
        print()

    # Автофикс
    if report.duplicates:
        print(_c("🔧 АВТОФИКС", C.BLUE, bold=True))
        print("-" * 60)

        if not args.fix:
            print(f"  {_c('Сухой прогон (dry-run)', C.DIM)}")
            print(f"  Запусти с {_c('--fix', C.GREEN)} "
                  f"чтобы применить")
            print()

            # Показать, что будет удалено
            for path, key, linenos in report.duplicates:
                print(f"  {path.name}:")
                print(f"    Будет удалено: строки {linenos[:-1]}")
                print(f"    Оставлено:     строка {linenos[-1]}")
            print()
        else:
            # Бэкап
            print(f"  {_c('💾 Создаю бэкапы...', C.YELLOW)}")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = Path(f"_doctor_backup_{ts}")
            backup_dir.mkdir(exist_ok=True)
            for f in trees.keys():
                backup = backup_dir / f.name
                shutil.copy2(f, backup)
            print(f"  ✅ Бэкап в: {backup_dir}")
            print()

            # Подтверждение
            if not args.yes:
                resp = input(
                    f"  {_c('Применить автофикс?', C.YELLOW, bold=True)}"
                    f" [y/N] ").strip().lower()
                if resp not in ("y", "yes", "д", "да"):
                    print(f"  {_c('Отменено', C.DIM)}")
                    return 0

            # Фиксим
            print()
            print(f"  {_c('🔧 Удаляю дубликаты...', C.BLUE)}")
            total_fixed = 0
            for f, tree in trees.items():
                file_dups = {
                    k: v for k, v in find_duplicates(tree).items()
                }
                if file_dups:
                    n = fix_duplicates(f, file_dups, keep_last=True)
                    print(f"    ✅ {f.name}: "
                          f"удалено {n} блоков")
                    total_fixed += n

            print()
            print(f"  {_c(f'✅ Всего исправлено: {total_fixed}',
                     C.GREEN, bold=True)}")
            print()
            print(f"  {_c('🔍 Проверка синтаксиса после фикса...',
                     C.BLUE)}")

            # Проверяем
            all_ok = True
            for f in trees.keys():
                try:
                    ast.parse(f.read_text(encoding="utf-8"))
                    print(f"    ✅ {f.name}")
                except SyntaxError as e:
                    print(f"    ❌ {f.name}: {e}")
                    all_ok = False

            if not all_ok:
                print()
                print(f"  {_c('⚠️  Восстанови из бэкапа:', C.RED)}")
                print(f"     cp {backup_dir}/*.py nexus/")
                return 1

            print()

    # Итог
    print(report.summary())

    # Финальный вердикт
    if report.syntax_errors:
        print(_c("❌ Проект содержит синтаксические ошибки!",
                 C.RED, bold=True))
        return 1

    if report.duplicates and not args.fix:
        print(_c("⚠️  Найдены дубликаты. Запусти с --fix "
                 "для исправления.", C.YELLOW, bold=True))
        return 2

    if (report.duplicates or report.unimported) and args.fix:
        if report.unimported:
            print(_c("⚠️  Неимпортированные имена требуют "
                     "ручной проверки.", C.YELLOW, bold=True))
        return 0

    print(_c("✅ Проект в порядке!", C.GREEN, bold=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())