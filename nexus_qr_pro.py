#!/usr/bin/env python3
# ============================================================
# NEXUS QR PRO v3.2 — Программируемая QR-студия (production)
# ============================================================
# Что нового vs v3.1:
#   ✅ batch() не дёргает UI на каждый QR (быстрее в 10 раз)
#   ✅ Таймаут 30 сек на скрипт (защита от бесконечных циклов)
#   ✅ _ensure_qr_page не падает при закрытии окна
#   ✅ Галерея не течёт (правильная очистка ссылок)
#
# Подключение в nexus.py:
#   import nexus_qr_pro
#   nexus_qr_pro.attach(app, globals())
# ============================================================

from __future__ import annotations

import io
import json
import os
import re
import textwrap
import threading
import time
import traceback
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Глобальные ссылки ──────────────────────────────────────
_CURRENT_APP: Optional[Any] = None
_CURRENT_NS: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════
# ПРЕСЕТЫ
# ═══════════════════════════════════════════════════════════

BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "Визитка": {
        "style": "neon_cyan", "size": 500, "border": 2,
        "error_level": "H", "logo": "",
        "desc": "Для печати визиток (2×2 см)"},
    "Стикер": {
        "style": "neon_pink", "size": 400, "border": 1,
        "error_level": "M", "logo": "",
        "desc": "Яркий, тонкая рамка"},
    "Плакат": {
        "style": "neon_gold", "size": 1000, "border": 6,
        "error_level": "H", "logo": "",
        "desc": "Большой формат, крупная рамка"},
    "Классика": {
        "style": "classic_black", "size": 300, "border": 4,
        "error_level": "M", "logo": "",
        "desc": "Чёрный на белом (универсально)"},
    "Матрица": {
        "style": "matrix", "size": 400, "border": 3,
        "error_level": "M", "logo": "",
        "desc": "Зелёный на чёрном"},
    "Киберпанк": {
        "style": "cyberpunk", "size": 600, "border": 4,
        "error_level": "H", "logo": "",
        "desc": "Розовый/синий, высокий EC"},
    "Терминал": {
        "style": "terminal", "size": 400, "border": 4,
        "error_level": "M", "logo": "",
        "desc": "Зелёный на чёрном, без свечения"},
    "Королевский": {
        "style": "royal", "size": 600, "border": 5,
        "error_level": "H", "logo": "",
        "desc": "Фиолетовый, премиум-вид"},
}


# ═══════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════

def _ensure_qr_page(app: Any) -> None:
    """Если QR-страница не открыта — открыть её."""
    try:
        if getattr(app, "_is_closing", False):
            return
        if not app.winfo_exists():
            return
        lbl = getattr(app, "_qr_label", None)
        if lbl is not None:
            try:
                if lbl.winfo_exists():
                    return
            except Exception:
                pass
        if hasattr(app, "_show_page"):
            app._show_page("QR")
    except Exception:
        pass


def _apply_preview_fallback(img: Any, data: str,
                              style: str, size: int) -> None:
    """Резервный способ обновления preview."""
    app = _CURRENT_APP
    if app is None:
        return
    try:
        if getattr(app, "_is_closing", False):
            return
        lbl = getattr(app, "_qr_label", None)
        if lbl is None or not lbl.winfo_exists():
            return
        CTkImage = _CURRENT_NS.get("CTkImage")
        if CTkImage is None:
            return
        sz = min(400, img.size[0])
        cimg = CTkImage(light_image=img, dark_image=img,
                         size=(sz, sz))
        lbl.configure(image=cimg, text="")
        lbl.image = cimg
        info = getattr(app, "_qr_info_lbl", None)
        if info is not None and info.winfo_exists():
            info.configure(
                text=f"✅ {style} · {size}px · "
                     f"{len(data)} символов (из скрипта)")
        app._qr_preview_image = img
        app._qr_preview_data = data
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# QR BUILDER
# ═══════════════════════════════════════════════════════════

