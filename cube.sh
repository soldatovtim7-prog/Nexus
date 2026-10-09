# 1. Создать все размеры иконки
python3 << 'EOF'
from PIL import Image
import os

src = Image.open("/tmp/nexus_icons_v2/cube.png")
os.makedirs("/tmp/nexus_cube_sizes", exist_ok=True)

for size in [16, 24, 32, 48, 64, 128, 256, 512]:
    resized = src.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(f"/tmp/nexus_cube_sizes/{size}.png", "PNG")
    print(f"  ✅ {size}x{size}")
EOF

# 2. Скопировать в системные папки
sudo mkdir -p /usr/share/icons/hicolor/{16x16,24x24,32x32,48x48,64x64,128x128,256x256,512x512}/apps

for size in 16 24 32 48 64 128 256 512; do
    sudo cp "/tmp/nexus_cube_sizes/${size}.png" \
        "/usr/share/icons/hicolor/${size}x${size}/apps/nexus.png"
done

# 3. Обновить кэш
sudo gtk-update-icon-cache -f /usr/share/icons/hicolor
rm -rf ~/.cache/icon-cache.kcache

# 4. Проверка
echo ""
echo "=== Установлено ==="
ls -la /usr/share/icons/hicolor/256x256/apps/nexus.png
file /usr/share/icons/hicolor/256x256/apps/nexus.png