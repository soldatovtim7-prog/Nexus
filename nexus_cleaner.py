#!/usr/bin/env python3
"""
NEXUS Cleaner v1.0 — ищет мусор, чистит, чинит баги.

Возможности:
  🔍 Сканирование проекта
  🗑️ Удаление мусора (в _trash/, не безвозвратно)
  🔧 Авто-фиксы багов
  📋 Отчёт

Запуск:
  python3 nexus_cleaner.py                # только отчёт (dry-run)
  python3 nexus_cleaner.py --clean        # очистка + фиксы
  python3 nexus_cleaner.py --clean --yes  # без подтверждений
  python3 nexus_cleaner.py --restore      # восстановить из бэкапа
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
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


def _c(text: str, color: str = C.RESET, *,
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
# МУСОРНЫЕ ПАТТЕРНЫ
# ═══════════════════════════════════════════════════════════
JUNK_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "build",
    "dist",
    ".tox",
    "htmlcov",
    ".ipynb_checkpoints",
}

JUNK_FILES_PATTERNS = [
    r".*\.pyc$",
    r".*\.pyo$",
    r".*\.pyd$",
    r".*\.bak$",
    r".*\.tmp$",
    r".*\.log\.\d+$",
    r".*~$",
    r"\.DS_Store",
    r"Thumbs\.db",
    r"\.swp$",
    r"\.swo$",
]

# Файлы/папки, которые НЕ трогаем никогда
PROTECTED = {
    "nexus_data",       # данные пользователя
    "nexus_logs",       # логи
    "nexus_backups",    # бэкапы
    ".venv1",
    ".venv",
    "venv",
    ".git",
    "nexus",            # пакет
    "docs",
    "assets",
}


# ═══════════════════════════════════════════════════════════
# ШАГ 1: СКАНИРОВАНИЕ
# ═══════════════════════════════════════════════════════════
class JunkFinder:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.junk: List[Tuple[Path, int, str]] = []
        # (путь, размер, тип)

    def scan(self) -> None:
        """Найти весь мусор."""
        self._scan_dirs()
        self._scan_files()

    def _scan_dirs(self) -> None:
        for p in self.root.rglob("*"):
            if not p.is_dir():
                continue
            if self._is_protected(p):
                continue
            if p.name in JUNK_DIRS:
                size = self._dir_size(p)
                self.junk.append((p, size, "dir"))

    def _scan_files(self) -> None:
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            if self._is_protected(p):
                continue
            for pattern in JUNK_FILES_PATTERNS:
                if re.match(pattern, p.name):
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = 0
                    self.junk.append((p, size, "file"))
                    break

    def _is_protected(self, path: Path) -> bool:
        """Проверить, защищён ли путь."""
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return True
        parts = set(rel.parts)
        if parts & PROTECTED:
            return True
        # Не трогаем файлы внутри _trash, .backup
        if "_trash" in parts or ".nexus_backup" in parts:
            return True
        return False

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def total_size(self) -> int:
        return sum(size for _, size, _ in self.junk)

    def print_report(self) -> None:
        if not self.junk:
            print(_c("  ✅ Мусора не найдено!", C.GREEN, bold=True))
            return

        print()
        print(_c(f"  Найдено: {len(self.junk)} элементов "
                 f"({self._fmt_size(self.total_size())})",
                 C.YELLOW, bold=True))
        print()

        # Группировка по типу
        dirs = [(p, s) for p, s, t in self.junk if t == "dir"]
        files = [(p, s) for p, s, t in self.junk if t == "file"]

        if dirs:
            print(_c("  📁 Папки:", C.BLUE, bold=True))
            for p, size in sorted(dirs, key=lambda x: -x[1])[:20]:
                rel = p.relative_to(self.root)
                print(f"    • {rel}  "
                      f"{_c(self._fmt_size(size), C.DIM)}")
            if len(dirs) > 20:
                print(f"    ... и ещё {len(dirs) - 20}")

        if files:
            print()
            print(_c("  📄 Файлы:", C.BLUE, bold=True))
            for p, size in sorted(files, key=lambda x: -x[1])[:20]:
                rel = p.relative_to(self.root)
                print(f"    • {rel}  "
                      f"{_c(self._fmt_size(size), C.DIM)}")
            if len(files) > 20:
                print(f"    ... и ещё {len(files) - 20}")

    @staticmethod
    def _fmt_size(size: int) -> str:
        for u in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {u}"
            size /= 1024
        return f"{size:.1f} TB"


# ═══════════════════════════════════════════════════════════
# ШАГ 2: ЧИСТКА (в _trash/, не rm)
# ═══════════════════════════════════════════════════════════
class JunkCleaner:
    def __init__(self, root: Path,
                 trash_dir: Optional[Path] = None) -> None:
        self.root = root.resolve()
        self.trash = (trash_dir or self.root / "_trash").resolve()
        self.trash.mkdir(exist_ok=True)
        self.moved: List[Path] = []

    def clean(self, junk: List[Tuple[Path, int, str]]) -> int:
        """Переместить мусор в _trash/. Возвращает кол-во."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_session = self.trash / f"session_{ts}"
        trash_session.mkdir(exist_ok=True)

        moved = 0
        for path, _size, _type in junk:
            try:
                if not path.exists():
                    continue
                # Сохраняем структуру относительно root
                try:
                    rel = path.relative_to(self.root)
                except ValueError:
                    rel = Path(path.name)

                dst = trash_session / rel
                dst.parent.mkdir(parents=True, exist_ok=True)

                shutil.move(str(path), str(dst))
                self.moved.append(dst)
                moved += 1
                print(f"    🗑️  {rel}")
            except Exception as e:
                print(f"    ❌ {path.name}: {e}")

        # Записать метаданные для восстановления
        meta = {
            "timestamp": ts,
            "root": str(self.root),
            "session": str(trash_session),
            "moved": [str(p.relative_to(trash_session))
                      for p in self.moved],
        }
        (trash_session / ".cleaner_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8")

        return moved

    @staticmethod
    def restore(trash_dir: Path) -> int:
        """Восстановить последний session_*."""
        sessions = sorted(
            trash_dir.glob("session_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True)
        if not sessions:
            print(_c("  ❌ Нет сессий для восстановления",
                     C.RED))
            return 0

        latest = sessions[0]
        meta_path = latest / ".cleaner_meta.json"
        if not meta_path.exists():
            print(_c("  ❌ Не найден meta файл", C.RED))
            return 0

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        root = Path(meta["root"])
        moved = meta.get("moved", [])

        restored = 0
        for rel in moved:
            src = latest / rel
            dst = root / rel
            if not src.exists():
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                restored += 1
                print(f"    ↻ {rel}")
            except Exception as e:
                print(f"    ❌ {rel}: {e}")

        print()
        print(_c(f"  ✅ Восстановлено: {restored}",
                 C.GREEN, bold=True))
        return restored


# ═══════════════════════════════════════════════════════════
# ШАГ 3: АВТО-ФИКСЫ БАГОВ
# ═══════════════════════════════════════════════════════════
class BugFixer:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.backup_dir = (
            self.root / ".nexus_backup" /
            datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.fixes: List[str] = []

    def find_issues(self) -> List[Dict[str, Any]]:
        """Найти исправимые проблемы."""
        issues: List[Dict[str, Any]] = []

        # Ищем в основных файлах
        for name in ("app.py", "app_pages.py",
                     "storage.py", "domain.py",
                     "security.py", "qr.py"):
            path = self.root / "nexus" / name
            if not path.exists():
                continue
            issues.extend(self._check_file(path))

        return issues

    def _check_file(self, path: Path) -> List[Dict[str, Any]]:
        """Проверить один файл."""
        issues: List[Dict[str, Any]] = []
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            return issues

        # Проблема 1: Config.save без default=
        if "json.dump(self.__dict__" in src and \
                "default=" not in src.split(
                    "json.dump(self.__dict__")[1][:200]:
            issues.append({
                "file": path,
                "type": "config_save_posixpath",
                "desc": ("AppConfig.save() не обрабатывает "
                          "PosixPath — добавим default="),
            })

        # Проблема 2: Path используется без импорта
        if "Path(" in src and "from pathlib import Path" \
                not in src and "import pathlib" not in src:
            issues.append({
                "file": path,
                "type": "missing_path_import",
                "desc": ("Path используется, но не импортирован "
                          "— добавим from pathlib import Path"),
            })

        # Проблема 3: дубликаты методов внутри одного класса
        try:
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods: Dict[str, List[ast.FunctionDef]] = {}
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.setdefault(item.name, []).append(
                            item)
                for name, defs in methods.items():
                    if len(defs) > 1:
                        # Проверяем, есть ли stub среди них
                        for d in defs:
                            body = ast.unparse(d)
                            if "_stub_dialog" in body:
                                issues.append({
                                    "file": path,
                                    "type": "duplicate_stub",
                                    "desc": (
                                        f"Класс {node.name}: "
                                        f"метод {name} имеет "
                                        f"stub-версию "
                                        f"(строка {d.lineno})"),
                                    "lineno": d.lineno,
                                    "class": node.name,
                                    "method": name,
                                    "end_lineno": d.end_lineno,
                                })
                                break
        except SyntaxError:
            pass

        return issues

    def fix_config_save(self, path: Path) -> bool:
        """Починить Config.save — добавить default=."""
        try:
            src = path.read_text(encoding="utf-8")

            # Ищем старую версию
            old = (
                "            with open(tmp, \"w\", "
                "encoding=\"utf-8\") as f:\n"
                "                json.dump(self.__dict__, f, "
                "indent=2,\n"
                "                          ensure_ascii=False)\n"
            )
            if old not in src:
                return False

            new = (
                "            def _default(obj: Any) -> Any:\n"
                "                from pathlib import Path as _Path\n"
                "                if isinstance(obj, _Path):\n"
                "                    return str(obj)\n"
                "                if isinstance(obj, (set, tuple)):\n"
                "                    return list(obj)\n"
                "                if isinstance(obj, bytes):\n"
                "                    return obj.decode(\n"
                "                        \"utf-8\", errors=\"replace\")\n"
                "                return str(obj)\n"
                "            with open(tmp, \"w\", "
                "encoding=\"utf-8\") as f:\n"
                "                json.dump(self.__dict__, f, "
                "indent=2,\n"
                "                          ensure_ascii=False,\n"
                "                          default=_default)\n"
            )
            src = src.replace(old, new)
            self._backup(path)
            path.write_text(src, encoding="utf-8")
            self.fixes.append(
                f"✅ {path.name}: Config.save → default=")
            return True
        except Exception as e:
            print(f"    ❌ {e}")
            return False

    def fix_missing_path(self, path: Path) -> bool:
        """Добавить from pathlib import Path."""
        try:
            src = path.read_text(encoding="utf-8")

            # Ищем подходящее место после first imports
            lines = src.splitlines(keepends=True)
            insert_at = 0
            in_docstring = False
            for i, line in enumerate(lines[:50]):
                stripped = line.strip()
                if stripped.startswith('"""') or \
                        stripped.startswith("'''"):
                    in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                if stripped.startswith("from ") or \
                        stripped.startswith("import "):
                    insert_at = i + 1
                elif stripped and not stripped.startswith("#") \
                        and insert_at > 0:
                    break

            if insert_at == 0:
                return False

            # Проверяем, что такого импорта нет
            if "from pathlib import Path" in src:
                return False

            lines.insert(insert_at,
                         "from pathlib import Path  "
                         "# auto-fix\n")

            self._backup(path)
            path.write_text("".join(lines), encoding="utf-8")
            self.fixes.append(
                f"✅ {path.name}: +from pathlib import Path")
            return True
        except Exception as e:
            print(f"    ❌ {e}")
            return False

    def fix_duplicate_stub(self, path: Path,
                            info: Dict[str, Any]) -> bool:
        """Удалить stub-версию дублирующегося метода."""
        try:
            src = path.read_text(encoding="utf-8")
            lines = src.splitlines(keepends=True)

            start = info["lineno"] - 1
            end = info["end_lineno"]

            # Удаляем stub
            del lines[start:end]

            self._backup(path)
            path.write_text("".join(lines), encoding="utf-8")
            self.fixes.append(
                f"✅ {path.name}: удалён stub "
                f"{info['class']}.{info['method']}")
            return True
        except Exception as e:
            print(f"    ❌ {e}")
            return False

    def _backup(self, path: Path) -> None:
        """Сделать бэкап файла."""
        try:
            rel = path.relative_to(self.root)
            dst = self.backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copy2(path, dst)
        except Exception:
            pass

    def apply_fixes(self, issues: List[Dict[str, Any]]) -> int:
        """Применить все фиксы."""
        if not issues:
            print(_c("  ✅ Нет исправимых проблем",
                     C.GREEN, bold=True))
            return 0

        print()
        print(_c(f"  Найдено проблем: {len(issues)}",
                 C.YELLOW, bold=True))
        print()

        applied = 0
        # Группировка по файлу
        by_file: Dict[Path, List[Dict[str, Any]]] = {}
        for iss in issues:
            by_file.setdefault(iss["file"], []).append(iss)

        for path, file_issues in by_file.items():
            print(f"  📄 {path.name}:")

            # Сортируем: дубликаты вниз (чтобы lineno не сбивались)
            file_issues.sort(
                key=lambda x: 0 if x["type"] != "duplicate_stub"
                else 1)

            for iss in file_issues:
                if iss["type"] == "config_save_posixpath":
                    if self.fix_config_save(path):
                        applied += 1
                elif iss["type"] == "missing_path_import":
                    if self.fix_missing_path(path):
                        applied += 1
                elif iss["type"] == "duplicate_stub":
                    if self.fix_duplicate_stub(path, iss):
                        applied += 1

        return applied


# ═══════════════════════════════════════════════════════════
# ГЛАВНАЯ
# ═══════════════════════════════════════════════════════════
def find_root() -> Path:
    """Найти корень проекта."""
    # Ищем маркеры проекта
    cwd = Path.cwd().resolve()
    for candidate in [cwd, cwd.parent, cwd.parent.parent]:
        if (candidate / "nexus").is_dir():
            return candidate
        if (candidate / "nexus-project" / "nexus").is_dir():
            return candidate / "nexus-project"
    return cwd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NEXUS Cleaner — чистит мусор и фиксит баги")
    parser.add_argument("--clean", action="store_true",
                        help="Чистить и фиксить")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Без подтверждений")
    parser.add_argument("--restore", action="store_true",
                        help="Восстановить из _trash/")
    parser.add_argument("--path", type=str,
                        help="Корень проекта")
    parser.add_argument("--no-junk", action="store_true",
                        help="Не чистить мусор")
    parser.add_argument("--no-fix", action="store_true",
                        help="Не фиксить баги")
    args = parser.parse_args()

    root = Path(args.path).resolve() \
        if args.path else find_root()

    print()
    print(_c("╔" + "═" * 58 + "╗", C.CYAN, bold=True))
    print(_c("║  🧹 NEXUS CLEANER v1.0" +
             " " * 36 + "║", C.CYAN, bold=True))
    print(_c("╚" + "═" * 58 + "╝", C.CYAN, bold=True))
    print()
    print(f"  📂 Проект: {root}")
    print(f"  🕐 Время:  "
          f"{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print()

    # Восстановление
    if args.restore:
        print(_c("♻️  ВОССТАНОВЛЕНИЕ", C.MAGENTA, bold=True))
        print("-" * 60)
        JunkCleaner.restore(root / "_trash")
        return 0

    # Сканирование мусора
    if not args.no_junk:
        print(_c("🔍 СКАНИРОВАНИЕ МУСОРА", C.BLUE, bold=True))
        print("-" * 60)
        finder = JunkFinder(root)
        finder.scan()
        finder.print_report()
        print()

        if args.clean and finder.junk:
            if not args.yes:
                resp = input(
                    f"  {_c('🗑️  Переместить в _trash/ ?', C.YELLOW, bold=True)}"
                    f" [y/N] ").strip().lower()
                if resp not in ("y", "yes", "д", "да"):
                    print(_c("  Отменено", C.DIM))
                else:
                    cleaner = JunkCleaner(root)
                    moved = cleaner.clean(finder.junk)
                    print()
                    print(_c(f"  ✅ Перемещено: {moved}",
                             C.GREEN, bold=True))
            else:
                cleaner = JunkCleaner(root)
                moved = cleaner.clean(finder.junk)
                print()
                print(_c(f"  ✅ Перемещено: {moved}",
                         C.GREEN, bold=True))
            print()
            print(_c(f"  ♻️  Восстановить: "
                     f"python3 nexus_cleaner.py --restore",
                     C.DIM))

    # Фикс багов
    if not args.no_fix:
        print()
        print(_c("🔧 ПОИСК БАГОВ", C.BLUE, bold=True))
        print("-" * 60)

        fixer = BugFixer(root)
        issues = fixer.find_issues()

        if not issues:
            print(_c("  ✅ Нет исправимых багов",
                     C.GREEN, bold=True))
        else:
            # Показать что нашли
            for iss in issues:
                print(f"  ⚠️  {iss['file'].name}: "
                      f"{iss['desc']}")
            print()

            if args.clean:
                if not args.yes:
                    resp = input(
                        f"  {_c('🔧 Применить фиксы?', C.YELLOW, bold=True)}"
                        f" [y/N] ").strip().lower()
                    if resp not in ("y", "yes", "д", "да"):
                        print(_c("  Отменено", C.DIM))
                        issues = []
                if issues:
                    applied = fixer.apply_fixes(issues)
                    print()
                    print(_c(
                        f"  ✅ Применено фиксов: {applied}",
                        C.GREEN, bold=True))
                    if applied > 0:
                        print()
                        print(_c(
                            f"  💾 Бэкап: {fixer.backup_dir}",
                            C.DIM))
                        print(_c(
                            "  ♻️  Откатить: скопируй файлы из бэкапа",
                            C.DIM))
            else:
                print(_c("  💡 Запусти с --clean, чтобы применить",
                         C.DIM))

    # Итог
    print()
    print(_c("═" * 60, C.CYAN, bold=True))
    print()

    if not args.clean:
        print(_c("  💡 Это dry-run. Ничего не изменено.",
                 C.YELLOW, bold=True))
        print(_c("     Запусти с --clean чтобы применить.",
                 C.DIM))

    return 0


if __name__ == "__main__":
    sys.exit(main())