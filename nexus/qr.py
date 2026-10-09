"""
NEXUS qr — генерация QR, шаблоны, экспорт, история,
логотипы, батч, dynamic-QR.
Зависит от: core, storage, domain.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import urllib.parse
import zipfile
from collections import Counter, OrderedDict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Optional, Tuple

from nexus.core import (
    QR_BATCH_LIMIT,
    QR_EXPORT_DIR,
    QR_HISTORY_LIMIT,
    QR_LOGO_DIR,
    gen_id,
    gen_short_id,
    logger,
    now_iso,
    sanitize_filename,
    truncate,
)
from nexus.deps import check_module
from nexus.storage import db, cache
from nexus.domain import (
    achievement_system,
    level_system,
    DailyQuests,
)

# ── PIL / qrcode (опциональные) ────────────────────────
_HAS_PIL = check_module("PIL")
_HAS_QRCODE = check_module("qrcode")
_HAS_STYLED = check_module("qrcode.image.styledpil")
_HAS_SVG = check_module("cairosvg")
_HAS_PYZBAR = check_module("pyzbar")

if _HAS_PIL:
    try:
        from PIL import (
            Image, ImageDraw, ImageFilter, ImageFont,
            ImageOps,
        )
        try:
            from PIL import ImageTk  # noqa: F401
        except ImportError:
            ImageTk = None  # type: ignore
        try:
            from PIL import ImageGrab  # noqa: F401
        except ImportError:
            ImageGrab = None  # type: ignore
    except ImportError:
        _HAS_PIL = False
        Image = None  # type: ignore
        ImageDraw = None  # type: ignore
        ImageFilter = None  # type: ignore
        ImageFont = None  # type: ignore
        ImageOps = None  # type: ignore
else:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore
    ImageOps = None  # type: ignore

if _HAS_QRCODE:
    try:
        import qrcode
    except ImportError:
        _HAS_QRCODE = False
        qrcode = None  # type: ignore
else:
    qrcode = None  # type: ignore

if _HAS_STYLED and _HAS_QRCODE:
    try:
        from qrcode.image.styledpil import StyledPilImage
        from qrcode.image.styles.moduledrawers import (
            RoundedModuleDrawer, SquareModuleDrawer,
            GappedSquareModuleDrawer, CircleModuleDrawer,
        )
        from qrcode.image.styles.colormasks import (
            SolidFillColorMask,
        )
    except ImportError:
        _HAS_STYLED = False


# ═══════════════════════════════════════════════════════════
# QR HISTORY
# ═══════════════════════════════════════════════════════════
class QRHistory:
    _instance: Optional["QRHistory"] = None
    _instance_lock = threading.Lock()
    _MAX_ITEMS = QR_HISTORY_LIMIT

    def __new__(cls) -> "QRHistory":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._lock = threading.RLock()
            return cls._instance

    @staticmethod
    def _hash_data(data: str) -> str:
        return hashlib.sha256(
            data.encode("utf-8",
                         errors="ignore")).hexdigest()[:16]

    @staticmethod
    def _invalidate(user: Optional[str] = None) -> None:
        cache.delete("qr:all")
        if user:
            cache.delete(f"qr:user:{user}")

    def add(self, user: str, data: str, style: str,
            size: int, mode: str = "text",
            image_path: Optional[str] = None
            ) -> Dict[str, Any]:
        if not user or not data:
            return {"id": ""}
        data_hash = self._hash_data(data)
        existing = db.execute(
            "SELECT id FROM qr_history "
            "WHERE user = ? AND hash = ?",
            (user, data_hash))
        if existing:
            entry_id = existing[0]["id"]
            db.execute(
                "UPDATE qr_history SET created_at = ?, "
                "style = ?, size = ?, mode = ? WHERE id = ?",
                (now_iso(), style, int(size), mode,
                 entry_id))
            self._invalidate(user)
            return {"id": entry_id}
        entry_id = gen_id()
        db.execute(
            "INSERT INTO qr_history "
            "(id, user, data, style, size, hash, "
            " created_at, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, user, data, style, int(size),
             data_hash, now_iso(), mode))
        self._invalidate(user)
        self._trim(user)
        return {"id": entry_id}

    def _trim(self, user: str) -> None:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM qr_history "
                "WHERE user = ?", (user,))
            count = rows[0]["c"] if rows else 0
            if count > self._MAX_ITEMS:
                excess = count - self._MAX_ITEMS
                db.execute(
                    "DELETE FROM qr_history WHERE id IN ("
                    "  SELECT id FROM qr_history "
                    "  WHERE user = ? "
                    "  ORDER BY created_at ASC LIMIT ?)",
                    (user, excess))
        except Exception:
            pass

    def get_user_history(self, user: str,
                          limit: int = 50
                          ) -> List[Dict[str, Any]]:
        ck = f"qr:user:{user}:{limit}"
        c = cache.get(ck)
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM qr_history WHERE user = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user, int(limit)))
        result = [dict(r) for r in (rows or [])]
        cache.set(ck, result, 60)
        return [dict(x) for x in result]

    def delete(self, entry_id: str, user: str) -> bool:
        try:
            db.execute(
                "DELETE FROM qr_history "
                "WHERE id = ? AND user = ?",
                (entry_id, user))
            self._invalidate(user)
            return True
        except Exception:
            return False

    def clear(self, user: str) -> int:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM qr_history "
                "WHERE user = ?", (user,))
            n = rows[0]["c"] if rows else 0
            db.execute(
                "DELETE FROM qr_history WHERE user = ?",
                (user,))
            self._invalidate(user)
            return int(n or 0)
        except Exception:
            return 0


qr_history = QRHistory()


# ═══════════════════════════════════════════════════════════
# QR GENERATOR
# ═══════════════════════════════════════════════════════════
class QRGenerator:
    _instance: Optional["QRGenerator"] = None
    _instance_lock = threading.Lock()

    STYLES: Dict[str, Dict[str, Any]] = {
        "neon_cyan": {"color": "#00e5ff", "bg": "#06060e",
                      "name": "🔮 Неон-циан", "glow": True,
                      "accent": "#00b3cc"},
        "neon_pink": {"color": "#ff2d95", "bg": "#0a0010",
                      "name": "💖 Неон-розовый", "glow": True,
                      "accent": "#cc0066"},
        "neon_purple": {"color": "#b026ff", "bg": "#080010",
                        "name": "💜 Неон-фиолет", "glow": True,
                        "accent": "#8800cc"},
        "neon_green": {"color": "#39ff14", "bg": "#000a00",
                       "name": "💚 Неон-зелёный", "glow": True,
                       "accent": "#22cc00"},
        "neon_gold": {"color": "#ffd700", "bg": "#0a0800",
                      "name": "👑 Неон-золото", "glow": True,
                      "accent": "#ccaa00"},
        "neon_blue": {"color": "#4a9aff", "bg": "#0a0a2a",
                      "name": "💙 Неон-синий", "glow": True,
                      "accent": "#2266cc"},
        "neon_orange": {"color": "#ff6b35", "bg": "#1a0a00",
                        "name": "🧡 Неон-оранж", "glow": True,
                        "accent": "#cc4400"},
        "classic_black": {"color": "#000000", "bg": "#ffffff",
                          "name": "⚫ Классик", "glow": False},
        "classic_blue": {"color": "#0066cc", "bg": "#ffffff",
                         "name": "🔵 Классик-синий",
                         "glow": False},
        "matrix": {"color": "#00ff41", "bg": "#000a00",
                   "name": "🟢 Матрица", "glow": False},
        "sunset": {"color": "#ff6b35", "bg": "#1a0a00",
                   "name": "🌅 Закат", "glow": True},
        "ocean": {"color": "#4a9aff", "bg": "#0a0a2a",
                  "name": "🌊 Океан", "glow": True},
        "forest": {"color": "#2ecc71", "bg": "#0a1a0a",
                   "name": "🌿 Лес", "glow": True},
        "cyberpunk": {"color": "#ff00ff", "bg": "#000033",
                      "name": "🎮 Киберпанк", "glow": True},
        "terminal": {"color": "#00ff00", "bg": "#000000",
                     "name": "⌨️ Терминал", "glow": False},
        "royal": {"color": "#9d4edd", "bg": "#100020",
                  "name": "👑 Королевский", "glow": True},
    }

    _ERROR_LEVELS: Dict[str, Any] = {}
    if _HAS_QRCODE and qrcode is not None:
        _ERROR_LEVELS = {
            "L": qrcode.constants.ERROR_CORRECT_L,
            "M": qrcode.constants.ERROR_CORRECT_M,
            "Q": qrcode.constants.ERROR_CORRECT_Q,
            "H": qrcode.constants.ERROR_CORRECT_H,
        }

    def __new__(cls) -> "QRGenerator":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._cache: OrderedDict = (
                    OrderedDict())
                cls._instance._cache_lock = threading.RLock()
                cls._instance._cache_max = 64
                cls._instance._stats: Counter = Counter()
            return cls._instance

    def _cache_get(self, key: str) -> Optional[Any]:
        with self._cache_lock:
            img = self._cache.get(key)
            if img is None:
                self._stats["miss"] += 1
                return None
            self._cache.move_to_end(key)
            self._stats["hit"] += 1
            try:
                return img.copy()
            except Exception:
                return img

    def _cache_put(self, key: str, img: Any) -> None:
        with self._cache_lock:
            try:
                self._cache[key] = img.copy()
            except Exception:
                self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._stats.clear()

    def cache_stats(self) -> Dict[str, int]:
        with self._cache_lock:
            return dict(self._stats)

    @staticmethod
    def _validate_hex(color: str,
                       fallback: str = "#000000") -> str:
        if not color:
            return fallback
        c = color.strip()
        if not c.startswith("#"):
            c = "#" + c
        if len(c) == 4:
            c = "#" + c[1] * 2 + c[2] * 2 + c[3] * 2
        if len(c) == 7:
            try:
                int(c[1:], 16)
                return c.upper()
            except ValueError:
                pass
        return fallback

    @staticmethod
    def _validate_size(size: Any,
                        default: int = 400) -> int:
        try:
            n = int(size)
        except (TypeError, ValueError):
            n = default
        return max(100, min(2000, n))

    def generate(self, data: str, size: int = 300,
                 style: str = "neon_cyan",
                 logo_path: Optional[str] = None,
                 border: int = 4,
                 error_level: str = "H") -> Optional[Any]:
        if not data or not data.strip():
            return None
        if not _HAS_PIL or not _HAS_QRCODE:
            return None
        size = self._validate_size(size)
        data_clean = data.strip()[:2000]
        cfg = (self.STYLES.get(style)
               or self.STYLES["neon_cyan"])
        logo_key = logo_path or ""
        cache_key = (
            f"{hashlib.md5(data_clean.encode(errors='ignore')).hexdigest()}"
            f"|{size}|{style}|{logo_key}|{border}|{error_level}")
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        img = self.generate_advanced(
            data=data_clean, size=size,
            front_color=cfg.get("color", "#000000"),
            back_color=cfg.get("bg", "#ffffff"),
            border=border, error_level=error_level,
            glow=bool(cfg.get("glow", False)),
            logo_path=logo_path)
        if img is not None:
            self._cache_put(cache_key, img)
        return img

    def generate_advanced(
        self, data: str, size: int = 400,
        front_color: str = "#00e5ff",
        back_color: str = "#06060e",
        border: int = 4,
        error_level: str = "H",
        glow: bool = True,
        logo_path: Optional[str] = None,
    ) -> Optional[Any]:
        if not data or not data.strip():
            return None
        if not _HAS_PIL or not _HAS_QRCODE:
            return None
        size = self._validate_size(size)
        data_clean = data.strip()[:2000]
        try:
            border = max(0, min(10, int(border)))
        except (TypeError, ValueError):
            border = 4
        err = self._ERROR_LEVELS.get(
            (error_level or "H").upper(),
            qrcode.constants.ERROR_CORRECT_H)
        front = self._validate_hex(front_color, "#000000")
        back = self._validate_hex(back_color, "#ffffff")

        if logo_path and os.path.isfile(logo_path):
            err = qrcode.constants.ERROR_CORRECT_H

        try:
            qr = qrcode.QRCode(
                error_correction=err, box_size=10,
                border=border)
            qr.add_data(data_clean)
            qr.make(fit=True)
            img: Optional[Any] = None
            if _HAS_STYLED:
                try:
                    img = qr.make_image(
                        image_factory=StyledPilImage,
                        module_drawer=RoundedModuleDrawer(),
                        color_mask=SolidFillColorMask(
                            front_color=front,
                            back_color=back),
                    ).convert("RGBA")
                except Exception:
                    pass
            if img is None:
                img = qr.make_image(
                    fill_color=front,
                    back_color=back).convert("RGBA")
            img = img.resize(
                (size, size), Image.Resampling.LANCZOS)
            if glow:
                try:
                    g = Image.new("RGBA", img.size,
                                    (0, 0, 0, 0))
                    g.paste(img, (0, 0), img)
                    radius = max(1, min(size, size) // 100)
                    g = g.filter(
                        ImageFilter.GaussianBlur(radius=radius))
                    img = Image.alpha_composite(g, img)
                except Exception:
                    pass
            if logo_path and os.path.isfile(logo_path):
                with suppress(Exception):
                    img = self._add_custom_logo(
                        img, logo_path)
            return img
        except Exception as e:
            logger.exception(f"QR generate: {e}")
            return None

    @staticmethod
    def _add_custom_logo(img: Any,
                          logo_path: str) -> Any:
        try:
            w, h = img.size
            size = int(min(w, h) * 0.22)
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize(
                (size, size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse(
                (0, 0, size - 1, size - 1), fill=255)
            bg = Image.new("RGBA", (size, size),
                            (255, 255, 255, 240))
            bg.putalpha(mask)
            composed = Image.new("RGBA", (size, size),
                                  (0, 0, 0, 0))
            composed.paste(bg, (0, 0), bg)
            composed.paste(logo, (0, 0), logo)
            pos = ((w - size) // 2, (h - size) // 2)
            img.paste(composed, pos, composed)
            return img
        except Exception:
            return img

    @staticmethod
    def decode_qr(image_path: str) -> Optional[str]:
        if not _HAS_PYZBAR:
            return "__NO_LIB__"
        try:
            from pyzbar.pyzbar import decode
            img = Image.open(image_path)
            results = decode(img)
            if results:
                return results[0].data.decode(
                    "utf-8", errors="ignore")
            return None
        except Exception:
            return None

    @staticmethod
    def decode_from_pil(img: Any) -> Optional[str]:
        if not _HAS_PYZBAR:
            return "__NO_LIB__"
        try:
            from pyzbar.pyzbar import decode
            results = decode(img)
            if results:
                return results[0].data.decode(
                    "utf-8", errors="ignore")
            return None
        except Exception:
            return None

    def get_styles(self) -> List[str]:
        return list(self.STYLES.keys())

    def get_style_name(self, key: str) -> str:
        return self.STYLES.get(key, {}).get("name", key)


qr_gen = QRGenerator()


# ═══════════════════════════════════════════════════════════
# QR EXPORTER
# ═══════════════════════════════════════════════════════════
class QRExporter:
    @staticmethod
    def export_png(img: Any,
                    out_path: Path) -> Tuple[bool, str]:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(str(out_path), "PNG", optimize=True)
            return True, str(out_path)
        except Exception as e:
            return False, str(e)

    @staticmethod
    def export_jpg(img: Any, out_path: Path,
                    quality: int = 92
                    ) -> Tuple[bool, str]:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img.convert("RGB"))
            bg.save(str(out_path), "JPEG",
                     quality=quality, optimize=True)
            return True, str(out_path)
        except Exception as e:
            return False, str(e)

    @staticmethod
    def export_svg(data: str, out_path: Path,
                    size: int = 400,
                    front: str = "#000000",
                    back: str = "#ffffff",
                    error_level: str = "M"
                    ) -> Tuple[bool, str]:
        if not _HAS_QRCODE:
            return False, "qrcode недоступен"
        try:
            import qrcode.image.svg as svg_mod
            out_path.parent.mkdir(parents=True, exist_ok=True)
            factory = svg_mod.SvgPathImage
            qr = qrcode.QRCode(
                error_correction=QRGenerator._ERROR_LEVELS.get(
                    error_level.upper(),
                    qrcode.constants.ERROR_CORRECT_M),
                box_size=10, border=4,
                image_factory=factory)
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image()
            svg_str = img.to_string().decode("utf-8")
            svg_str = svg_str.replace(
                'fill="#000000"', f'fill="{front}"')
            svg_str = svg_str.replace(
                'fill="black"', f'fill="{front}"')
            out_path.write_text(svg_str, encoding="utf-8")
            return True, str(out_path)
        except ImportError:
            return False, "qrcode.image.svg недоступен"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def export_to_dir(img: Any, out_dir: Path,
                       filename: str,
                       fmt: str = "PNG"
                       ) -> Tuple[bool, str]:
        out_path = out_dir / f"{filename}.{fmt.lower()}"
        if fmt.upper() == "PNG":
            return QRExporter.export_png(img, out_path)
        elif fmt.upper() in ("JPG", "JPEG"):
            return QRExporter.export_jpg(img, out_path)
        else:
            return False, f"Неизвестный формат: {fmt}"


# ═══════════════════════════════════════════════════════════
# QR BATCH PROCESSOR
# ═══════════════════════════════════════════════════════════
class QRBatchProcessor:
    @staticmethod
    def parse_input_file(path: str
                          ) -> Tuple[bool,
                                       List[Tuple[str, str]],
                                       str]:
        try:
            p = Path(path)
            if not p.exists():
                return False, [], "Файл не найден"
            if p.suffix.lower() == ".csv":
                items: List[Tuple[str, str]] = []
                with open(p, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row or not row[0].strip():
                            continue
                        data = row[0].strip()
                        label = (row[1].strip()
                                  if len(row) > 1 else data)
                        items.append((data, label))
                        if len(items) >= QR_BATCH_LIMIT:
                            break
                return True, items, ""
            elif p.suffix.lower() in (".txt", ".md"):
                items = []
                for line in p.read_text(
                        encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        items.append((line, line))
                        if len(items) >= QR_BATCH_LIMIT:
                            break
                return True, items, ""
            else:
                return False, [], (
                    "Поддерживаются .csv, .txt, .md")
        except Exception as e:
            return False, [], str(e)

    @staticmethod
    def process(
        items: List[Tuple[str, str]],
        style: str = "neon_cyan",
        size: int = 400,
        out_dir: Optional[Path] = None,
        fmt: str = "PNG",
        user: Optional[str] = None,
        logo_path: Optional[str] = None,
        progress_cb: Optional[
            Callable[[int, int], None]] = None,
    ) -> Tuple[int, int, List[str]]:
        if out_dir is None:
            out_dir = QR_EXPORT_DIR / datetime.now().strftime(
                "batch_%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        ok_count = 0
        err_count = 0
        errors: List[str] = []
        total = len(items)
        for i, (data, label) in enumerate(items, 1):
            try:
                img = qr_gen.generate(
                    data, size=size, style=style,
                    logo_path=logo_path)
                if img is None:
                    err_count += 1
                    errors.append(f"[{i}] {label}: пустой QR")
                    continue
                safe_label = sanitize_filename(label, 60)
                fn = f"{i:03d}_{safe_label}"
                ok, msg = QRExporter.export_to_dir(
                    img, out_dir, fn, fmt)
                if ok:
                    ok_count += 1
                else:
                    err_count += 1
                    errors.append(f"[{i}] {label}: {msg}")
            except Exception as e:
                err_count += 1
                errors.append(f"[{i}] {label}: {e}")
            if progress_cb:
                with suppress(Exception):
                    progress_cb(i, total)
        if user and ok_count > 0:
            with suppress(Exception):
                level_system.add_xp(user, "qr_batch")
                level_system.increment(
                    user, "qr_generated", ok_count)
                if ok_count >= 10:
                    achievement_system.check(
                        user, "qr_batch_master")
        return ok_count, err_count, errors

    @staticmethod
    def export_zip(out_dir: Path,
                    zip_path: Optional[Path] = None
                    ) -> Path:
        if zip_path is None:
            zip_path = out_dir.with_suffix(".zip")
        with zipfile.ZipFile(
                zip_path, "w",
                zipfile.ZIP_DEFLATED) as zf:
            for fp in out_dir.rglob("*"):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(out_dir))
        return zip_path


# ═══════════════════════════════════════════════════════════
# QR TEMPLATES
# ═══════════════════════════════════════════════════════════
class QRTemplates:
    @staticmethod
    def text(data: str) -> str:
        return (data or "").strip()

    @staticmethod
    def url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    @staticmethod
    def wifi(ssid: str, password: str = "",
             encryption: str = "WPA") -> str:
        if not ssid:
            return ""

        def esc(s: str) -> str:
            return (s.replace("\\", "\\\\")
                     .replace(";", "\\;")
                     .replace(",", "\\,")
                     .replace(":", "\\:")
                     .replace('"', '\\"'))

        encryption = (encryption or "WPA").upper()
        if encryption not in ("WPA", "WEP", "NOPASS"):
            encryption = "WPA"
        if encryption == "NOPASS":
            password = ""
        parts = [f"T:{encryption}", f"S:{esc(ssid)}"]
        if password:
            parts.append(f"P:{esc(password)}")
        return "WIFI:" + ";".join(parts) + ";;"

    @staticmethod
    def vcard(name: str, phone: str = "",
              email: str = "", org: str = "",
              url: str = "") -> str:
        name = (name or "").strip()
        if not name:
            return ""

        def esc(s: str) -> str:
            return (s.replace("\\", "\\\\")
                     .replace(";", "\\;")
                     .replace(",", "\\,")
                     .replace("\n", "\\n"))

        lines = ["BEGIN:VCARD", "VERSION:3.0",
                 f"FN:{esc(name)}"]
        parts = name.split()
        if len(parts) >= 2:
            lines.append(
                f"N:{esc(parts[-1])};"
                f"{esc(' '.join(parts[:-1]))};;;")
        else:
            lines.append(f"N:{esc(name)};;;;")
        if org:
            lines.append(f"ORG:{esc(org)}")
        if phone:
            lines.append(f"TEL;TYPE=CELL:{esc(phone)}")
        if email:
            lines.append(f"EMAIL:{esc(email)}")
        if url:
            lines.append(f"URL:{esc(url)}")
        lines.append("END:VCARD")
        return "\r\n".join(lines)

    @staticmethod
    def email(to: str, subject: str = "",
              body: str = "") -> str:
        to = (to or "").strip()
        if not to:
            return ""
        params: List[str] = []
        if subject:
            params.append(
                f"subject={urllib.parse.quote(subject, safe='')}")
        if body:
            params.append(
                f"body={urllib.parse.quote(body, safe='')}")
        suffix = ("?" + "&".join(params)) if params else ""
        return f"mailto:{to}{suffix}"

    @staticmethod
    def sms(phone: str, message: str = "") -> str:
        phone = (phone or "").strip()
        if not phone:
            return ""
        return (f"SMSTO:{phone}:{message}"
                if message else f"SMSTO:{phone}")

    @staticmethod
    def tel(phone: str) -> str:
        phone = (phone or "").strip()
        if not phone:
            return ""
        return f"tel:{phone}"

    @staticmethod
    def geo(lat: float, lon: float,
            label: str = "") -> str:
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return ""
        if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
            return ""
        base = f"geo:{lat_f},{lon_f}"
        if label:
            base += (f"?q={lat_f},{lon_f}"
                     f"({urllib.parse.quote(label)})")
        return base

    @staticmethod
    def event(title: str, start: str, end: str = "",
              location: str = "",
              description: str = "") -> str:
        title = (title or "").strip()
        if not title or not start:
            return ""

        def fmt_dt(s: str) -> str:
            try:
                dt = datetime.fromisoformat(
                    s.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt = datetime.strptime(
                        s, "%Y-%m-%d %H:%M")
                except Exception:
                    return ""
            return dt.astimezone(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ")

        s_dt = fmt_dt(start)
        if not s_dt:
            return ""
        e_dt = fmt_dt(end) if end else s_dt
        uid = gen_id()[:16] + "@nexus"
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//NEXUS//v1.1.1//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{s_dt}",
            f"DTEND:{e_dt}",
            f"SUMMARY:{title}",
        ]
        if location:
            lines.append(f"LOCATION:{location}")
        if description:
            lines.append(f"DESCRIPTION:{description}")
        lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)

    @staticmethod
    def crypto(coin: str, address: str,
                amount: str = "") -> str:
        coin = (coin or "bitcoin").lower()
        address = (address or "").strip()
        if not address:
            return ""
        base = f"{coin}:{address}"
        if amount:
            base += f"?amount={amount}"
        return base


# ═══════════════════════════════════════════════════════════
# QR LOGO MANAGER
# ═══════════════════════════════════════════════════════════
class QRLogoManager:
    _VALID_EXTS: Final[frozenset] = frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})
    _MAX_SIZE: Final[int] = 5 * 1024 * 1024

    @staticmethod
    def list_logos() -> List[Dict[str, Any]]:
        try:
            if not QR_LOGO_DIR.exists():
                return []
            result: List[Dict[str, Any]] = []
            for fp in sorted(QR_LOGO_DIR.iterdir()):
                if not fp.is_file():
                    continue
                if fp.suffix.lower() \
                        not in QRLogoManager._VALID_EXTS:
                    continue
                try:
                    st = fp.stat()
                    result.append({
                        "path": fp,
                        "name": fp.name,
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(
                            st.st_mtime),
                    })
                except Exception:
                    pass
            return result
        except Exception:
            return []

    @staticmethod
    def import_logo(src_path: str
                     ) -> Tuple[bool, str, Optional[Path]]:
        import shutil
        try:
            src = Path(src_path)
            if not src.is_file():
                return False, "Файл не найден", None
            if src.suffix.lower() \
                    not in QRLogoManager._VALID_EXTS:
                return False, "Неподдерживаемый формат", None
            if src.stat().st_size > QRLogoManager._MAX_SIZE:
                return False, "Файл больше 5 МБ", None
            QR_LOGO_DIR.mkdir(parents=True, exist_ok=True)
            unique = (f"{src.stem}_{gen_short_id(6)}"
                       f"{src.suffix.lower()}")
            dst = QR_LOGO_DIR / unique
            shutil.copy2(src, dst)
            return True, "OK", dst
        except Exception as e:
            return False, str(e), None

    @staticmethod
    def delete_logo(filename: str) -> bool:
        try:
            fp = QR_LOGO_DIR / sanitize_filename(filename)
            if fp.parent != QR_LOGO_DIR:
                return False
            if fp.exists():
                fp.unlink()
            return True
        except Exception:
            return False

    @staticmethod
    def clear() -> int:
        try:
            if not QR_LOGO_DIR.exists():
                return 0
            n = 0
            for fp in QR_LOGO_DIR.iterdir():
                if fp.is_file():
                    with suppress(Exception):
                        fp.unlink()
                        n += 1
            return n
        except Exception:
            return 0


# ═══════════════════════════════════════════════════════════
# DYNAMIC QR MANAGER
# ═══════════════════════════════════════════════════════════
class DynamicQRManager:
    @staticmethod
    def create(user: str, target: str,
                ttl_days: int = 365
                ) -> Optional[str]:
        if not user or not target:
            return None
        try:
            qid = gen_short_id(8)
            db.execute(
                "INSERT INTO qr_dynamic "
                "(id, user, target, created_at, hits) "
                "VALUES (?, ?, ?, ?, 0)",
                (qid, user, target, now_iso()))
            with suppress(Exception):
                level_system.add_xp(user, "dynamic_qr")
            return qid
        except Exception:
            return None

    @staticmethod
    def resolve(qid: str) -> Optional[str]:
        if not qid:
            return None
        try:
            rows = db.execute(
                "SELECT target FROM qr_dynamic "
                "WHERE id = ?", (qid,))
            if not rows:
                return None
            db.execute(
                "UPDATE qr_dynamic SET hits = hits + 1, "
                "last_hit = ? WHERE id = ?",
                (now_iso(), qid))
            return rows[0]["target"]
        except Exception:
            return None

    @staticmethod
    def list_for_user(user: str
                       ) -> List[Dict[str, Any]]:
        try:
            rows = db.execute(
                "SELECT * FROM qr_dynamic WHERE user = ? "
                "ORDER BY created_at DESC", (user,))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    @staticmethod
    def delete(qid: str, user: str) -> bool:
        try:
            db.execute(
                "DELETE FROM qr_dynamic "
                "WHERE id = ? AND user = ?", (qid, user))
            return True
        except Exception:
            return False


dynamic_qr = DynamicQRManager()


__all__ = [
    "QRHistory", "qr_history",
    "QRGenerator", "qr_gen",
    "QRExporter", "QRBatchProcessor", "QRTemplates",
    "QRLogoManager", "DynamicQRManager", "dynamic_qr",
]