#!/bin/bash
# ============================================================
# NEXUS Icon Recreation Script v2
# ============================================================
# Пересоздаёт иконку NEXUS:
#   - Красивая cyberpunk-стилистика
#   - Неоново-циановые цвета
#   - Все размеры (16-512)
#   - Устанавливает в систему + пользователя
#   - Обновляет кэш
#
# FIX: работает с sudo через двухэтапную схему
#      (сначала в /tmp, потом sudo cp)
#
# Использование:
#   chmod +x recreate_icon.sh
#   ./recreate_icon.sh
# ============================================================

set -e

echo ""
echo "🎨 NEXUS Icon Recreation v2"
echo "════════════════════════════════════════"

# ── 1. Проверка PIL ─────────────────────────────────────
if ! python3 -c "import PIL" 2>/dev/null; then
    echo "❌ PIL (Pillow) не установлен"
    echo "   pip install Pillow"
    exit 1
fi
echo "✅ PIL доступен"

# ── 2. Генерация иконки 1024x1024 ───────────────────────
echo ""
echo "🖼️  Генерирую 1024x1024 исходник..."

python3 << 'PYEOF'
"""Генератор иконки NEXUS — cyberpunk-стилистика."""
from PIL import Image, ImageDraw, ImageFilter
import math

# ── Параметры ───────────────────────────────────────────
SIZE = 1024
BG_DARK = (10, 10, 26, 255)         # тёмный фон
NEON_CYAN = (0, 229, 255, 255)       # cyan
NEON_PURPLE = (176, 38, 255, 255)    # accent
NEON_PINK = (255, 45, 149, 255)      # pink
GLOW_ALPHA = 80                       # прозрачность свечения

# ── Создаём базовое изображение ─────────────────────────
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Фон — скруглённый квадрат с градиентом
margin = 40
radius = 180

# Градиентный фон
for i in range(margin, SIZE - margin):
    t = (i - margin) / (SIZE - 2 * margin)
    r = int(10 + (20 - 10) * t)
    g = int(10 + (15 - 10) * t)
    b = int(26 + (40 - 26) * t)
    d.line([(i, margin), (i, SIZE - margin)],
           fill=(r, g, b, 255))

# Скруглённые углы — маска
mask = Image.new('L', (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [margin, margin, SIZE - margin, SIZE - margin],
    radius=radius, fill=255)

# Применяем маску
bg = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
gradient = Image.new('RGBA', (SIZE, SIZE), BG_DARK)
bg.paste(gradient, (0, 0), mask)
img = bg
d = ImageDraw.Draw(img)

# ── Внешняя неоновая рамка ──────────────────────────────
# Свечение
for w in range(20, 0, -1):
    alpha = int(40 * (1 - w / 20))
    d.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin],
        radius=radius, outline=(0, 229, 255, alpha),
        width=w)

# Основная рамка
d.rounded_rectangle(
    [margin, margin, SIZE - margin, SIZE - margin],
    radius=radius, outline=NEON_CYAN, width=6)

# ── Декоративные линии по углам ────────────────────────
corner_len = 80
corner_offset = margin + 40

# Левый верхний
d.line([(corner_offset, corner_offset),
        (corner_offset + corner_len, corner_offset)],
       fill=NEON_CYAN, width=8)
d.line([(corner_offset, corner_offset),
        (corner_offset, corner_offset + corner_len)],
       fill=NEON_CYAN, width=8)

# Правый верхний
d.line([(SIZE - corner_offset, corner_offset),
        (SIZE - corner_offset - corner_len, corner_offset)],
       fill=NEON_CYAN, width=8)
d.line([(SIZE - corner_offset, corner_offset),
        (SIZE - corner_offset, corner_offset + corner_len)],
       fill=NEON_CYAN, width=8)

# Левый нижний
d.line([(corner_offset, SIZE - corner_offset),
        (corner_offset + corner_len, SIZE - corner_offset)],
       fill=NEON_CYAN, width=8)
