#!/usr/bin/env python3
# ============================================================
# NEXUS CODE PRO — самостоятельный модуль улучшений Code Studio
# ============================================================
# Использование в nexus.py:
#   import nexus_code_pro
#   nexus_code_pro.attach(app, globals())
# ============================================================

from __future__ import annotations

import re
import traceback
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


# ═══════════════════════════════════════════════════════════
# ПУБЛИЧНЫЙ API
# ═══════════════════════════════════════════════════════════

def attach(app: Any, ns: Dict[str, Any]) -> None:
    """
    Навешивает на app методы `code_pro_*` и биндит хоткеи
    на существующий _code_widget (если он есть).
    """
    app.code_pro_bind_widget = lambda: bind_widget(app, ns)
    app.code_pro_snippet_palette = lambda: show_snippet_palette(app, ns)
    app.code_pro_toggle_fullscreen = lambda: toggle_fullscreen(app)
    app.code_pro_run_selection = lambda: run_selection(app, ns)
    app.code_pro_insert_imports = lambda: insert_imports(app)
    app.code_pro_export_markdown = lambda: export_markdown(app, ns)
    app.code_pro_build_toolbar = lambda parent: build_toolbar(
        app, ns, parent)

    # Если виджет уже есть — биндим сразу
    with suppress(Exception):
        bind_widget(app, ns)
    print("✅ CODE PRO: attached")


# ──────────────────────────────────────────────────────────
# БИНДЫ НА WIDGET
# ──────────────────────────────────────────────────────────

def bind_widget(app: Any, ns: Dict[str, Any]) -> None:
    """Навешивает клавиатурные бинды на app._code_widget."""
    w = getattr(app, "_code_widget", None)
    if w is None:
        return
    try:
        if not w.winfo_exists():
            return
    except Exception:
        return

    # Auto-indent + auto-brackets
    w.bind("<KeyRelease>",
           lambda e: _on_keyrelease(app, e),
           add="+")

    # Tab → 4 пробела
    w.bind("<Tab>",
           lambda e: _on_tab(app, e),
           add="+")
    w.bind("<Shift-Tab>",
           lambda e: _on_shift_tab(app, e),
           add="+")
    w.bind("<ISO_Left_Tab>",
           lambda e: _on_shift_tab(app, e),
           add="+")

    # Enter → auto-indent
    w.bind("<Return>",
           lambda e: _on_return(app, e),
           add="+")

    # Ctrl+Space — палитра сниппетов
    w.bind("<Control-space>",
           lambda e: (show_snippet_palette(app, ns), "break")[1],
           add="+")
    w.bind("<Control-KP_Space>",
           lambda e: (show_snippet_palette(app, ns), "break")[1],
           add="+")

    # F11 — полный экран
    w.bind("<F11>",
           lambda e: (toggle_fullscreen(app), "break")[1],
           add="+")

    # Ctrl+Shift+Enter — запуск выделения
    w.bind("<Control-Shift-Return>",
           lambda e: (run_selection(app, ns), "break")[1],
           add="+")

    # Ctrl+Shift+I — вставить импорты
    w.bind("<Control-Shift-I>",
           lambda e: (insert_imports(app), "break")[1],
           add="+")

    # Ctrl+M — markdown-отчёт
    w.bind("<Control-m>",
           lambda e: (export_markdown(app, ns), "break")[1],
           add="+")

    print("✅ CODE PRO: widget bound")


# ──────────────────────────────────────────────────────────
# AUTO-INDENT / AUTO-BRACKETS
# ──────────────────────────────────────────────────────────

def _on_tab(app: Any, event: Any) -> str:
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return "break"
    try:
        sel = w.tag_ranges("sel")
        if sel:
            first = int(w.index(f"{sel[0]} linestart").split(".")[0])
            last = int(w.index(f"{sel[1]} lineend").split(".")[0])
            for ln in range(first, last + 1):
                w.insert(f"{ln}.0", "    ")
        else:
            w.insert("insert", "    ")
    except Exception:
        pass
    return "break"


def _on_shift_tab(app: Any, event: Any) -> str:
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return "break"
    try:
        sel = w.tag_ranges("sel")
        if sel:
            first = int(w.index(f"{sel[0]} linestart").split(".")[0])
            last = int(w.index(f"{sel[1]} lineend").split(".")[0])
            for ln in range(first, last + 1):
                line_start = f"{ln}.0"
                chunk = w.get(line_start, f"{ln}.4")
                if chunk == "    ":
                    w.delete(line_start, f"{ln}.4")
                elif chunk.startswith(" "):
                    stripped = len(chunk) - len(chunk.lstrip(" "))
                    w.delete(line_start, f"{ln}.{stripped}")
        else:
            ln = int(w.index("insert").split(".")[0])
            line_start = f"{ln}.0"
            chunk = w.get(line_start, f"{ln}.4")
            if chunk == "    ":
                w.delete(line_start, f"{ln}.4")
            elif chunk.startswith(" "):
                stripped = len(chunk) - len(chunk.lstrip(" "))
                w.delete(line_start, f"{ln}.{stripped}")
    except Exception:
        pass
    return "break"


