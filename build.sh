#!/bin/bash
# ============================================================
# NEXUS Build Script (Linux) — сборка standalone
# ============================================================
# Использование:
#   chmod +x build.sh
#   ./build.sh
#
# Результат:
#   dist/nexus/nexus      — исполняемый
#   dist/nexus/_internal/ — библиотеки
# ============================================================

set -e

NEXUS_VERSION="1.1.1"
BUILD_START=$(date +%s)

echo "🧠 NEXUS Build Script v${NEXUS_VERSION}"
echo "════════════════════════════════════════"
echo ""

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 не найден"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "🐍 Python: $PY_VERSION"

if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo "✅ Версия Python OK"
else
    echo "❌ Требуется Python 3.10+. У тебя: $PY_VERSION"
    exit 1
fi
echo ""

# 2. venv
VENV_DIR=""
if [ -d ".venv1" ]; then
    VENV_DIR=".venv1"
elif [ -d ".venv" ]; then
    VENV_DIR=".venv"
fi

if [ -z "$VENV_DIR" ]; then
    echo "⚠️  venv не найден. Создаю .venv1..."
    python3 -m venv .venv1
    VENV_DIR=".venv1"
fi

echo "📦 venv: $VENV_DIR"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# 3. PyInstaller
echo ""
echo "📦 Установка PyInstaller..."
pip install --quiet --upgrade pyinstaller

# 4. Зависимости
echo "📦 Установка зависимостей NEXUS..."
if [ -f "requirements.txt" ]; then
    pip install --quiet -r requirements.txt
else
    pip install --quiet \
        customtkinter Pillow qrcode cryptography \
        argon2-cffi pyzbar
fi
echo "✅ Зависимости установлены"

# 5. spec
echo ""
if [ ! -f "nexus.spec" ]; then
    echo "❌ nexus.spec не найден!"
    exit 1
fi
echo "✅ nexus.spec найден"

# 6. Очистка
echo ""
echo "🧹 Очистка старых сборок..."
rm -rf build/ dist/ 2>/dev/null || true
echo "✅ Готово"

# 7. Сборка
echo ""
echo "🔨 Сборка... (5-10 минут)"
echo ""
pyinstaller nexus.spec --clean --noconfirm

BUILD_END=$(date +%s)
BUILD_TIME=$((BUILD_END - BUILD_START))

# 8. Проверка
echo ""
if [ -f "dist/nexus/nexus" ]; then
    SIZE=$(du -sh dist/nexus | cut -f1)
    echo "════════════════════════════════════════"
    echo "✅ СБОРКА ЗАВЕРШЕНА!"
    echo "════════════════════════════════════════"
    echo "   Режим:  onedir (папка)"
    echo "   Папка:  dist/nexus/"
    echo "   Размер: $SIZE"
    echo "   Время:  ${BUILD_TIME}с"
    echo ""
    echo "🚀 Запуск:"
    echo "   ./dist/nexus/nexus"
    echo "════════════════════════════════════════"
    echo ""

    read -r -p "🎬 Запустить NEXUS сейчас? [Y/n] " response
    response=${response:-Y}
    if [[ "$response" =~ ^[YyДд] ]]; then
        echo ""
        echo "🎬 Запускаю NEXUS..."
        echo ""
        ./dist/nexus/nexus
    else
        echo ""
        echo "👋 Готово. Запусти вручную: ./dist/nexus/nexus"
    fi

elif [ -f "dist/nexus" ]; then
    SIZE=$(du -h dist/nexus | cut -f1)
    echo "════════════════════════════════════════"
    echo "✅ СБОРКА ЗАВЕРШЕНА!"
    echo "════════════════════════════════════════"
    echo "   Режим:  onefile (один файл)"
    echo "   Файл:   dist/nexus"
    echo "   Размер: $SIZE"
    echo "   Время:  ${BUILD_TIME}с"
    echo ""
    echo "🚀 Запуск:"
    echo "   ./dist/nexus"
    echo "════════════════════════════════════════"
    echo ""
else
    echo "════════════════════════════════════════"
    echo "❌ ОШИБКА СБОРКИ"
    echo "════════════════════════════════════════"
    ls -la dist/ 2>/dev/null || echo "   dist/ не существует"
    exit 1
fi