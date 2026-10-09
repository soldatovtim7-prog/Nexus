#!/bin/bash
# ============================================================
# NEXUS .deb Builder — создаёт Debian-пакет
# ============================================================
# Требуется: dpkg-deb (обычно уже есть)
# Проверка: which dpkg-deb
# Установка (если нет): sudo apt install dpkg-dev
#
# Что делает:
#   1. Проверяет dist/nexus (если нет — запускает build.sh)
#   2. Создаёт структуру Debian-пакета
#   3. Собирает .deb через dpkg-deb
#
# Использование:
#   chmod +x build_deb.sh
#   ./build_deb.sh
#
# Результат:
#   nexus_1.1.1_amd64.deb
#
# Установка:
#   sudo dpkg -i nexus_1.1.1_amd64.deb
#   sudo apt --fix-broken install
# ============================================================

set -e

NEXUS_VERSION="1.1.1"
NEXUS_ARCH="amd64"
PACKAGE_NAME="nexus"
PACKAGE_DIR="nexus_${NEXUS_VERSION}_${NEXUS_ARCH}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$SRC_DIR"

echo ""
echo "🧠 NEXUS .deb Builder v${NEXUS_VERSION}"
echo "════════════════════════════════════════"
echo "📂 Рабочая папка: $SRC_DIR"
echo ""

# ── 1. Проверка dpkg-deb ────────────────────────────────
if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "❌ dpkg-deb не найден"
    echo ""
    echo "Установи:"
    echo "  sudo apt install dpkg-dev"
    exit 1
fi
echo "✅ dpkg-deb доступен"

# ── 2. Проверка dist/nexus ──────────────────────────────
echo ""
echo "📦 Проверяю standalone-сборку..."

if [ ! -d "dist/nexus" ]; then
    echo "   dist/nexus не найден."
    echo ""
    echo "🔨 Запускаю сборку standalone..."
    echo "   (это займёт 5-10 минут)"
    echo ""

    if [ ! -f "build.sh" ]; then
        echo "❌ build.sh не найден"
        exit 1
    fi

    # Убираем tail — видно прогресс
    ./build.sh
fi

if [ ! -d "dist/nexus" ]; then
    echo "❌ dist/nexus не создан после сборки"
    exit 1
fi

if [ ! -f "dist/nexus/nexus" ]; then
    echo "❌ dist/nexus/nexus (executable) не найден"
    ls -la dist/nexus/ | head
    exit 1
fi

echo "✅ dist/nexus найден"
echo "   Размер: $(du -sh dist/nexus | cut -f1)"

# ── 3. Создаём структуру пакета ────────────────────────
echo ""
echo "📁 Создаю структуру пакета..."

rm -rf "$PACKAGE_DIR" 2>/dev/null || true
mkdir -p "$PACKAGE_DIR/DEBIAN"
mkdir -p "$PACKAGE_DIR/opt/nexus"
mkdir -p "$PACKAGE_DIR/usr/share/applications"
mkdir -p "$PACKAGE_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$PACKAGE_DIR/usr/bin"
mkdir -p "$PACKAGE_DIR/usr/share/doc/nexus"

