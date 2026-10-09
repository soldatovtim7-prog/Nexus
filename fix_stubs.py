"""
fix_stubs.py — удаляет дублирующиеся stub-методы.

Логика:
  1. Парсит app.py через ast
  2. Находит все методы класса NexusApp
  3. Находит дубликаты (по имени)
  4. Если один из дубликатов содержит self._stub_dialog —
     удаляет его (stub)
  5. Сохраняет обратно
"""
import ast
import re
from pathlib import Path


def fix_file(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)

    # Парсим AST
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"❌ Синтаксис: {e}")
        return

    # Находим класс NexusApp
    nexus_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "NexusApp":
            nexus_cls = node
            break
    if nexus_cls is None:
        print("❌ NexusApp не найден")
        return

    # Группируем методы по имени
    methods: dict[str, list[ast.FunctionDef]] = {}
    for item in nexus_cls.body:
        if isinstance(item, ast.FunctionDef):
            methods.setdefault(item.name, []).append(item)

    # Ищем дубликаты
    to_delete: list[tuple[int, int]] = []  # (lineno, end_lineno)
    for name, defs in methods.items():
        if len(defs) <= 1:
            continue
        # Сортируем по lineno — первый самый ранний
        defs_sorted = sorted(defs, key=lambda d: d.lineno)
        print(f"⚠️  {name}: {len(defs)} определений "
              f"(строки {[d.lineno for d in defs_sorted]})")

        # Ищем stub среди дубликатов
        for d in defs_sorted:
            # Проверяем, есть ли в теле _stub_dialog
            body_src = ast.unparse(d)
            if "_stub_dialog" in body_src:
                print(f"   → удаляю stub на строке {d.lineno}")
                to_delete.append((d.lineno, d.end_lineno))

    if not to_delete:
        print("✅ Дубликатов со stub не найдено")
        return

    # Удаляем диапазоны (в обратном порядке, чтобы не сбить индексы)
    to_delete.sort(reverse=True)
    for start, end in to_delete:
        # +0 индексация: строка start = index start-1
        del lines[start - 1:end]

    path.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Удалено блоков: {len(to_delete)}")


if __name__ == "__main__":
    app_py = Path("nexus/app.py")
    if not app_py.exists():
        print("❌ nexus/app.py не найден")
        exit(1)
    print(f"📝 Обрабатываю: {app_py}")
    print()
    fix_file(app_py)