def _on_return(app: Any, event: Any) -> str:
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return ""
    try:
        cursor = w.index("insert")
        ln = int(cursor.split(".")[0])
        line = w.get(f"{ln}.0", cursor)
        indent = re.match(r"^([ \t]*)", line).group(1)
        if line.rstrip().endswith(":"):
            indent += "    "
        if indent:
            w.insert("insert", "\n" + indent)
            return "break"
    except Exception:
        pass
    return ""


def _on_keyrelease(app: Any, event: Any) -> str:
    """Auto-brackets."""
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return ""
    keysym = event.keysym
    pairs = {
        "parenleft": ("(", ")"),
        "bracketleft": ("[", "]"),
        "braceleft": ("{", "}"),
        "quotedbl": ('"', '"'),
        "apostrophe": ("'", "'"),
    }
    closing = {
        "parenright": ")",
        "bracketright": "]",
        "braceright": "}",
    }
    try:
        if keysym in pairs:
            l, r = pairs[keysym]
            sel = w.tag_ranges("sel")
            if sel:
                selected = w.get(sel[0], sel[1])
                w.delete(sel[0], sel[1])
                w.insert(sel[0], l + selected + r)
                return "break"
            w.insert("insert", l + r)
            w.mark_set("insert", "insert-1c")
            return "break"
        if keysym in closing:
            ch = closing[keysym]
            nxt = w.get("insert", "insert+1c")
            if nxt == ch:
                w.mark_set("insert", "insert+1c")
                return "break"
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────────────────
# ПАЛИТРА СНИППЕТОВ
# ──────────────────────────────────────────────────────────

def show_snippet_palette(app: Any, ns: Dict[str, Any]) -> None:
    tk = ns.get("tk")
    snippet_manager = ns.get("snippet_manager")
    user = getattr(app, "current_user", None)
    if not (tk and snippet_manager and user):
        return

    snippets = snippet_manager.get_all(user)
    if not snippets:
        _toast(app, "✂️ Нет сниппетов", "info", 2000)
        return

    win = tk.Toplevel(app)
    win.title("✂️ Сниппеты")
    win.geometry("640x420")
    win.configure(bg="#1a1a2e")
    win.transient(app)
    win.grab_set()

    tk.Label(win, text="✂️ ПАЛИТРА СНИППЕТОВ",
             bg="#1a1a2e", fg="#00e5ff",
             font=("Segoe UI", 14, "bold")).pack(pady=(15, 5))

    search = tk.Entry(win, font=("Segoe UI", 13),
                       bg="#12122a", fg="#e8e8ff",
                       insertbackground="#00e5ff")
    search.pack(fill="x", padx=20, pady=8)
    search.focus()

    lst = tk.Listbox(win, font=("Courier", 11),
                      bg="#12122a", fg="#e8e8ff",
                      selectbackground="#00e5ff",
                      selectforeground="#000",
                      activestyle="none")
    lst.pack(fill="both", expand=True, padx=20, pady=(0, 15))

    def render(query: str = "") -> None:
        lst.delete(0, "end")
        q = query.lower().strip()
        for s in snippets:
            name = s.get("name", "?")
            code = s.get("code", "")
            if q and q not in name.lower() and q not in code.lower():
                continue
            lst.insert("end",
                        f"✂️ {name}  [{s.get('language', 'py')}]")
        if lst.size() == 0:
            lst.insert("end", "😕 Ничего")

    def insert_sel(e: Any = None) -> None:
        sel = lst.curselection()
        if not sel:
            return
        text = lst.get(sel[0])
        if not text.startswith("✂️"):
            return
        name = text[2:].split("  [")[0].strip()
        for s in snippets:
            if s.get("name") == name:
                w = app._code_widget
                if w and w.winfo_exists():
                    w.insert("insert", s.get("code", ""))
                    _toast(app, f"✂️ {name}", "success", 1500)
                win.destroy()
                return

    search.bind("<KeyRelease>",
                 lambda e: render(search.get()))
    search.bind("<Return>", insert_sel)
    lst.bind("<Double-Button-1>", insert_sel)
    win.bind("<Escape>", lambda e: win.destroy())
    render()


# ──────────────────────────────────────────────────────────
# ПОЛНЫЙ ЭКРАН
# ──────────────────────────────────────────────────────────

def toggle_fullscreen(app: Any) -> None:
    try:
        cur = app.attributes("-fullscreen")
        app.attributes("-fullscreen", not cur)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# ЗАПУСК ВЫДЕЛЕНИЯ
# ──────────────────────────────────────────────────────────

