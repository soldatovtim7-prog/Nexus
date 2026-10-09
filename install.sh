#!/bin/bash
# ============================================================
# NEXUS Install Script — установка на Linux
# ============================================================
# Что делает:
#   1. Проверяет Python 3.10+
#   2. Проверяет Tkinter
#   3. Создаёт .venv1
#   4. Устанавливает зависимости
#   5. Создаёт ярлык в меню приложений (.desktop)
#   6. Готово
#
# Использование:
#   chmod +x install.sh
#   ./install.sh
# ============================================================

set -e

NEXUS_VERSION="1.1.1"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "🧠 NEXUS Installer v${NEXUS_VERSION}"
echo "════════════════════════════════════════"
echo "📂 Установка в: $INSTALL_DIR"
echo ""

# ── 1. Проверка Python ──────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 не найден!"
    echo ""
    echo "Установи системные пакеты:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv python3-tk"
    echo "  Fedora:        sudo dnf install python3 python3-pip python3-tkinter"
    echo "  Arch:          sudo pacman -S python python-pip tk"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

echo "🐍 Python: $PY_VERSION"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "❌ Требуется Python 3.10+. У тебя: $PY_VERSION"
    exit 1
fi
echo "✅ Python OK"
echo ""

# ── 2. Проверка Tkinter ─────────────────────────────────
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "❌ Tkinter не установлен!"
    echo ""
    echo "Установи:"
    echo "  Ubuntu/Debian: sudo apt install python3-tk"
    echo "  Fedora:        sudo dnf install python3-tkinter"
    echo "  Arch:          sudo pacman -S tk"
    exit 1
fi
echo "✅ Tkinter OK"
echo ""

# ── 3. Создание venv ────────────────────────────────────
if [ ! -d "$INSTALL_DIR/.venv1" ]; then
    echo "📦 Создание .venv1..."
    python3 -m venv "$INSTALL_DIR/.venv1"
fi

# shellcheck source=/dev/null
source "$INSTALL_DIR/.venv1/bin/activate"

# ── 4. Обновление pip ───────────────────────────────────
echo "📦 Обновление pip..."
pip install --quiet --upgrade pip setuptools wheel

# ── 5. Установка зависимостей ───────────────────────────
echo "📦 Установка зависимостей NEXUS..."
echo "   (займёт 1-2 минуты)"
echo ""

if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    pip install --quiet -r "$INSTALL_DIR/requirements.txt"
else
    pip install --quiet \
        "customtkinter>=5.2.0" \
        "Pillow>=10.0.0" \
        "qrcode[pil]>=7.4.0" \
        "cryptography>=41.0.0" \
        "argon2-cffi>=23.1.0" \
        "pyzbar>=0.1.9"
fi
echo ""
echo "✅ Зависимости установлены"
echo ""

# ── 6. Проверка критичных модулей ───────────────────────
echo "🔍 Проверка модулей..."
python3 -c "
import sys
mods = ['customtkinter', 'PIL', 'qrcode', 'cryptography']
missing = []
for m in mods:
    try:
        __import__(m)
        print(f'  ✅ {m}')
    except ImportError:
        print(f'  ❌ {m}')
        missing.append(m)
if missing:
    sys.exit(1)
" || {
    echo ""
    echo "❌ Не все обязательные модули установлены"
    exit 1
}
echo ""

# ── 7. Проверка, что NEXUS импортируется ────────────────
echo "🔍 Проверка импорта NEXUS..."
cd "$INSTALL_DIR"
if python3 -c "from nexus import core; print(f'  ✅ NEXUS v{core.VERSION}')"; then
    echo ""
else
    echo "  ❌ Не удалось импортировать nexus"
    exit 1
fi

# ── 8. Создание .desktop ярлыка ─────────────────────────
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

DESKTOP_FILE="$DESKTOP_DIR/nexus.desktop"

# Ищем иконку
ICON_PATH=""
for p in "$INSTALL_DIR/nexus_icon.png" \
         "$INSTALL_DIR/icon.png" \
         "$INSTALL_DIR/nexus/icon.png"; do
    if [ -f "$p" ]; then
        ICON_PATH="$p"
        break
    fi
done
[ -z "$ICON_PATH" ] && ICON_PATH="utilities-terminal"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=NEXUS
GenericName=Digital Universe
Comment=Notes, code, QR, tasks, blockchain
Exec=bash -c "cd '$INSTALL_DIR' && source .venv1/bin/activate && python3 -m nexus"
Icon=$ICON_PATH
Terminal=false
Categories=Utility;Development;Office;
Keywords=notes;code;qr;tasks;nexus;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"
echo "✅ Ярлык создан: $DESKTOP_FILE"

# Обновляем кэш приложений
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# ── 9. Готово ───────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "✅ NEXUS УСТАНОВЛЕН!"
echo "════════════════════════════════════════"
echo ""
echo "🚀 Способы запуска:"
echo ""
echo "   1) Из терминала:"
echo "      cd '$INSTALL_DIR'"
echo "      ./run.sh"
echo ""
echo "   2) Через меню приложений:"
echo "      Открой меню → найди 'NEXUS'"
echo ""
echo "   3) Вручную:"
echo "      source .venv1/bin/activate"
echo "      python3 -m nexus"
echo ""
echo "════════════════════════════════════════"