d.line([(corner_offset, SIZE - corner_offset),
        (corner_offset, SIZE - corner_offset - corner_len)],
       fill=NEON_CYAN, width=8)

# Правый нижний
d.line([(SIZE - corner_offset, SIZE - corner_offset),
        (SIZE - corner_offset - corner_len,
         SIZE - corner_offset)],
       fill=NEON_CYAN, width=8)
d.line([(SIZE - corner_offset, SIZE - corner_offset),
        (SIZE - corner_offset, SIZE - corner_offset - corner_len)],
       fill=NEON_CYAN, width=8)

# ── Центральный символ: мозг/сеть ──────────────────────
cx, cy = SIZE // 2, SIZE // 2
brain_r = 220

# Внешний круг (мозг) — пунктирный
for angle in range(0, 360, 12):
    rad = math.radians(angle)
    x1 = cx + int((brain_r - 15) * math.cos(rad))
    y1 = cy + int((brain_r - 15) * math.sin(rad))
    x2 = cx + int(brain_r * math.cos(rad))
    y2 = cy + int(brain_r * math.sin(rad))
    d.line([(x1, y1), (x2, y2)], fill=NEON_CYAN, width=5)

# Внутренний круг
d.ellipse([cx - brain_r + 40, cy - brain_r + 40,
           cx + brain_r - 40, cy + brain_r - 40],
          outline=NEON_PURPLE, width=4)

# ── Стилизованные "нейроны" ────────────────────────────
# Центральная точка
d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30],
          fill=NEON_CYAN)

# Точки вокруг
points = [
    (cx - 100, cy - 80, 20, NEON_CYAN),
    (cx + 100, cy - 80, 20, NEON_PURPLE),
    (cx - 120, cy + 40, 18, NEON_PURPLE),
    (cx + 120, cy + 40, 18, NEON_CYAN),
    (cx - 60, cy + 110, 16, NEON_PINK),
    (cx + 60, cy + 110, 16, NEON_PINK),
    (cx, cy - 130, 22, NEON_PURPLE),
]

for px, py, r, color in points:
    d.ellipse([px - r, py - r, px + r, py + r],
              fill=color)

# Соединяем центральную с остальными
for px, py, r, color in points:
    d.line([(cx, cy), (px, py)],
           fill=(*color[:3], 180), width=3)

# Соединяем между собой некоторые
connections = [
    (0, 6), (1, 6), (2, 4), (3, 5),
    (0, 2), (1, 3), (4, 5),
    (0, 1), (2, 3),
]
for i, j in connections:
    x1, y1 = points[i][0], points[i][1]
    x2, y2 = points[j][0], points[j][1]
    d.line([(x1, y1), (x2, y2)],
           fill=(*NEON_CYAN[:3], 100), width=2)

# ── Дополнительный glow ─────────────────────────────────
glow = img.filter(ImageFilter.GaussianBlur(radius=15))
final = Image.alpha_composite(glow, img)

# ── Сохраняем ───────────────────────────────────────────
final.save('/tmp/nexus_icon_1024.png', 'PNG', optimize=True)
print('✅ /tmp/nexus_icon_1024.png создана (1024x1024)')
PYEOF

# ── 3. Скалирование во временную папку ─────────────────
echo ""
echo "📐 Скалирую во все размеры (во временную папку)..."

mkdir -p /tmp/nexus_icons

python3 << 'PYEOF'
from PIL import Image

src = Image.open('/tmp/nexus_icon_1024.png')

sizes = [16, 24, 32, 48, 64, 128, 256, 512]
for size in sizes:
    resized = src.resize((size, size), Image.Resampling.LANCZOS)
    dst = f'/tmp/nexus_icons/nexus_{size}.png'
    resized.save(dst, 'PNG', optimize=True)
    print(f'  ✅ {size}x{size}')

# Копия 1024 для пользовательской папки
src.save('/tmp/nexus_icons/nexus_1024.png', 'PNG', optimize=True)
print('  ✅ 1024x1024 (для пользователя)')
PYEOF