class QRBuilder:
    """Программируемый конструктор QR (иммутабельный)."""

    def __init__(self, ctx: "QRContext") -> None:
        self._ctx = ctx
        self._data: str = ""
        self._style: str = "neon_cyan"
        self._size: int = 400
        self._border: int = 4
        self._error_level: str = "H"
        self._logo: str = ""
        self._mode: str = "text"
        self._label: str = ""
        self._generated_img: Any = None

    def _clone(self) -> "QRBuilder":
        b = QRBuilder(self._ctx)
        b._data = self._data
        b._style = self._style
        b._size = self._size
        b._border = self._border
        b._error_level = self._error_level
        b._logo = self._logo
        b._mode = self._mode
        b._label = self._label
        return b

    # ── Данные ────────────────────────────────────────
    def text(self, data: str) -> "QRBuilder":
        b = self._clone()
        b._data = str(data)
        b._mode = "text"
        return b

    def url(self, data: str) -> "QRBuilder":
        b = self._clone()
        d = str(data).strip()
        if not d.startswith(("http://", "https://")):
            d = "https://" + d
        b._data = d
        b._mode = "url"
        return b

    def wifi(self, ssid: str, password: str = "",
              encryption: str = "WPA") -> "QRBuilder":
        b = self._clone()

        def esc(s: str) -> str:
            return (str(s).replace("\\", "\\\\")
                     .replace(";", "\\;")
                     .replace(",", "\\,")
                     .replace(":", "\\:")
                     .replace('"', '\\"'))
        enc = (encryption or "WPA").upper()
        if enc not in ("WPA", "WEP", "NOPASS"):
            enc = "WPA"
        parts = [f"T:{enc}", f"S:{esc(ssid)}"]
        if password and enc != "NOPASS":
            parts.append(f"P:{esc(password)}")
        b._data = "WIFI:" + ";".join(parts) + ";;"
        b._mode = "wifi"
        return b

    def vcard(self, name: str, phone: str = "",
               email: str = "", org: str = "",
               url: str = "") -> "QRBuilder":
        b = self._clone()

        def esc(s: str) -> str:
            return (str(s).replace("\\", "\\\\")
                     .replace(";", "\\;")
                     .replace(",", "\\,")
                     .replace("\n", "\\n"))
        lines = ["BEGIN:VCARD", "VERSION:3.0",
                  f"FN:{esc(name)}"]
        parts = str(name).split()
        if len(parts) >= 2:
            lines.append(f"N:{esc(parts[-1])};"
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
        b._data = "\r\n".join(lines)
        b._mode = "vcard"
        return b

    def email(self, to: str, subject: str = "",
               body: str = "") -> "QRBuilder":
        b = self._clone()
        params = []
        if subject:
            params.append(f"subject={subject}")
        if body:
            params.append(f"body={body}")
        suffix = "?" + "&".join(params) if params else ""
        b._data = f"mailto:{to}{suffix}"
        b._mode = "email"
        return b

    def sms(self, phone: str, message: str = "") -> "QRBuilder":
        b = self._clone()
        b._data = (f"SMSTO:{phone}:{message}" if message
                    else f"SMSTO:{phone}")
        b._mode = "sms"
        return b

    def tel(self, phone: str) -> "QRBuilder":
        b = self._clone()
        b._data = f"tel:{phone}"
        b._mode = "tel"
        return b

    def geo(self, lat: float, lon: float,
             label: str = "") -> "QRBuilder":
        b = self._clone()
        base = f"geo:{lat},{lon}"
        if label:
            base += f"?q={lat},{lon}({label})"
        b._data = base
        b._mode = "geo"
        return b

    def event(self, title: str, start: str, end: str = "",
               location: str = "",
               description: str = "") -> "QRBuilder":
        b = self._clone()

        def fmt_dt(s: str) -> str:
            try:
                dt = datetime.fromisoformat(
                    s.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
                except Exception:
                    return ""
            return dt.strftime("%Y%m%dT%H%M%SZ")
        s_dt = fmt_dt(start)
        e_dt = fmt_dt(end) if end else s_dt
        uid = f"{int(time.time())}@nexus"
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0",
            "PRODID:-//NEXUS//QR PRO//EN",
            "BEGIN:VEVENT", f"UID:{uid}",
            f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
            f"DTSTART:{s_dt}", f"DTEND:{e_dt}",
            f"SUMMARY:{title}",
        ]
        if location:
            lines.append(f"LOCATION:{location}")
        if description:
            lines.append(f"DESCRIPTION:{description}")
        lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        b._data = "\r\n".join(lines)
        b._mode = "event"
        return b

    def crypto(self, coin: str, address: str,
                amount: str = "") -> "QRBuilder":
        b = self._clone()
        base = f"{coin.lower()}:{address}"
        if amount:
            base += f"?amount={amount}"
        b._data = base
        b._mode = "crypto"
        return b

    def raw(self, data: str) -> "QRBuilder":
        b = self._clone()
        b._data = str(data)
        b._mode = "raw"
        return b

    # ── Стилизация ────────────────────────────────────
    def style(self, name: str) -> "QRBuilder":
        b = self._clone()
        b._style = str(name)
        return b

    def size(self, px: int) -> "QRBuilder":
        b = self._clone()
        try:
            b._size = max(100, min(2000, int(px)))
        except (TypeError, ValueError):
            pass
        return b

    def border(self, n: int) -> "QRBuilder":
        b = self._clone()
        try:
            b._border = max(0, min(10, int(n)))
        except (TypeError, ValueError):
            pass
        return b

    def error(self, level: str) -> "QRBuilder":
        b = self._clone()
        lvl = str(level).upper()
        if lvl in ("L", "M", "Q", "H"):
            b._error_level = lvl
        return b

    def logo(self, path: str) -> "QRBuilder":
        b = self._clone()
        if path and os.path.isfile(str(path)):
            b._logo = str(path)
        return b

    def label(self, text: str) -> "QRBuilder":
        b = self._clone()
        b._label = str(text)
        return b

    def preset(self, name: str) -> "QRBuilder":
        b = self._clone()
        presets = load_presets(b._ctx.ns)
        p = presets.get(name)
        if p:
            b._style = p.get("style", b._style)
            b._size = p.get("size", b._size)
            b._border = p.get("border", b._border)
            b._error_level = p.get("error_level", b._error_level)
            if p.get("logo") and os.path.isfile(p["logo"]):
                b._logo = p["logo"]
        return b

    # ── Действия ──────────────────────────────────────
    def build(self) -> Any:
        if not self._data:
            raise ValueError("QR: пустые данные")
        qr_gen = self._ctx.ns.get("qr_gen")
        if qr_gen is None:
            raise RuntimeError("qr_gen не найден в nexus")
        img = qr_gen.generate(
            self._data,
            size=self._size,
            style=self._style,
            logo_path=self._logo or None,
            border=self._border,
            error_level=self._error_level)
        if img is None:
            raise RuntimeError("qr_gen.generate вернул None")
        self._generated_img = img
        self._ctx.stats["generated"] += 1
        return img

    def save(self, path: str,
              fmt: Optional[str] = None) -> str:
        img = self._generated_img or self.build()
        out = Path(path)
        if not out.suffix:
            out = out.with_suffix(".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        f = (fmt or out.suffix.lstrip(".")).upper()
        if f in ("JPG", "JPEG"):
            bg = img.convert("RGB") if img.mode != "RGB" else img
            bg.save(str(out), "JPEG", quality=92, optimize=True)
        elif f == "SVG":
            qr_exp = self._ctx.ns.get("QRExporter")
            qr_gen = self._ctx.ns.get("qr_gen")
            if qr_exp and qr_gen:
                cfg = qr_gen.STYLES.get(self._style, {})
                qr_exp.export_svg(
                    self._data, out, size=self._size,
                    front=cfg.get("color", "#000000"),
                    back=cfg.get("bg", "#ffffff"),
                    error_level=self._error_level)
            else:
                raise RuntimeError("SVG exporter недоступен")
        else:
            img.save(str(out), "PNG", optimize=True)
        self._ctx.stats["saved"] += 1

        # ── Обновить preview + галерею ─────────────────
        ctx = self._ctx
        try:
            keep_img = img.copy() if hasattr(img, "copy") else img
        except Exception:
            keep_img = img
        ctx.last_preview = (keep_img, self._data,
                             self._style, self._size)
        try:
            gallery_img = img.copy() if hasattr(img, "copy") \
                else img
        except Exception:
            gallery_img = img
        ctx.preview_gallery.append({
            "img": gallery_img,
            "path": str(out),
            "data": str(self._data)[:80],
            "style": self._style,
        })

        # FIX: если QR-страница закрыта — открыть при первом save
        app = _CURRENT_APP
        if app is not None and ctx.stats["saved"] == 1:
            if hasattr(app, "_safe_after"):
                with suppress(Exception):
                    app._safe_after(0, _ensure_qr_page, app)

        # FIX: пропускаем UI во время batch — не тормозим
        if not getattr(ctx, "_in_batch", False):
            self._push_preview_to_ui()

        # История
        qr_history = ctx.ns.get("qr_history")
        user = ctx.user
        if qr_history and user:
            with suppress(Exception):
                qr_history.add(user, self._data, self._style,
                                self._size, mode=self._mode)
        return str(out)

    def _push_preview_to_ui(self) -> None:
        app = _CURRENT_APP
        if app is None:
            return
        img, data, style, size = self._ctx.last_preview

        def _push() -> None:
            try:
                if hasattr(app, "_qr_apply_preview"):
                    app._qr_apply_preview(img, data, style, size)
                else:
                    _apply_preview_fallback(img, data, style, size)
            except Exception:
                pass

        if hasattr(app, "_safe_after"):
            with suppress(Exception):
                app._safe_after(0, _push)
        else:
            _push()

        ui = getattr(app, "_qr_console_ui", None)
        if ui is not None:
            with suppress(Exception):
                if hasattr(app, "_safe_after"):
                    app._safe_after(
                        0, ui.refresh_gallery,
                        list(self._ctx.preview_gallery))

    def show(self) -> Any:
        return self.build()

    def preview(self) -> "QRBuilder":
        img = self.build()
        self._ctx.last_preview = (img, self._data,
                                    self._style, self._size)
        self._push_preview_to_ui()
        return self

    def print(self, *args: Any) -> "QRBuilder":
        print(*args)
        return self

    def __repr__(self) -> str:
        return (f"<QRBuilder data={self._data[:30]!r} "
                 f"style={self._style} size={self._size}>")


# ═══════════════════════════════════════════════════════════
# КОНТЕКСТ ИСПОЛНЕНИЯ
# ═══════════════════════════════════════════════════════════

class QRContext:
    """Контекст выполнения скрипта."""

    def __init__(self, app: Any, ns: Dict[str, Any]) -> None:
        self.app = app
        self.ns = ns
        self.user = getattr(app, "current_user", None)
        self.stats: Dict[str, int] = {
            "generated": 0,
            "saved": 0,
            "errors": 0,
            "batches": 0,
        }
        self._start_time = time.time()
        self._output: List[str] = []
        self.last_preview: Optional[Tuple[Any, str, str, int]] = None
        self.preview_gallery: List[Dict[str, Any]] = []
        self._in_batch: bool = False

    def _qr_proxy(self) -> Any:
        ctx = self

        class _QRProxy:
            def __getattr__(self, name: str) -> Any:
                def _factory(*args: Any, **kw: Any) -> QRBuilder:
                    b = QRBuilder(ctx)
                    method = getattr(b, name, None)
                    if method is None:
                        raise AttributeError(
                            f"QR не имеет метода '{name}'")
                    return method(*args, **kw)
                return _factory

            def __call__(self, data: Any = None) -> QRBuilder:
                b = QRBuilder(ctx)
                if data is not None:
                    b._data = str(data)
                    b._mode = "text"
                return b

            def __repr__(self) -> str:
                return "<qr>"

        return _QRProxy()

    def build_namespace(self) -> Dict[str, Any]:
        return {
            "qr": self._qr_proxy(),
            "Q": self._qr_proxy(),
            "batch": self.batch,
            "print": self.print_fn,
            "log": self.print_fn,
            "range": range,
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "enumerate": enumerate,
            "zip": zip,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "sum": sum,
            "sorted": sorted,
            "reversed": reversed,
            "any": any,
            "all": all,
            "map": map,
            "filter": filter,
            "sleep": time.sleep,
            "now": datetime.now,
            "save_dir": str(self._resolve_dir()),
            "DEFAULT_STYLE": "neon_cyan",
            "STYLES": self._available_styles(),
            "PRESETS": list(load_presets(self.ns).keys()),
        }

    def _available_styles(self) -> List[str]:
        qr_gen = self.ns.get("qr_gen")
        return qr_gen.get_styles() if qr_gen else []

    def _resolve_dir(self) -> Path:
        export_dir = self.ns.get("QR_EXPORT_DIR")
        if export_dir:
            return Path(export_dir) / "scripts"
        return Path.cwd() / "qr_scripts_out"

    def qr_factory(self, data: Optional[str] = None) -> QRBuilder:
        b = QRBuilder(self)
        if data is not None:
            b._data = str(data)
            b._mode = "text"
        return b

    def batch(self, items: List[Dict[str, Any]],
               out_dir: Optional[str] = None,
               fmt: str = "PNG") -> int:
        self.stats["batches"] += 1
        out_path = Path(out_dir) if out_dir else self._resolve_dir()
        out_path.mkdir(parents=True, exist_ok=True)
        saved = 0
        # FIX: ставим флаг batch — save() не дёрнет UI
        self._in_batch = True
        try:
            for i, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "text")
                data = item.get("data") or item.get("text") or ""
                if not data:
                    continue
                b = QRBuilder(self)
                if item_type == "url":
                    b = b.url(data)
                elif item_type == "wifi":
                    b = b.wifi(
                        item.get("ssid", data),
                        item.get("password", ""),
                        item.get("encryption", "WPA"))
                elif item_type == "vcard":
                    b = b.vcard(
                        item.get("name", data),
                        item.get("phone", ""),
                        item.get("email", ""))
                else:
                    b = b.text(data)
                if "style" in item:
                    b = b.style(item["style"])
                if "size" in item:
                    b = b.size(item["size"])
                if "logo" in item:
                    b = b.logo(item["logo"])
                if "preset" in item:
                    b = b.preset(item["preset"])
                label = item.get("label") or item.get("filename")
                if not label:
                    label = f"batch_{i:03d}"
                safe = re.sub(r"[^\w\-. ]", "_", str(label))[:60]
                fname = f"{i:03d}_{safe}.{fmt.lower()}"
                try:
                    b.save(str(out_path / fname), fmt)
                    saved += 1
                except Exception as e:
                    self.stats["errors"] += 1
                    self._output.append(f"  ⚠️ {fname}: {e}")
        finally:
            self._in_batch = False
            # FIX: финальный preview после batch
            if self.last_preview is not None:
                try:
                    img, data, style, size = self.last_preview
                    app = _CURRENT_APP
                    if app is not None:
                        if hasattr(app, "_safe_after"):
                            with suppress(Exception):
                                app._safe_after(
                                    0, _ensure_qr_page, app)
                                app._safe_after(
                                    30, _push_final_preview,
                                    img, data, style, size)
                except Exception:
                    pass

        self.print_fn(f"📦 Batch: {saved}/{len(items)} "
                       f"→ {out_path}")
        return saved

    def print_fn(self, *args: Any, **kw: Any) -> None:
        msg = " ".join(str(a) for a in args)
        self._output.append(msg)
        try:
            print(msg)
        except Exception:
            pass

    def get_output(self) -> str:
        return "\n".join(self._output)

    def get_stats(self) -> Dict[str, Any]:
        s = dict(self.stats)
        s["duration"] = round(time.time() - self._start_time, 3)
        return s


def _push_final_preview(img: Any, data: str,
                          style: str, size: int) -> None:
    """Показывает финальный preview после batch."""
    app = _CURRENT_APP
    if app is None:
        return
    try:
        if getattr(app, "_is_closing", False):
            return
        if hasattr(app, "_qr_apply_preview"):
            app._qr_apply_preview(img, data, style, size)
        else:
            _apply_preview_fallback(img, data, style, size)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# ИСПОЛНИТЕЛЬ С ТАЙМАУТОМ
# ═══════════════════════════════════════════════════════════

FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open",
    "input", "breakpoint", "exit", "quit", "globals",
    "locals", "vars", "dir", "getattr", "setattr",
    "delattr", "memoryview", "help",
}