def run_selection(app: Any, ns: Dict[str, Any]) -> None:
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return
    try:
        sel = w.tag_ranges("sel")
        if not sel:
            _toast(app, "Выделите код (или Ctrl+Enter для всего)",
                   "info", 2000)
            return
        code = w.get(sel[0], sel[1])
        original = w.get("1.0", "end-1c")
    except Exception:
        return
    if not code.strip():
        return

    # Подменяем, запускаем, восстанавливаем
    try:
        w.delete("1.0", "end")
        w.insert("1.0", code)
    except Exception:
        return

    with suppress(Exception):
        app._run_code_current()

    def restore() -> None:
        try:
            w2 = app._code_widget
            if w2 and w2.winfo_exists():
                w2.delete("1.0", "end")
                w2.insert("1.0", original)
        except Exception:
            pass

    with suppress(Exception):
        app._safe_after(1500, restore)


# ──────────────────────────────────────────────────────────
# ВСТАВИТЬ ИМПОРТЫ
# ──────────────────────────────────────────────────────────

def insert_imports(app: Any) -> None:
    w = app._code_widget
    if w is None or not w.winfo_exists():
        return
    snippet = ("import sys\n"
                "import math\n"
                "import random\n"
                "from datetime import datetime\n\n")
    try:
        content = w.get("1.0", "end-1c")
        lines = content.splitlines()
        insert_line = 1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                insert_line = i + 2
            elif stripped and not stripped.startswith("#"):
                break
        w.insert(f"{insert_line}.0", snippet)
        _toast(app, "📥 Импорты вставлены", "success", 1500)
    except Exception as e:
        _toast(app, f"❌ {e}", "error")


# ──────────────────────────────────────────────────────────
# MARKDOWN-ОТЧЁТ
# ──────────────────────────────────────────────────────────

def export_markdown(app: Any, ns: Dict[str, Any]) -> None:
    filedialog = ns.get("filedialog")
    if not filedialog:
        return
    w = app._code_widget
    out = app._code_out
    if w is None or out is None:
        _toast(app, "❌ Нет активного кода", "warning")
        return
    try:
        code = w.get("1.0", "end-1c")
        output = out.get("1.0", "end-1c")
    except Exception:
        return
    if not code.strip():
        _toast(app, "❌ Пустой код", "warning")
        return

    fp = filedialog.asksaveasfilename(
        defaultextension=".md",
        initialfile=f"code_report_"
                    f"{datetime.now():%Y%m%d_%H%M%S}.md",
        filetypes=[("Markdown", "*.md")])
    if not fp:
        return

    lang = "python"
    if getattr(app, "_code_lang_var", None):
        lang = app._code_lang_var.get() or "python"

    version = ns.get("VERSION", "?")
    md = (
        f"# 💻 NEXUS Code Report\n\n"
        f"**Дата:** {datetime.now():%Y-%m-%d %H:%M:%S}  \n"
        f"**Пользователь:** {app.current_user}  \n"
        f"**Язык:** {lang}  \n"
        f"**NEXUS:** v{version}\n\n"
        f"## 📝 Код\n\n"
        f"```{lang}\n{code}\n```\n\n"
        f"## 📤 Вывод\n\n"
        f"```\n{output}\n```\n"
    )
    try:
        Path(fp).write_text(md, encoding="utf-8")
        _toast(app, f"📝 {Path(fp).name}", "success")
        with suppress(Exception):
            app._safe_after(100, app._open_file, Path(fp))
    except Exception as e:
        _toast(app, f"❌ {e}", "error")


# ──────────────────────────────────────────────────────────
# ДОП. КНОПКИ В TOOLBAR
# ──────────────────────────────────────────────────────────

def build_toolbar(app: Any, ns: Dict[str, Any],
                   parent: Any) -> None:
    """Добавляет кнопки в tools-панель Code Studio."""
    CTkButton = ns.get("CTkButton")
    CTkFont = ns.get("CTkFont")
    theme = ns.get("theme_manager")
    if not all((CTkButton, CTkFont, theme)):
        return

    for txt, color, cmd in [
        ("📝 MD-отчёт", theme.get("secondary"),
         lambda: export_markdown(app, ns)),
        ("✂️ Сниппеты", theme.get("accent"),
         lambda: show_snippet_palette(app, ns)),
        ("📥 Импорты", theme.get("primary"),
         lambda: insert_imports(app)),
    ]:
        CTkButton(parent, text=txt, width=120, height=30,
                  font=CTkFont(size=10, weight="bold"),
                  fg_color=color,
                  text_color=theme.get("bg"),
                  command=cmd).pack(side="right", padx=2)


# ──────────────────────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────────────────────

def _toast(app: Any, msg: str, typ: str = "info",
           duration: int = 3000) -> None:
    with suppress(Exception):
        app._show_toast(msg, typ, duration)