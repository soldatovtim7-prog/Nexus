#!/usr/bin/env python3
"""
Генератор иконок NEXUS v2 — простой и надёжный.

Создаёт 5 техно-иконок в /tmp/nexus_icons_v2/
"""
from PIL import Image, ImageDraw, ImageFilter
import math
import traceback
from pathlib import Path


SIZE = 512
OUT_DIR = Path("/tmp/nexus_icons_v2")
OUT_DIR.mkdir(exist_ok=True)

CYAN = (0, 229, 255, 255)
PURPLE = (176, 38, 255, 255)
PINK = (255, 45, 149, 255)
WHITE = (255, 255, 255, 255)
DARK = (10, 10, 26, 255)


def base_canvas() -> Image.Image:
    """Создать базовый фон с скруглением."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [20, 20, SIZE - 20, SIZE - 20],
        radius=90, fill=DARK,
        outline=CYAN, width=6)
    return img


def apply_glow(img: Image.Image) -> Image.Image:
    """Добавить свечение."""
    glow = img.filter(ImageFilter.GaussianBlur(12))
    return Image.alpha_composite(glow, img)


def save_icon(img: Image.Image, name: str) -> None:
    """Сохранить иконку."""
    path = OUT_DIR / f"{name}.png"
    img.save(str(path), "PNG", optimize=True)
    print(f"  ✅ {name}.png")


# ═══════════════════════════════════════════════════════════
# 1: Кубик N
# ═══════════════════════════════════════════════════════════
def make_cube() -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2

    # Передняя грань
    d.rounded_rectangle(
        [cx - 130, cy - 130, cx + 130, cy + 130],
        radius=20,
        fill=(30, 30, 70, 255),
        outline=CYAN, width=8)

    # Буква N
    d.line([(cx - 60, cy - 60), (cx - 60, cy + 60)],
            fill=CYAN, width=18)
    d.line([(cx - 60, cy - 60), (cx + 60, cy + 60)],
            fill=PURPLE, width=18)
    d.line([(cx + 60, cy - 60), (cx + 60, cy + 60)],
            fill=CYAN, width=18)

    # Уголки
    for px, py in [
        (cx - 150, cy - 150), (cx + 150, cy - 150),
        (cx - 150, cy + 150), (cx + 150, cy + 150)]:
        d.ellipse([px - 12, py - 12, px + 12, py + 12], fill=PURPLE)

    return apply_glow(img)


# ═══════════════════════════════════════════════════════════
# 2: Нейросеть
# ═══════════════════════════════════════════════════════════
def make_neural() -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2

    nodes = [
        (0, 0),
        (-110, -110), (110, -110),
        (-140, 40), (140, 40),
        (-70, 140), (70, 140),
        (0, -150),
    ]

    connections = [
        (0, 1), (0, 2), (0, 3), (0, 4),
        (0, 5), (0, 6), (0, 7),
        (1, 7), (2, 7),
        (1, 3), (2, 4),
        (3, 5), (4, 6),
    ]

    for i, j in connections:
        x1, y1 = cx + nodes[i][0], cy + nodes[i][1]
        x2, y2 = cx + nodes[j][0], cy + nodes[j][1]
        d.line([(x1, y1), (x2, y2)],
               fill=(0, 229, 255, 100), width=5)

    for i, (x, y) in enumerate(nodes):
        px, py = cx + x, cy + y
        r = 25 if i == 0 else 16
        color = CYAN if i == 0 else (PURPLE if i % 2 else CYAN)
        d.ellipse([px - r, py - r, px + r, py + r], fill=color)

    return apply_glow(img)


# ═══════════════════════════════════════════════════════════
# 3: Атом
# ═══════════════════════════════════════════════════════════
def make_atom() -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2

    # 3 эллипса-орбиты
    for angle_deg in (0, 60, 120):
        angle = math.radians(angle_deg)
        pts = []
        a, b = 180, 70
        for t in range(0, 360, 4):
            tr = math.radians(t)
            x = a * math.cos(tr)
            y = b * math.sin(tr)
            xr = x * math.cos(angle) - y * math.sin(angle)
            yr = x * math.sin(angle) + y * math.cos(angle)
            pts.append((cx + xr, cy + yr))
        d.line(pts, fill=CYAN, width=6)

    # Ядро
    d.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=PURPLE)
    d.ellipse([cx - 45, cy - 45, cx + 45, cy + 45],
              outline=WHITE, width=4)

    # Точки
    for dx, dy, c in [
        (130, -30, CYAN), (-140, 20, PINK), (40, 100, CYAN)]:
        d.ellipse([cx + dx - 14, cy + dy - 14,
                    cx + dx + 14, cy + dy + 14], fill=c)

    return apply_glow(img)


# ═══════════════════════════════════════════════════════════
# 4: Терминал N
# ═══════════════════════════════════════════════════════════
def make_terminal() -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2

    # Заголовок "окна" терминала
    d.rectangle([cx - 180, cy - 180, cx + 180, cy - 140],
                 fill=(50, 50, 80, 255), outline=CYAN, width=3)

    # Три точки (close, max, min)
    for i, c in enumerate([PINK, PURPLE, CYAN]):
        d.ellipse([cx - 165 + i * 25, cy - 168,
                    cx - 155 + i * 25, cy - 158], fill=c)

    # N в терминале
    d.line([(cx - 80, cy - 80), (cx - 80, cy + 100)],
            fill=CYAN, width=16)
    d.line([(cx - 80, cy - 80), (cx + 60, cy + 100)],
            fill=PURPLE, width=16)
    d.line([(cx + 60, cy - 80), (cx + 60, cy + 100)],
            fill=CYAN, width=16)

    # Промпт $ >
    d.text((cx - 140, cy + 120), "$",
            fill=CYAN)
    d.rectangle([cx - 110, cy + 130, cx - 80, cy + 150],
                 fill=CYAN)

    return apply_glow(img)


# ═══════════════════════════════════════════════════════════
# 5: Геометрия </>
# ═══════════════════════════════════════════════════════════
def make_geom() -> Image.Image:
    img = base_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = SIZE // 2, SIZE // 2

    # Внешний ромб
    d.polygon([
        (cx, cy - 190),
        (cx + 190, cy),
        (cx, cy + 190),
        (cx - 190, cy),
    ], outline=CYAN, width=8)

    # Внутренний ромб
    d.polygon([
        (cx, cy - 130),
        (cx + 130, cy),
        (cx, cy + 130),
        (cx - 130, cy),
    ], outline=PURPLE, width=4)

    # Символы < / >
    # <
    d.line([(cx - 70, cy - 40), (cx - 40, cy)],
            fill=CYAN, width=14)
    d.line([(cx - 40, cy), (cx - 70, cy + 40)],
            fill=CYAN, width=14)
    # /
    d.line([(cx - 15, cy + 50), (cx + 15, cy - 50)],
            fill=PINK, width=14)
    # >
    d.line([(cx + 40, cy - 40), (cx + 70, cy)],
            fill=CYAN, width=14)
    d.line([(cx + 70, cy), (cx + 40, cy + 40)],
            fill=CYAN, width=14)

    return apply_glow(img)


# ═══════════════════════════════════════════════════════════
# Главная
# ═══════════════════════════════════════════════════════════
def main() -> None:
    print("🎨 Генерация 5 техно-иконок NEXUS v2")
    print("=" * 50)
    print()

    generators = [
        ("cube", make_cube, "🧊 Кубик N"),
        ("neural", make_neural, "🕸️ Нейросеть"),
        ("atom", make_atom, "⚛️ Атом"),
        ("terminal", make_terminal, "💻 Терминал"),
        ("geom", make_geom, "🔷 Геометрия"),
    ]

    ok_count = 0
    for name, fn, desc in generators:
        try:
            img = fn()
            save_icon(img, name)
            print(f"     {desc}")
            ok_count += 1
        except Exception as e:
            print(f"  ❌ {name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    print()
    print(f"✅ Успешно: {ok_count}/{len(generators)}")
    print(f"📁 Папка: {OUT_DIR}")
    print()
    if ok_count > 0:
        print("Открой:")
        print(f"   xdg-open {OUT_DIR}")
        print()


if __name__ == "__main__":
    main()