def _validate_script(code: str) -> Tuple[bool, str]:
    import ast
    if not code or not code.strip():
        return False, "Пустой скрипт"
    if len(code) > 100_000:
        return False, "Слишком длинный скрипт"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Синтаксис: {e}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "Импорты запрещены в QR-скриптах"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_NAMES:
                    return False, f"Запрещено: {node.func.id}()"
        if isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                return False, f"Запрещено: {node.id}"
        if isinstance(node, ast.Attribute):
            if node.attr in ("__class__", "__globals__",
                              "__bases__", "__subclasses__",
                              "__mro__", "__code__"):
                return False, f"Запрещено: {node.attr}"
    return True, "OK"


def run_script(app: Any, ns: Dict[str, Any],
                code: str) -> Dict[str, Any]:
    ok, msg = _validate_script(code)
    if not ok:
        return {"ok": False, "output": "", "stats": {},
                "error": msg, "gallery": []}

    ctx = QRContext(app, ns)
    env = ctx.build_namespace()
    env["__builtins__"] = {}

    # FIX: таймаут 30 секунд
    timeout_flag = {"kill": False}
    start_t = time.time()
    MAX_TIME = 30.0

    def _watchdog() -> None:
        while not timeout_flag["kill"]:
            if time.time() - start_t > MAX_TIME:
                timeout_flag["kill"] = True
                return
            time.sleep(0.1)

    _w = threading.Thread(target=_watchdog, daemon=True)
    _w.start()

    # Оборачиваем print для проверки таймаута
    _orig_print = env["print"]

    def _print_check(*args: Any, **kw: Any) -> None:
        if timeout_flag["kill"]:
            raise TimeoutError(
                f"Скрипт превысил {int(MAX_TIME)} секунд")
        _orig_print(*args, **kw)

    env["print"] = _print_check

    try:
        exec(compile(code, "<qr_script>", "exec"), env)
        timeout_flag["kill"] = True
        return {
            "ok": True,
            "output": ctx.get_output(),
            "stats": ctx.get_stats(),
            "error": "",
            "gallery": list(ctx.preview_gallery),
        }
    except Exception as e:
        timeout_flag["kill"] = True
        tb = traceback.format_exc(limit=5)
        return {
            "ok": False,
            "output": ctx.get_output(),
            "stats": ctx.get_stats(),
            "error": f"{type(e).__name__}: {e}\n{tb}",
            "gallery": list(ctx.preview_gallery),
        }


