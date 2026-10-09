"""
fix_duplicates.py — удаляет первую версию дублирующихся методов.

Логика:
  1. Парсит app.py через ast
  2. Находит все методы класса NexusApp
  3. Находит дубликаты (по имени)
  4. Удаляет ПЕРВУЮ версию (оставляет последнюю — актуальную)
  5. Сохраняет обратно
"""
import ast
from pathlib import Path


def fix_file(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f"❌ Синтаксис: {e}")
        return

    nexus_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "NexusApp":
            nexus_cls = node
            break
    if nexus_cls is None:
        print("❌ NexusApp не найден")
        return

    methods: dict[str, list[ast.FunctionDef]] = {}
    for item in nexus_cls.body:
        if isinstance(item, ast.FunctionDef):
            methods.setdefault(item.name, []).append(item)

    to_delete: list[tuple[int, int, str]] = []
    for name, defs in methods.items():
        if len(defs) <= 1:
            continue
        defs_sorted = sorted(defs, key=lambda d: d.lineno)
        print(f"⚠️  {name}: {len(defs)} определений "
              f"(строки {[d.lineno for d in defs_sorted]})")
        # Удаляем все, кроме последнего
        for d in defs_sorted[:-1]:
            to_delete.append((d.lineno, d.end_lineno, name))

    if not to_delete:
        print("✅ Дубликатов не найдено")
        return

    # Удаляем в обратном порядке
    to_delete.sort(reverse=True)
    for start, end, name in to_delete:
        print(f"   → удаляю {name} на строке {start}")
        del lines[start - 1:end]

    path.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Удалено блоков: {len(to_delete)}")


if __name__ == "__main__":
    app_py = Path("nexus/app.py")
    if not app_py.exists():
        print("❌ nexus/app.py не найден")
        exit(1)

    # Бэкап
    backup = app_py.with_suffix(".py.backup")
    backup.write_text(app_py.read_text(encoding="utf-8"),
                      encoding="utf-8")
    print(f"📦 Бэкап: {backup}")
    print()

    fix_file(app_py)