# ── 4. Установка в /usr/share/icons через sudo ─────────
echo ""
echo "📁 Устанавливаю в систему (нужен sudo)..."
echo ""

# Создаём папки
for size in 16 24 32 48 64 128 256 512; do
    sudo mkdir -p "/usr/share/icons/hicolor/${size}x${size}/apps"
done

# Копируем все размеры
for size in 16 24 32 48 64 128 256 512; do
    sudo cp "/tmp/nexus_icons/nexus_${size}.png" \
        "/usr/share/icons/hicolor/${size}x${size}/apps/nexus.png"
    echo "  ✅ ${size}x${size} → /usr/share/icons/hicolor/${size}x${size}/apps/nexus.png"
done

# ── 5. Установка в ~/.local/share/icons (без sudo) ─────
echo ""
echo "📁 Устанавливаю в ~/.local/share/icons..."

mkdir -p ~/.local/share/icons/hicolor/256x256/apps
cp /tmp/nexus_icons/nexus_256.png \
    ~/.local/share/icons/hicolor/256x256/apps/nexus.png
echo "  ✅ ~/.local/share/icons/hicolor/256x256/apps/nexus.png"

# ── 6. Обновление кэшей ────────────────────────────────
echo ""
echo "🔄 Обновляю кэши..."

sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>&1 | tail -1 || true
gtk-update-icon-cache -f ~/.local/share/icons/hicolor 2>&1 | tail -1 || true

update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
sudo update-desktop-database /usr/share/applications/ 2>/dev/null || true

# Очищаем кэш иконок GNOME
rm -rf ~/.cache/icon-cache.kcache 2>/dev/null || true
rm -rf ~/.cache/thumbnails/* 2>/dev/null || true

# ── 7. Проверка ────────────────────────────────────────
echo ""
echo "🔍 Проверка установки:"
echo ""

echo "📂 Системные иконки (/usr/share/icons/hicolor/):"
ls -la /usr/share/icons/hicolor/*/apps/nexus.png 2>/dev/null | \
    awk '{printf "  %-12s %s\n", $5, $NF}'
echo ""

echo "📂 Пользовательская (~/.local/share/icons/):"
ls -la ~/.local/share/icons/hicolor/256x256/apps/nexus.png 2>/dev/null | \
    awk '{printf "  %-12s %s\n", $5, $NF}'
echo ""

echo "📏 Тип файла 256x256:"
file /usr/share/icons/hicolor/256x256/apps/nexus.png 2>/dev/null | \
    sed 's/^/  /'

# ── 8. Обновление ярлыка на рабочем столе ──────────────
echo ""
echo "🖥️  Обновляю ярлык на рабочем столе..."

DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
DESKTOP_FILE="$DESKTOP_DIR/nexus.desktop"

if [ -f "$DESKTOP_FILE" ]; then
    # Обновляем trusted флаг
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
    echo "  ✅ $DESKTOP_FILE — trusted флаг установлен"
else
    # Создаём заново
    cat > "$DESKTOP_FILE" <<EOF
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
    chmod +x "$DESKTOP_FILE"
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
    echo "  ✅ $DESKTOP_FILE — создан заново"
fi

# ── 9. Готово ───────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "✅ ИКОНКА ПЕРЕСОЗДАНА!"
echo "════════════════════════════════════════"
echo ""
echo "📱 Что делать дальше:"
echo ""
echo "   1. Logout и Login (или перезагрузка)"
echo "      GNOME должен перечитать кэш иконок"
echo ""
echo "   2. Или: Alt+F2 → 'r' → Enter"
echo "      (только X11, на Wayland не работает)"
echo ""
echo "   3. Проверь иконку на рабочем столе:"
echo "      $DESKTOP_FILE"
echo ""
echo "🧹 Очистка (опционально):"
echo "   rm -rf /tmp/nexus_icons /tmp/nexus_icon_1024.png"
echo ""
echo "════════════════════════════════════════"