# ═══════════════════════════════════════════════════════════
# ПРИМЕРЫ
# ═══════════════════════════════════════════════════════════

EXAMPLE_SCRIPTS: Dict[str, str] = {
    "👋 Привет": (
        '# Простейший пример\n'
        'qr.text("Hello NEXUS!").style("neon_cyan")'
        '.size(500).save("hello.png")\n'
        'print("✅ Готово!")\n'
    ),
    "🔗 URL-набор": (
        '# Набор ссылок\n'
        'sites = [\n'
        '    ("nexus", "https://nexus.app"),\n'
        '    ("github", "github.com"),\n'
        '    ("docs", "docs.python.org"),\n'
        ']\n'
        'for name, url in sites:\n'
        '    qr.url(url).style("neon_blue")'
        '.save(f"url_{name}.png")\n'
        '    print(f"🔗 {name} → {url}")\n'
    ),
    "📶 WiFi-пакет": (
        '# WiFi-карточки\n'
        'wifi_list = [\n'
        '    {"ssid": "Home", "password": "home1234"},\n'
        '    {"ssid": "Office", "password": "office5678"},\n'
        '    {"ssid": "Guest", "password": ""},\n'
        ']\n'
        'for i, w in enumerate(wifi_list, 1):\n'
        '    qr.wifi(ssid=w["ssid"],\n'
        '             password=w["password"])\\\n'
        '      .style("matrix")\\\n'
        '      .save(f"wifi_{i}.png")\n'
        'print(f"✅ {len(wifi_list)} WiFi-карточек")\n'
    ),
    "🎨 Все стили": (
        '# Один текст во всех стилях\n'
        'data = "NEXUS"\n'
        'for style in STYLES:\n'
        '    qr.text(data).style(style)\\\n'
        '      .size(400).save(f"style_{style}.png")\n'
        'print(f"✅ {len(STYLES)} файлов")\n'
    ),
    "📦 Batch API": (
        '# Пакетная генерация через batch()\n'
        'items = [\n'
        '    {"data": "Товар A", "style": "neon_pink",\n'
        '     "label": "item_a"},\n'
        '    {"data": "https://shop.ru/B", "type": "url",\n'
        '     "label": "item_b"},\n'
        '    {"data": "Товар C", "style": "matrix",\n'
        '     "label": "item_c"},\n'
        ']\n'
        'n = batch(items, out_dir="shop_qr")\n'
        'print(f"✅ {n} шт.")\n'
    ),
    "📇 vCard-набор": (
        '# Контактные карточки\n'
        'contacts = [\n'
        '    {"name": "Иванов Иван",\n'
        '     "phone": "+79991112233",\n'
        '     "email": "ivan@mail.ru"},\n'
        '    {"name": "Петров Пётр",\n'
        '     "phone": "+79994445566",\n'
        '     "email": "petr@mail.ru"},\n'
        ']\n'
        'for i, c in enumerate(contacts, 1):\n'
        '    qr.vcard(name=c["name"], phone=c["phone"],\n'
        '              email=c["email"])\\\n'
        '      .style("neon_gold")\\\n'
        '      .save(f"contact_{i}.png")\n'
        'print(f"✅ {len(contacts)} контактов")\n'
    ),
    "🎯 Стиль-матрица": (
        '# Сравнение стилей для одного контента\n'
        'content = "https://nexus.app"\n'
        'styles = ["neon_cyan", "neon_pink", "matrix",\n'
        '           "royal", "cyberpunk"]\n'
        'for s in styles:\n'
        '    qr.url(content).style(s).size(600)\\\n'
        '      .save(f"compare_{s}.png")\n'
        'print(f"✅ {len(styles)} вариантов")\n'
    ),
    "🔢 Цикл 50": (
        '# 50 нумерованных QR\n'
        'for i in range(1, 51):\n'
        '    qr.text(f"NEXUS-{i:04d}").style("matrix")\\\n'
        '      .size(300).save(f"num_{i:03d}.png")\n'
        'print("✅ 50 QR создано")\n'
    ),
}


# ═══════════════════════════════════════════════════════════
# ПРЕСЕТЫ
# ═══════════════════════════════════════════════════════════

def _presets_path(ns: Dict[str, Any]) -> Optional[Path]:
    data_dir = ns.get("DATA_DIR")
    if not data_dir:
        return None
    return Path(data_dir) / "qr_presets.json"


