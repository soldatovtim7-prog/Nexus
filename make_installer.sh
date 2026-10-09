#!/bin/bash
# ============================================================
# NEXUS Installer Builder
# ============================================================
# Создаёт папку NEXUS-Установщик/ с "data-*" файлами
# как Windows-инсталлятор.
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$HOME/Рабочий стол/NEXUS-Установщик"
VERSION="1.1.1"
BUILD_DATE=$(date +%Y%m%d)

echo ""
echo "📦 NEXUS Installer Builder v$VERSION"
echo "════════════════════════════════════════"
echo ""

# ── 1. Очистка ──────────────────────────────────────────
echo "🧹 Очистка..."
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# ── 2. data-1-core ──────────────────────────────────────
echo "📦 data-1-core.tar.gz (ядро приложения)..."
cd "$PROJECT_DIR"
tar -czf "$OUTPUT_DIR/data-1-core.tar.gz" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    nexus/
echo "  ✅ $(du -h "$OUTPUT_DIR/data-1-core.tar.gz" | cut -f1)"

# ── 3. data-2-pro ───────────────────────────────────────
echo "📦 data-2-pro.tar.gz (PRO-модули)..."
tar -czf "$OUTPUT_DIR/data-2-pro.tar.gz" \
    nexus_qr_pro.py \
    nexus_code_pro.py \
    nexus_debugger.py \
    nexus_ctk_patch.py
echo "  ✅ $(du -h "$OUTPUT_DIR/data-2-pro.tar.gz" | cut -f1)"

# ── 4. data-3-scripts ───────────────────────────────────
echo "📦 data-3-scripts.tar.gz (скрипты)..."
tar -czf "$OUTPUT_DIR/data-3-scripts.tar.gz" \
    build.sh build_deb.sh run.sh install.sh 2>/dev/null \
    nexus.spec requirements.txt 2>/dev/null || \
tar -czf "$OUTPUT_DIR/data-3-scripts.tar.gz" \
    $(ls *.sh nexus.spec requirements.txt 2>/dev/null)
echo "  ✅ $(du -h "$OUTPUT_DIR/data-3-scripts.tar.gz" | cut -f1)"

# ── 5. data-4-docs ──────────────────────────────────────
echo "📦 data-4-docs.tar.gz (документация)..."
DOCS_FILES=""
[ -f README.md ] && DOCS_FILES="$DOCS_FILES README.md"
[ -f LICENSE ] && DOCS_FILES="$DOCS_FILES LICENSE"
if [ -n "$DOCS_FILES" ]; then
    tar -czf "$OUTPUT_DIR/data-4-docs.tar.gz" $DOCS_FILES
    echo "  ✅ $(du -h "$OUTPUT_DIR/data-4-docs.tar.gz" | cut -f1)"
else
    echo "  ⚠️  Нет README/LICENSE"
fi

# ── 6. setup.ini ────────────────────────────────────────
echo "📝 setup.ini..."
cat > "$OUTPUT_DIR/setup.ini" <<EOF
; ============================================================
; NEXUS Installer Configuration
; ============================================================
[NEXUS]
version = $VERSION
build_date = $BUILD_DATE
publisher = NEXUS Team
website = https://nexus.local

[Install]
; Куда устанавливать
default_dir = /opt/nexus
; Создавать ярлык в меню
create_menu_shortcut = true
; Создавать ярлык на рабочем столе
create_desktop_shortcut = true
; Добавлять в PATH (команда 'nexus')
add_to_path = true
; Язык по умолчанию
default_language = ru

[Files]
data1 = data-1-core.tar.gz
data2 = data-2-pro.tar.gz
data3 = data-3-scripts.tar.gz
data4 = data-4-docs.tar.gz

[Requirements]
python_min = 3.10
os = linux
gui = tk
EOF
echo "  ✅ setup.ini"

# ── 7. README.txt ───────────────────────────────────────
echo "📝 README.txt..."
cat > "$OUTPUT_DIR/README.txt" <<EOF
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║              🧠  N E X U S  —  Installer                 ║
║                                                          ║
║              v$VERSION · CORE++ FIX · $BUILD_DATE     ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝

NEXUS — цифровая вселенная в одном приложении.
Работает полностью локально. Без облака. Без интернета.

──────────────────────────────────────────────────────────

🚀 УСТАНОВКА

  1. Открой терминал в этой папке:

     cd "$OUTPUT_DIR"

  2. Запусти установщик:

     bash setup.sh

  3. Следуй инструкциям мастера.

──────────────────────────────────────────────────────────

📦 ЧТО В ПАПКЕ

  setup.sh                  ← главный установщик
  data-1-core.tar.gz        ← ядро приложения
  data-2-pro.tar.gz         ← PRO-модули (QR, Code, Debug)
  data-3-scripts.tar.gz     ← скрипты сборки
  data-4-docs.tar.gz        ← документация
  setup.ini                 ← конфигурация
  uninstall.sh              ← деинсталлятор
  README.txt                ← этот файл

──────────────────────────────────────────────────────────

📋 ТРЕБОВАНИЯ

  • Python 3.10+
  • Tkinter (sudo apt install python3-tk)
  • ~200 МБ свободного места

──────────────────────────────────────────────────────────

🎯 ПОСЛЕ УСТАНОВКИ

  • Команда 'nexus' в терминале
  • Иконка в меню приложений
  • Ярлык на рабочем столе
  • Данные в ~/.nexus-data/

──────────────────────────────────────────────────────────

🗑️ УДАЛЕНИЕ

  bash uninstall.sh

  Или:
  sudo apt remove nexus

──────────────────────────────────────────────────────────

MIT License · NEXUS Team
EOF
echo "  ✅ README.txt"

# ── 8. LICENSE.txt ──────────────────────────────────────
if [ -f "$PROJECT_DIR/LICENSE" ]; then
    cp "$PROJECT_DIR/LICENSE" "$OUTPUT_DIR/LICENSE.txt"
else
    cat > "$OUTPUT_DIR/LICENSE.txt" <<EOF
MIT License

Copyright (c) 2025 NEXUS Team

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
EOF
fi
echo "  ✅ LICENSE.txt"

echo ""
echo "════════════════════════════════════════"
echo "✅ Собран установщик!"
echo "════════════════════════════════════════"
echo ""
echo "📂 Папка: $OUTPUT_DIR"
echo ""
ls -la "$OUTPUT_DIR"
echo ""
echo "📊 Размер: $(du -sh "$OUTPUT_DIR" | cut -f1)"
echo ""