# Копируем standalone-сборку
cp -r dist/nexus/* "$PACKAGE_DIR/opt/nexus/"

# Launcher (символическая ссылка) — чтобы `nexus` работал из терминала
ln -sf /opt/nexus/nexus "$PACKAGE_DIR/usr/bin/nexus"

# ── 4. .desktop файл ────────────────────────────────────
cat > "$PACKAGE_DIR/usr/share/applications/nexus.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=NEXUS
GenericName=Digital Universe
Comment=Notes, code, QR, tasks, blockchain
Exec=/opt/nexus/nexus
Icon=nexus
Terminal=false
Categories=Utility;Development;Office;
Keywords=notes;code;qr;tasks;nexus;
StartupNotify=true
EOF

# ── 5. Иконка (если есть) ──────────────────────────────
ICON_PATH=""
for p in "nexus_icon.png" "icon.png" "nexus/icon.png"; do
    if [ -f "$p" ]; then
        ICON_PATH="$p"
        break
    fi
done

if [ -n "$ICON_PATH" ]; then
    cp "$ICON_PATH" \
        "$PACKAGE_DIR/usr/share/icons/hicolor/256x256/apps/nexus.png"
    echo "✅ Иконка добавлена: $ICON_PATH"
else
    # Создаём простую иконку
    if command -v python3 >/dev/null 2>&1 && \
       python3 -c "import PIL" 2>/dev/null; then
        python3 -c "
from PIL import Image, ImageDraw
img = Image.new('RGBA', (256, 256), (10, 10, 26, 255))
d = ImageDraw.Draw(img)
d.ellipse((20, 20, 236, 236), outline='#00e5ff', width=8)
d.text((90, 100), '🧠', fill='#00e5ff')
img.save('$PACKAGE_DIR/usr/share/icons/hicolor/256x256/apps/nexus.png')
" 2>/dev/null && echo "✅ Иконка сгенерирована" || \
            echo "⚠️  Иконка пропущена (нет PIL)"
    fi
fi

# ── 6. README ────────────────────────────────────────────
if [ -f "README.md" ]; then
    cp README.md "$PACKAGE_DIR/usr/share/doc/nexus/"
fi

# ── 7. DEBIAN/control ───────────────────────────────────
INSTALL_SIZE=$(du -sk "$PACKAGE_DIR/opt" | cut -f1)

cat > "$PACKAGE_DIR/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $NEXUS_VERSION
Section: utils
Priority: optional
Architecture: $NEXUS_ARCH
Maintainer: NEXUS Team <nexus@local>
Description: NEXUS — Digital Universe
 Modular productivity app with notes, code, QR,
 tasks, blockchain, encryption, HTTP API.
 .
 Features:
  - Code Studio with sandbox and debugger
  - QR Studio with 16 styles, 11 modes
  - Tasks with Kanban and Gantt
  - Notes with FTS5 search
  - Blockchain with Merkle root
  - AES-256-GCM encryption
  - HTTP API and Webhooks
Installed-Size: $INSTALL_SIZE
Depends: python3, python3-tk, libzbar0
Recommends: libgl1
EOF

# ── 8. DEBIAN/postinst ─────────────────────────────────
cat > "$PACKAGE_DIR/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e

if [ -x "$(command -v update-desktop-database)" ]; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

if [ -x "$(command -v gtk-update-icon-cache)" ]; then
    gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
fi

echo ""
echo "════════════════════════════════════════"
echo "✅ NEXUS установлен!"
echo "════════════════════════════════════════"
echo ""
echo "🚀 Запуск:"
echo "   nexus                  ← из терминала"
echo "   Меню приложений → NEXUS"
echo ""
echo "🗑️  Удаление:"
echo "   sudo apt remove nexus"
echo ""
POSTINST

chmod 755 "$PACKAGE_DIR/DEBIAN/postinst"

# ── 9. DEBIAN/prerm ────────────────────────────────────
cat > "$PACKAGE_DIR/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
set -e
# Ничего не делаем — данные пользователя в ~/.nexus-data
PRERM

chmod 755 "$PACKAGE_DIR/DEBIAN/prerm"

# ── 10. DEBIAN/postrm ──────────────────────────────────
cat > "$PACKAGE_DIR/DEBIAN/postrm" <<'POSTRM'
#!/bin/bash
set -e

if [ -x "$(command -v update-desktop-database)" ]; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

# Удаляем пустые папки
rmdir /opt/nexus 2>/dev/null || true
POSTRM

chmod 755 "$PACKAGE_DIR/DEBIAN/postrm"

# ── 11. Собираем .deb ──────────────────────────────────
echo ""
echo "🔨 Собираю .deb..."

dpkg-deb --build --root-owner-group "$PACKAGE_DIR"

DEB_FILE="${PACKAGE_DIR}.deb"

if [ -f "$DEB_FILE" ]; then
    SIZE=$(du -h "$DEB_FILE" | cut -f1)
    echo ""
    echo "════════════════════════════════════════"
    echo "✅ .DEB СОБРАН!"
    echo "════════════════════════════════════════"
    echo "   Файл:   $DEB_FILE"
    echo "   Размер: $SIZE"
    echo ""
    echo "🚀 Установка:"
    echo ""
    echo "   sudo dpkg -i $DEB_FILE"
    echo "   sudo apt --fix-broken install"
    echo ""
    echo "   Или одной командой:"
    echo "   sudo apt install ./$DEB_FILE"
    echo ""
    echo "🗑️  Удаление:"
    echo "   sudo apt remove nexus"
    echo ""
    echo "🔍 Проверка:"
    echo "   dpkg -l | grep nexus"
    echo "════════════════════════════════════════"
else
    echo "❌ Ошибка сборки: $DEB_FILE не создан"
    exit 1
fi

# ── 12. Очистка ────────────────────────────────────────
rm -rf "$PACKAGE_DIR"

echo ""
echo "🧹 Временные файлы очищены"
echo ""