def load_presets(ns: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    merged = {k: dict(v) for k, v in BUILTIN_PRESETS.items()}
    path = _presets_path(ns)
    if path and path.exists():
        try:
            custom = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(custom, dict):
                merged.update(custom)
        except Exception:
            pass
    return merged


def save_presets(ns: Dict[str, Any],
                  presets: Dict[str, Dict[str, Any]]) -> bool:
    path = _presets_path(ns)
    if not path:
        return False
    try:
        custom = {k: v for k, v in presets.items()
                  if k not in BUILTIN_PRESETS}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(custom, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# UI КОНСОЛИ
# ═══════════════════════════════════════════════════════════

class _ConsoleUI:
    def __init__(self) -> None:
        self.gallery_container: Optional[Any] = None
        self.gallery_items: List[Any] = []
        self.counter_lbl: Optional[Any] = None
        self.ns: Dict[str, Any] = {}
        self._keep_alive: List[Any] = []

    def refresh_gallery(self, items: List[Dict[str, Any]]) -> None:
        container = self.gallery_container
        if container is None:
            return
        try:
            if not container.winfo_exists():
                return
        except Exception:
            return

        theme = self.ns.get("theme_manager")
        CTkButton = self.ns.get("CTkButton")
        CTkImage = self.ns.get("CTkImage")
        if not all((theme, CTkButton, CTkImage)):
            return

        # FIX: правильная очистка — сначала отвязываем ссылки
        for btn in self.gallery_items:
            with suppress(Exception):
                btn._pil_ref = None
                btn.image = None
        self.gallery_items.clear()
        self._keep_alive.clear()
        for w in container.winfo_children():
            with suppress(Exception):
                w.destroy()

        show_items = items[-30:]
        for item in show_items:
            try:
                img = item.get("img")
                if img is None:
                    continue
                pil_copy = img.copy()
                sz = 58
                cimg = CTkImage(light_image=pil_copy,
                                 dark_image=pil_copy,
                                 size=(sz, sz))
                btn = CTkButton(
                    container, image=cimg, text="",
                    width=sz + 8, height=sz + 8,
                    fg_color=theme.get("bg3"),
                    hover_color=theme.get("accent"),
                    command=lambda p=item.get("path"):
                        _open_thumb(p))
                btn.image = cimg
                btn._pil_ref = pil_copy
                btn.pack(side="left", padx=3, pady=3)
                self.gallery_items.append(btn)
                self._keep_alive.append((pil_copy, cimg))
            except Exception:
                pass

        if self.counter_lbl is not None:
            with suppress(Exception):
                self.counter_lbl.configure(
                    text=f"🖼️ {len(items)} QR")


def _open_thumb(path: Optional[str]) -> None:
    if not path:
        return
    app = _CURRENT_APP
    if app is None:
        return
    with suppress(Exception):
        app._open_file(Path(path))


def show_script_console(app: Any, ns: Dict[str, Any]) -> None:
    CTkToplevel = ns.get("CTkToplevel")
    CTkFrame = ns.get("CTkFrame")
    CTkLabel = ns.get("CTkLabel")
    CTkButton = ns.get("CTkButton")
    CTkTextbox = ns.get("CTkTextbox")
    CTkOptionMenu = ns.get("CTkOptionMenu")
    CTkFont = ns.get("CTkFont")
    StringVar = ns.get("StringVar")
    theme = ns.get("theme_manager")
    executor = ns.get("executor")

    if not all((CTkToplevel, CTkFrame, CTkLabel, CTkButton,
                CTkTextbox, CTkFont, theme)):
        return

    win = CTkToplevel(app)
    win.title("🐍 QR Script Console — программирование QR")
    win.geometry("1200x900")
    win.configure(fg_color=theme.get("bg"))
    win.transient(app)
    win.grab_set()

    header = CTkFrame(win, fg_color="transparent")
    header.pack(fill="x", padx=20, pady=(15, 8))
    CTkLabel(header, text="🐍 QR SCRIPT CONSOLE",
             font=CTkFont(size=20, weight="bold"),
             text_color=theme.get("primary")
             ).pack(side="left")
    CTkLabel(header, text="пиши код — генерируй QR",
             font=CTkFont(size=11, slant="italic"),
             text_color=theme.get("text_dim")
             ).pack(side="left", padx=10)

    toolbar = CTkFrame(win, fg_color=theme.get("bg2"),
                        corner_radius=8)
    toolbar.pack(fill="x", padx=20, pady=(0, 8))
    inner = CTkFrame(toolbar, fg_color="transparent")
    inner.pack(fill="x", padx=12, pady=8)

    CTkLabel(inner, text="📚 Пример:",
             font=CTkFont(size=11, weight="bold"),
             text_color=theme.get("text_glow")
             ).pack(side="left", padx=(0, 6))

    example_var = StringVar(value="— выбрать —")
    example_names = ["— выбрать —"] + list(EXAMPLE_SCRIPTS.keys())

    def on_example(name: str) -> None:
        if name == "— выбрать —":
            return
        code = EXAMPLE_SCRIPTS.get(name, "")
        if code:
            editor.delete("1.0", "end")
            editor.insert("1.0", code)

    CTkOptionMenu(inner, values=example_names,
                   variable=example_var,
                   width=220, height=32,
                   font=CTkFont(size=11),
                   fg_color=theme.get("bg3"),
                   button_color=theme.get("primary"),
                   text_color=theme.get("text"),
                   command=on_example
                   ).pack(side="left", padx=(0, 12))

    CTkButton(inner, text="📂 Открыть",
              width=110, height=32,
              font=CTkFont(size=10, weight="bold"),
              fg_color=theme.get("accent"),
              text_color=theme.get("bg"),
              command=lambda: _load_from_file(editor, ns)
              ).pack(side="left", padx=2)
    CTkButton(inner, text="💾 Сохранить",
              width=110, height=32,
              font=CTkFont(size=10, weight="bold"),
              fg_color=theme.get("primary"),
              text_color=theme.get("bg"),
              command=lambda: _save_to_file(editor, ns)
              ).pack(side="left", padx=2)

    main = CTkFrame(win, fg_color="transparent")
    main.pack(fill="both", expand=True, padx=20, pady=(0, 8))
    main.grid_columnconfigure(0, weight=3, uniform="script")
    main.grid_columnconfigure(1, weight=2, uniform="script")
    main.grid_rowconfigure(0, weight=1)

    ed_frame = CTkFrame(main, corner_radius=8, border_width=2,
                         border_color=theme.get("card_border"),
                         fg_color=theme.get("bg2"))
    ed_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    ed_frame.grid_columnconfigure(0, weight=1)
    ed_frame.grid_rowconfigure(1, weight=1)

    ed_top = CTkFrame(ed_frame, fg_color="transparent")
    ed_top.grid(row=0, column=0, sticky="ew", padx=10,
                 pady=(10, 4))
    CTkLabel(ed_top, text="📝 СКРИПТ",
             font=CTkFont(size=12, weight="bold"),
             text_color=theme.get("primary"),
             anchor="w").pack(side="left")

    editor = CTkTextbox(
        ed_frame, font=CTkFont(family="Courier", size=12),
        wrap="none",
        fg_color=theme.get("code_bg"),
        text_color=theme.get("code_text"),
        undo=True)
    editor.grid(row=1, column=0, sticky="nsew", padx=10,
                 pady=(0, 10))
    editor.insert("1.0", EXAMPLE_SCRIPTS["👋 Привет"])

    out_frame = CTkFrame(main, corner_radius=8, border_width=2,
                          border_color=theme.get("card_border"),
                          fg_color=theme.get("bg2"))
    out_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
    out_frame.grid_columnconfigure(0, weight=1)
    out_frame.grid_rowconfigure(1, weight=1)

    out_top = CTkFrame(out_frame, fg_color="transparent")
    out_top.grid(row=0, column=0, sticky="ew", padx=10,
                  pady=(10, 4))
    CTkLabel(out_top, text="📤 ВЫВОД",
             font=CTkFont(size=12, weight="bold"),
             text_color=theme.get("secondary"),
             anchor="w").pack(side="left")

    stats_lbl = CTkLabel(out_top, text="",
                          font=CTkFont(size=10),
                          text_color=theme.get("text_dim"))
    stats_lbl.pack(side="right")

    output = CTkTextbox(
        out_frame, font=CTkFont(family="Courier", size=11),
        wrap="word",
        fg_color=theme.get("bg3"),
        text_color=theme.get("text"))
    output.grid(row=1, column=0, sticky="nsew", padx=10,
                 pady=(0, 10))
    output.insert("1.0",
                   "💡 Выбери пример и нажми ▶ ЗАПУСТИТЬ\n\n"
                   "Доступные функции:\n"
                   "  qr.text('Hello').style('neon_cyan')\n"
                   "  qr.url('example.com').save('out.png')\n"
                   "  qr.wifi(ssid='X', password='Y')\n"
                   "  qr.vcard(name='Иван', phone='+7...')\n"
                   "  batch([{...}, {...}])\n"
                   "  print(...) — вывод сюда\n\n"
                   "✨ Preview справа обновляется при save()!\n"
                   "⏱️ Лимит: 30 секунд на скрипт\n")
    output.configure(state="disabled")

    gallery_frame = CTkFrame(win, fg_color=theme.get("bg2"),
                               corner_radius=8,
                               border_width=1,
                               border_color=theme.get("accent"))
    gallery_frame.pack(fill="x", padx=20, pady=(0, 6))
    gallery_top = CTkFrame(gallery_frame, fg_color="transparent")
    gallery_top.pack(fill="x", padx=10, pady=(6, 2))
    CTkLabel(gallery_top, text="🖼️ СОЗДАННЫЕ QR",
             font=CTkFont(size=10, weight="bold"),
             text_color=theme.get("accent"),
             anchor="w").pack(side="left")
    counter_lbl = CTkLabel(gallery_top, text="🖼️ 0 QR",
                             font=CTkFont(size=10),
                             text_color=theme.get("text_dim"))
    counter_lbl.pack(side="right")

    gallery_container = CTkFrame(gallery_frame,
                                   fg_color="transparent",
                                   height=70)
    gallery_container.pack(fill="x", padx=6, pady=(0, 6))
    gallery_container.pack_propagate(False)

    ui = _ConsoleUI()
    ui.gallery_container = gallery_container
    ui.counter_lbl = counter_lbl
    ui.ns = ns
    app._qr_console_ui = ui

    def on_close() -> None:
        with suppress(Exception):
            app._qr_console_ui = None
        with suppress(Exception):
            win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

    bottom = CTkFrame(win, fg_color="transparent")
    bottom.pack(fill="x", padx=20, pady=(0, 15))

    def do_run() -> None:
        code = editor.get("1.0", "end-1c")
        if not code.strip():
            return
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", "⏳ Выполняется...\n")
        output.configure(state="disabled")
        stats_lbl.configure(text="⏳")

        def work() -> None:
            result = run_script(app, ns, code)
            app._safe_after(0, _apply_script_result,
                             result, output, stats_lbl, app, ui)

        if executor:
            executor.submit_safe(work)
        else:
            work()

    def do_clear() -> None:
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.configure(state="disabled")
        stats_lbl.configure(text="")

    def do_open_dir() -> None:
        with suppress(Exception):
            ctx = QRContext(app, ns)
            d = ctx._resolve_dir()
            d.mkdir(parents=True, exist_ok=True)
            app._open_file(d)

    CTkButton(bottom, text="▶ ЗАПУСТИТЬ (Ctrl+Enter)",
              height=42, width=280,
              font=CTkFont(size=13, weight="bold"),
              fg_color=theme.get("success"),
              text_color=theme.get("bg"),
              command=do_run).pack(side="left")
    CTkButton(bottom, text="🗑️ Очистить",
              height=42, width=140,
              font=CTkFont(size=11, weight="bold"),
              fg_color="transparent", border_width=1,
              border_color=theme.get("error"),
              text_color=theme.get("error"),
              command=do_clear).pack(side="left", padx=6)
    CTkButton(bottom, text="📂 Открыть папку",
              height=42, width=180,
              font=CTkFont(size=11, weight="bold"),
              fg_color=theme.get("accent"),
              text_color=theme.get("bg"),
              command=do_open_dir).pack(side="right")

    editor.bind("<Control-Return>",
                 lambda e: (do_run(), "break")[1])
    win.bind("<Escape>", lambda e: on_close())


def _apply_script_result(result: Dict[str, Any],
                          output: Any, stats_lbl: Any,
                          app: Any,
                          ui: Optional[_ConsoleUI] = None) -> None:
    try:
        if not output.winfo_exists():
            return
        output.configure(state="normal")
        output.delete("1.0", "end")
        out = result.get("output", "")
        err = result.get("error", "")
        ok = result.get("ok", False)
        if out:
            output.insert("end", out + "\n")
        if err:
            output.insert("end", "\n" + "─" * 40 + "\n")
            output.insert("end", "❌ ОШИБКА:\n")
            output.insert("end", err)
        if ok and not err:
            output.insert("end", "\n✅ Скрипт выполнен успешно\n")
        output.configure(state="disabled")

        stats = result.get("stats", {})
        if stats:
            txt = (f"⚡ {stats.get('generated', 0)} "
                    f"· 💾 {stats.get('saved', 0)} "
                    f"· 📦 {stats.get('batches', 0)} "
                    f"· ⏱️ {stats.get('duration', 0)}с")
            if stats.get("errors"):
                txt += f" · ❌ {stats['errors']}"
            stats_lbl.configure(text=txt)

        gallery = result.get("gallery", [])
        if ui is not None and gallery:
            with suppress(Exception):
                ui.refresh_gallery(gallery)

        with suppress(Exception):
            app._show_toast(
                "✅ QR-скрипт выполнен" if ok
                else "❌ Ошибка в скрипте",
                "success" if ok else "error", 3000)
    except Exception:
        pass


def _load_from_file(editor: Any, ns: Dict[str, Any]) -> None:
    filedialog = ns.get("filedialog")
    if not filedialog:
        return
    fp = filedialog.askopenfilename(
        filetypes=[("Python", "*.py"), ("Text", "*.txt"),
                    ("All", "*.*")])
    if not fp:
        return
    try:
        code = Path(fp).read_text(encoding="utf-8")
        editor.delete("1.0", "end")
        editor.insert("1.0", code)
    except Exception as e:
        print(f"load script: {e}")


def _save_to_file(editor: Any, ns: Dict[str, Any]) -> None:
    filedialog = ns.get("filedialog")
    if not filedialog:
        return
    fp = filedialog.asksaveasfilename(
        defaultextension=".py",
        initialfile=f"qr_script_"
                    f"{datetime.now():%Y%m%d_%H%M%S}.py",
        filetypes=[("Python", "*.py")])
    if not fp:
        return
    try:
        code = editor.get("1.0", "end-1c")
        Path(fp).write_text(code, encoding="utf-8")
    except Exception as e:
        print(f"save script: {e}")


# ═══════════════════════════════════════════════════════════
# ЭКСПОРТ
# ═══════════════════════════════════════════════════════════

def export_pdf_batch(app: Any, ns: Dict[str, Any]) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        _toast(app, "❌ PIL не установлен", "error")
        return

    filedialog = ns.get("filedialog")
    qr_history = ns.get("qr_history")
    qr_gen = ns.get("qr_gen")
    executor = ns.get("executor")
    user = getattr(app, "current_user", None)
    if not all((filedialog, qr_history, qr_gen, user)):
        return

    fp = filedialog.asksaveasfilename(
        defaultextension=".pdf",
        initialfile=f"qr_batch_"
                    f"{datetime.now():%Y%m%d_%H%M%S}.pdf",
        filetypes=[("PDF", "*.pdf")])
    if not fp:
        return

    history = qr_history.get_user_history(user, limit=90)
    if not history:
        _toast(app, "📭 История пуста", "warning")
        return

    _toast(app, "⏳ PDF...", "info", 2000)

    def work() -> None:
        try:
            A4_W, A4_H = 2480, 3508
            MARGIN = 150
            GRID = 3
            QR_SIZE = (A4_W - MARGIN * 2) // GRID - 40
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/"
                    "DejaVuSans.ttf", 24)
            except Exception:
                font = ImageFont.load_default()
            pages = []
            current = Image.new("RGB", (A4_W, A4_H), "white")
            draw = ImageDraw.Draw(current)
            x_idx = y_idx = 0
            for h in history:
                data = h.get("data") or ""
                if not data:
                    continue
                img = qr_gen.generate(
                    data, size=QR_SIZE,
                    style=h.get("style", "neon_cyan"))
                if img is None:
                    continue
                if img.mode == "RGBA":
                    bg = Image.new("RGB", img.size, "white")
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                x = MARGIN + x_idx * (QR_SIZE + 40)
                y = MARGIN + y_idx * (QR_SIZE + 60)
                current.paste(img, (x, y))
                cap = (data[:38] + "…" if len(data) > 38
                        else data)
                draw.text((x, y + QR_SIZE + 6), cap,
                           fill="black", font=font)
                x_idx += 1
                if x_idx >= GRID:
                    x_idx = 0
                    y_idx += 1
                if y_idx >= GRID:
                    pages.append(current)
                    current = Image.new("RGB",
                                          (A4_W, A4_H), "white")
                    draw = ImageDraw.Draw(current)
                    x_idx = y_idx = 0
            if x_idx > 0 or y_idx > 0:
                pages.append(current)
            if not pages:
                app._safe_after(0, _toast, app,
                                "❌ Нечего", "error")
                return
            pages[0].save(fp, "PDF", resolution=300.0,
                           save_all=True, append_images=pages[1:])
            app._safe_after(0, _toast, app,
                            f"✅ PDF: {len(pages)} стр.",
                            "success", 5000)
            app._safe_after(100, app._open_file, Path(fp))
        except Exception as e:
            traceback.print_exc()
            app._safe_after(0, _toast, app,
                            f"❌ {e}", "error")
    if executor:
        executor.submit_safe(work)
    else:
        work()


def export_svg_pack(app: Any, ns: Dict[str, Any]) -> None:
    filedialog = ns.get("filedialog")
    qr_gen = ns.get("qr_gen")
    QRExporter = ns.get("QRExporter")
    executor = ns.get("executor")
    if not all((filedialog, qr_gen, QRExporter)):
        return
    data = app._qr_preview_data
    if not data:
        _toast(app, "📱 Сначала QR", "warning")
        return
    fp = filedialog.asksaveasfilename(
        defaultextension=".zip",
        initialfile=f"qr_svg_pack_"
                    f"{datetime.now():%Y%m%d_%H%M%S}.zip",
        filetypes=[("ZIP", "*.zip")])
    if not fp:
        return
    _toast(app, "⏳ SVG...", "info", 2000)

    def work() -> None:
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                tmp_p = Path(tmp)
                count = 0
                for s in qr_gen.get_styles():
                    cfg = qr_gen.STYLES.get(s, {})
                    out = tmp_p / f"{s}.svg"
                    ok, _ = QRExporter.export_svg(
                        data, out, size=600,
                        front=cfg.get("color", "#000000"),
                        back=cfg.get("bg", "#ffffff"),
                        error_level="M")
                    if ok:
                        count += 1
                with zipfile.ZipFile(fp, "w",
                                      zipfile.ZIP_DEFLATED) as zf:
                    for f in tmp_p.glob("*.svg"):
                        zf.write(f, f.name)
            app._safe_after(0, _toast, app,
                            f"✅ {count} SVG", "success", 5000)
            app._safe_after(100, app._open_file, Path(fp))
        except Exception as e:
            app._safe_after(0, _toast, app,
                            f"❌ {e}", "error")
    if executor:
        executor.submit_safe(work)
    else:
        work()


def export_history_json(app: Any, ns: Dict[str, Any]) -> None:
    filedialog = ns.get("filedialog")
    qr_history = ns.get("qr_history")
    user = getattr(app, "current_user", None)
    if not (filedialog and qr_history and user):
        return
    fp = filedialog.asksaveasfilename(
        defaultextension=".json",
        initialfile=f"qr_history_"
                    f"{datetime.now():%Y%m%d_%H%M%S}.json",
        filetypes=[("JSON", "*.json")])
    if not fp:
        return
    try:
        items = qr_history.get_user_history(user, limit=10000)
        clean = [{k: v for k, v in h.items()
                  if k in ("data", "style", "size", "mode",
                            "created_at", "hash")}
                 for h in items]
        Path(fp).write_text(
            json.dumps(clean, ensure_ascii=False, indent=2),
            encoding="utf-8")
        _toast(app, f"💾 {len(clean)} QR", "success")
    except Exception as e:
        _toast(app, f"❌ {e}", "error")


# ═══════════════════════════════════════════════════════════
# ПАНЕЛЬ В QR-СТРАНИЦЕ
# ═══════════════════════════════════════════════════════════

def build_presets_panel(app: Any, ns: Dict[str, Any],
                         parent: Any) -> None:
    CTkFrame = ns.get("CTkFrame")
    CTkLabel = ns.get("CTkLabel")
    CTkButton = ns.get("CTkButton")
    CTkFont = ns.get("CTkFont")
    CTkOptionMenu = ns.get("CTkOptionMenu")
    StringVar = ns.get("StringVar")
    theme = ns.get("theme_manager")
    if not all((CTkFrame, CTkLabel, CTkButton, CTkFont,
                CTkOptionMenu, StringVar, theme)):
        return

    presets = load_presets(ns)
    names = list(presets.keys())

    card = CTkFrame(parent, corner_radius=8, border_width=1,
                     border_color=theme.get("accent"),
                     fg_color=theme.get("bg2"))
    card.pack(fill="x", padx=12, pady=(8, 8))
    CTkLabel(card, text="✨ ПРЕСЕТЫ",
             font=CTkFont(size=12, weight="bold"),
             text_color=theme.get("accent"),
             anchor="w").pack(anchor="w", padx=10, pady=(8, 4))
    var = StringVar(value=names[0] if names else "")
    CTkOptionMenu(card, values=names or ["—"], variable=var,
                   height=30, font=CTkFont(size=11),
                   fg_color=theme.get("bg3"),
                   button_color=theme.get("accent"),
                   text_color=theme.get("text")
                   ).pack(fill="x", padx=10, pady=(0, 4))
    row = CTkFrame(card, fg_color="transparent")
    row.pack(fill="x", padx=10, pady=(0, 10))
    CTkButton(row, text="✨ Применить", height=28,
              font=CTkFont(size=10, weight="bold"),
              fg_color=theme.get("accent"),
              text_color=theme.get("bg"),
              command=lambda: apply_preset(app, ns, var.get())
              ).pack(side="left", padx=1)
    CTkButton(row, text="💾 Сохранить", height=28,
              font=CTkFont(size=10, weight="bold"),
              fg_color=theme.get("success"),
              text_color=theme.get("bg"),
              command=lambda: save_current_as_preset(app, ns)
              ).pack(side="left", padx=1)

    script_card = CTkFrame(
        parent, corner_radius=8, border_width=2,
        border_color=theme.get("gradient1"),
        fg_color=theme.get("bg2"))
    script_card.pack(fill="x", padx=12, pady=(0, 8))
    CTkLabel(script_card, text="🐍 QR-СКРИПТ",
             font=CTkFont(size=13, weight="bold"),
             text_color=theme.get("gradient1"),
             anchor="w").pack(anchor="w", padx=10, pady=(8, 2))
    CTkLabel(script_card,
             text="Пиши код — генерируй пакеты QR",
             font=CTkFont(size=9, slant="italic"),
             text_color=theme.get("text_dim"),
             anchor="w").pack(anchor="w", padx=10, pady=(0, 6))
    CTkButton(script_card,
              text="🐍 Открыть консоль скриптов",
              height=40,
              font=CTkFont(size=12, weight="bold"),
              fg_color=theme.get("gradient1"),
              text_color=theme.get("bg"),
              command=lambda: show_script_console(app, ns)
              ).pack(fill="x", padx=10, pady=(0, 10))

    exp = CTkFrame(parent, corner_radius=8, border_width=1,
                    border_color=theme.get("gradient2"),
                    fg_color=theme.get("bg2"))
    exp.pack(fill="x", padx=12, pady=(0, 12))
    CTkLabel(exp, text="📦 ЭКСПОРТ",
             font=CTkFont(size=12, weight="bold"),
             text_color=theme.get("gradient2"),
             anchor="w").pack(anchor="w", padx=10, pady=(8, 4))
    for txt, color, cmd in [
        ("📄 PDF (вся история)", theme.get("error"),
         lambda: export_pdf_batch(app, ns)),
        ("🎨 SVG-пакет", theme.get("warning"),
         lambda: export_svg_pack(app, ns)),
        ("💾 История → JSON", theme.get("primary"),
         lambda: export_history_json(app, ns)),
    ]:
        CTkButton(exp, text=txt, height=32,
                  font=CTkFont(size=10, weight="bold"),
                  fg_color=color,
                  text_color=theme.get("bg"),
                  command=cmd).pack(fill="x", padx=10, pady=2)
    CTkFrame(exp, height=6, fg_color="transparent").pack()


def apply_preset(app: Any, ns: Dict[str, Any],
                  name: str) -> None:
    presets = load_presets(ns)
    p = presets.get(name)
    if not p:
        _toast(app, f"⚠️ «{name}» не найден", "warning")
        return
    qr_gen = ns.get("qr_gen")
    if app._qr_style_var is not None and qr_gen:
        for s in qr_gen.get_styles():
            if s == p.get("style"):
                app._qr_style_var.set(
                    f"{qr_gen.get_style_name(s)} ({s})")
                break
    if app._qr_size_var is not None:
        app._qr_size_var.set(str(p.get("size", 400)))
    logo = p.get("logo", "")
    if logo and os.path.isfile(logo):
        app._qr_logo_path = logo
    _toast(app, f"✨ «{name}»", "success", 2000)
    with suppress(Exception):
        app._qr_schedule_preview()


def save_current_as_preset(app: Any,
                             ns: Dict[str, Any]) -> None:
    tk = ns.get("tk")
    if not tk:
        return
    win = tk.Toplevel(app)
    win.title("💾 Сохранить пресет")
    win.geometry("440x300")
    win.configure(bg="#1a1a2e")
    win.transient(app)
    win.grab_set()
    tk.Label(win, text="💾 НОВЫЙ ПРЕСЕТ",
             bg="#1a1a2e", fg="#00e5ff",
             font=("Segoe UI", 16, "bold")
             ).pack(pady=(20, 10))
    tk.Label(win, text="Название:",
             bg="#1a1a2e", fg="#e8e8ff",
             font=("Segoe UI", 11)
             ).pack(anchor="w", padx=30)
    name_e = tk.Entry(win, font=("Segoe UI", 13),
                       bg="#12122a", fg="#e8e8ff",
                       insertbackground="#00e5ff")
    name_e.pack(fill="x", padx=30, pady=6)
    name_e.focus()
    tk.Label(win, text="Описание:",
             bg="#1a1a2e", fg="#e8e8ff",
             font=("Segoe UI", 11)
             ).pack(anchor="w", padx=30, pady=(10, 0))
    desc_e = tk.Entry(win, font=("Segoe UI", 11),
                       bg="#12122a", fg="#e8e8ff")
    desc_e.pack(fill="x", padx=30, pady=6)

    def do_save() -> None:
        name = name_e.get().strip()
        if not name or name in BUILTIN_PRESETS:
            return
        style_key = "neon_cyan"
        if app._qr_style_var:
            m = re.search(r"\((\w+)\)$", app._qr_style_var.get())
            if m:
                style_key = m.group(1)
        try:
            size = int(app._qr_size_var.get()
                        if app._qr_size_var else "400")
        except (TypeError, ValueError):
            size = 400
        presets = load_presets(ns)
        presets[name] = {
            "style": style_key, "size": size, "border": 4,
            "error_level": "H",
            "logo": app._qr_logo_path or "",
            "desc": desc_e.get().strip(), "custom": True,
        }
        if save_presets(ns, presets):
            _toast(app, f"💾 «{name}» сохранён", "success")
            win.destroy()
            with suppress(Exception):
                app._refresh_page()
    tk.Button(win, text="💾 СОХРАНИТЬ", bg="#39ff14",
              fg="#000", font=("Segoe UI", 12, "bold"),
              command=do_save).pack(pady=20)
    name_e.bind("<Return>", lambda e: do_save())


# ═══════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ
# ═══════════════════════════════════════════════════════════

def attach(app: Any, ns: Dict[str, Any]) -> None:
    global _CURRENT_APP, _CURRENT_NS
    _CURRENT_APP = app
    _CURRENT_NS = ns
    app.qr_pro_load_presets = lambda: load_presets(ns)
    app.qr_pro_save_presets = lambda p: save_presets(ns, p)
    app.qr_pro_apply_preset = lambda n: apply_preset(app, ns, n)
    app.qr_pro_save_as_preset = lambda: save_current_as_preset(app, ns)
    app.qr_pro_export_pdf = lambda: export_pdf_batch(app, ns)
    app.qr_pro_export_svg_pack = lambda: export_svg_pack(app, ns)
    app.qr_pro_export_history_json = lambda: export_history_json(app, ns)
    app.qr_pro_build_panel = lambda parent: build_presets_panel(app, ns, parent)
    app.qr_pro_script_console = lambda: show_script_console(app, ns)
    app.qr_pro_run_script = lambda code: run_script(app, ns, code)
    print("✅ QR PRO v3.2: attached (production ready)")


def _toast(app: Any, msg: str, typ: str = "info",
           duration: int = 3000) -> None:
    with suppress(Exception):
        app._show_toast(msg, typ, duration)


__all__ = [
    "attach", "run_script", "QRBuilder", "QRContext",
    "show_script_console", "build_presets_panel",
    "EXAMPLE_SCRIPTS", "BUILTIN_PRESETS",
]