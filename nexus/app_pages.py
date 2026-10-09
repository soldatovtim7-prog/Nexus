"""
NEXUS app pages — реализации всех _page_* методов.

Волна 4.2b-i: Home, Code, Snippets, QR.
Волна 4.2b-ii: Tasks, Timers, Forum, Passwords, Encrypt, Recovery.
Волна 4.2b-iii: Blockchain, Achievements, Stats, Audit, About,
                 Settings, Export, Import, Vaults, Sync, Plugins,
                 Webhooks, API.

Регистрируется через app_pages.register(app).
"""
from __future__ import annotations

import os
import re
import threading
import time
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import customtkinter as ctk
from customtkinter import (
    CTk, CTkFrame, CTkLabel, CTkButton, CTkEntry, CTkTextbox,
    CTkScrollableFrame, CTkOptionMenu, CTkProgressBar,
    CTkCheckBox, CTkRadioButton, CTkSegmentedButton,
    CTkSwitch,
    CTkImage, CTkToplevel, CTkFont,
)
from tkinter import (
    BooleanVar, StringVar, messagebox, filedialog, END,
)

from nexus.core import (
    APP_ICON, APP_NAME, APP_TITLE, VERSION, VERSION_NAME,
    UI_BUTTON_HEIGHT, UI_BUTTON_WIDTH, UI_HEADER_HEIGHT,
    UI_PAD_CARD, UI_PAD_INNER, UI_PAD_OUTER,
    UI_FORM_ENTRY_HEIGHT, UI_QR_PREVIEW_SIZE,
    UI_QR_DEBOUNCE_MS, UI_SEARCH_DEBOUNCE_MS,
    UI_CODE_AUTOSAVE_MS, UI_CODE_HIGHLIGHT_MS,
    UI_HOME_WIDGET_MIN_H, UI_HOME_WIDGET_MIN_W,
    UI_QUICK_BTN_MIN_W, UI_WRAP_MED, UI_WRAP_WIDE,
    UI_VIRTUAL_BATCH_SIZE,
    CODE_MULTI_TAB_MAX,
    DAILY_QUESTS_COUNT,
    PASSWORD_MIN_LENGTH,
    SHAMIR_PARTS, SHAMIR_THRESHOLD,
    QR_BATCH_LIMIT, MAX_TAGS,
    copy_to_clipboard, clipboard_image,
    extract_tags, fmt_dt, fmt_duration, fmt_mmss,
    fmt_size, humanize_relative,
    logger, now_iso, parse_iso, quote,
    sanitize_filename, truncate,
)
from nexus.storage import (
    cache, db, event_bus, executor, toast_dedup,
    undo_stack,
)
from nexus.domain import (
    BlockchainManager, CodeDraftManager, CodeHistory,
    CodeTemplates, DailyQuests, DraftManager, ForumManager,
    NoteManager, PatternEngine, SnippetManager, SubtaskManager,
    TaskManager, Timeline, achievement_system,
    chat_bot, context_tracker, level_system,
    pattern_engine, pomodoro, snippet_manager,
    timer_manager,
)
from nexus.security import (
    PasswordVault, UserManager, vault_session,
)
from nexus.qr import (
    QRBatchProcessor, QRExporter, QRLogoManager,
    QRTemplates, qr_gen, qr_history,
    DynamicQRManager,
)


# ═══════════════════════════════════════════════════════════
# Регистрация всех страниц
# ═══════════════════════════════════════════════════════════
def register(app: Any) -> None:
    """Зарегистрировать все страницы в app.pages."""
    app.pages.update({
        "Home": lambda parent: _page_home(app, parent),
        "Code": lambda parent: _page_code(app, parent),
        "Snippets": lambda parent: _page_snippets(app, parent),
        "QR": lambda parent: _page_qr(app, parent),
        "Tasks": lambda parent: _page_tasks(app, parent),
        "Timers": lambda parent: _page_timers(app, parent),
        "Forum": lambda parent: _page_forum(app, parent),
        "Passwords": lambda parent: _page_passwords(app, parent),
        "Encrypt": lambda parent: _page_encrypt(app, parent),
        "Recovery": lambda parent: _page_recovery(app, parent),
        "Blockchain": lambda parent: _page_blockchain(app, parent),
        "Achievements":
            lambda parent: _page_achievements(app, parent),
        "Stats": lambda parent: _page_stats(app, parent),
        "Audit": lambda parent: _page_audit(app, parent),
        "About": lambda parent: _page_about(app, parent),
        "Settings": lambda parent: _page_settings(app, parent),
        "Export": lambda parent: _page_export(app, parent),
        "Import": lambda parent: _page_import(app, parent),
        "Vaults": lambda parent: _page_vaults(app, parent),
        "Sync": lambda parent: _page_sync(app, parent),
        "Plugins": lambda parent: _page_plugins(app, parent),
        "Webhooks": lambda parent: _page_webhooks(app, parent),
        "API": lambda parent: _page_api(app, parent),
    })
    logger.info(f"✅ Зарегистрировано страниц: {len(app.pages)}")


# ═══════════════════════════════════════════════════════════
# PAGE: HOME
# ═══════════════════════════════════════════════════════════
def _page_home(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(
        parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)

    # ── Header ───────────────────────────────────────
    header = CTkFrame(scroll, fg_color="transparent",
                       height=UI_HEADER_HEIGHT)
    header.pack(fill="x", pady=(0, UI_PAD_INNER))
    header.pack_propagate(False)

    left = CTkFrame(header, fg_color="transparent")
    left.pack(side="left", fill="y")
    progress = level_system.get_progress(app.current_user)
    rank = level_system.get_rank(progress["level"])
    CTkLabel(left, text=_greeting(app),
             font=CTkFont(size=22, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(anchor="w")
    CTkLabel(left, text=rank,
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("accent"),
             anchor="w").pack(anchor="w", pady=(2, 0))

    right = CTkFrame(header, fg_color="transparent")
    right.pack(side="right", fill="y")
    for icon, color, cmd in [
        ("🍅", t.get("error"), app._show_pomodoro),
        ("🎯", t.get("warning"), app._show_quests),
        ("📸", t.get("secondary"), app._take_screenshot),
        ("⌘", t.get("primary"), app._show_command_palette),
        ("💻", t.get("accent"),
         lambda: app._show_page("Code")),
    ]:
        CTkButton(right, text=icon, width=36, height=36,
                  font=CTkFont(size=14, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  command=cmd).pack(side="right", padx=3)

    # ── Stat cards ───────────────────────────────────
    widgets = CTkFrame(scroll, fg_color="transparent")
    widgets.pack(fill="x", pady=UI_PAD_INNER)
    for i in range(4):
        widgets.grid_columnconfigure(
            i, weight=1, minsize=UI_HOME_WIDGET_MIN_W,
            uniform="home")
    widgets.grid_rowconfigure(
        0, weight=0, minsize=UI_HOME_WIDGET_MIN_H)

    today = datetime.now().date().isoformat()
    rows = db.execute(
        "SELECT count FROM daily_activity "
        "WHERE user = ? AND day = ?",
        (app.current_user, today))
    tc = rows[0]["count"] if rows else 0
    tasks = TaskManager.get_active()
    qr_count_row = db.execute(
        "SELECT COUNT(*) AS c FROM qr_history WHERE user = ?",
        (app.current_user,))
    qr_count = qr_count_row[0]["c"] if qr_count_row else 0
    q_done = sum(
        1 for q in DailyQuests.get_user_quests(app.current_user)
        if q.get("done"))

    _home_stat_card(widgets, 0, "⚡ СЕГОДНЯ", str(tc),
                     "действий", t.get("primary"),
                     t.get("primary"))
    _home_stat_card(widgets, 1, "✅ ЗАДАЧИ", str(len(tasks)),
                     "в работе", t.get("success"),
                     t.get("success"))
    _home_stat_card(widgets, 2, "📱 QR", str(qr_count),
                     "штук", t.get("accent"),
                     t.get("accent"))
    _home_stat_card(widgets, 3, "🎯 КВЕСТЫ",
                     f"{q_done}/{DAILY_QUESTS_COUNT}",
                     "сегодня", t.get("warning"),
                     t.get("warning"))

    # ── Quests ───────────────────────────────────────
    if app.config_data.quests_enabled:
        q_card = CTkFrame(scroll, corner_radius=10,
                            border_width=2,
                            border_color=t.get("warning"),
                            fg_color=t.get("bg2"))
        q_card.pack(fill="x", pady=UI_PAD_INNER)
        q_top = CTkFrame(q_card, fg_color="transparent")
        q_top.pack(fill="x", padx=UI_PAD_CARD,
                    pady=(12, 4))
        CTkLabel(q_top, text="🎯 ЕЖЕДНЕВНЫЕ КВЕСТЫ",
                 font=CTkFont(size=14, weight="bold"),
                 text_color=t.get("warning"),
                 anchor="w").pack(side="left")
        CTkButton(q_top, text="→ Все",
                  width=70, height=26,
                  font=CTkFont(size=10, weight="bold"),
                  fg_color="transparent",
                  border_width=1,
                  border_color=t.get("warning"),
                  text_color=t.get("warning"),
                  command=app._show_quests
                  ).pack(side="right")
        q_inner = CTkFrame(q_card, fg_color="transparent")
        q_inner.pack(fill="x", padx=UI_PAD_CARD,
                        pady=(0, UI_PAD_CARD))
        for q in DailyQuests.get_user_quests(app.current_user):
            row = CTkFrame(q_inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            done = q.get("done")
            icon = "✅" if done else "⬜"
            CTkLabel(row,
                     text=f"{icon} {q.get('name', '')}",
                     font=CTkFont(
                         size=12,
                         weight="bold" if not done
                         else "normal"),
                     text_color=(t.get("text_dim") if done
                                  else t.get("text")),
                     anchor="w").pack(side="left",
                                        fill="x",
                                        expand=True)
            if done:
                CTkLabel(row, text="+20 XP",
                         font=CTkFont(size=10),
                         text_color=t.get("success")
                         ).pack(side="right")

    # ── Quick start ──────────────────────────────────
    quick_card = CTkFrame(scroll, corner_radius=10,
                            border_width=2,
                            border_color=t.get("secondary"),
                            fg_color=t.get("bg2"))
    quick_card.pack(fill="x", pady=UI_PAD_INNER)
    CTkLabel(quick_card, text="⚡ БЫСТРЫЙ СТАРТ",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 6))
    grid = CTkFrame(quick_card, fg_color="transparent")
    grid.pack(fill="x", padx=UI_PAD_CARD,
                pady=(0, UI_PAD_CARD))
    for i in range(4):
        grid.grid_columnconfigure(
            i, weight=1, minsize=UI_QUICK_BTN_MIN_W,
            uniform="quick")

    quick_items = [
        ("💻\nCode Studio",
         lambda: app._show_page("Code"),
         t.get("accent")),
        ("📱\nQR-студия",
         lambda: app._show_page("QR"),
         t.get("warning")),
        ("⛓️\nЦепь",
         lambda: app._show_page("Blockchain"),
         t.get("primary")),
        ("✂️\nСниппеты",
         lambda: app._show_page("Snippets"),
         t.get("gradient1")),
        ("🍅\nPomodoro", app._show_pomodoro,
         t.get("error")),
        ("⏱️\nТаймеры",
         lambda: app._show_page("Timers"),
         t.get("success")),
        ("💬\nФорум",
         lambda: app._show_page("Forum"),
         t.get("gradient2")),
        ("🏆\nНаграды",
         lambda: app._show_page("Achievements"),
         t.get("accent")),
    ]
    for i, (txt, cmd, color) in enumerate(quick_items):
        row_idx = i // 4
        col_idx = i % 4
        CTkButton(grid, text=txt, height=54,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  corner_radius=8, command=cmd
                  ).grid(row=row_idx, column=col_idx,
                         padx=3, pady=2, sticky="nsew")

    # ── Timeline ─────────────────────────────────────
    tl_card = CTkFrame(scroll, corner_radius=10,
                         border_width=2,
                         border_color=t.get("gradient1"),
                         fg_color=t.get("bg2"))
    tl_card.pack(fill="x", pady=UI_PAD_INNER)
    CTkLabel(tl_card, text="🎬 ХРОНИКА ДНЯ",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("gradient1"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 4))
    tl_inner = CTkFrame(tl_card, fg_color="transparent")
    tl_inner.pack(fill="x", padx=UI_PAD_CARD,
                    pady=(0, UI_PAD_CARD))
    events = Timeline.get_today_events(app.current_user)[:6]
    if not events:
        CTkLabel(tl_inner, text="Пока ничего за сегодня 🌙",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=10)
    else:
        for e in events:
            row = CTkFrame(tl_inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            CTkLabel(row, text=e.get("icon", "•"),
                     font=CTkFont(size=14),
                     text_color=t.get("primary"),
                     width=24).pack(side="left", padx=(0, 6))
            CTkLabel(row,
                     text=fmt_dt(e.get("ts", ""), "%H:%M"),
                     font=CTkFont(size=10),
                     text_color=t.get("text_dim"),
                     width=44).pack(side="left", padx=(0, 8))
            CTkLabel(row, text=e.get("title", ""),
                     font=CTkFont(size=11),
                     text_color=t.get("text"),
                     anchor="w", justify="left",
                     wraplength=UI_WRAP_MED
                     ).pack(side="left", fill="x",
                            expand=True)

    # ── Quote ────────────────────────────────────────
    qf = CTkFrame(scroll, corner_radius=10,
                    border_width=2,
                    border_color=t.get("card_border"),
                    fg_color=t.get("bg2"))
    qf.pack(fill="x", pady=UI_PAD_INNER)
    CTkLabel(qf, text="📜 ЦИТАТА ДНЯ",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 6))
    CTkLabel(qf, text=f'"{quote("qr")}"',
             font=CTkFont(size=14, slant="italic"),
             text_color=t.get("text_dim"),
             wraplength=UI_WRAP_WIDE, justify="center",
             anchor="center"
             ).pack(pady=(0, UI_PAD_CARD),
                    padx=UI_PAD_CARD, fill="x")


def _greeting(app: Any) -> str:
    h = datetime.now().hour
    if 5 <= h < 12:
        base = "☀️ Доброе утро"
    elif 12 <= h < 18:
        base = "🌤️ Добрый день"
    elif 18 <= h < 23:
        base = "🌆 Добрый вечер"
    else:
        base = "🌙 Доброй ночи"
    progress = level_system.get_progress(app.current_user)
    streak = progress.get("streak", 0)
    if streak >= 30:
        suffix = f" 🔥 {streak} дней — впечатляет!"
    elif streak >= 7:
        suffix = f" 🔥 {streak} дней — отличный темп!"
    else:
        suffix = " ✨ Рад тебя видеть!"
    return f"{base}, {app.current_user}!{suffix}"


def _home_stat_card(parent: Any, col: int, title: str,
                     value: str, subtitle: str,
                     border_color: str,
                     value_color: str) -> None:
    t = _t()
    card = CTkFrame(parent, corner_radius=10,
                    border_width=2,
                    border_color=border_color,
                    fg_color=t.get("bg2"),
                    height=UI_HOME_WIDGET_MIN_H)
    card.grid(row=0, column=col, padx=6, pady=4,
                sticky="nsew")
    card.grid_propagate(False)
    CTkLabel(card, text=title,
             font=CTkFont(size=13, weight="bold"),
             text_color=border_color,
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 4))
    CTkLabel(card, text=value,
             font=CTkFont(size=32, weight="bold"),
             text_color=value_color,
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD)
    CTkLabel(card, text=subtitle,
             font=CTkFont(size=11),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(0, 6))


# ═══════════════════════════════════════════════════════════
# PAGE: CODE STUDIO
# ═══════════════════════════════════════════════════════════
def _page_code(app: Any, parent: CTkFrame) -> None:
    # Сброс старых ссылок (если страница
    # открывается повторно после переключения)
    with suppress(Exception):
        app._reset_code_refs()

    t = _t()
    cont = CTkFrame(parent, fg_color=t.get("bg"))
    cont.pack(fill="both", expand=True)

    # ── Header ───────────────────────────────────────
    header = CTkFrame(cont, fg_color="transparent",
                       height=UI_HEADER_HEIGHT)
    header.pack(fill="x", pady=(0, UI_PAD_INNER))
    header.pack_propagate(False)

    hleft = CTkFrame(header, fg_color="transparent")
    hleft.pack(side="left", fill="y")
    CTkLabel(hleft, text="💻 CODE STUDIO",
             font=CTkFont(size=22, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(anchor="w")
    CTkLabel(hleft,
             text="безопасная песочница · AST + bytecode · "
                  "syntax highlight · multi-tab",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w")

    hright = CTkFrame(header, fg_color="transparent")
    hright.pack(side="right", fill="y")
    for txt, color, cmd in [
        ("📜", t.get("warning"), app._show_code_history),
        ("📚", t.get("gradient1"), app._show_templates),
        ("🔍", t.get("accent"), app._show_code_search),
        ("🔄", t.get("success"), app._refresh_page),
    ]:
        CTkButton(hright, text=txt, width=40,
                  height=UI_BUTTON_HEIGHT,
                  font=CTkFont(size=14, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  command=cmd).pack(side="right", padx=3)

    # ── Tabs bar ─────────────────────────────────────
    tabs_bar = CTkFrame(cont, fg_color=t.get("bg2"),
                         corner_radius=8, border_width=1,
                         border_color=t.get("card_border"),
                         height=44)
    tabs_bar.pack(fill="x", pady=(0, UI_PAD_INNER))
    tabs_bar.pack_propagate(False)
    app._code_tabs_bar = tabs_bar
    tabs_inner = CTkFrame(tabs_bar, fg_color="transparent")
    tabs_inner.pack(side="left", fill="both", expand=True,
                     padx=4, pady=4)
    app._code_tabs_container = tabs_inner
    CTkButton(tabs_bar, text="➕", width=36, height=32,
              font=CTkFont(size=14, weight="bold"),
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=app._code_new_tab).pack(side="right",
                                                padx=4)

    # ── Toolbar ──────────────────────────────────────
    tools = CTkFrame(cont, fg_color=t.get("bg2"),
                      corner_radius=8, border_width=1,
                      border_color=t.get("card_border"))
    tools.pack(fill="x", pady=(0, UI_PAD_INNER))
    inner = CTkFrame(tools, fg_color="transparent")
    inner.pack(fill="x", padx=UI_PAD_CARD, pady=8)

        # ── PRO: toolbar CODE PRO ─────────────────────────
    with suppress(Exception):
        if hasattr(app, "code_pro_build_toolbar"):
            app.code_pro_build_toolbar(inner)

    CTkLabel(inner, text="🗣️",
             font=CTkFont(size=11, weight="bold"),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 4))
    # Всегда создаём НОВУЮ StringVar, привязанную
    # к главному окну — иначе после перерисовки
    # получим мёртвую ссылку.
    app._code_lang_var = StringVar(
        master=app,
        value=app.config_data.code_last_language or "python")
    CTkOptionMenu(
        inner,
        values=[
            "python", "shell", "javascript", "typescript",
            "json", "html", "css", "sql",
            "markdown", "yaml", "xml",
            "c", "cpp", "rust", "go", "java",
            "ruby", "php", "kotlin", "swift",
        ],
        variable=app._code_lang_var,
        width=140, height=30,
        font=CTkFont(size=11),
        fg_color=t.get("bg3"),
        button_color=t.get("primary"),
        text_color=t.get("text"),
        command=app._on_code_lang_change
    ).pack(side="left", padx=(0, 6))

    # ── Индикатор статуса языка ──────────────────────
    lang_status_lbl = CTkLabel(
        inner, text="",
        font=CTkFont(size=9, slant="italic"),
        text_color=t.get("text_dim"))
    lang_status_lbl.pack(side="left", padx=(0, 10))

    def _update_lang_status(lang: str) -> None:
        try:
            from nexus.sandbox import lang_available
            ok, hint = lang_available(lang)
            if ok:
                lang_status_lbl.configure(
                    text="✅ готов",
                    text_color=t.get("success"))
            else:
                lang_status_lbl.configure(
                    text=f"⚠️ {hint[:40]}",
                    text_color=t.get("warning"))
        except Exception:
            lang_status_lbl.configure(text="")

    # Патчим on_code_lang_change, чтобы обновлять статус
    _orig_lang_change = app._on_code_lang_change
    def _patched_lang_change(lang: str) -> None:
        _update_lang_status(lang)
        _orig_lang_change(lang)
    app._on_code_lang_change = _patched_lang_change

    # Первичная проверка
    _update_lang_status(
        app.config_data.code_last_language or "python")

    CTkLabel(inner, text="📚",
             font=CTkFont(size=11, weight="bold"),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 4))
    tpl_names = ["— не выбран —"] + CodeTemplates.get_names()
    app._code_template_var = StringVar(
        master=app, value="— не выбран —")
        # Сбросить старые ссылки (если страница
    # открывается повторно после переключения)
    app._code_out = None
    app._code_widget = None
    app._code_status_lbl = None
    app._code_tabs_container = None
    app._code_tabs_bar = None
    CTkOptionMenu(inner, values=tpl_names,
                   variable=app._code_template_var,
                   width=220, height=30,
                   font=CTkFont(size=11),
                   fg_color=t.get("bg3"),
                   button_color=t.get("accent"),
                   text_color=t.get("text"),
                   command=app._on_template_selected
                   ).pack(side="left", padx=(0, 10))

    CTkLabel(inner, text="🔠",
             font=CTkFont(size=11, weight="bold"),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 4))
    fs_var = StringVar(
        value=str(app.config_data.code_font_size or 13))
    CTkOptionMenu(inner,
                   values=[str(i) for i in range(10, 21)],
                   variable=fs_var,
                   width=60, height=30,
                   font=CTkFont(size=11),
                   fg_color=t.get("bg3"),
                   button_color=t.get("primary"),
                   text_color=t.get("text"),
                   command=lambda v: app._on_font_size(
                       v, fs_var)
                   ).pack(side="left", padx=(0, 10))

    auto_var = BooleanVar(
        value=app.config_data.code_auto_save)
    CTkCheckBox(inner, text="💾 Авто",
                variable=auto_var,
                font=CTkFont(size=11),
                text_color=t.get("text"),
                fg_color=t.get("success"),
                command=lambda:
                    app._toggle_code_autosave(auto_var)
                ).pack(side="left", padx=(0, 8))

    hl_var = BooleanVar(
        value=app.config_data.code_syntax_highlight)
    CTkCheckBox(inner, text="🎨 Подсветка",
                variable=hl_var,
                font=CTkFont(size=11),
                text_color=t.get("text"),
                fg_color=t.get("accent"),
                command=lambda:
                    app._toggle_syntax_highlight(hl_var)
                ).pack(side="left", padx=(0, 10))

    app._code_status_lbl = CTkLabel(
        inner, text="💤 Готов",
        font=CTkFont(size=10, slant="italic"),
        text_color=t.get("text_dim"))
    app._code_status_lbl.pack(side="right", padx=4)

    # ── Main area: Editor | Output ───────────────────
    main = CTkFrame(cont, fg_color="transparent")
    main.pack(fill="both", expand=True)
    main.grid_columnconfigure(0, weight=3, uniform="code")
    main.grid_columnconfigure(1, weight=2, uniform="code")
    main.grid_rowconfigure(0, weight=1)

    # Editor
    ed = CTkFrame(main, corner_radius=8, border_width=2,
                  border_color=t.get("card_border"),
                  fg_color=t.get("bg2"))
    ed.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    ed.grid_columnconfigure(0, weight=1)
    ed.grid_rowconfigure(1, weight=1)

    ed_top = CTkFrame(ed, fg_color="transparent")
    ed_top.grid(row=0, column=0, sticky="ew", padx=10,
                 pady=(10, 4))
    CTkLabel(ed_top, text="📝 РЕДАКТОР",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")
    CTkButton(ed_top,
              text="▶ ЗАПУСТИТЬ (Ctrl+Enter)",
              width=200, height=32,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=app._run_code_current
              ).pack(side="right", padx=3)
    CTkButton(ed_top, text="📋", width=36, height=32,
              font=CTkFont(size=13),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=app._copy_code_current
              ).pack(side="right", padx=3)
    CTkButton(ed_top, text="💾 Сниппет", width=120,
              height=32,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=app._save_as_snippet_current
              ).pack(side="right", padx=3)
        # ── NEW: Быстрые действия ──────────────────────────
    CTkButton(
        ed_top, text="▶▶ Все",
        width=70, height=32,
        font=CTkFont(size=10, weight="bold"),
        fg_color=t.get("accent"),
        text_color=t.get("bg"),
        command=app._run_all_tabs
    ).pack(side="right", padx=3)

    CTkButton(
        ed_top, text="🎨 Формат",
        width=90, height=32,
        font=CTkFont(size=10, weight="bold"),
        fg_color=t.get("gradient1"),
        text_color=t.get("bg"),
        command=app._format_code
    ).pack(side="right", padx=3)

    CTkButton(
        ed_top, text="💾 Файл",
        width=80, height=32,
        font=CTkFont(size=10, weight="bold"),
        fg_color=t.get("warning"),
        text_color=t.get("bg"),
        command=app._save_code_as_file
    ).pack(side="right", padx=3)

    fs = app.config_data.code_font_size or 13
    code = CTkTextbox(ed,
                      font=CTkFont(family="Courier", size=fs),
                      wrap="none",
                      fg_color=t.get("code_bg"),
                      text_color=t.get("code_text"),
                      undo=True)
    code.grid(row=1, column=0, sticky="nsew", padx=10,
                pady=(0, 10))
    app._code_widget = code

    code.bind("<Control-Return>",
               lambda e: (app._run_code_current(),
                           "break")[1])
    code.bind("<Control-s>",
               lambda e: (app._save_code_draft(),
                           "break")[1])
    code.bind("<KeyRelease>",
               lambda e: app._on_code_change())

    # Output
    right = CTkFrame(main, corner_radius=8,
                     border_width=2,
                     border_color=t.get("card_border"),
                     fg_color=t.get("bg2"))
    right.grid(row=0, column=1, sticky="nsew",
                 padx=(5, 0))
    right.grid_columnconfigure(0, weight=1)
    right.grid_rowconfigure(1, weight=1)

    out_top = CTkFrame(right, fg_color="transparent")
    out_top.grid(row=0, column=0, sticky="ew", padx=10,
                   pady=(10, 4))
    CTkLabel(out_top, text="📤 ВЫВОД",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(side="left")
    CTkButton(out_top, text="🗑️", width=36, height=30,
              font=CTkFont(size=13),
              fg_color="transparent",
              border_width=1,
              border_color=t.get("error"),
              text_color=t.get("error"),
              command=app._clear_code_output
              ).pack(side="right", padx=3)
    CTkButton(out_top, text="📋", width=36, height=30,
              font=CTkFont(size=13),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=app._copy_output
              ).pack(side="right", padx=3)

    out = CTkTextbox(right,
                     font=CTkFont(family="Courier", size=12),
                     wrap="word",
                     fg_color=t.get("bg3"),
                     text_color=t.get("text"))
    out.grid(row=1, column=0, sticky="nsew", padx=10,
               pady=(0, 10))
    app._code_out = out
    out.insert("1.0",
               "💡 Готов к работе!\n\n"
               "• Ctrl+Enter — запустить код\n"
               "• Ctrl+S — сохранить черновик\n"
               "• Выбери шаблон в панели инструментов\n"
               "• Код выполняется в безопасной песочнице")

    # ── PRO: CODE PRO toolbar (только один раз) ──────
    if not getattr(app, "_code_pro_toolbar_added", False):
        with suppress(Exception):
            if hasattr(app, "code_pro_build_toolbar"):
                app.code_pro_build_toolbar(inner)
                app._code_pro_toolbar_added = True


    # ── PRO: CODE PRO на редакторе ───────────────────
    with suppress(Exception):
        if hasattr(app, "code_pro_bind_widget"):
            app.code_pro_bind_widget()


    # ── PRO: Debugger + CODE PRO на редакторе ────────
    with suppress(Exception):
        if hasattr(app, "debugger_bind_editor"):
            app.debugger_bind_editor(code)
    with suppress(Exception):
        if hasattr(app, "code_pro_bind_widget"):
            app.code_pro_bind_widget()

    # ── Загрузка черновика ───────────────────────────
    if not app._code_tabs:
        draft = CodeDraftManager.load()

        draft_code = ""
        if draft and isinstance(draft, dict):
            draft_code = (draft.get("code") or "").strip()
        is_meaningful = (
            draft_code
            and len(draft_code) >= 5
            and not draft_code.isdigit()
            and any(marker in draft_code for marker in (
                "print", "def ", "class ", "import ",
                "from ", "=", "return", "if ", "for ",
            ))
        )
        if is_meaningful:
            app._code_tabs = [{
                "name": "draft",
                "code": draft_code,
                "lang": (draft.get("language", "python")
                          if draft else "python"),
            }]
        else:
            with suppress(Exception):
                CodeDraftManager.clear()
            app._code_tabs = [{
                "name": "main",
                "code": CodeTemplates.BUILTIN[
                    "hello_world"]["code"],
                "lang": "python",
            }]
        app._code_current_tab = 0


    app._code_render_tabs()
    app._code_load_active_tab()


# ═══════════════════════════════════════════════════════════
# PAGE: SNIPPETS
# ═══════════════════════════════════════════════════════════
def _page_snippets(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    snippets = snippet_manager.get_all(app.current_user)
    app._page_header(
        scroll, "СНИППЕТЫ", "✂️",
        f"библиотека кода · {len(snippets)} шт",
        buttons=[
            ("➕ Новый", t.get("success"),
             app._new_snippet_dialog),
            ("🔄", t.get("primary"), app._refresh_page)])

    search_row = CTkFrame(scroll, fg_color="transparent")
    search_row.pack(fill="x", pady=(0, UI_PAD_INNER))
    search_e = CTkEntry(
        search_row,
        placeholder_text="🔍 Поиск по сниппетам...",
        height=36, font=CTkFont(size=12),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    search_e.pack(side="left", fill="x", expand=True,
                   padx=(0, 6))

    if not snippets:
        empty = app._section_card(
            scroll, "✂️ Нет сниппетов",
            t.get("card_border"))
        CTkLabel(empty,
                 text="Сохрани код из Code Studio — "
                      "он будет под рукой",
                 font=CTkFont(size=13),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
        return

    list_frame = CTkFrame(scroll, fg_color="transparent")
    list_frame.pack(fill="both", expand=True)

    def render(query: str = "") -> None:
        for w in list_frame.winfo_children():
            w.destroy()
        q = query.lower().strip()
        filtered = snippets if not q else [
            s for s in snippets
            if q in (s.get("name", "")
                      + s.get("code", "")
                      + s.get("description", "")).lower()]
        if not filtered:
            CTkLabel(list_frame, text="😕 Ничего",
                     font=CTkFont(size=13),
                     text_color=t.get("text_dim")
                     ).pack(pady=20)
            return
        for s in filtered:
            _render_snippet_card(app, list_frame, s)

    def on_search(e: Any = None) -> None:
        app._timer_pool.schedule(
            "snippet_search", UI_SEARCH_DEBOUNCE_MS,
            render, search_e.get())

    search_e.bind("<KeyRelease>", on_search)
    render()


def _render_snippet_card(app: Any, parent: Any,
                          s: Dict[str, Any]) -> None:
    t = _t()
    card = CTkFrame(parent, fg_color=t.get("bg2"),
                     corner_radius=8, border_width=1,
                     border_color=t.get("card_border"))
    card.pack(fill="x", pady=4)
    top = CTkFrame(card, fg_color="transparent")
    top.pack(fill="x", padx=UI_PAD_CARD, pady=(8, 4))
    CTkLabel(top, text=f"✂️ {s.get('name', '')}",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")
    CTkLabel(top,
             text=f"· {s.get('language', 'python')} · "
                  f"исп. {s.get('uses', 0)}",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(side="left", padx=6)
    code_preview = s.get("code", "")[:300]
    if len(s.get("code", "")) > 300:
        code_preview += "\n..."
    code_box = CTkTextbox(
        card, height=100,
        font=CTkFont(family="Courier", size=11),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("code_text"),
        wrap="none")
    code_box.pack(fill="x", padx=UI_PAD_CARD,
                    pady=(0, 6))
    code_box.insert("1.0", code_preview)
    code_box.configure(state="disabled")
    br = CTkFrame(card, fg_color="transparent")
    br.pack(fill="x", padx=UI_PAD_CARD, pady=(0, 8))
    CTkButton(br, text="📋 В редактор", width=140,
              height=28,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=lambda c=s.get("code", ""): (
                  app._load_code_to_editor(c),
                  app._show_page("Code"))
              ).pack(side="left", padx=2)
    CTkButton(br, text="📄 Копировать", width=140,
              height=28,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=lambda c=s.get("code", ""),
                  sid=s["id"]: (
                      copy_to_clipboard(c),
                      snippet_manager.increment_uses(sid),
                      app._show_toast("📋 Скопировано",
                                        "success", 1500))
              ).pack(side="left", padx=2)
    CTkButton(br, text="🗑️", width=36, height=28,
              font=CTkFont(size=11),
              fg_color="transparent",
              text_color=t.get("error"),
              command=lambda sid=s["id"]: (
                  snippet_manager.delete(sid),
                  app._refresh_page())
              ).pack(side="right", padx=2)


# ═══════════════════════════════════════════════════════════
# PAGE: QR STUDIO
# ═══════════════════════════════════════════════════════════
def _page_qr(app: Any, parent: CTkFrame) -> None:
    t = _t()
    cont = CTkFrame(parent, fg_color=t.get("bg"))
    cont.pack(fill="both", expand=True)

    # ── Header ───────────────────────────────────────
    header = CTkFrame(cont, fg_color="transparent",
                       height=UI_HEADER_HEIGHT)
    header.pack(fill="x", pady=(0, UI_PAD_INNER))
    header.pack_propagate(False)
    hleft = CTkFrame(header, fg_color="transparent")
    hleft.pack(side="left", fill="y")
    CTkLabel(hleft, text="📱 QR-СТУДИЯ",
             font=CTkFont(size=22, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(anchor="w")
    CTkLabel(hleft,
             text="16 стилей · 11 режимов · live-preview · "
                  "сканер · батч · dynamic-QR",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w")
    hright = CTkFrame(header, fg_color="transparent")
    hright.pack(side="right", fill="y")
    for txt, color, cmd in [
        ("📋 Буфер", t.get("success"),
         app._qr_scan_from_clipboard),
        ("📥 Файл", t.get("accent"),
         app._qr_scan_from_file),
        ("📚", t.get("warning"), app._show_qr_history),
        ("📦", t.get("gradient1"), app._show_qr_batch),
        ("🔄", t.get("primary"), app._refresh_page),
    ]:
        CTkButton(hright, text=txt, width=90,
                  height=UI_BUTTON_HEIGHT,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  command=cmd).pack(side="right", padx=3)

    # ── Main: Left (controls) | Right (preview) ──────
    main = CTkFrame(cont, fg_color="transparent")
    main.pack(fill="both", expand=True)
    main.grid_columnconfigure(0, weight=1, uniform="qr")
    main.grid_columnconfigure(1, weight=1, uniform="qr")
    main.grid_rowconfigure(0, weight=1)

    # Left panel
    left = CTkScrollableFrame(main, fg_color=t.get("bg2"))
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    left.grid_columnconfigure(0, weight=1)

    CTkLabel(left, text="🎯 РЕЖИМ",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(10, 4))
    mode_var = StringVar(value="📝 Текст")
    CTkSegmentedButton(
        left,
        values=["📝 Текст", "🔗 URL", "📶 WiFi", "👤 vCard"],
        font=CTkFont(size=10, weight="bold"),
        selected_color=t.get("primary"),
        unselected_color=t.get("bg3"),
        text_color=t.get("text"),
        variable=mode_var
    ).pack(fill="x", padx=UI_PAD_INNER, pady=(0, 4))
    CTkSegmentedButton(
        left,
        values=["📧 Email", "📱 SMS", "☎️ Tel", "📍 Geo"],
        font=CTkFont(size=10, weight="bold"),
        selected_color=t.get("primary"),
        unselected_color=t.get("bg3"),
        text_color=t.get("text"),
        variable=mode_var
    ).pack(fill="x", padx=UI_PAD_INNER, pady=(0, 4))
    mode_row3 = CTkSegmentedButton(
        left,
        values=["📅 Event", "💰 Crypto", "🔀 Dynamic"],
        font=CTkFont(size=10, weight="bold"),
        selected_color=t.get("primary"),
        unselected_color=t.get("bg3"),
        text_color=t.get("text"),
        variable=mode_var)
    mode_row3.pack(fill="x", padx=UI_PAD_INNER, pady=(0, 10))

    mode_keys = {
        "📝 Текст": "text", "🔗 URL": "url",
        "📶 WiFi": "wifi", "👤 vCard": "vcard",
        "📧 Email": "email", "📱 SMS": "sms",
        "☎️ Tel": "tel", "📍 Geo": "geo",
        "📅 Event": "event", "💰 Crypto": "crypto",
        "🔀 Dynamic": "dynamic",
    }
    mode_container = CTkFrame(left, fg_color="transparent")
    mode_container.pack(fill="x", padx=UI_PAD_INNER, pady=4)
    app._qr_mode_data = {}
    app._qr_current_mode = "text"
    app._qr_preview_image = None

    def get_mode_data() -> str:
        mode = app._qr_current_mode
        d = app._qr_mode_data
        try:
            if mode == "text":
                w = d.get("text")
                return (w.get("1.0", "end-1c").strip()
                        if w else "")
            if mode == "url":
                w = d.get("url")
                return QRTemplates.url(w.get()) if w else ""
            if mode == "wifi":
                ssid = d.get("wifi_ssid")
                pwd = d.get("wifi_pass")
                if not ssid:
                    return ""
                enc = d.get("wifi_enc")
                enc_val = (enc.get()
                            if hasattr(enc, "get")
                            else "WPA")
                return QRTemplates.wifi(
                    ssid.get(),
                    pwd.get() if pwd else "", enc_val)
            if mode == "vcard":
                name = d.get("vcard_name")
                if not name or not name.get().strip():
                    return ""
                phone = d.get("vcard_phone")
                email = d.get("vcard_email")
                return QRTemplates.vcard(
                    name.get(),
                    phone.get() if phone else "",
                    email.get() if email else "")
            if mode == "email":
                to = d.get("email_to")
                if not to or not to.get().strip():
                    return ""
                subj = d.get("email_subject")
                body = d.get("email_body")
                return QRTemplates.email(
                    to.get(),
                    subj.get() if subj else "",
                    body.get() if body else "")
            if mode == "sms":
                phone = d.get("sms_phone")
                msg = d.get("sms_msg")
                return (QRTemplates.sms(
                    phone.get(),
                    msg.get() if msg else "")
                    if phone else "")
            if mode == "tel":
                phone = d.get("tel_phone")
                return (QRTemplates.tel(phone.get())
                        if phone else "")
            if mode == "geo":
                lat = d.get("geo_lat")
                lon = d.get("geo_lon")
                label = d.get("geo_label")
                if not lat or not lon:
                    return ""
                return QRTemplates.geo(
                    lat.get(), lon.get(),
                    label.get() if label else "")
            if mode == "event":
                title = d.get("evt_title")
                start = d.get("evt_start")
                if not title or not start:
                    return ""
                end = d.get("evt_end")
                loc = d.get("evt_loc")
                desc = d.get("evt_desc")
                return QRTemplates.event(
                    title.get(), start.get(),
                    end.get() if end else "",
                    loc.get() if loc else "",
                    desc.get() if desc else "")
            if mode == "crypto":
                coin = d.get("crypto_coin")
                addr = d.get("crypto_addr")
                amt = d.get("crypto_amount")
                if not coin or not addr:
                    return ""
                return QRTemplates.crypto(
                    coin.get(), addr.get(),
                    amt.get() if amt else "")
            if mode == "dynamic":
                url = d.get("dyn_url")
                if not url or not url.get().strip():
                    return ""
                raw = url.get().strip()
                qid = DynamicQRManager.create(
                    app.current_user, raw)
                if not qid:
                    return raw
                app._last_dynamic_id = qid
                port = (app.config_data.api_port or 8765)
                return (f"http://127.0.0.1:{port}"
                        f"/r/{qid}")
        except Exception as e:
            logger.debug(f"qr mode: {e}")
        return ""

    app._qr_get_mode_data = get_mode_data

    def rebuild_fields(mode_label: str) -> None:
        app._qr_mode_data.clear()
        for w in mode_container.winfo_children():
            w.destroy()

        def mk_entry(label: str, ph: str, key: str,
                      show: str = "") -> None:
            CTkLabel(mode_container, text=label,
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("text_glow"),
                     anchor="w").pack(anchor="w", fill="x",
                                        pady=(6, 3))
            e = CTkEntry(mode_container,
                          placeholder_text=ph, height=34,
                          font=CTkFont(size=12),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"),
                          show=show)
            e.pack(fill="x", pady=(0, 4))
            e.bind("<KeyRelease>",
                    lambda ev: app._qr_schedule_preview())
            app._qr_mode_data[key] = e

        def mk_text(label: str, key: str,
                     default: str = "") -> None:
            CTkLabel(mode_container, text=label,
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("text_glow"),
                     anchor="w").pack(anchor="w", fill="x",
                                        pady=(6, 3))
            tx = CTkTextbox(mode_container, height=80,
                             font=CTkFont(size=12),
                             fg_color=t.get("bg3"),
                             border_color=t.get("card_border"),
                             text_color=t.get("text"),
                             wrap="word")
            tx.pack(fill="x", pady=(0, 4))
            if default:
                tx.insert("1.0", default)
            tx.bind("<KeyRelease>",
                     lambda ev: app._qr_schedule_preview())
            app._qr_mode_data[key] = tx

        mode_key = mode_keys.get(mode_label, "text")
        app._qr_current_mode = mode_key
        if mode_key == "text":
            mk_text("📝 Текст:", "text", "🌀 NEXUS v1.1.1")
        elif mode_key == "url":
            mk_entry("🔗 URL:", "example.com", "url")
        elif mode_key == "wifi":
            mk_entry("📶 SSID:", "MyWiFi", "wifi_ssid")
            mk_entry("🔑 Пароль:", "password",
                      "wifi_pass", show="•")
            enc_var = StringVar(value="WPA")
            CTkOptionMenu(mode_container,
                           values=["WPA", "WEP", "NOPASS"],
                           variable=enc_var, height=32,
                           font=CTkFont(size=12),
                           fg_color=t.get("bg3"),
                           button_color=t.get("primary"),
                           text_color=t.get("text"),
                           command=lambda v:
                               app._qr_schedule_preview()
                           ).pack(fill="x", pady=4)
            app._qr_mode_data["wifi_enc"] = enc_var
        elif mode_key == "vcard":
            mk_entry("👤 ФИО:", "Иванов Иван",
                      "vcard_name")
            mk_entry("📞 Тел:", "+79991234567",
                      "vcard_phone")
            mk_entry("📧 Email:", "ivan@nexus.app",
                      "vcard_email")
        elif mode_key == "email":
            mk_entry("📧 Кому:", "someone@mail.com",
                      "email_to")
            mk_entry("✉️ Тема:", "Тема", "email_subject")
            mk_entry("📄 Текст:", "", "email_body")
        elif mode_key == "sms":
            mk_entry("📱 Номер:", "+79991234567",
                      "sms_phone")
            mk_entry("✉️ Сообщение:", "", "sms_msg")
        elif mode_key == "tel":
            mk_entry("☎️ Номер:", "+79991234567",
                      "tel_phone")
        elif mode_key == "geo":
            mk_entry("🌍 Широта:", "55.7558", "geo_lat")
            mk_entry("🌍 Долгота:", "37.6173", "geo_lon")
            mk_entry("🏷️ Метка:", "Москва", "geo_label")
        elif mode_key == "event":
            mk_entry("📅 Название:", "Встреча", "evt_title")
            mk_entry("⏰ Начало:", "2025-01-15 10:00",
                      "evt_start")
            mk_entry("⏰ Конец:", "2025-01-15 11:00",
                      "evt_end")
            mk_entry("📍 Место:", "", "evt_loc")
            mk_entry("📝 Описание:", "", "evt_desc")
        elif mode_key == "crypto":
            coin_var = StringVar(value="bitcoin")
            CTkLabel(mode_container, text="💰 Монета:",
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("text_glow"),
                     anchor="w").pack(anchor="w", fill="x",
                                        pady=(6, 3))
            CTkOptionMenu(mode_container,
                           values=["bitcoin", "ethereum",
                                     "litecoin", "dogecoin"],
                           variable=coin_var, height=32,
                           font=CTkFont(size=12),
                           fg_color=t.get("bg3"),
                           button_color=t.get("primary"),
                           text_color=t.get("text"),
                           command=lambda v:
                               app._qr_schedule_preview()
                           ).pack(fill="x", pady=(0, 4))
            app._qr_mode_data["crypto_coin"] = coin_var
            mk_entry("📍 Адрес:", "", "crypto_addr")
            mk_entry("💵 Сумма:", "0.001", "crypto_amount")
        elif mode_key == "dynamic":
            CTkLabel(mode_container,
                     text="🔀 Динамический QR — URL можно "
                          "менять после печати:",
                     font=CTkFont(size=10),
                     text_color=t.get("text_dim"),
                     anchor="w", justify="left",
                     wraplength=380
                     ).pack(anchor="w", fill="x", pady=4)
            mk_entry("🔗 Целевой URL:",
                      "https://example.com/page", "dyn_url")
            CTkLabel(mode_container,
                     text="⚠️ Работает когда API включён "
                          "в Настройках",
                     font=CTkFont(size=9, slant="italic"),
                     text_color=t.get("warning"),
                     anchor="w").pack(anchor="w", pady=2)

        app._qr_schedule_preview()

    # Привязка segmented buttons — перестройка
    # (Simplified: рендерим один раз "text" и слушаем изменения
    # через command в SegmentedButton)
    # Для простоты — начальная форма
    rebuild_fields("📝 Текст")

    # Hook up mode changes
    def on_mode_change(value: str) -> None:
        rebuild_fields(value)

    # Rebind commands (нельзя сделать в конструкторе из-за
    # порядка создания — делаем после)
    for widget in left.winfo_children():
        if isinstance(widget, ctk.CTkSegmentedButton):
            with suppress(Exception):
                widget.configure(command=on_mode_change)

    # ── Style picker ─────────────────────────────────
    CTkLabel(left, text="🎨 СТИЛЬ",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(10, 4))
    style_names = [
        f"{qr_gen.get_style_name(k)} ({k})"
        for k in qr_gen.get_styles()]
    default_style = (app.config_data.qr_default_style
                      or "neon_cyan")
    default_label = next(
        (s for s in style_names
         if f"({default_style})" in s),
        style_names[0] if style_names else "")
    app._qr_style_var = StringVar(value=default_label)
    CTkOptionMenu(left, values=style_names,
                   variable=app._qr_style_var, height=34,
                   font=CTkFont(size=11),
                   fg_color=t.get("bg3"),
                   button_color=t.get("primary"),
                   text_color=t.get("text"),
                   command=lambda v:
                       app._qr_on_style_change(v)
                   ).pack(fill="x", padx=UI_PAD_CARD,
                          pady=(0, 6))

    CTkLabel(left, text="📐 РАЗМЕР (px)",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(6, 4))
    app._qr_size_var = StringVar(
        value=str(app.config_data.qr_default_size or 400))
    CTkOptionMenu(left,
                   values=["200", "300", "400", "500",
                            "600", "800", "1000"],
                   variable=app._qr_size_var, height=32,
                   font=CTkFont(size=11),
                   fg_color=t.get("bg3"),
                   button_color=t.get("primary"),
                   text_color=t.get("text"),
                   command=lambda v:
                       app._qr_schedule_preview()
                   ).pack(fill="x", padx=UI_PAD_CARD,
                          pady=(0, 6))

    CTkLabel(left, text="🖼️ ЛОГОТИП",
             font=CTkFont(size=13, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(6, 4))
    logo_row = CTkFrame(left, fg_color="transparent")
    logo_row.pack(fill="x", padx=UI_PAD_CARD, pady=(0, 6))
    app._qr_logo_lbl = CTkLabel(
        logo_row,
        text=("✅ Загружен" if app._qr_logo_path
               else "— не выбран —"),
        font=CTkFont(size=10, slant="italic"),
        text_color=t.get("text_dim"))
    app._qr_logo_lbl.pack(side="left")
    CTkButton(logo_row, text="📂", width=36, height=28,
              font=CTkFont(size=12),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=app._qr_choose_logo
              ).pack(side="right", padx=2)
    if app._qr_logo_path:
        CTkButton(logo_row, text="✕", width=36, height=28,
                  font=CTkFont(size=12),
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=app._qr_clear_logo
                  ).pack(side="right", padx=2)

    btns = CTkFrame(left, fg_color="transparent")
    btns.pack(fill="x", padx=UI_PAD_CARD,
               pady=(10, UI_PAD_CARD))
    CTkButton(btns, text="🎨 СГЕНЕРИРОВАТЬ",
              height=42,
              font=CTkFont(size=13, weight="bold"),
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=app._qr_generate_click
              ).pack(fill="x", pady=2)
    CTkButton(btns, text="💾 Сохранить в историю",
              height=32,
              font=CTkFont(size=11, weight="bold"),
              fg_color="transparent", border_width=1,
              border_color=t.get("success"),
              text_color=t.get("success"),
              command=app._qr_save_to_history
              ).pack(fill="x", pady=2)

    # ── Right panel (preview) ────────────────────────
    right = CTkFrame(main, corner_radius=8,
                     border_width=2,
                     border_color=t.get("card_border"),
                     fg_color=t.get("bg2"))
    right.grid(row=0, column=1, sticky="nsew",
                 padx=(5, 0))
    right.grid_columnconfigure(0, weight=1)
    right.grid_rowconfigure(1, weight=1)

    CTkLabel(right, text="◈ ПРЕДПРОСМОТР (live)",
             font=CTkFont(size=16, weight="bold"),
             text_color=t.get("secondary"),
             anchor="w").grid(row=0, column=0, sticky="ew",
                                padx=15, pady=(12, 6))
    pf = CTkFrame(right, fg_color=t.get("bg3"),
                   corner_radius=8,
                   width=UI_QR_PREVIEW_SIZE + 20,
                   height=UI_QR_PREVIEW_SIZE + 20)
    pf.grid(row=1, column=0, padx=15, pady=8,
             sticky="nsew")
    pf.grid_propagate(False)
    app._qr_label = CTkLabel(
        pf, text="📱 Введите данные...",
        font=CTkFont(size=15, weight="bold"),
        text_color=t.get("text_dim"))
    app._qr_label.pack(expand=True)

    app._qr_info_lbl = CTkLabel(
        right, text="💡 Live-preview активируется при вводе",
        font=CTkFont(size=10, slant="italic"),
        text_color=t.get("text_dim"))
    app._qr_info_lbl.grid(row=2, column=0, sticky="ew",
                            padx=15, pady=(4, 4))

    bottom = CTkFrame(right, fg_color="transparent")
    bottom.grid(row=3, column=0, sticky="ew", padx=15,
                 pady=(6, 12))
    for txt, color, cmd in [
        ("💾 PNG", t.get("success"),
         lambda: app._qr_save_as("PNG")),
        ("📷 JPG", t.get("accent"),
         lambda: app._qr_save_as("JPEG")),
        ("🎨 SVG", t.get("warning"),
         lambda: app._qr_save_as("SVG")),
        ("📋", t.get("primary"), app._qr_copy_data),
    ]:
        CTkButton(bottom, text=txt, width=90, height=34,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  command=cmd).pack(side="left", padx=3)
    # ── PRO: панель пресетов и скриптов ──────────────
    with suppress(Exception):
        if hasattr(app, "qr_pro_build_panel"):
            app.qr_pro_build_panel(left)
            logger.info("✅ QR PRO panel attached")


# ═══════════════════════════════════════════════════════════
# Заглушки для остальных страниц (4.2b-ii, 4.2b-iii)
# ═══════════════════════════════════════════════════════════
def _make_stub(name: str) -> Callable:
    def stub(app: Any, parent: CTkFrame) -> None:
        t = _t()
        app._page_header(parent, name.upper(), "🚧",
                          "будет добавлено в 4.2b-ii/iii")
        card = CTkFrame(parent, fg_color=t.get("bg2"),
                         corner_radius=10, border_width=2,
                         border_color=t.get("warning"))
        card.pack(fill="both", expand=True,
                   pady=UI_PAD_INNER)
        CTkLabel(card,
                 text=f"🚧 Страница «{name}»\n\n"
                      f"Будет добавлена в волне 4.2b-ii "
                      f"или 4.2b-iii",
                 font=CTkFont(size=16, weight="bold"),
                 text_color=t.get("text_dim"),
                 justify="center").pack(expand=True)
    return stub


# ═══════════════════════════════════════════════════════════
# PAGE: TASKS
# ═══════════════════════════════════════════════════════════
def _page_tasks(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    stats = TaskManager.stats_for(app.current_user)
    subtitle = (f"всего: {stats['total']} · "
                f"в работе: "
                f"{stats['pending'] + stats['in_progress']} · "
                f"выполнено: {stats['done']}")
    app._page_header(
        scroll, "ЗАДАЧИ", "✅", subtitle,
        buttons=[("🍅 Pomodoro", t.get("error"),
                  app._show_pomodoro),
                 ("🔄", t.get("success"),
                  app._refresh_page)])

    view_mode = StringVar(value=app._tasks_view)
    vf = CTkFrame(scroll, fg_color="transparent")
    vf.pack(fill="x", pady=(0, UI_PAD_INNER))
    CTkLabel(vf, text="👁 Вид:", font=CTkFont(size=11),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=4)

    def set_view(mode: str) -> None:
        app._tasks_view = mode
        if mode == "kanban":
            with suppress(Exception):
                achievement_system.check(
                    app.current_user, "kanban_user")
        app._refresh_page()

    CTkRadioButton(vf, text="📋 Список",
                    variable=view_mode, value="list",
                    font=CTkFont(size=11),
                    text_color=t.get("text"),
                    command=lambda: set_view("list")
                    ).pack(side="left", padx=4)
    CTkRadioButton(vf, text="🗂 Kanban",
                    variable=view_mode, value="kanban",
                    font=CTkFont(size=11),
                    text_color=t.get("text"),
                    command=lambda: set_view("kanban")
                    ).pack(side="left", padx=4)
    CTkRadioButton(vf, text="📅 Gantt-lite",
                    variable=view_mode, value="gantt",
                    font=CTkFont(size=11),
                    text_color=t.get("text"),
                    command=lambda: set_view("gantt")
                    ).pack(side="left", padx=4)

    if view_mode.get() == "kanban":
        _render_kanban(app, scroll)
        return
    if view_mode.get() == "gantt":
        _render_gantt(app, scroll)
        return

    # ── Форма создания ───────────────────────────────
    create = app._section_card(
        scroll, "✅ НОВАЯ ЗАДАЧА", t.get("secondary"))
    title_e = CTkEntry(
        create, placeholder_text="📌 Что нужно сделать?",
        height=UI_FORM_ENTRY_HEIGHT,
        font=CTkFont(size=14),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    title_e.pack(padx=UI_PAD_CARD, pady=4, fill="x")

    prio = StringVar(value="normal")
    repeat_var = StringVar(value="—")
    pf = CTkFrame(create, fg_color="transparent")
    pf.pack(padx=UI_PAD_CARD, pady=6, fill="x")
    CTkLabel(pf, text="🎯 Приоритет:",
             font=CTkFont(size=13),
             text_color=t.get("text_dim"),
             anchor="w").pack(side="left", padx=(0, 10))
    for p, ic, lbl in [("critical", "🔥", "Крит"),
                        ("high", "🔴", "Выс"),
                        ("normal", "🟡", "Сред"),
                        ("low", "🟢", "Низ")]:
        CTkRadioButton(pf, text=f"{ic} {lbl}",
                        variable=prio, value=p,
                        font=CTkFont(size=12),
                        text_color=t.get("text"),
                        fg_color=t.get("primary")
                        ).pack(side="left", padx=4)

    repf = CTkFrame(create, fg_color="transparent")
    repf.pack(padx=UI_PAD_CARD, pady=4, fill="x")
    CTkLabel(repf, text="🔁 Повтор:",
             font=CTkFont(size=12),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 6))
    CTkOptionMenu(repf,
                   values=["—", "daily", "weekly",
                             "monthly"],
                   variable=repeat_var, width=120,
                   height=28, font=CTkFont(size=11),
                   fg_color=t.get("bg3"),
                   button_color=t.get("primary"),
                   text_color=t.get("text")
                   ).pack(side="left")

    dl_row = CTkFrame(create, fg_color="transparent")
    dl_row.pack(padx=UI_PAD_CARD, pady=4, fill="x")
    CTkLabel(dl_row, text="📅 Дедлайн:",
             font=CTkFont(size=12),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 6))
    dl_e = CTkEntry(dl_row,
                     placeholder_text="YYYY-MM-DD",
                     height=30, font=CTkFont(size=11),
                     fg_color=t.get("bg3"),
                     border_color=t.get("card_border"),
                     text_color=t.get("text"))
    dl_e.pack(side="left", fill="x", expand=True)

    def add() -> None:
        title = title_e.get().strip()
        if not title:
            return
        tags = extract_tags(title)
        rp = repeat_var.get()
        if rp == "—":
            rp = ""
        dl = dl_e.get().strip() or None
        if TaskManager.create(
                title, app.current_user, prio.get(),
                tags, repeat=rp, deadline=dl):
            title_e.delete(0, "end")
            dl_e.delete(0, "end")
            app._show_toast("✅ Создано", "success")
            app._refresh_page()

    CTkButton(create, text="💾 ДОБАВИТЬ", height=38,
              width=160,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=add
              ).pack(pady=(6, UI_PAD_CARD),
                     padx=UI_PAD_CARD, anchor="w")
    title_e.bind("<Return>", lambda e: add())

    # ── Списки задач ─────────────────────────────────
    tasks = TaskManager.get_all()
    active = [x for x in tasks
              if x.get("status") != "done"]
    done = [x for x in tasks if x.get("status") == "done"]

    if not active and not done:
        empty = app._section_card(
            scroll, "🎉 Нет задач", t.get("success"))
        CTkLabel(empty, text="Можно отдохнуть!",
                 font=CTkFont(size=13),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
        return

    _BATCH = UI_VIRTUAL_BATCH_SIZE

    if active:
        CTkLabel(scroll,
                 text=f"🟢 В РАБОТЕ ({len(active)})",
                 font=CTkFont(size=14, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(anchor="w",
                                    pady=(UI_PAD_INNER, 4))
        _cont = CTkFrame(scroll, fg_color="transparent")
        _cont.pack(fill="x")
        _shown = {"n": 0}

        def _show_more() -> None:
            start = _shown["n"]
            end = min(len(active), start + _BATCH)
            for task in active[start:end]:
                _render_task_card(app, _cont, task, False)
            _shown["n"] = end
            if end < len(active):
                CTkButton(
                    _cont,
                    text=f"⬇ Показать ещё "
                         f"({len(active) - end})",
                    height=32, width=280,
                    font=CTkFont(size=11, weight="bold"),
                    fg_color=t.get("accent"),
                    text_color=t.get("bg"),
                    command=_show_more
                ).pack(pady=6)

        _show_more()

    if done:
        CTkLabel(scroll,
                 text=f"✅ ВЫПОЛНЕНО ({len(done)})",
                 font=CTkFont(size=14, weight="bold"),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    pady=(UI_PAD_INNER, 4))
        _cont = CTkFrame(scroll, fg_color="transparent")
        _cont.pack(fill="x")
        _shown = {"n": 0}

        def _show_more_done() -> None:
            start = _shown["n"]
            end = min(len(done), start + _BATCH)
            for task in done[start:end]:
                _render_task_card(app, _cont, task, True)
            _shown["n"] = end
            if end < len(done):
                CTkButton(
                    _cont,
                    text=f"⬇ Показать ещё "
                         f"({len(done) - end})",
                    height=32, width=280,
                    font=CTkFont(size=11, weight="bold"),
                    fg_color=t.get("accent"),
                    text_color=t.get("bg"),
                    command=_show_more_done
                ).pack(pady=6)

        _show_more_done()


def _render_kanban(app: Any, parent: Any) -> None:
    t = _t()
    tasks = TaskManager.get_all()
    cols = [
        ("pending", "🟡 ОЖИДАЕТ", t.get("warning")),
        ("in_progress", "🔵 В РАБОТЕ", t.get("primary")),
        ("done", "✅ ГОТОВО", t.get("success")),
    ]
    grid = CTkFrame(parent, fg_color="transparent")
    grid.pack(fill="both", expand=True)
    for i in range(3):
        grid.grid_columnconfigure(i, weight=1, uniform="kanban")
    grid.grid_rowconfigure(0, weight=1, minsize=500)

    for i, (status, label, color) in enumerate(cols):
        col = CTkFrame(grid, fg_color=t.get("bg2"),
                       corner_radius=8, border_width=2,
                       border_color=color)
        col.grid(row=0, column=i, padx=4, pady=4,
                    sticky="nsew")
        col.grid_propagate(False)
        CTkLabel(col, text=label,
                 font=CTkFont(size=13, weight="bold"),
                 text_color=color).pack(pady=(8, 4))
        ccol = CTkScrollableFrame(col, fg_color="transparent")
        ccol.pack(fill="both", expand=True, padx=6,
                    pady=(0, 6))
        for task in tasks:
            if task.get("status") != status:
                continue
            card = CTkFrame(ccol, fg_color=t.get("bg3"),
                             corner_radius=4)
            card.pack(fill="x", pady=2)
            CTkLabel(card,
                     text=truncate(task.get("title", ""), 40),
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("text"),
                     anchor="w", wraplength=200,
                     justify="left"
                     ).pack(anchor="w", padx=8,
                            pady=(6, 2))
            if status == "pending":
                CTkButton(
                    card, text="→ Начать", height=22,
                    font=CTkFont(size=10),
                    fg_color=t.get("primary"),
                    text_color=t.get("bg"),
                    command=lambda tid=task["id"]: (
                        TaskManager.set_status(
                            tid, "in_progress",
                            app.current_user),
                        app._refresh_page())
                ).pack(anchor="e", padx=6, pady=(0, 6))
            elif status == "in_progress":
                CTkButton(
                    card, text="✅ Готово", height=22,
                    font=CTkFont(size=10),
                    fg_color=t.get("success"),
                    text_color=t.get("bg"),
                    command=lambda tid=task["id"]: (
                        TaskManager.complete(
                            tid, app.current_user),
                        app._refresh_page())
                ).pack(anchor="e", padx=6, pady=(0, 6))


def _render_gantt(app: Any, parent: Any) -> None:
    import tkinter as tk
    t = _t()
    tasks = TaskManager.get_by_deadline(app.current_user)
    if not tasks:
        empty = app._section_card(
            parent, "📅 Нет задач с дедлайнами",
            t.get("card_border"))
        CTkLabel(empty,
                 text="Укажи дедлайн (YYYY-MM-DD) при "
                      "создании задачи",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
        return
    card = CTkFrame(parent, fg_color=t.get("bg2"),
                     corner_radius=8, border_width=2,
                     border_color=t.get("primary"))
    card.pack(fill="both", expand=True,
               pady=UI_PAD_INNER)
    CTkLabel(card,
             text="📅 GANTT-LITE (по дедлайну)",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("primary")
             ).pack(anchor="w", padx=UI_PAD_CARD,
                    pady=(12, 8))
    canvas = tk.Canvas(card, bg=t.get("bg3"),
                        highlightthickness=0, height=400)
    canvas.pack(fill="both", expand=True,
                 padx=UI_PAD_CARD,
                 pady=(0, UI_PAD_CARD))
    try:
        dates = [parse_iso(x.get("deadline", ""))
                 for x in tasks]
        dates = [d for d in dates if d]
        if not dates:
            return
        d_min = min(dates).date()
        d_max = max(dates).date()
        span = max(1, (d_max - d_min).days)
        W = 800
        H = min(600, 40 * len(tasks) + 40)
        canvas.configure(height=H)
        canvas.create_text(
            20, 15, anchor="w",
            text=f"📅 {d_min} → {d_max}",
            fill=t.get("text"),
            font=("Segoe UI", 11, "bold"))
        for i, task in enumerate(tasks):
            dt = parse_iso(task.get("deadline", ""))
            if not dt:
                continue
            day = (dt.date() - d_min).days
            y = 40 + i * 40
            pct = day / span
            x = 20 + int(pct * (W - 100))
            pcolor = {
                "critical": t.get("error"),
                "high": t.get("error"),
                "normal": t.get("warning"),
                "low": t.get("success")}.get(
                task.get("priority", "normal"),
                t.get("warning"))
            canvas.create_text(
                20, y + 10, anchor="w",
                text=truncate(task.get("title", ""), 30),
                fill=t.get("text"),
                font=("Segoe UI", 10))
            canvas.create_rectangle(
                x, y, x + 60, y + 20,
                fill=pcolor, outline="")
            canvas.create_text(
                x + 30, y + 10, anchor="center",
                text=dt.strftime("%d.%m"),
                fill=t.get("bg"),
                font=("Segoe UI", 9, "bold"))
    except Exception as e:
        logger.debug(f"gantt: {e}")


def _render_task_card(app: Any, parent: Any,
                       task: Dict[str, Any],
                       is_done: bool) -> None:
    t = _t()
    f = CTkFrame(parent, corner_radius=6,
                 border_width=1,
                 border_color=t.get("card_border"),
                 fg_color=t.get("card"))
    f.pack(fill="x", pady=4)
    pcolor = {
        "critical": t.get("error"),
        "high": t.get("error"),
        "normal": t.get("warning"),
        "low": t.get("success")}.get(
        task.get("priority", "normal"),
        t.get("warning"))
    if not is_done:
        CTkFrame(f, height=3, fg_color=pcolor,
                 corner_radius=0).pack(fill="x")
    top = CTkFrame(f, fg_color="transparent")
    top.pack(fill="x", padx=UI_PAD_CARD, pady=(8, 0))
    picon = {"critical": "🔥", "high": "🔴",
             "normal": "🟡", "low": "🟢"}.get(
        task.get("priority"), "🟡")
    text_color = (t.get("text_dim") if is_done
                   else t.get("text"))
    prefix = "✅" if is_done else "📌"
    CTkLabel(top,
             text=f"{prefix} {task.get('title', '')}",
             font=CTkFont(size=15, weight="bold"),
             text_color=text_color,
             anchor="w").pack(side="left")
    if not is_done:
        CTkLabel(
            top,
            text=f"{picon} "
                 f"{(task.get('priority') or 'normal').upper()}",
            font=CTkFont(size=12, weight="bold"),
            text_color=pcolor).pack(side="right")

    # Прогресс подзадач
    st_done, st_total = SubtaskManager.progress(task["id"])
    if st_total > 0:
        pbar = CTkProgressBar(f, height=6,
                                corner_radius=3,
                                progress_color=pcolor,
                                fg_color=t.get("bg3"))
        pbar.pack(fill="x", padx=UI_PAD_CARD, pady=(4, 0))
        pbar.set(st_done / max(1, st_total))
        CTkLabel(f,
                 text=f"🪜 Подзадачи: {st_done}/{st_total}",
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(2, 0))

    if timer_manager.is_running(task["id"]):
        elapsed = timer_manager.elapsed(task["id"])
        CTkLabel(f,
                 text=f"⏱️ {fmt_duration(elapsed)} (идёт)",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=t.get("success"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(2, 0))

    info = CTkFrame(f, fg_color="transparent")
    info.pack(fill="x", padx=UI_PAD_CARD, pady=(6, 8))
    time_spent = int(task.get("time_spent") or 0)
    time_str = (f" · ⏱️ {fmt_duration(time_spent)}"
                 if time_spent > 0 else "")
    repeat_str = (f" · 🔁 {task.get('repeat')}"
                   if task.get("repeat") else "")
    dl = task.get("deadline", "")
    dl_str = ""
    if dl:
        d = parse_iso(dl)
        if d:
            days_left = (
                d.date() -
                datetime.now(timezone.utc).date()).days
            if days_left < 0:
                dl_str = f" · ⚠️ просрочено на {-days_left}д"
            elif days_left <= 3:
                dl_str = f" · ⏰ через {days_left}д"
            else:
                dl_str = f" · 📅 {days_left}д"

    CTkLabel(info,
             text=f"👤 {task.get('author', '')} | "
                  f"{fmt_dt(task.get('created_at', ''))}"
                  f"{time_str}{repeat_str}{dl_str}",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(side="left")

    btns = CTkFrame(info, fg_color="transparent")
    btns.pack(side="right")
    if not is_done:
        if timer_manager.is_running(task["id"]):
            CTkButton(
                btns, text="⏹", width=40, height=28,
                font=CTkFont(size=11, weight="bold"),
                fg_color=t.get("error"),
                text_color=t.get("bg"),
                command=lambda tid=task["id"]: (
                    timer_manager.stop(tid),
                    app._show_toast("⏱️ Остановлен", "info"),
                    app._refresh_page())
            ).pack(side="left", padx=2)
        else:
            CTkButton(
                btns, text="⏱️", width=40, height=28,
                font=CTkFont(size=11, weight="bold"),
                fg_color=t.get("accent"),
                text_color=t.get("bg"),
                command=lambda tid=task["id"]: (
                    timer_manager.start(tid, app.current_user),
                    level_system.add_xp(
                        app.current_user, "timer_started"),
                    level_system.increment(
                        app.current_user, "timers_started"),
                    app._show_toast("⏱️ Запущен", "success"),
                    app._refresh_page())
            ).pack(side="left", padx=2)
        CTkButton(btns, text="🍅", width=40, height=28,
                  font=CTkFont(size=11),
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=lambda tsk=task:
                      _start_pomodoro_for_task(app, tsk)
                  ).pack(side="left", padx=1)
        CTkButton(btns, text="🪜", width=34, height=28,
                  font=CTkFont(size=11),
                  fg_color="transparent",
                  text_color=t.get("accent"),
                  command=lambda tsk=task:
                      app._show_subtasks_dialog(tsk)
                  ).pack(side="left", padx=1)
        CTkButton(btns, text="✅", width=34, height=28,
                  font=CTkFont(size=11),
                  fg_color="transparent",
                  text_color=t.get("success"),
                  command=lambda tsk=task:
                      _complete_task(app, tsk)
                  ).pack(side="left", padx=1)
    CTkButton(btns, text="🗑️", width=34, height=28,
              font=CTkFont(size=11),
              fg_color="transparent",
              text_color=t.get("error"),
              command=lambda tsk=task:
                  _delete_task(app, tsk)
              ).pack(side="left", padx=1)


def _start_pomodoro_for_task(app: Any,
                              task: Dict[str, Any]) -> None:
    if pomodoro.is_active():
        app._show_toast("Pomodoro уже запущен", "warning")
        return
    if pomodoro.start_work(app.current_user, task["id"]):
        if not timer_manager.is_running(task["id"]):
            timer_manager.start(task["id"], app.current_user)
        app._show_toast("🍅 Pomodoro 25 мин!",
                          "success", 4000)
        app._show_pomodoro()


def _complete_task(app: Any, task: Dict[str, Any]) -> None:
    if TaskManager.complete(task["id"], app.current_user):
        app._show_toast("🎉 Выполнено!", "success")
        app._refresh_page()


def _delete_task(app: Any, task: Dict[str, Any]) -> None:
    if not messagebox.askyesno("Удалить",
                                 f"«{task['title']}»?"):
        return
    TaskManager.delete(task["id"], hard=False)
    app._show_toast("🗑️ Удалено (Ctrl+Z — отмена)",
                      "info")

    def undo() -> None:
        try:
            TaskManager.restore(task["id"])
            app._show_toast("✅ Восстановлено", "success")
        except Exception:
            pass

    undo_stack.push(undo, f"task:{task['id']}")
    app._refresh_page()


# ═══════════════════════════════════════════════════════════
# PAGE: TIMERS
# ═══════════════════════════════════════════════════════════
def _page_timers(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    active = timer_manager.get_active()
    app._page_header(
        scroll, "ТАЙМЕРЫ", "⏱️",
        f"активных: {len(active)}",
        buttons=[("🍅 Pomodoro", t.get("error"),
                  app._show_pomodoro),
                 ("🔄", t.get("primary"),
                  app._refresh_page)])

    if not active:
        empty = app._section_card(
            scroll, "⏱️ Нет активных таймеров",
            t.get("card_border"))
        CTkLabel(empty,
                 text="Запусти таймер на любой задаче "
                      "во вкладке ✅ ЗАДАЧИ",
                 font=CTkFont(size=13),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
    else:
        active_card = app._section_card(
            scroll, f"⏱️ АКТИВНЫЕ ({len(active)})",
            t.get("success"))
        for tid, entry in list(active.items()):
            task = TaskManager.get_by_id(tid)
            elapsed = timer_manager.elapsed(tid)
            row = CTkFrame(active_card,
                             fg_color=t.get("bg3"),
                             corner_radius=6)
            row.pack(fill="x", padx=UI_PAD_CARD, pady=3)
            CTkLabel(
                row,
                text=f"⏱️ "
                     f"{truncate(task.get('title', '?') if task else '?', 40)}",
                font=CTkFont(size=13, weight="bold"),
                text_color=t.get("text"),
                anchor="w").pack(side="left",
                                    padx=10, pady=8)
            CTkLabel(row, text=fmt_duration(elapsed),
                     font=CTkFont(size=14, weight="bold"),
                     text_color=t.get("success"),
                     anchor="e").pack(side="left", padx=10)
            CTkButton(row, text="⏹ Стоп", width=80,
                      height=28,
                      font=CTkFont(size=11, weight="bold"),
                      fg_color=t.get("error"),
                      text_color=t.get("bg"),
                      command=lambda tid2=tid: (
                          timer_manager.stop(tid2),
                          app._show_toast("⏱️ Остановлен",
                                            "info"),
                          app._refresh_page())
                      ).pack(side="right", padx=10)

    history_card = app._section_card(
        scroll, "📊 ВРЕМЯ ПО ЗАДАЧАМ", t.get("accent"))
    rows = db.execute(
        "SELECT title, time_spent FROM tasks "
        "WHERE author = ? AND time_spent > 0 "
        "ORDER BY time_spent DESC LIMIT 20",
        (app.current_user,))
    if not rows:
        CTkLabel(history_card, text="Пока нет данных",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
    else:
        for r in rows:
            row = CTkFrame(history_card, fg_color="transparent")
            row.pack(fill="x", padx=UI_PAD_CARD, pady=2)
            CTkLabel(row,
                     text=f"• {truncate(r['title'], 40)}",
                     font=CTkFont(size=11),
                     text_color=t.get("text"),
                     anchor="w").pack(side="left", fill="x",
                                        expand=True)
            CTkLabel(row,
                     text=fmt_duration(
                         int(r["time_spent"] or 0)),
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("success"),
                     anchor="e").pack(side="right")
        CTkFrame(history_card, height=6,
                 fg_color="transparent").pack()


# ═══════════════════════════════════════════════════════════
# PAGE: FORUM
# ═══════════════════════════════════════════════════════════
def _page_forum(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    posts = ForumManager.get_all()
    app._page_header(
        scroll, "ФОРУМ", "💬",
        f"всего: {len(posts)} · Markdown поддерживается",
        buttons=[("🔄", t.get("success"),
                  app._refresh_page)])

    create = app._section_card(
        scroll, "▸ НОВОЕ СООБЩЕНИЕ", t.get("secondary"))
    title_e = CTkEntry(
        create, placeholder_text="📌 Заголовок",
        height=UI_FORM_ENTRY_HEIGHT,
        font=CTkFont(size=14),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    title_e.pack(padx=UI_PAD_CARD, pady=4, fill="x")
    content_e = CTkTextbox(
        create, height=120, font=CTkFont(size=13),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"), wrap="word")
    content_e.pack(padx=UI_PAD_CARD, pady=4, fill="x")
    CTkLabel(create,
             text="💡 Поддерживается: **жирный** "
                  "*курсив* `код` ```блок``` # H1 ## H2",
             font=CTkFont(size=9, slant="italic"),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w", padx=UI_PAD_CARD)

    def add() -> None:
        title = title_e.get().strip()
        content = content_e.get("1.0", "end-1c").strip()
        if not title or not content:
            return
        if ForumManager.create_post(
                title, content, app.current_user,
                extract_tags(title)):
            title_e.delete(0, "end")
            content_e.delete("1.0", "end")
            app._show_toast("✅ Опубликовано", "success")
            app._safe_after(300, app._refresh_page)

    CTkButton(create, text="▶ ОПУБЛИКОВАТЬ", height=36,
              width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=add
              ).pack(pady=(6, UI_PAD_CARD),
                     padx=UI_PAD_CARD, anchor="w")

    for post in posts:
        _render_forum_post(app, scroll, post)


def _render_forum_post(app: Any, parent: Any,
                        post: Dict[str, Any]) -> None:
    t = _t()
    card = CTkFrame(parent, corner_radius=6,
                    border_width=1,
                    border_color=t.get("card_border"),
                    fg_color=t.get("card"))
    card.pack(fill="x", pady=4)
    top = CTkFrame(card, fg_color="transparent")
    top.pack(fill="x", padx=UI_PAD_CARD, pady=(8, 2))
    CTkLabel(top, text=post.get("title", ""),
             font=CTkFont(size=15, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")
    likes = int(post.get("likes", 0))
    liked_by = post.get("liked_by") or []
    is_liked = app.current_user in liked_by
    CTkButton(top,
              text=f"{'❤️' if is_liked else '🤍'} {likes}",
              width=66, height=28,
              font=CTkFont(size=11, weight="bold"),
              fg_color=(t.get("error") if is_liked
                         else "transparent"),
              text_color=(t.get("error") if is_liked
                           else t.get("text_dim")),
              command=lambda: (
                  ForumManager.toggle_like(
                      post["id"], app.current_user),
                  app._refresh_page())
              ).pack(side="right", padx=2)

    md_text = (post.get("content", "") or "")[:800]
    md_box = CTkTextbox(
        card, height=min(220, 60 + len(md_text) // 3),
        font=CTkFont(size=13), fg_color="transparent",
        border_width=0, text_color=t.get("text"),
        wrap="word")
    md_box.pack(fill="x", padx=UI_PAD_CARD, pady=(4, 6))
    md_box.insert("1.0", md_text)
    md_box.configure(state="disabled")

    comments = post.get("comments") or []
    if comments:
        CTkLabel(card,
                 text=f"💬 {len(comments)} комментариев",
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD)
        for cm in comments[-3:]:
            CTkLabel(card,
                     text=f"  {cm.get('author', '?')}: "
                          f"{truncate(cm.get('text', ''), 60)}",
                     font=CTkFont(size=10),
                     text_color=t.get("text_dim"),
                     anchor="w",
                     wraplength=UI_WRAP_MED
                     ).pack(anchor="w", fill="x",
                            padx=UI_PAD_CARD, pady=1)

    bottom = CTkFrame(card, fg_color="transparent")
    bottom.pack(fill="x", padx=UI_PAD_CARD, pady=(4, 8))
    CTkLabel(bottom,
             text=f"👤 {post.get('author', '')} | "
                  f"{fmt_dt(post.get('created_at', ''))}",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(side="left")

    is_owner = (post.get("author") == app.current_user)

    # Кнопки автора
    if is_owner:
        # Удалить
        CTkButton(
            bottom, text="🗑️ Удалить",
            width=100, height=26,
            font=CTkFont(size=10, weight="bold"),
            fg_color="transparent", border_width=1,
            border_color=t.get("error"),
            text_color=t.get("error"),
            command=lambda p=post: _delete_post(app, p)
        ).pack(side="right", padx=2)

        # Редактировать
        CTkButton(
            bottom, text="✏️ Редактировать",
            width=130, height=26,
            font=CTkFont(size=10, weight="bold"),
            fg_color="transparent", border_width=1,
            border_color=t.get("warning"),
            text_color=t.get("warning"),
            command=lambda p=post: _edit_post_dialog(app, p)
        ).pack(side="right", padx=2)

        # Закрепить / открепить
        pin_label = ("📌 Открепить" if post.get("pinned")
                     else "📌 Закрепить")
        CTkButton(
            bottom, text=pin_label,
            width=110, height=26,
            font=CTkFont(size=10, weight="bold"),
            fg_color=("transparent" if not post.get("pinned")
                       else t.get("warning")),
            border_width=1,
            border_color=t.get("accent"),
            text_color=(t.get("accent")
                         if not post.get("pinned")
                         else t.get("bg")),
            command=lambda p=post: _toggle_pin(app, p)
        ).pack(side="right", padx=2)

    # Комментировать — доступно всем
    CTkButton(
        bottom, text="💬 Комментировать",
        width=160, height=26,
        font=CTkFont(size=10, weight="bold"),
        fg_color="transparent", border_width=1,
        border_color=t.get("primary"),
        text_color=t.get("primary"),
        command=lambda p=post: _add_comment_dialog(app, p)
    ).pack(side="right", padx=2)


def _add_comment_dialog(app: Any, post: Dict[str, Any]) -> None:
    t = _t()
    win = CTkToplevel(app)
    win.title(f"💬 {post.get('title', '')}")
    win.geometry("560x520")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())
    CTkLabel(win, text="💬 КОММЕНТАРИИ",
             font=CTkFont(size=18, weight="bold"),
             text_color=t.get("primary")
             ).pack(pady=(15, 4))
    CTkLabel(win, text=truncate(post.get("title", ""), 50),
             font=CTkFont(size=12),
             text_color=t.get("text_dim")
             ).pack(pady=(0, 10))
    add_row = CTkFrame(win, fg_color="transparent")
    add_row.pack(fill="x", padx=20, pady=(0, 8))
    new_e = CTkEntry(add_row,
                      placeholder_text="Ваш комментарий...",
                      height=36, font=CTkFont(size=12),
                      fg_color=t.get("bg3"),
                      border_color=t.get("card_border"),
                      text_color=t.get("text"))
    new_e.pack(side="left", fill="x", expand=True,
                padx=(0, 6))
    scroll = CTkScrollableFrame(win, fg_color=t.get("bg2"))
    scroll.pack(fill="both", expand=True, padx=20,
                 pady=(0, 15))

    def render() -> None:
        for w in scroll.winfo_children():
            w.destroy()
        pd = ForumManager.get_by_id(post["id"])
        comments = (pd or {}).get("comments") or []
        if not comments:
            CTkLabel(scroll, text="Пока нет комментариев",
                     font=CTkFont(size=12),
                     text_color=t.get("text_dim")
                     ).pack(pady=20)
            return
        for cm in comments:
            row = CTkFrame(scroll, fg_color=t.get("bg3"),
                             corner_radius=6)
            row.pack(fill="x", pady=3)

            top = CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 2))
            CTkLabel(top,
                     text=f"👤 {cm.get('author', '?')} "
                          f"· {fmt_dt(cm.get('created_at', ''), '%d.%m %H:%M')}"
                          + (" (изм.)" if cm.get("edited_at") else ""),
                     font=CTkFont(size=10, weight="bold"),
                     text_color=t.get("accent"),
                     anchor="w").pack(side="left")

            # Кнопки для автора комментария
            if cm.get("author") == app.current_user:
                CTkButton(
                    top, text="🗑️", width=28, height=24,
                    font=CTkFont(size=10),
                    fg_color="transparent",
                    text_color=t.get("error"),
                    command=lambda cid=cm.get("id"):
                        _delete_comment(app, post, cid, render)
                ).pack(side="right", padx=1)
                CTkButton(
                    top, text="✏️", width=28, height=24,
                    font=CTkFont(size=10),
                    fg_color="transparent",
                    text_color=t.get("warning"),
                    command=lambda cid=cm.get("id"),
                        txt=cm.get("text", ""):
                        _edit_comment_dialog(
                            app, post, cid, txt, render)
                ).pack(side="right", padx=1)

            CTkLabel(row, text=cm.get("text", ""),
                     font=CTkFont(size=12),
                     text_color=t.get("text"),
                     anchor="w", justify="left",
                     wraplength=460
                     ).pack(anchor="w", fill="x", padx=10,
                            pady=(0, 6))

    def add_comment() -> None:
        text = new_e.get().strip()
        if not text:
            return
        if ForumManager.add_comment(
                post["id"], app.current_user, text):
            new_e.delete(0, "end")
            render()
            app._show_toast("💬 +3 XP", "success")

    CTkButton(add_row, text="➤", width=44, height=36,
              font=CTkFont(size=14, weight="bold"),
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=add_comment).pack(side="right")
    new_e.bind("<Return>", lambda e: add_comment())
    render()

# ═══════════════════════════════════════════════════════════
# FORUM: удалить / редактировать / закрепить
# ═══════════════════════════════════════════════════════════
def _delete_post(app: Any, post: Dict[str, Any]) -> None:
    """Удалить пост с подтверждением."""
    title = truncate(post.get("title", ""), 50)
    if not messagebox.askyesno(
            "Удалить пост?",
            f"Удалить «{title}»?\n\n"
            f"Пост будет помечен как удалённый. "
            f"Восстановить нельзя."):
        return
    if ForumManager.delete_post(post["id"], app.current_user):
        app._show_toast("🗑️ Пост удалён", "success", 2000)
        app._safe_after(300, app._refresh_page)
    else:
        app._show_toast("❌ Ошибка удаления", "error")


def _toggle_pin(app: Any, post: Dict[str, Any]) -> None:
    """Закрепить/открепить пост."""
    result = ForumManager.toggle_pin(post["id"], app.current_user)
    if result is None:
        app._show_toast("❌ Нет прав", "error")
        return
    if result:
        app._show_toast("📌 Пост закреплён", "success", 1500)
    else:
        app._show_toast("📌 Пост откреплён", "info", 1500)
    app._safe_after(300, app._refresh_page)


def _edit_post_dialog(app: Any, post: Dict[str, Any]) -> None:
    """Диалог редактирования поста."""
    t = _t()
    win = CTkToplevel(app)
    win.title(f"✏️ Редактировать: {truncate(post.get('title', ''), 40)}")
    win.geometry("640x600")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())

    CTkLabel(win, text="✏️ РЕДАКТИРОВАНИЕ ПОСТА",
             font=CTkFont(size=18, weight="bold"),
             text_color=t.get("warning")
             ).pack(pady=(15, 4))
    CTkLabel(win,
             text=f"Автор: {post.get('author', '')}",
             font=CTkFont(size=11),
             text_color=t.get("text_dim")
             ).pack(pady=(0, 12))

    CTkLabel(win, text="📌 Заголовок:",
             font=CTkFont(size=12, weight="bold"),
             text_color=t.get("text_glow"),
             anchor="w").pack(anchor="w", padx=20, pady=(8, 3))
    title_e = CTkEntry(win, height=36, font=CTkFont(size=13),
                        fg_color=t.get("bg3"),
                        border_color=t.get("card_border"),
                        text_color=t.get("text"))
    title_e.pack(fill="x", padx=20, pady=(0, 8))
    title_e.insert(0, post.get("title", ""))
    title_e.focus()

    CTkLabel(win, text="📝 Содержимое:",
             font=CTkFont(size=12, weight="bold"),
             text_color=t.get("text_glow"),
             anchor="w").pack(anchor="w", padx=20, pady=(8, 3))
    content_e = CTkTextbox(win, height=250,
                            font=CTkFont(size=13),
                            fg_color=t.get("bg3"),
                            border_color=t.get("card_border"),
                            text_color=t.get("text"),
                            wrap="word")
    content_e.pack(fill="both", expand=True, padx=20, pady=(0, 8))
    content_e.insert("1.0", post.get("content", ""))

    msg = CTkLabel(win, text="", font=CTkFont(size=11),
                    text_color=t.get("error"))
    msg.pack(pady=4)

    def save() -> None:
        new_title = title_e.get().strip()
        new_content = content_e.get("1.0", "end-1c").strip()
        if not new_title or not new_content:
            msg.configure(text="Заголовок и контент обязательны")
            return
        ok = ForumManager.update_post(
            post["id"], app.current_user,
            title=new_title, content=new_content)
        if ok:
            app._show_toast("✅ Пост обновлён", "success")
            win.destroy()
            app._safe_after(300, app._refresh_page)
        else:
            msg.configure(text="❌ Ошибка сохранения")

    bf = CTkFrame(win, fg_color="transparent")
    bf.pack(fill="x", padx=20, pady=(6, 15))
    CTkButton(bf, text="💾 Сохранить", height=38, width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=save).pack(side="left")
    CTkButton(bf, text="❌ Отмена", height=38, width=140,
              fg_color="transparent", border_width=1,
              border_color=t.get("error"),
              text_color=t.get("error"),
              command=win.destroy).pack(side="left", padx=6)

    win.bind("<Control-Return>", lambda e: save())


# ═══════════════════════════════════════════════════════════
# PAGE: PASSWORDS
# ═══════════════════════════════════════════════════════════
def _page_passwords(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(
        scroll, "ХРАНИЛИЩЕ ПАРОЛЕЙ", "🔑",
        "AES-256-GCM · PBKDF2 · auto-lock 15м")

    from nexus.crypto import CryptoManager, check_pwned
    if not CryptoManager.is_available():
        warn = app._section_card(
            scroll, "⚠️ cryptography не установлена",
            t.get("warning"))
        CTkLabel(warn, text="pip install cryptography",
                 font=CTkFont(size=12),
                 text_color=t.get("warning")
                 ).pack(pady=(0, UI_PAD_CARD))
        return

    # ── Master password ──────────────────────────────
    mp_card = app._section_card(
        scroll, "🔐 МАСТЕР-ПАРОЛЬ", t.get("warning"))
    unlocked = vault_session.is_unlocked
    CTkLabel(mp_card,
             text=("✅ Разблокировано · auto-lock 15 мин"
                   if unlocked else "🔒 Заблокировано"),
             font=CTkFont(size=12, weight="bold"),
             text_color=(t.get("success") if unlocked
                          else t.get("warning")),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(0, 4))
    mp_e = CTkEntry(mp_card,
                     placeholder_text="Мастер-пароль",
                     height=UI_FORM_ENTRY_HEIGHT,
                     font=CTkFont(size=14), show="•",
                     fg_color=t.get("bg3"),
                     border_color=t.get("card_border"),
                     text_color=t.get("text"))
    mp_e.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    def do_unlock() -> None:
        mp = mp_e.get().strip()
        if not mp:
            app._show_toast("Введите пароль", "warning")
            return
        ok, msg = vault_session.unlock(mp, allow_create=True)
        if ok:
            app._show_toast(f"✅ {msg}", "success")
            app._refresh_page()
        else:
            app._show_toast(f"❌ {msg}", "error")

    def do_lock() -> None:
        vault_session.lock()
        app._show_toast("🔒 Заблокировано", "info")
        app._refresh_page()

    row = CTkFrame(mp_card, fg_color="transparent")
    row.pack(fill="x", padx=UI_PAD_CARD,
              pady=(6, UI_PAD_CARD))
    CTkButton(row, text="🔓 Открыть", height=38,
              width=140,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=do_unlock).pack(side="left")
    if unlocked:
        CTkButton(row, text="🔒 Заблокировать", height=38,
                  width=160,
                  fg_color=t.get("warning"),
                  text_color=t.get("bg"),
                  command=do_lock).pack(side="left", padx=6)
    mp_e.bind("<Return>", lambda e: do_unlock())

    if not unlocked:
        info = app._section_card(
            scroll, "ℹ️ ИНФО", t.get("text_dim"))
        CTkLabel(info,
                 text="• Хранилище создаётся автоматически "
                      "при первом вводе пароля\n"
                      "• Auto-lock 15 минут без активности",
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim"),
                 anchor="w", justify="left"
                 ).pack(anchor="w", padx=UI_PAD_CARD,
                        pady=(0, UI_PAD_CARD))
        return

    # ── Add entry ────────────────────────────────────
    add_card = app._section_card(
        scroll, "➕ НОВАЯ ЗАПИСЬ", t.get("secondary"))
    svc_e = CTkEntry(add_card, placeholder_text="Сервис",
                      height=UI_FORM_ENTRY_HEIGHT,
                      font=CTkFont(size=14),
                      fg_color=t.get("bg3"),
                      border_color=t.get("card_border"),
                      text_color=t.get("text"))
    svc_e.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    login_e = CTkEntry(add_card, placeholder_text="Логин",
                        height=UI_FORM_ENTRY_HEIGHT,
                        font=CTkFont(size=14),
                        fg_color=t.get("bg3"),
                        border_color=t.get("card_border"),
                        text_color=t.get("text"))
    login_e.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    pwd_row = CTkFrame(add_card, fg_color="transparent")
    pwd_row.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    pwd_e = CTkEntry(pwd_row, placeholder_text="Пароль",
                      height=UI_FORM_ENTRY_HEIGHT,
                      font=CTkFont(size=14), show="•",
                      fg_color=t.get("bg3"),
                      border_color=t.get("card_border"),
                      text_color=t.get("text"))
    pwd_e.pack(side="left", fill="x", expand=True,
                padx=(0, 6))

    def gen_password() -> None:
        pw = PasswordVault.generate_password(16, True)
        pwd_e.delete(0, "end")
        pwd_e.insert(0, pw)
        pwd_e.configure(show="")
        app._show_toast("🎲 Сгенерировано", "info", 2000)

    CTkButton(pwd_row, text="🎲", width=44, height=38,
              font=CTkFont(size=14, weight="bold"),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=gen_password).pack(side="right")

    def save_entry() -> None:
        svc = svc_e.get().strip()
        pwd = pwd_e.get()
        if not svc or not pwd:
            app._show_toast("Заполните поля", "warning")
            return
        entry = vault_session.add(
            service=svc,
            login=login_e.get().strip(),
            password=pwd,
            user=app.current_user)
        if entry:
            app._show_toast("🔐 Сохранено", "success")
            svc_e.delete(0, "end")
            login_e.delete(0, "end")
            pwd_e.delete(0, "end")
            app._refresh_page()

    CTkButton(add_card, text="💾 СОХРАНИТЬ", height=38,
              width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=save_entry
              ).pack(pady=(6, UI_PAD_CARD),
                     padx=UI_PAD_CARD, anchor="w")

    # ── List entries ─────────────────────────────────
    items = vault_session.items
    list_card = app._section_card(
        scroll, f"📋 ЗАПИСИ ({len(items)})",
        t.get("text_glow"))
    if not items:
        CTkLabel(list_card, text="Пока пусто",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
    else:
        for item in items:
            card = CTkFrame(list_card,
                             fg_color=t.get("bg3"),
                             corner_radius=6)
            card.pack(fill="x", pady=3)
            row2 = CTkFrame(card, fg_color="transparent")
            row2.pack(fill="x", padx=10, pady=(6, 2))
            CTkLabel(row2,
                     text=f"🔑 {item.get('service', '')}",
                     font=CTkFont(size=13, weight="bold"),
                     text_color=t.get("primary"),
                     anchor="w").pack(side="left")
            lv = item.get("login", "")
            if lv:
                CTkLabel(row2, text=f"· {lv}",
                         font=CTkFont(size=10),
                         text_color=t.get("text_dim"),
                         anchor="w").pack(side="left",
                                            padx=6)
            bottom = CTkFrame(card, fg_color="transparent")
            bottom.pack(fill="x", padx=10, pady=(0, 6))
            pwd = item.get("password", "")
            pwd_lbl = CTkLabel(
                bottom, text="•" * min(len(pwd), 20),
                font=CTkFont(family="Courier", size=12),
                text_color=t.get("warning"),
                anchor="w")
            pwd_lbl.pack(side="left")

            def toggle_pwd(lbl: Any = pwd_lbl,
                            p: str = pwd) -> None:
                current = lbl.cget("text")
                hidden = "•" * min(len(p), 20)
                lbl.configure(text=p if current == hidden
                               else hidden)

            CTkButton(bottom, text="👁", width=30, height=24,
                      font=CTkFont(size=10),
                      fg_color="transparent",
                      text_color=t.get("primary"),
                      command=toggle_pwd
                      ).pack(side="left", padx=2)
            CTkButton(bottom, text="📋", width=30, height=24,
                      font=CTkFont(size=10),
                      fg_color="transparent",
                      text_color=t.get("primary"),
                      command=lambda p=pwd: (
                          copy_to_clipboard(p),
                          app._show_toast("📋 Скопировано",
                                            "success"))
                      ).pack(side="left", padx=2)
            if app.config_data.hibp_check_enabled and pwd:
                CTkButton(
                    bottom, text="🛡️", width=30, height=24,
                    font=CTkFont(size=10),
                    fg_color="transparent",
                    text_color=t.get("accent"),
                    command=lambda p=pwd:
                        _check_hibp(app, p)
                ).pack(side="left", padx=2)
            CTkButton(bottom, text="🗑️", width=30, height=24,
                      font=CTkFont(size=10),
                      fg_color="transparent",
                      text_color=t.get("error"),
                      command=lambda iid=item.get("id"): (
                          vault_session.delete(iid),
                          app._show_toast("🗑️ Удалено",
                                            "info"),
                          app._refresh_page())
                      ).pack(side="right", padx=2)


def _check_hibp(app: Any, password: str) -> None:
    from nexus.crypto import check_pwned
    app._show_toast("⏳ HIBP...", "info", 2000)

    def work() -> None:
        pwned = check_pwned(password, timeout=3)
        app._safe_after(
            0, app._show_toast,
            ("⚠️ Пароль найден в утечках!" if pwned
             else "✅ Не найден в утечках"),
            "warning" if pwned else "success", 4000)
    executor.submit_safe(work)


# ═══════════════════════════════════════════════════════════
# PAGE: ENCRYPT
# ═══════════════════════════════════════════════════════════
def _page_encrypt(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(scroll, "ШИФРОВАНИЕ", "🔐",
                      "AES-256-GCM + PBKDF2")

    from nexus.crypto import CryptoManager
    if not CryptoManager.is_available():
        warn = app._section_card(
            scroll, "⚠️ cryptography не установлена",
            t.get("warning"))
        CTkLabel(warn, text="pip install cryptography",
                 font=CTkFont(size=12),
                 text_color=t.get("warning")
                 ).pack(pady=(0, UI_PAD_CARD))
        return

    # ── Encrypt ──────────────────────────────────────
    enc_card = app._section_card(
        scroll, "🔒 ЗАШИФРОВАТЬ", t.get("warning"))
    enc_in = CTkTextbox(
        enc_card, height=100, font=CTkFont(size=12),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"), wrap="word")
    enc_in.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    enc_pwd = CTkEntry(
        enc_card, placeholder_text="Пароль",
        height=UI_FORM_ENTRY_HEIGHT, show="•",
        font=CTkFont(size=14),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    enc_pwd.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    enc_out = CTkTextbox(
        enc_card, height=80,
        font=CTkFont(family="Courier", size=10),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("success"), wrap="word")
    enc_out.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    def do_encrypt() -> None:
        text = enc_in.get("1.0", "end-1c")
        pwd = enc_pwd.get()
        if not text or not pwd:
            return
        result = CryptoManager.encrypt_text(text, pwd)
        if result:
            enc_out.delete("1.0", "end")
            enc_out.insert("1.0", result)
            app._show_toast("🔐 Зашифровано", "success")

    btn_row = CTkFrame(enc_card, fg_color="transparent")
    btn_row.pack(fill="x", padx=UI_PAD_CARD,
                  pady=(6, UI_PAD_CARD))
    CTkButton(btn_row, text="🔒 Зашифровать",
              height=38, width=180,
              fg_color=t.get("warning"),
              text_color=t.get("bg"),
              command=do_encrypt).pack(side="left")
    CTkButton(btn_row, text="📋 Копировать",
              height=38, width=180,
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=lambda: (
                  copy_to_clipboard(
                      enc_out.get("1.0", "end-1c")),
                  app._show_toast("📋 Скопировано",
                                    "success"))
              ).pack(side="left", padx=6)

    # ── Decrypt ──────────────────────────────────────
    dec_card = app._section_card(
        scroll, "🔓 РАСШИФРОВАТЬ", t.get("success"))
    dec_in = CTkTextbox(
        dec_card, height=80,
        font=CTkFont(family="Courier", size=10),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"), wrap="word")
    dec_in.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    dec_pwd = CTkEntry(
        dec_card, placeholder_text="Пароль",
        height=UI_FORM_ENTRY_HEIGHT, show="•",
        font=CTkFont(size=14),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    dec_pwd.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    dec_out = CTkTextbox(
        dec_card, height=100, font=CTkFont(size=12),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("success"), wrap="word")
    dec_out.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    def do_decrypt() -> None:
        text = (dec_in.get("1.0", "end-1c").strip()
                 .strip('"').strip("'"))
        pwd = dec_pwd.get()
        if not text or not pwd:
            return
        result = CryptoManager.decrypt_text(text, pwd)
        if result:
            dec_out.delete("1.0", "end")
            dec_out.insert("1.0", result)
            app._show_toast("🔓 Расшифровано", "success")
        else:
            app._show_toast("❌ Неверный пароль", "error")

    CTkButton(dec_card, text="🔓 Расшифровать",
              height=38, width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=do_decrypt
              ).pack(pady=(6, UI_PAD_CARD),
                     padx=UI_PAD_CARD, anchor="w")


# ═══════════════════════════════════════════════════════════
# PAGE: BLOCKCHAIN
# ═══════════════════════════════════════════════════════════
def _page_blockchain(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    stats = BlockchainManager.stats()
    merkle = stats.get("merkle_root", "")[:16]
    app._page_header(
        scroll, "БЛОКЧЕЙН", "⛓️",
        f"активных: {stats['active']} · "
        f"удалено: {stats['deleted']} · "
        f"contracts: {stats['contracts']} · "
        f"Merkle: {merkle}…",
        buttons=[
            ("✔ Проверить", t.get("success"),
             lambda: _verify_chain(app)),
            ("📤 Экспорт", t.get("accent"),
             lambda: _export_chain(app)),
            ("📥 Импорт", t.get("warning"),
             lambda: _import_chain(app)),
            ("🔄", t.get("primary"), app._refresh_page)])

    # ── Форма добавления ─────────────────────────────
    form = app._section_card(
        scroll, "✏️ НОВЫЙ БЛОК", t.get("secondary"))
    data_e = CTkTextbox(form, height=70,
                         font=CTkFont(size=13),
                         fg_color=t.get("bg3"),
                         border_color=t.get("card_border"),
                         text_color=t.get("text"),
                         wrap="word")
    data_e.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    tags_row = CTkFrame(form, fg_color="transparent")
    tags_row.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    CTkLabel(tags_row, text="🏷️ Теги:",
             font=CTkFont(size=11, weight="bold"),
             text_color=t.get("text_glow"),
             anchor="w").pack(side="left", padx=(0, 6))
    tags_e = CTkEntry(tags_row, placeholder_text="#тег1 #тег2",
                       height=32, font=CTkFont(size=12),
                       fg_color=t.get("bg3"),
                       border_color=t.get("card_border"),
                       text_color=t.get("text"))
    tags_e.pack(side="left", fill="x", expand=True)

    contract_row = CTkFrame(form, fg_color="transparent")
    contract_row.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    CTkLabel(contract_row, text="🤖 Contract-lite:",
             font=CTkFont(size=11, weight="bold"),
             text_color=t.get("text_glow"),
             anchor="w").pack(side="left", padx=(0, 6))
    contract_e = CTkEntry(
        contract_row,
        placeholder_text='например: amount > 100',
        height=32, font=CTkFont(size=11),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    contract_e.pack(side="left", fill="x", expand=True)
    CTkLabel(form,
             text="💡 Переменные: amount, tag, date, sender, "
                  "count; операторы ==, !=, <, >, <=, >=, "
                  "and, or, not, in",
             font=CTkFont(size=9, slant="italic"),
             text_color=t.get("text_dim"),
             anchor="w").pack(anchor="w", padx=UI_PAD_CARD)

    def add_block() -> None:
        data = data_e.get("1.0", "end-1c").strip()
        if not data:
            app._show_toast("Введите данные", "warning")
            return
        tags = extract_tags(tags_e.get())
        contract = contract_e.get().strip()
        if BlockchainManager.add_block(
                data, app.current_user, tags, contract):
            data_e.delete("1.0", "end")
            tags_e.delete(0, "end")
            contract_e.delete(0, "end")
            app._show_toast("⛓️ +20 XP", "success")
            app._refresh_page()

    CTkButton(form, text="➕ ДОБАВИТЬ БЛОК", height=36,
              width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=add_block
              ).pack(pady=(6, UI_PAD_CARD),
                     padx=UI_PAD_CARD, anchor="w")

    # ── Список блоков ────────────────────────────────
    blocks = BlockchainManager.get_all()
    if not blocks:
        empty = app._section_card(
            scroll, "⛓️ Цепь пуста", t.get("card_border"))
        CTkLabel(empty, text="Добавьте первый блок!",
                 font=CTkFont(size=13),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, UI_PAD_CARD))
        return

    _BATCH = UI_VIRTUAL_BATCH_SIZE
    _state = {"shown": 0}
    _container = CTkFrame(scroll, fg_color="transparent")
    _container.pack(fill="x")
    _rev = list(reversed(blocks))

    def show_more() -> None:
        start = _state["shown"]
        end = min(len(_rev), start + _BATCH)
        for block in _rev[start:end]:
            _render_block_card(app, _container, block)
        _state["shown"] = end
        if end < len(_rev):
            remain = len(_rev) - end
            CTkButton(
                _container,
                text=f"⬇ Показать ещё {_BATCH} "
                     f"(осталось {remain})",
                height=36, width=320,
                font=CTkFont(size=12, weight="bold"),
                fg_color=t.get("accent"),
                text_color=t.get("bg"),
                command=show_more
            ).pack(pady=6)

    show_more()


def _render_block_card(app: Any, parent: Any,
                        block: Dict[str, Any]) -> None:
    t = _t()
    is_owner = (block.get("sender") == app.current_user)
    card = CTkFrame(parent, corner_radius=6,
                     border_width=1,
                     border_color=t.get("card_border"),
                     fg_color=t.get("card"))
    card.pack(fill="x", pady=4)
    top = CTkFrame(card, fg_color="transparent")
    top.pack(fill="x", padx=UI_PAD_CARD, pady=(8, 2))
    CTkLabel(top, text=f"⛓️ {block.get('id', '')[:12]}…",
             font=CTkFont(family="Courier", size=11,
                           weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")
    CTkLabel(top, text=f"👤 {block.get('sender', '')}",
             font=CTkFont(size=11),
             text_color=t.get("text_dim")
             ).pack(side="right")

    data_text = str(block.get("data", ""))
    CTkLabel(card, text=data_text[:500],
             wraplength=UI_WRAP_MED, justify="left",
             font=CTkFont(size=12),
             text_color=t.get("text"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(4, 6), fill="x")

    if block.get("contract"):
        ct_card = CTkFrame(card, fg_color=t.get("bg3"),
                             corner_radius=4)
        ct_card.pack(fill="x", padx=UI_PAD_CARD,
                      pady=(0, 4))
        CTkLabel(ct_card, text=f"🤖 {block.get('contract')}",
                 font=CTkFont(family="Courier", size=10),
                 text_color=t.get("accent"),
                 anchor="w").pack(anchor="w", padx=8, pady=4)

    info = CTkFrame(card, fg_color="transparent")
    info.pack(fill="x", padx=UI_PAD_CARD, pady=(0, 8))
    CTkLabel(info,
             text=f"📅 {fmt_dt(block.get('timestamp', ''))}"
                  f" · 🔐 "
                  f"{(block.get('hash') or '')[:16]}…",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="w").pack(side="left")
    btns = CTkFrame(info, fg_color="transparent")
    btns.pack(side="right")
    CTkButton(btns, text="📋", width=30, height=26,
              font=CTkFont(size=10),
              fg_color="transparent",
              text_color=t.get("primary"),
              command=lambda d=data_text: (
                  copy_to_clipboard(d),
                  app._show_toast("📋 Скопировано",
                                    "success"))
              ).pack(side="left", padx=1)
    CTkButton(btns, text="🧪", width=30, height=26,
              font=CTkFont(size=10),
              fg_color="transparent",
              text_color=t.get("accent"),
              command=lambda b=block:
                  _test_contract(app, b)
              ).pack(side="left", padx=1)
    if is_owner:
        CTkButton(btns, text="🗑️", width=30, height=26,
                  font=CTkFont(size=10),
                  fg_color="transparent",
                  text_color=t.get("error"),
                  command=lambda b=block: (
                      _delete_block(app, b))
                  ).pack(side="left", padx=1)


def _verify_chain(app: Any) -> None:
    ok, msg = BlockchainManager.verify_chain()
    app._show_toast(msg, "success" if ok else "error", 6000)


def _export_chain(app: Any) -> None:
    data = BlockchainManager.export_chain()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = filedialog.asksaveasfilename(
        defaultextension=".json",
        initialfile=f"blockchain_{ts}.json",
        filetypes=[("JSON", "*.json")])
    if not fp:
        return
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        app._show_toast(f"📤 {os.path.basename(fp)}",
                          "success")
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _import_chain(app: Any) -> None:
    fp = filedialog.askopenfilename(
        filetypes=[("JSON", "*.json")])
    if not fp:
        return
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")
        return
    if not messagebox.askyesno("Импорт",
                                 "Merge (добавить)?"):
        return
    ok, m = BlockchainManager.import_chain(
        data, app.current_user, mode="merge")
    app._show_toast(
        f"{'✅' if ok else '❌'} {m}",
        "success" if ok else "error")
    if ok:
        app._refresh_page()


def _delete_block(app: Any, block: Dict[str, Any]) -> None:
    if not messagebox.askyesno("Удалить?",
                                 "Пометить как удалённый?"):
        return
    ok, m = BlockchainManager.delete_block(
        block["id"], app.current_user, hard=False)
    app._show_toast(
        f"{'🗑️' if ok else '❌'} {m}",
        "warning" if ok else "error")
    if ok:
        app._refresh_page()


def _test_contract(app: Any, block: Dict[str, Any]) -> None:
    expr = block.get("contract", "")
    if not expr:
        app._show_toast("У блока нет contract", "info")
        return
    ctx = {
        "amount": 150, "tag": "test",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "sender": block.get("sender", ""),
        "count": 1,
    }
    matched, msg = BlockchainManager.evaluate_contract(
        block, ctx)
    text = (f"🤖 Contract: {expr}\n"
            f"Context: amount=150, tag=test, "
            f"date={ctx['date']}\n"
            f"Result: "
            f"{'✅ matched' if matched else '❌ no'}\n"
            f"Message: {msg}")
    t = _t()
    win = CTkToplevel(app)
    win.title("🧪 Тест contract")
    win.geometry("500x300")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    CTkLabel(win, text="🧪 РЕЗУЛЬТАТ",
             font=CTkFont(size=16, weight="bold"),
             text_color=t.get("primary")
             ).pack(pady=(15, 6))
    box = CTkTextbox(win,
                      font=CTkFont(family="Courier", size=11),
                      fg_color=t.get("bg3"),
                      text_color=t.get("text"))
    box.pack(fill="both", expand=True, padx=20, pady=10)
    box.insert("1.0", text)
    box.configure(state="disabled")
    CTkButton(win, text="OK", width=120, height=34,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=win.destroy).pack(pady=(0, 15))


# ═══════════════════════════════════════════════════════════
# PAGE: ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════
def _page_achievements(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    all_ach = achievement_system.get_all(include_hidden=False)
    user_ach = set(
        achievement_system.get_user_achievements(
            app.current_user))
    total, unlocked, points = \
        achievement_system.get_progress(app.current_user)
    app._page_header(
        scroll, "ДОСТИЖЕНИЯ", "🏆",
        f"{unlocked}/{total} · ⭐ {points}",
        buttons=[("🔄", t.get("success"),
                  app._refresh_page)])

    grid = CTkFrame(scroll, fg_color="transparent")
    grid.pack(fill="both", expand=True)
    for c in range(2):
        grid.grid_columnconfigure(c, weight=1, uniform="ach")

    i = 0
    for key, a in all_ach.items():
        r, c = i // 2, i % 2
        i += 1
        is_unlocked = key in user_ach
        rcolor = achievement_system.get_rarity_color(
            a.get("rarity", "common"))
        f = CTkFrame(
            grid, corner_radius=8, border_width=2,
            border_color=(rcolor if is_unlocked
                           else t.get("card_border")),
            fg_color=(t.get("bg2") if is_unlocked
                       else t.get("card")))
        f.grid(row=r, column=c, padx=6, pady=6,
                sticky="nsew")
        CTkLabel(f,
                 text=f"{'✅' if is_unlocked else '🔒'} "
                      f"{a.get('name', '')}",
                 font=CTkFont(size=13, weight="bold"),
                 text_color=(rcolor if is_unlocked
                              else t.get("text_dim")),
                 anchor="w").pack(anchor="w", fill="x",
                                    padx=UI_PAD_CARD,
                                    pady=(10, 2))
        CTkLabel(f, text=a.get("desc", ""),
                 font=CTkFont(size=11),
                 text_color=(t.get("text_glow")
                              if is_unlocked
                              else t.get("text_dim")),
                 wraplength=400, justify="left",
                 anchor="w").pack(anchor="w", fill="x",
                                    padx=UI_PAD_CARD,
                                    pady=(0, 10))


# ═══════════════════════════════════════════════════════════
# PAGE: STATS
# ═══════════════════════════════════════════════════════════
def _page_stats(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(
        scroll, "СТАТИСТИКА", "📊", "",
        buttons=[("🌐 HTML", t.get("secondary"),
                  lambda: _export_html_report(app))])

    s = level_system.get_stats(app.current_user)
    grid = CTkFrame(scroll, fg_color="transparent")
    grid.pack(fill="x", pady=(0, UI_PAD_INNER))
    for i in range(4):
        grid.grid_columnconfigure(i, weight=1, uniform="stat")

    items = [
        ("🏆 Уровень", s.get("level", 1), t.get("primary")),
        ("⭐ Опыт",
         f"{s.get('xp', 0)}/{s.get('xp_next', 100)}",
         t.get("accent")),
        ("🔥 Стрик", f"{s.get('streak', 0)}д",
         t.get("warning")),
        ("🍅 Помидоров",
         s.get("pomodoro_sessions", 0),
         t.get("error")),
        ("📱 QR", s.get("qr_generated", 0),
         t.get("gradient2")),
        ("💻 Кода", s.get("code_executed", 0),
         t.get("accent")),
        ("✅ Задач", s.get("tasks_completed", 0),
         t.get("success")),
        ("⛓️ Блоков", s.get("blocks_created", 0),
         t.get("primary")),
        ("✂️ Сниппетов", s.get("snippets_saved", 0),
         t.get("accent")),
        ("🪜 Подзадач", s.get("subtasks_completed", 0),
         t.get("gradient2")),
        ("⏱️ Таймеров", s.get("timers_started", 0),
         t.get("success")),
        ("🎯 Квестов", s.get("quests_completed", 0),
         t.get("warning")),
        ("📋 Сканов QR", s.get("clipboard_scans", 0),
         t.get("gradient1")),
        ("💬 Постов", s.get("posts_created", 0),
         t.get("primary")),
        ("🏅 Награды",
         len(achievement_system.get_user_achievements(
             app.current_user)),
         t.get("warning")),
        ("⏳ Время",
         fmt_duration(int(s.get("total_time") or 0)),
         t.get("text_glow")),
    ]
    for i, (lbl, v, color) in enumerate(items):
        r, c = i // 4, i % 4
        card = CTkFrame(grid, corner_radius=8,
                        border_width=2,
                        border_color=t.get("card_border"),
                        fg_color=t.get("card"))
        card.grid(row=r, column=c, padx=6, pady=6,
                    sticky="nsew")
        CTkLabel(card, text=lbl,
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(10, 2))
        CTkLabel(card, text=str(v),
                 font=CTkFont(size=18, weight="bold"),
                 text_color=color,
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(0, 10))

    # Heatmap
    hm_card = app._section_card(
        scroll, "🗓️ HEATMAP 12 НЕДЕЛЬ", t.get("secondary"))
    try:
        from PIL import Image, ImageDraw
        data = Timeline.get_heatmap(app.current_user, weeks=12)
        max_v = max(data.values(), default=1) or 1
        W, H = 12 * 16, 7 * 16
        from nexus.core import hex_to_rgb
        hm_img = Image.new("RGB", (W, H),
                            hex_to_rgb(t.get("bg3")))
        d = ImageDraw.Draw(hm_img)
        today = datetime.now().date()
        start = today - timedelta(days=12 * 7 - 1)
        c_bg = hex_to_rgb(t.get("bg3"))
        c_mid = hex_to_rgb(t.get("accent"))
        c_hi = hex_to_rgb(t.get("primary"))
        for week in range(12):
            for day in range(7):
                dt = start + timedelta(days=week * 7 + day)
                if dt > today:
                    continue
                cnt = data.get(dt.isoformat(), 0)
                if cnt == 0:
                    color = c_bg
                elif cnt / max_v > 0.7:
                    color = c_hi
                else:
                    color = c_mid
                x, y = week * 16, day * 16
                d.rectangle([x, y, x + 14, y + 14],
                              fill=color)
        cimg = CTkImage(light_image=hm_img,
                         dark_image=hm_img, size=(W, H))
        lbl = CTkLabel(hm_card, image=cimg, text="")
        lbl.image = cimg
        lbl.pack(padx=UI_PAD_CARD, pady=(0, UI_PAD_CARD))
    except Exception as e:
        logger.debug(f"heatmap: {e}")


def _export_html_report(app: Any) -> None:
    from nexus.integrations import HTMLExporter
    app._show_toast("⏳ HTML...", "info", 2000)

    def work() -> None:
        try:
            from nexus.core import HTML_REPORTS_DIR
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = (HTML_REPORTS_DIR /
                   f"report_{app.current_user}_{ts}.html")
            ok = HTMLExporter.export_user_report(
                app.current_user, out)
            if ok:
                app._safe_after(
                    0, app._show_toast,
                    f"✅ {out.name}", "success", 5000)
            else:
                app._safe_after(0, app._show_toast,
                                  "❌ Ошибка", "error")
        except Exception as e:
            app._safe_after(0, app._show_toast,
                              f"❌ {e}", "error")
    executor.submit_safe(work)


# ═══════════════════════════════════════════════════════════
# PAGE: AUDIT
# ═══════════════════════════════════════════════════════════
def _page_audit(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(
        scroll, "АУДИТ", "🔍", "журнал действий",
        buttons=[
            ("📊 CSV", t.get("accent"),
             lambda: _export_audit_csv(app)),
            ("🗑️ Очистить старые", t.get("warning"),
             lambda: _clear_old_audit(app)),
            ("🔄", t.get("success"), app._refresh_page)])

    from nexus.domain import AuditManager
    entries = AuditManager.get_recent(app.current_user, limit=300)
    CTkLabel(scroll, text=f"📋 Записей: {len(entries)}",
             font=CTkFont(size=12, weight="bold"),
             text_color=t.get("text_glow"),
             anchor="w").pack(anchor="w", pady=(0, 6))

    for e in entries:
        card = CTkFrame(scroll, fg_color=t.get("bg2"),
                         corner_radius=4)
        card.pack(fill="x", pady=2)
        row = CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        CTkLabel(row,
                 text=fmt_dt(e.get("created_at", ""),
                              "%d.%m %H:%M:%S"),
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim"),
                 width=110, anchor="w").pack(side="left")
        CTkLabel(row, text=e.get("action", "?"),
                 font=CTkFont(size=11, weight="bold"),
                 text_color=t.get("primary"),
                 width=150, anchor="w").pack(side="left")
        CTkLabel(row,
                 text=truncate(e.get("details", ""), 80),
                 font=CTkFont(size=10),
                 text_color=t.get("text"),
                 anchor="w").pack(side="left", fill="x",
                                    expand=True)


def _export_audit_csv(app: Any) -> None:
    from nexus.domain import AuditManager
    from nexus.core import EXPORT_DIR
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = EXPORT_DIR / f"audit_{app.current_user}_{ts}.csv"
    if AuditManager.export_csv(app.current_user, out):
        app._show_toast(f"📊 {out.name}", "success")
    else:
        app._show_toast("❌ Ошибка экспорта", "error")


def _clear_old_audit(app: Any) -> None:
    from nexus.domain import AuditManager
    if not messagebox.askyesno("Очистить",
                                 "Удалить записи старше 90 дней?"):
        return
    n = AuditManager.clear_old(days=90)
    app._show_toast(f"🗑️ Удалено: {n}", "info")
    app._refresh_page()


# ═══════════════════════════════════════════════════════════
# PAGE: ABOUT
# ═══════════════════════════════════════════════════════════
def _page_about(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)

    head = CTkFrame(scroll, fg_color="transparent")
    head.pack(fill="x", pady=(20, 4))
    CTkLabel(head,
             text=f"{APP_ICON}  NEXUS {VERSION_NAME}  {APP_ICON}",
             font=CTkFont(size=28, weight="bold"),
             text_color=t.get("primary")
             ).pack(fill="x")
    CTkLabel(head, text=f"v{VERSION} · build modular · release",
             font=CTkFont(size=14),
             text_color=t.get("text_glow")
             ).pack(fill="x")
    CTkLabel(head, text="© 2025 NEXUS Team",
             font=CTkFont(size=12),
             text_color=t.get("text_dim")
             ).pack(fill="x", pady=(4, 16))

    story = CTkFrame(scroll, corner_radius=10,
                     border_width=2,
                     border_color=t.get("primary"),
                     fg_color=t.get("bg2"))
    story.pack(fill="x", pady=UI_PAD_INNER)
    CTkLabel(story, text="🌀 NEXUS — цифровая вселенная",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 6))
    features = [
        "💻 Code Studio: 14 шаблонов, подсветка, multi-tab",
        "📱 QR-студия: 16 стилей, 11 режимов, live-preview",
        "📥 Сканер QR из буфера / файла",
        "🔀 Dynamic QR (short-links)",
        "📦 Батч-генерация QR + логотип",
        "🎨 Экспорт QR: PNG / JPG / SVG",
        "🍅 Pomodoro 25/5/15",
        "🎯 Ежедневные квесты",
        "⛓️ Блокчейн + Merkle + smart-contracts-lite",
        "🔐 AES-256-GCM + Argon2id + pepper",
        "🛡️ Sandbox: AST + bytecode + rlimits",
        "🔑 Password Vault (auto-lock)",
        "🧩 Shamir's Secret Sharing (2 из 3)",
        "✂️ Snippets — библиотека кода",
        "⏱️ Timers — time tracking",
        "🪜 Subtasks — подзадачи",
        "💬 Forum с Markdown",
        "📊 Gantt-lite, Kanban, Heatmap",
        "🏆 53 достижения",
        "🎨 Темы (cyber, dark, light, ...)",
        "⌘ Command Palette",
        "🔐 Rolling-сессии + machine-bound",
        "📡 HTTP API (Bearer + rate-limit)",
        "🌐 Webhooks (SSRF-safe + retry-queue)",
        "📊 JSONL-логи + метрики",
        "🔌 Плагины (hot-reload)",
        "💾 Инкрементальный backup",
        "🌍 4 языка (ru/en/uk/pl)",
    ]
    for f in features:
        CTkLabel(story, text=f"  • {f}",
                 font=CTkFont(size=12),
                 text_color=t.get("text"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=1)
    CTkFrame(story, height=8,
             fg_color="transparent").pack()

    sc_box = CTkFrame(scroll, corner_radius=10,
                       border_width=2,
                       border_color=t.get("warning"),
                       fg_color=t.get("bg2"))
    sc_box.pack(fill="x", pady=UI_PAD_INNER)
    CTkLabel(sc_box, text="⌨️ ГОРЯЧИЕ КЛАВИШИ",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("warning"),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(12, 6))
    for key, action in [
        ("Ctrl+Shift+C", "💻 Code Studio"),
        ("Ctrl+Shift+M", "💭 Что я забыл?"),
        ("Ctrl+Shift+N", "🧠 Быстрая запись"),
        ("Ctrl+Shift+K", "✂️ Сниппеты"),
        ("Ctrl+Shift+T", "⏱️ Таймеры"),
        ("Ctrl+Shift+L", "🔑 Пароли"),
        ("Ctrl+Shift+Q", "🎯 Квесты"),
        ("Ctrl+Shift+P", "🍅 Pomodoro"),
        ("Ctrl+Q", "📱 QR-студия"),
        ("Ctrl+K", "Command Palette"),
        ("Ctrl+Shift+S", "Скриншот"),
        ("Ctrl+Shift+H", "Panic Hide"),
        ("Ctrl+Z", "Отменить удаление"),
        ("Ctrl+S", "Сохранить всё"),
        ("Ctrl+F", "Поиск"),
        ("Ctrl+W", "Закрыть окно"),
        ("Ctrl+1..8", "Быстрая навигация"),
    ]:
        row = CTkFrame(sc_box, fg_color="transparent")
        row.pack(fill="x", padx=UI_PAD_CARD, pady=2)
        CTkLabel(row, text=key,
                 font=CTkFont(family="Courier", size=11,
                               weight="bold"),
                 text_color=t.get("primary"),
                 width=160, anchor="w").pack(side="left")
        CTkLabel(row, text=action,
                 font=CTkFont(size=11),
                 text_color=t.get("text"),
                 anchor="w").pack(side="left", fill="x",
                                    expand=True)


# ═══════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ═══════════════════════════════════════════════════════════
def _page_settings(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)

    # ── Заголовок страницы ────────────────────────────
    app._page_header(scroll, "НАСТРОЙКИ", "⚙️",
                      "конфигурация NEXUS")

    # ── Панель инструментов: поиск + действия ────────
    toolbar = CTkFrame(scroll, fg_color=t.get("bg2"),
                        corner_radius=8, border_width=1,
                        border_color=t.get("card_border"))
    toolbar.pack(fill="x", pady=(0, UI_PAD_INNER))
    tb_inner = CTkFrame(toolbar, fg_color="transparent")
    tb_inner.pack(fill="x", padx=UI_PAD_CARD, pady=8)

    # Поиск
    CTkLabel(tb_inner, text="🔍",
             font=CTkFont(size=13),
             text_color=t.get("text_dim")
             ).pack(side="left", padx=(0, 4))
    search_e = CTkEntry(
        tb_inner,
        placeholder_text="Поиск по настройкам...",
        height=34, font=CTkFont(size=12),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"))
    search_e.pack(side="left", fill="x", expand=True,
                  padx=(0, 8))

    # Кнопки
    CTkButton(
        tb_inner, text="💾 Сохранить",
        width=130, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("success"),
        text_color=t.get("bg"),
        command=lambda: (
            app.config_data.save(),
            app._show_toast("💾 Сохранено", "success"))
    ).pack(side="right", padx=2)

    CTkButton(
        tb_inner, text="↺ Сброс",
        width=110, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("warning"),
        text_color=t.get("bg"),
        command=lambda: _settings_reset(app)
    ).pack(side="right", padx=2)

    CTkButton(
        tb_inner, text="📤 Экспорт",
        width=120, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("accent"),
        text_color=t.get("bg"),
        command=lambda: _settings_export(app)
    ).pack(side="right", padx=2)

    CTkButton(
        tb_inner, text="📥 Импорт",
        width=120, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("primary"),
        text_color=t.get("bg"),
        command=lambda: _settings_import(app)
    ).pack(side="right", padx=2)

    # ── Контейнер для секций ──────────────────────────
    sections_container = CTkFrame(scroll,
                                    fg_color="transparent")
    sections_container.pack(fill="both", expand=True)

    # Рендерим все секции
    for section_title, items in SETTINGS_SECTIONS:
        _render_settings_section(
            app, sections_container, section_title, items)

    # ── Фильтрация по поиску ──────────────────────────
    def _filter_sections(*_a: Any) -> None:
        q = search_e.get().strip().lower()
        for section_card in sections_container.winfo_children():
            # section_card = CTkFrame секции
            # внутри — title + строки
            matched = False
            for sub in section_card.winfo_children():
                # sub может быть CTkLabel (title) или CTkFrame (row)
                if isinstance(sub, CTkFrame):
                    # Проверяем текст лейблов внутри строки
                    for lbl in sub.winfo_children():
                        try:
                            txt = str(lbl.cget("text")).lower()
                            if q in txt:
                                matched = True
                                break
                        except Exception:
                            pass
                else:
                    # Это заголовок секции — тоже проверяем
                    try:
                        if q in str(sub.cget("text")).lower():
                            matched = True
                    except Exception:
                        pass
                if matched:
                    break

            if not q or matched:
                section_card.pack(fill="x", pady=UI_PAD_INNER)
            else:
                section_card.pack_forget()

    search_e.bind("<KeyRelease>", _filter_sections)

    # ── Danger-зона внизу ─────────────────────────────
    _render_danger_zone(app, scroll)

def _render_settings_section(app: Any, parent: Any,
                              title: str,
                              items: List[Tuple]) -> None:
    """Одна секция настроек (карточка с заголовком)."""
    t = _t()

    # Карточка секции
    card = CTkFrame(parent, corner_radius=10,
                    border_width=2,
                    border_color=t.get("primary"),
                    fg_color=t.get("bg2"))
    card.pack(fill="x", pady=UI_PAD_INNER)

    # Заголовок
    head = CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=UI_PAD_CARD, pady=(12, 6))

    icon, _, text = title.partition(" ")
    CTkLabel(head, text=icon,
             font=CTkFont(size=16)).pack(side="left")
    CTkLabel(head, text=text,
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left", padx=(6, 0))

    # Тонкий разделитель
    CTkFrame(card, height=1,
             fg_color=t.get("card_border")
             ).pack(fill="x", padx=UI_PAD_CARD, pady=(0, 6))

    # Строки настроек
    for label, attr, kind, meta in items:
        _render_setting_row(app, card, label, attr, kind, meta)

    # Отступ снизу
    CTkFrame(card, height=6,
             fg_color="transparent").pack()

def _render_setting_row(app: Any, parent: Any,
                         label: str, attr: str,
                         kind: str, meta: Any) -> None:
    """Один пункт настройки: [Label] ......... [Control]."""
    t = _t()

    row = CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    # Лейбл слева
    CTkLabel(row, text=label,
             font=CTkFont(size=12),
             text_color=t.get("text"),
             anchor="w"
             ).pack(side="left", padx=(0, 12))

    # Текущее значение из конфига
    current = getattr(app.config_data, attr, None)

    # ═══════════════════════════════════════════════════
    # BOOL → CTkSwitch
    # ═══════════════════════════════════════════════════
    if kind == "bool":
        var = BooleanVar(value=bool(current))

        def _on_toggle() -> None:
            val = bool(var.get())
            setattr(app.config_data, attr, val)
            app.config_data.save()

            # Особые случаи
            if attr == "api_enabled":
                from nexus.integrations import api_server
                if val:
                    ok, msg = api_server.start(
                        app.config_data.api_port)
                    app._show_toast(
                        f"{'✅' if ok else '❌'} API: {msg}",
                        "success" if ok else "error")
                else:
                    api_server.stop()
                    app._show_toast("📡 API остановлен",
                                      "info")
            elif attr == "dev_mode":
                app._show_toast(
                    f"🔧 Dev {'ВКЛ' if val else 'ВЫКЛ'}",
                    "info", 1500)

        CTkSwitch(row, text="", variable=var,
                  width=52, height=26,
                  fg_color=t.get("bg3"),
                  progress_color=t.get("success"),
                  button_color=t.get("text"),
                  button_hover_color=t.get("accent"),
                  command=_on_toggle
                  ).pack(side="right")

    # ═══════════════════════════════════════════════════
    # SELECT → CTkOptionMenu
    # ═══════════════════════════════════════════════════
    elif kind == "select":
        values = [str(x) for x in (meta or [])]
        init = str(current) if current is not None \
                else (values[0] if values else "")
        if init not in values and values:
            init = values[0]
        var = StringVar(value=init)

        def _on_select(choice: str) -> None:
            setattr(app.config_data, attr, choice)
            app.config_data.save()

            # Особые случаи — переключение темы/языка
            if attr == "theme":
                _change_theme(app, choice)
            elif attr == "language":
                _change_language(app, choice)
            elif attr == "qr_default_size":
                with suppress(Exception):
                    app.config_data.qr_default_size = int(choice)
                    app.config_data.save()
            else:
                app._show_toast(
                    f"✅ {label}: {choice}", "info", 1500)

        CTkOptionMenu(row, values=values,
                       variable=var,
                       width=200, height=32,
                       font=CTkFont(size=11),
                       fg_color=t.get("bg3"),
                       button_color=t.get("primary"),
                       text_color=t.get("text"),
                       command=_on_select
                       ).pack(side="right")

    # ═══════════════════════════════════════════════════
    # INT → Entry + подсказка диапазона
    # ═══════════════════════════════════════════════════
    elif kind == "int":
        lo, hi = meta if meta else (0, 999_999)

        # Подсказка диапазона — справа от entry
        CTkLabel(row, text=f"{lo}–{hi}",
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim")
                 ).pack(side="right", padx=(6, 0))

        entry = CTkEntry(row, width=110, height=32,
                          font=CTkFont(size=12),
                          justify="right",
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(side="right")
        entry.insert(0, str(current if current is not None
                              else lo))

        def _save_int(_e: Any = None) -> None:
            try:
                val = int(entry.get())
            except (ValueError, TypeError):
                val = current if current is not None else lo
            val = max(lo, min(hi, val))
            setattr(app.config_data, attr, val)
            app.config_data.save()
            entry.delete(0, "end")
            entry.insert(0, str(val))

        entry.bind("<FocusOut>", _save_int)
        entry.bind("<Return>", _save_int)

    # ═══════════════════════════════════════════════════
    # PATH → Entry + 📂 кнопка
    # ═══════════════════════════════════════════════════
    elif kind == "path":
        entry = CTkEntry(row, height=32,
                          font=CTkFont(size=11),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(side="left", fill="x", expand=True,
                    padx=(0, 4))
        entry.insert(0, str(current or ""))

        def _save_path(_e: Any = None) -> None:
            setattr(app.config_data, attr,
                     entry.get().strip())
            app.config_data.save()

        def _choose_path() -> None:
            from tkinter import filedialog as _fd
            d = _fd.askdirectory(
                parent=app,
                title="Выберите папку")
            if d:
                entry.delete(0, "end")
                entry.insert(0, d)
                _save_path()
                app._show_toast("📂 Путь выбран", "success",
                                  1500)

        CTkButton(row, text="📂", width=40, height=32,
                  font=CTkFont(size=13),
                  fg_color=t.get("accent"),
                  text_color=t.get("bg"),
                  command=_choose_path
                  ).pack(side="right")
        entry.bind("<FocusOut>", _save_path)

    # ═══════════════════════════════════════════════════
    # STR → обычный Entry
    # ═══════════════════════════════════════════════════
    elif kind == "str":
        entry = CTkEntry(row, height=32,
                          font=CTkFont(size=12),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, str(current or ""))

        def _save_str(_e: Any = None) -> None:
            setattr(app.config_data, attr,
                     entry.get().strip())
            app.config_data.save()

        entry.bind("<FocusOut>", _save_str)
        entry.bind("<Return>", _save_str)

def _settings_reset(app: Any) -> None:
    """Сбросить все настройки на дефолт."""
    if not messagebox.askyesno(
            "Сбросить настройки?",
            "Все настройки вернутся к значениям по умолчанию.\n"
            "Задачи, заметки и другие данные — НЕ удалятся.\n\n"
            "Продолжить?"):
        return
    try:
        from nexus.app import AppConfig
        app.config_data = AppConfig()
        app.config_data.save()
        app._show_toast("↺ Настройки сброшены", "success",
                          2500)
        app._safe_after(300, app._refresh_page)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _settings_export(app: Any) -> None:
    """Экспорт настроек в JSON-файл."""
    import json
    from tkinter import filedialog as _fd

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = _fd.asksaveasfilename(
        parent=app,
        defaultextension=".json",
        initialfile=f"nexus_settings_{ts}.json",
        title="Экспорт настроек",
        filetypes=[("JSON", "*.json")])
    if not fp:
        return
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(app.config_data.__dict__, f,
                      indent=2, ensure_ascii=False,
                      default=str)
        app._show_toast(
            f"📤 {os.path.basename(fp)}", "success", 3000)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _settings_import(app: Any) -> None:
    """Импорт настроек из JSON."""
    import json
    from tkinter import filedialog as _fd

    fp = _fd.askopenfilename(
        parent=app,
        title="Импорт настроек",
        filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")])
    if not fp:
        return
    if not messagebox.askyesno(
            "Импорт настроек",
            "Текущие настройки будут перезаписаны.\n"
            "Продолжить?"):
        return
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Файл не содержит объект JSON")
        applied = 0
        for k, v in data.items():
            if hasattr(app.config_data, k):
                setattr(app.config_data, k, v)
                applied += 1
        app.config_data.save()
        app._show_toast(
            f"📥 Импортировано: {applied} настроек",
            "success", 3000)
        app._safe_after(300, app._refresh_page)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")

def _render_danger_zone(app: Any, parent: Any) -> None:
    """Опасные действия в самом низу настроек."""
    t = _t()
    card = CTkFrame(parent, corner_radius=10,
                    border_width=2,
                    border_color=t.get("error"),
                    fg_color=t.get("bg2"))
    card.pack(fill="x", pady=(UI_PAD_INNER, 20))

    head = CTkFrame(card, fg_color="transparent")
    head.pack(fill="x", padx=UI_PAD_CARD, pady=(12, 6))
    CTkLabel(head, text="🚨",
             font=CTkFont(size=16)).pack(side="left")
    CTkLabel(head, text="ОПАСНАЯ ЗОНА",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("error"),
             anchor="w").pack(side="left", padx=(6, 0))

    CTkLabel(card,
             text="Эти действия необратимы. "
                  "Дважды подумай перед нажатием.",
             font=CTkFont(size=10, slant="italic"),
             text_color=t.get("text_dim"),
             anchor="w"
             ).pack(anchor="w", padx=UI_PAD_CARD, pady=(0, 8))

    actions = CTkFrame(card, fg_color="transparent")
    actions.pack(fill="x", padx=UI_PAD_CARD, pady=(0, 12))

    CTkButton(
        actions, text="🗑️ Очистить историю QR",
        width=220, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color="transparent", border_width=1,
        border_color=t.get("warning"),
        text_color=t.get("warning"),
        command=lambda: _danger_clear_qr_history(app)
    ).pack(side="left", padx=4)

    CTkButton(
        actions, text="🔥 Сбросить кэш и перезапуск",
        width=260, height=34,
        font=CTkFont(size=11, weight="bold"),
        fg_color="transparent", border_width=1,
        border_color=t.get("error"),
        text_color=t.get("error"),
        command=lambda: _danger_restart(app)
    ).pack(side="left", padx=4)


def _danger_clear_qr_history(app: Any) -> None:
    if not messagebox.askyesno(
            "Очистить историю QR?",
            "Вся история QR-генераций будет удалена."):
        return
    try:
        from nexus.qr import qr_history
        n = qr_history.clear(app.current_user)
        app._show_toast(f"🗑️ Удалено: {n}", "info")
        app._refresh_page()
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _danger_restart(app: Any) -> None:
    if not messagebox.askyesno(
            "Перезапустить NEXUS?",
            "Приложение закроется. Запусти его снова вручную."):
        return
    app._on_closing()

def _change_theme(app: Any, theme: str) -> None:
    from nexus.app import theme_manager
    import customtkinter as ctk
    theme_manager.set(theme)
    app.config_data.theme = theme
    app.config_data.save()
    base = theme if theme in ("light", "dark") else "dark"
    ctk.set_appearance_mode(base)
    app._refresh_page()


def _change_language(app: Any, lang: str) -> None:
    from nexus.app import I18n
    I18n.set_lang(lang)
    app.config_data.language = lang
    app.config_data.save()
    app._refresh_page()
    app._show_toast(f"🌍 {lang.upper()}", "success")


def _change_password_dialog(app: Any) -> None:
    t = _t()
    win = CTkToplevel(app)
    win.title("🔑 Смена пароля")
    win.geometry("450x440")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    CTkLabel(win, text="🔑 СМЕНА ПАРОЛЯ",
             font=CTkFont(size=20, weight="bold"),
             text_color=t.get("primary")
             ).pack(pady=(20, 5))
    card = CTkFrame(win, corner_radius=10,
                     border_width=2,
                     border_color=t.get("card_border"),
                     fg_color=t.get("bg2"))
    card.pack(fill="x", padx=30, pady=10)
    entries: Dict[str, Any] = {}
    for key, lbl in [("old", "🔒 Текущий"),
                     ("new", "🔑 Новый"),
                     ("confirm", "✅ Подтверждение")]:
        CTkLabel(card, text=lbl,
                 font=CTkFont(size=13, weight="bold"),
                 text_color=t.get("text")
                 ).pack(anchor="w", padx=15, pady=(8, 4))
        ent = CTkEntry(card, placeholder_text=lbl,
                        height=35, font=CTkFont(size=14),
                        fg_color=t.get("bg3"),
                        border_color=t.get("card_border"),
                        text_color=t.get("text"), show="•")
        ent.pack(fill="x", padx=15, pady=4)
        entries[key] = ent
    msg = CTkLabel(card, text="",
                    font=CTkFont(size=11),
                    text_color=t.get("error"))
    msg.pack(pady=4)

    def do() -> None:
        old = entries["old"].get().strip()
        new = entries["new"].get().strip()
        confirm = entries["confirm"].get().strip()
        if new != confirm:
            msg.configure(text="Пароли не совпадают",
                          text_color=t.get("error"))
            return
        ok, m = UserManager.change_password(
            app.current_user, old, new)
        msg.configure(
            text=m,
            text_color=(t.get("success") if ok
                         else t.get("error")))
        if ok:
            app._show_toast("🔑 Пароль изменён",
                              "success")
            win.after(1500, win.destroy)

    CTkButton(win, text="💾 СМЕНИТЬ", height=40,
              width=200,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=do).pack(pady=15)


def _forget_session(app: Any) -> None:
    if messagebox.askyesno("Забыть",
                             "Удалить токен сессии?"):
        SessionManager.clear()
        app.config_data.remember_me = False
        app.config_data.last_user = ""
        app.config_data.session_auto_login = False
        app.config_data.save()
        app._show_toast("🚪 Забыто", "success")


# ═══════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# SETTINGS — декларативное описание всех настроек
# ═══════════════════════════════════════════════════════════
# Каждая строка: (label, attr_name, kind, meta)
#   kind = "bool"   → CTkSwitch                 (meta: None)
#   kind = "select" → CTkOptionMenu             (meta: [варианты])
#   kind = "int"    → CTkEntry (число)          (meta: (min, max))
#   kind = "path"   → CTkEntry + 📂 кнопка      (meta: None)
#   kind = "str"    → CTkEntry (текст)          (meta: None)
# ═══════════════════════════════════════════════════════════

SETTINGS_SECTIONS: List[Tuple[str, List[Tuple]]] = [
    ("🎨 ВНЕШНИЙ ВИД", [
        ("Тема",                  "theme",              "select",
         ["dark", "light", "cyber", "sunset", "forest",
          "ocean", "matrix_green"]),
        ("Язык интерфейса",       "language",           "select",
         ["ru", "en", "uk", "pl"]),
        ("Размер шрифта кода",    "code_font_size",     "int",
         (10, 20)),
        ("Авто-тема (по системе)", "auto_theme",         "bool", None),
        ("Авто-тема (по времени)", "auto_theme_time",    "bool", None),
        ("Авто-определение языка", "auto_detect_language", "bool", None),
    ]),

    ("🔔 УВЕДОМЛЕНИЯ И ЗВУК", [
        ("Уведомления-тосты",     "notifications",      "bool", None),
        ("Звуковые эффекты",      "sound_effects",      "bool", None),
        ("Длительность тоста (мс)", "toast_duration",   "int",
         (1000, 10000)),
        ("Напоминать о дедлайнах", "notify_deadlines",  "bool", None),
    ]),

    ("🔒 ПРИВАТНОСТЬ И БЕЗОПАСНОСТЬ", [
        ("Автовыход при простое", "auto_logout_idle",   "bool", None),
        ("Автоблокировка хранилища", "auto_lock_idle",  "bool", None),
        ("Таймаут сессии (мин)",  "session_timeout",    "int",
         (5, 480)),
        ("Таймаут автоблокировки (мин)", "auto_lock_timeout", "int",
         (1, 120)),
        ("HIBP-проверка паролей", "hibp_check_enabled", "bool", None),
        ("Запомнить меня по умолчанию", "remember_me",  "bool", None),
    ]),

    ("💻 CODE STUDIO", [
        ("Автосохранение кода",   "code_auto_save",     "bool", None),
        ("Подсветка синтаксиса",  "code_syntax_highlight", "bool", None),
        ("Автосохранение черновиков", "autosave_drafts", "bool", None),
        ("Язык по умолчанию",     "code_last_language", "select",
         ["python", "javascript", "shell", "sql", "html",
          "css", "json", "yaml"]),
    ]),

    ("📱 QR-СТУДИЯ", [
        ("Live-preview",          "qr_live_preview",    "bool", None),
        ("Размер по умолчанию",   "qr_default_size",    "select",
         ["200", "300", "400", "500", "800", "1000"]),
        ("Стиль по умолчанию",    "qr_default_style",   "select",
         ["neon_cyan", "cyber_pink", "matrix_green",
          "sunset_orange", "ocean_blue", "forest_green"]),
    ]),

    ("⚙️ СИСТЕМА", [
        ("Автобэкап",             "auto_backup",        "bool", None),
        ("Ежедневные квесты",     "quests_enabled",     "bool", None),
        ("HTTP API",              "api_enabled",        "bool", None),
        ("Порт API",              "api_port",           "int",
         (1024, 65535)),
        ("Dev-режим",             "dev_mode",           "bool", None),
    ]),

    ("☁️ СИНХРОНИЗАЦИЯ", [
        ("Автосинхронизация",     "sync_auto",          "bool", None),
        ("Путь для синхронизации", "sync_path",         "path", None),
    ]),
]

# ═══════════════════════════════════════════════════════════
# PAGE: EXPORT
# ═══════════════════════════════════════════════════════════
def _page_export(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(scroll, "ЭКСПОРТ", "📤", "сохрани всё")
    card = app._section_card(
        scroll, "📦 ФОРМАТЫ", t.get("card_border"))

    def export_zip() -> None:
        from nexus.integrations import DataExporter
        app._show_toast("⏳ Экспорт...", "info", 2000)

        def work() -> None:
            ok, msg = DataExporter.export_all(
                app.current_user)
            if ok:
                app._safe_after(
                    0, app._show_toast,
                    f"✅ {os.path.basename(msg)}",
                    "success", 5000)
            else:
                app._safe_after(0, app._show_toast,
                                  f"❌ {msg}", "error")
        executor.submit_safe(work)

    for txt, color, cmd in [
        ("📤 ZIP (всё)", t.get("success"), export_zip),
        ("🌐 HTML отчёт", t.get("secondary"),
         lambda: _export_html_report(app)),
        ("⛓️ Блокчейн JSON", t.get("warning"),
         lambda: _export_chain(app)),
        ("📊 Аудит CSV", t.get("primary"),
         lambda: _export_audit_csv(app)),
        ("💾 Full DB backup", t.get("accent"),
         lambda: (db.backup(),
                   app._show_toast("💾 Backup создан",
                                     "success"))),
        ("📦 SQL dump", t.get("gradient1"),
         lambda: (db.incremental_backup(),
                   app._show_toast("📦 SQL dump создан",
                                     "success"))),
    ]:
        CTkButton(card, text=txt, height=42, width=320,
                  font=CTkFont(size=14, weight="bold"),
                  fg_color=color,
                  text_color=t.get("bg"),
                  command=cmd
                  ).pack(pady=8, padx=UI_PAD_CARD,
                         anchor="w")


# ═══════════════════════════════════════════════════════════
# PAGE: IMPORT
# ═══════════════════════════════════════════════════════════
def _page_import(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(scroll, "ИМПОРТ", "📥",
                      "восстанови данные")
    card = app._section_card(
        scroll, "📥 ИСТОЧНИКИ", t.get("warning"))

    def import_zip() -> None:
        from nexus.integrations import DataExporter
        fp = filedialog.askopenfilename(
            filetypes=[("ZIP", "*.zip")])
        if not fp:
            return
        if not messagebox.askyesno("Импорт",
                                     "Перезапишет данные?"):
            return
        ok, msg = DataExporter.import_from_zip(
            fp, app.current_user)
        app._show_toast(
            f"{'✅' if ok else '❌'} {msg}",
            "success" if ok else "error")

    CTkButton(card, text="📥 Импорт ZIP (NEXUS)",
              height=42, width=320,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=import_zip
              ).pack(pady=8, padx=UI_PAD_CARD, anchor="w")
    CTkButton(card, text="⛓️ Импорт цепи (JSON)",
              height=42, width=320,
              fg_color=t.get("warning"),
              text_color=t.get("bg"),
              command=lambda: _import_chain(app)
              ).pack(pady=8, padx=UI_PAD_CARD, anchor="w")


# ═══════════════════════════════════════════════════════════
# PAGE: VAULTS
# ═══════════════════════════════════════════════════════════
def _page_vaults(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(scroll, "ХРАНИЛИЩА", "💾",
                      "локальные изолированные папки")
    from nexus.integrations import VaultManager

    # ── Создание ─────────────────────────────────────
    create_row = CTkFrame(scroll, fg_color="transparent")
    create_row.pack(fill="x", pady=UI_PAD_INNER)
    name_e = CTkEntry(create_row,
                       placeholder_text="Имя хранилища",
                       height=UI_FORM_ENTRY_HEIGHT,
                       fg_color=t.get("bg3"),
                       border_color=t.get("card_border"),
                       text_color=t.get("text"))
    name_e.pack(side="left", fill="x", expand=True,
                 padx=(0, 6))

    def create() -> None:
        n = name_e.get().strip()
        if not n:
            app._show_toast("Введите имя", "warning")
            return
        # Санитизация — на всякий случай
        safe = sanitize_filename(n)
        if safe != n:
            app._show_toast(
                f"⚠️ Имя заменено на «{safe}»", "info", 2500)
        ok, msg = VaultManager.create_vault(safe)
        app._show_toast(
            f"{'✅' if ok else '❌'} {msg}",
            "success" if ok else "error")
        if ok:
            name_e.delete(0, "end")
            app._refresh_page()

    CTkButton(create_row, text="＋ Создать", height=38,
              width=160,
              font=CTkFont(size=12, weight="bold"),
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=create).pack(side="right")
    name_e.bind("<Return>", lambda e: create())

    # ── Список ───────────────────────────────────────
    vaults = VaultManager.list_vaults()
    if not vaults:
        empty = app._section_card(
            scroll, "💾 Пока нет хранилищ",
            t.get("card_border"))
        CTkLabel(empty,
                 text="Создай первое хранилище выше — "
                      "это изолированная папка для данных",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(0, UI_PAD_CARD))
        return

    for v in vaults:
        _render_vault_card(app, scroll, v, VaultManager)


def _render_vault_card(app: Any, parent: Any,
                        v: Dict[str, Any],
                        VaultManager: Any) -> None:
    """Карточка одного хранилища с кнопками действий."""
    t = _t()
    card = CTkFrame(parent, fg_color=t.get("bg2"),
                     corner_radius=8, border_width=1,
                     border_color=t.get("card_border"))
    card.pack(fill="x", pady=4)

    # ── Заголовок ────────────────────────────────────
    top = CTkFrame(card, fg_color="transparent")
    top.pack(fill="x", padx=UI_PAD_CARD, pady=(8, 4))
    CTkLabel(top, text=f"💾 {v['name']}",
             font=CTkFont(size=14, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")
    CTkLabel(top,
             text=f"{fmt_size(v['size'])} · "
                  f"{v['mtime']:%d.%m.%Y %H:%M}",
             font=CTkFont(size=10),
             text_color=t.get("text_dim"),
             anchor="e").pack(side="right")

    # ── Кнопки действий ──────────────────────────────
    br = CTkFrame(card, fg_color="transparent")
    br.pack(fill="x", padx=UI_PAD_CARD, pady=(4, 8))

    CTkButton(
        br, text="📂 Открыть", width=110, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("primary"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_open(app, VaultManager, n)
    ).pack(side="left", padx=2)

    CTkButton(
        br, text="📎 Файл", width=100, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("secondary"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_upload_file(app, VaultManager, n)
    ).pack(side="left", padx=2)

    CTkButton(
        br, text="📥 ZIP", width=90, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("accent"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_extract_zip(app, VaultManager, n)
    ).pack(side="left", padx=2)

    CTkButton(
        br, text="📄 Содержимое", width=140, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("warning"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_show_contents(app, VaultManager, n)
    ).pack(side="left", padx=2)

    CTkButton(
        br, text="✏️", width=44, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("text_dim"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_rename(app, VaultManager, n)
    ).pack(side="left", padx=2)

    CTkButton(
        br, text="🗑️", width=44, height=30,
        font=CTkFont(size=11, weight="bold"),
        fg_color=t.get("error"),
        text_color=t.get("bg"),
        command=lambda n=v["name"]:
            _vault_delete(app, VaultManager, n)
    ).pack(side="right", padx=2)

    # ── Мини-превью содержимого ──────────────────────
    try:
        files = VaultManager.list_files(v["name"])
    except Exception:
        files = []

    if files:
        preview = CTkFrame(card, fg_color="transparent")
        preview.pack(fill="x", padx=UI_PAD_CARD,
                      pady=(0, 8))
        CTkLabel(preview,
                 text=f"📄 {len(files)} файлов:",
                 font=CTkFont(size=10, weight="bold"),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w")

        # Показываем первые 3 файла
        for f in files[:3]:
            ext = Path(f["name"]).suffix.lower()
            icon = {
                ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️",
                ".gif": "🖼️", ".webp": "🖼️", ".svg": "🎨",
                ".pdf": "📕", ".txt": "📄", ".md": "📝",
                ".zip": "📦", ".py": "💻", ".js": "💻",
            }.get(ext, "📎")

            frow = CTkFrame(preview, fg_color="transparent")
            frow.pack(fill="x", pady=1)
            CTkLabel(frow,
                     text=f"  {icon} {truncate(f['name'], 40)}",
                     font=CTkFont(size=11),
                     text_color=t.get("text"),
                     anchor="w").pack(side="left")
            CTkLabel(frow, text=fmt_size(f["size"]),
                     font=CTkFont(size=10),
                     text_color=t.get("text_dim")
                     ).pack(side="right")

        if len(files) > 3:
            CTkButton(
                preview,
                text=f"→ ещё {len(files) - 3} файлов",
                height=22, width=180,
                font=CTkFont(size=10),
                fg_color="transparent",
                text_color=t.get("accent"),
                command=lambda n=v["name"]:
                    _vault_show_contents(app, VaultManager, n)
            ).pack(anchor="w", pady=(4, 0))
    else:
        # Пустое хранилище — показываем подсказку
        CTkLabel(card, text="📭 Пусто — загрузи файл",
                 font=CTkFont(size=10, slant="italic"),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=(0, 8))

# ─── Действия над хранилищами ──────────────────────────

def _vault_open(app: Any, VaultManager: Any,
                 name: str) -> None:
    """Открыть папку хранилища в системном менеджере."""
    try:
        from nexus.core import VAULTS_DIR
        path = VAULTS_DIR / name
        if not path.exists():
            app._show_toast("❌ Папка не найдена", "error")
            return
        app._open_file(path)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _vault_export(app: Any, VaultManager: Any,
                   name: str) -> None:
    """Упаковать хранилище в ZIP."""
    from tkinter import filedialog as _fd
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = _fd.asksaveasfilename(
        parent=app,
        defaultextension=".zip",
        initialfile=f"vault_{name}_{ts}.zip",
        filetypes=[("ZIP", "*.zip")])
    if not fp:
        return

    def work() -> None:
        try:
            ok, msg = VaultManager.export_vault(name, fp)
            app._safe_after(
                0, app._show_toast,
                f"{'✅' if ok else '❌'} {msg}",
                "success" if ok else "error", 5000)
        except Exception as e:
            app._safe_after(0, app._show_toast,
                              f"❌ {e}", "error")
    executor.submit_safe(work)


def _vault_rename(app: Any, VaultManager: Any,
                   old_name: str) -> None:
    """Диалог переименования."""
    t = _t()
    win = CTkToplevel(app)
    win.title("✏️ Переименовать хранилище")
    win.geometry("440x220")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())

    CTkLabel(win, text="✏️ ПЕРЕИМЕНОВАТЬ",
             font=CTkFont(size=16, weight="bold"),
             text_color=t.get("warning")
             ).pack(pady=(15, 4))
    CTkLabel(win, text=f"Текущее: {old_name}",
             font=CTkFont(size=11),
             text_color=t.get("text_dim")
             ).pack(pady=(0, 8))
    e = CTkEntry(win, height=36, font=CTkFont(size=13),
                  fg_color=t.get("bg3"),
                  border_color=t.get("card_border"),
                  text_color=t.get("text"))
    e.pack(fill="x", padx=20, pady=6)
    e.insert(0, old_name)
    e.focus()
    e.select_range(0, "end")

    msg = CTkLabel(win, text="", font=CTkFont(size=11),
                    text_color=t.get("error"))
    msg.pack(pady=2)

    def save() -> None:
        new_name = sanitize_filename(e.get().strip())
        if not new_name:
            msg.configure(text="Пустое имя")
            return
        if new_name == old_name:
            win.destroy()
            return
        ok, m = VaultManager.rename_vault(old_name, new_name)
        if ok:
            app._show_toast(f"✅ {new_name}", "success")
            win.destroy()
            app._refresh_page()
        else:
            msg.configure(text=f"❌ {m}")

    bf = CTkFrame(win, fg_color="transparent")
    bf.pack(fill="x", padx=20, pady=(6, 15))
    CTkButton(bf, text="💾 Сохранить", height=36, width=150,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=save).pack(side="left")
    CTkButton(bf, text="❌ Отмена", height=36, width=120,
              fg_color="transparent", border_width=1,
              border_color=t.get("error"),
              text_color=t.get("error"),
              command=win.destroy).pack(side="left", padx=6)
    e.bind("<Return>", lambda ev: save())


def _vault_delete(app: Any, VaultManager: Any,
                   name: str) -> None:
    """Удалить хранилище с подтверждением и undo."""
    if not messagebox.askyesno(
            "Удалить хранилище?",
            f"Удалить «{name}»?\n\n"
            f"Данные будут упакованы в папку "
            f"_trash/ и доступны для восстановления "
            f"через Ctrl+Z."):
        return
    ok, msg = VaultManager.delete_vault(name, soft=True)
    if not ok:
        app._show_toast(f"❌ {msg}", "error")
        return
    app._show_toast("🗑️ Удалено (Ctrl+Z — отмена)",
                      "info", 3000)

    def undo() -> None:
        try:
            ok2, m2 = VaultManager.restore_vault(name)
            if ok2:
                app._show_toast("✅ Восстановлено",
                                  "success")
                app._refresh_page()
            else:
                app._show_toast(f"❌ {m2}", "error")
        except Exception as e:
            app._show_toast(f"❌ {e}", "error")

    undo_stack.push(undo, f"vault:{name}")
    app._refresh_page()

# ─── Загрузка файла ────────────────────────────────────
def _vault_upload_file(app: Any, VaultManager: Any,
                        name: str) -> None:
    """
    Открыть системный диалог, выбрать файл,
    скопировать его в хранилище.
    """
    from tkinter import filedialog as _fd

    fp = _fd.askopenfilename(
        parent=app,
        title=f"Выберите файл для «{name}»",
        filetypes=[
            ("Все файлы", "*.*"),
            ("Изображения", "*.png *.jpg *.jpeg *.gif *.webp *.svg"),
            ("Документы", "*.pdf *.docx *.xlsx *.pptx *.txt *.md"),
            ("Архивы", "*.zip *.tar *.gz *.7z"),
            ("Код", "*.py *.js *.html *.css *.json *.sql"),
        ])
    if not fp:
        return

    # В фоне — чтобы UI не подвисал на больших файлах
    def work() -> None:
        try:
            ok, msg = VaultManager.upload_file(name, fp)
            app._safe_after(
                0, app._show_toast,
                f"{'📎' if ok else '❌'} {msg}",
                "success" if ok else "error", 4000)
            if ok:
                # Обновим страницу через 200мс
                app._safe_after(200, app._refresh_page)
        except Exception as e:
            app._safe_after(0, app._show_toast,
                              f"❌ {e}", "error")

    executor.submit_safe(work)

# ─── Извлечение ZIP ────────────────────────────────────
def _vault_extract_zip(app: Any, VaultManager: Any,
                        name: str) -> None:
    """Распаковать ZIP в хранилище."""
    from tkinter import filedialog as _fd

    fp = _fd.askopenfilename(
        parent=app,
        title=f"ZIP для «{name}»",
        filetypes=[
            ("ZIP-архивы", "*.zip"),
            ("Все файлы", "*.*"),
        ])
    if not fp:
        return

    if not messagebox.askyesno(
            "Распаковать?",
            f"Распаковать «{Path(fp).name}» в «{name}»?\n\n"
            f"Структура папок сохранится."):
        return

    def work() -> None:
        try:
            ok, msg = VaultManager.extract_zip(name, fp)
            app._safe_after(
                0, app._show_toast,
                f"{'📥' if ok else '❌'} {msg}",
                "success" if ok else "error", 5000)
            if ok:
                app._safe_after(200, app._refresh_page)
        except Exception as e:
            app._safe_after(0, app._show_toast,
                              f"❌ {e}", "error")

    executor.submit_safe(work)

# ─── Просмотр содержимого ──────────────────────────────
def _vault_show_contents(app: Any, VaultManager: Any,
                          name: str) -> None:
    """
    Отдельное окно со списком файлов внутри хранилища.
    Пересоздаётся при удалении/загрузке — простое решение.
    """
    t = _t()

    # ── Окно ─────────────────────────────────────────
    win = CTkToplevel(app)
    win.title(f"📄 {name}")
    win.geometry("820x640")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())

    # ── Шапка ────────────────────────────────────────
    head = CTkFrame(win, fg_color="transparent")
    head.pack(fill="x", padx=15, pady=(15, 8))

    files_info = VaultManager.list_files(name)
    total_size = sum(f["size"] for f in files_info)

    CTkLabel(head, text=f"📄 СОДЕРЖИМОЕ: {name}",
             font=CTkFont(size=17, weight="bold"),
             text_color=t.get("primary"),
             anchor="w").pack(side="left")

    CTkLabel(head,
             text=f"{len(files_info)} файлов · "
                  f"{fmt_size(total_size)}",
             font=CTkFont(size=11),
             text_color=t.get("text_dim")).pack(side="right")

    # ── Кнопки сверху ────────────────────────────────
    top_actions = CTkFrame(win, fg_color="transparent")
    top_actions.pack(fill="x", padx=15, pady=(0, 8))

    CTkButton(top_actions, text="📎 Загрузить файл",
              height=32, width=170,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("secondary"),
              text_color=t.get("bg"),
              command=lambda: (
                  win.destroy(),
                  _vault_upload_file(app, VaultManager, name))
              ).pack(side="left", padx=2)

    CTkButton(top_actions, text="📥 Распаковать ZIP",
              height=32, width=170,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=lambda: (
                  win.destroy(),
                  _vault_extract_zip(app, VaultManager, name))
              ).pack(side="left", padx=2)

    CTkButton(top_actions, text="📦 Экспорт ZIP",
              height=32, width=150,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("warning"),
              text_color=t.get("bg"),
              command=lambda:
                  _vault_export(app, VaultManager, name)
              ).pack(side="left", padx=2)

    CTkButton(top_actions, text="🔄",
              height=32, width=44,
              font=CTkFont(size=13, weight="bold"),
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=lambda: (
                  win.destroy(),
                  _vault_show_contents(app, VaultManager, name))
              ).pack(side="right", padx=2)

    # ── Список файлов ────────────────────────────────
    scroll = CTkScrollableFrame(win, fg_color=t.get("bg2"))
    scroll.pack(fill="both", expand=True, padx=15,
                 pady=(0, 15))

    if not files_info:
        CTkLabel(scroll,
                 text="📭 Пока пусто\n\n"
                      "Загрузи файл или распакуй ZIP",
                 font=CTkFont(size=13),
                 text_color=t.get("text_dim"),
                 justify="center").pack(pady=40)
        return

    for f in files_info:
        _render_vault_file_row(
            app, scroll, name, f, VaultManager, win)

def _render_vault_file_row(app: Any, parent: Any,
                            vault_name: str,
                            f: Dict[str, Any],
                            VaultManager: Any,
                            parent_win: Any) -> None:
    """Одна строка-карточка файла в хранилище."""
    t = _t()

    # Иконка по расширению
    ext = Path(f["name"]).suffix.lower()
    icon_map = {
        ".txt": "📄", ".md": "📝", ".pdf": "📕",
        ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️",
        ".gif": "🖼️", ".webp": "🖼️", ".svg": "🎨",
        ".mp3": "🎵", ".wav": "🎵", ".ogg": "🎵",
        ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬",
        ".zip": "📦", ".tar": "📦", ".gz": "📦",
        ".py": "💻", ".js": "💻", ".html": "💻",
        ".css": "💻", ".json": "💻", ".sql": "💻",
        ".docx": "📘", ".xlsx": "📗", ".pptx": "📙",
    }
    icon = icon_map.get(ext, "📎")

    row = CTkFrame(parent, fg_color=t.get("bg3"),
                    corner_radius=6)
    row.pack(fill="x", pady=2)

    # Верхняя строка: имя + размер
    top = CTkFrame(row, fg_color="transparent")
    top.pack(fill="x", padx=10, pady=(6, 2))

    CTkLabel(top, text=f"{icon} {f['name']}",
             font=CTkFont(size=12, weight="bold"),
             text_color=t.get("text"),
             anchor="w").pack(side="left")

    CTkLabel(top, text=fmt_size(f["size"]),
             font=CTkFont(size=10),
             text_color=t.get("text_dim")).pack(side="right")

    # Нижняя строка: действия
    sub = CTkFrame(row, fg_color="transparent")
    sub.pack(fill="x", padx=10, pady=(0, 6))

    CTkButton(
        sub, text="📂 Открыть", width=100, height=26,
        font=CTkFont(size=10, weight="bold"),
        fg_color=t.get("primary"),
        text_color=t.get("bg"),
        command=lambda fn=f["name"]:
            _vault_open_file(app, vault_name, fn)
    ).pack(side="left", padx=2)

    CTkButton(
        sub, text="📍 Путь", width=80, height=26,
        font=CTkFont(size=10),
        fg_color="transparent",
        border_width=1,
        border_color=t.get("accent"),
        text_color=t.get("accent"),
        command=lambda fn=f["name"]:
            _vault_show_path(app, vault_name, fn)
    ).pack(side="left", padx=2)

    CTkButton(
        sub, text="🗑️", width=32, height=26,
        font=CTkFont(size=10),
        fg_color="transparent",
        text_color=t.get("error"),
        command=lambda fn=f["name"]: _vault_delete_file(
            app, VaultManager, vault_name, fn, parent_win)
    ).pack(side="right", padx=2)

    CTkLabel(sub,
             text=f["mtime"].strftime("%d.%m.%Y %H:%M"),
             font=CTkFont(size=9),
             text_color=t.get("text_dim")).pack(side="right",
                                                  padx=6)

def _vault_open_file(app: Any, vault_name: str,
                      file_name: str) -> None:
    """Открыть конкретный файл системным приложением."""
    try:
        from nexus.core import VAULTS_DIR
        path = VAULTS_DIR / vault_name / file_name
        if not path.exists():
            app._show_toast("❌ Файл не найден", "error")
            return
        app._open_file(path)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")


def _vault_show_path(app: Any, vault_name: str,
                      file_name: str) -> None:
    """Скопировать путь к файлу в буфер обмена."""
    try:
        from nexus.core import VAULTS_DIR, copy_to_clipboard
        path = VAULTS_DIR / vault_name / file_name
        if copy_to_clipboard(str(path)):
            app._show_toast("📋 Путь скопирован",
                              "success", 2000)
    except Exception as e:
        app._show_toast(f"❌ {e}", "error")

def _vault_delete_file(app: Any, VaultManager: Any,
                        vault_name: str, file_name: str,
                        parent_win: Any) -> None:
    """Удалить файл из хранилища (soft)."""
    if not messagebox.askyesno(
            "Удалить файл?",
            f"Удалить «{file_name}»?\n\n"
            f"Файл уйдёт в _trash/ — можно восстановить."):
        return

    ok, msg = VaultManager.delete_file(vault_name, file_name)
    if ok:
        app._show_toast("🗑️ Удалено", "info", 1500)
        # Пересоздаём окно — так проще, чем инкрементально
        # удалять строку из CTkScrollableFrame
        with suppress(Exception):
            parent_win.destroy()
        _vault_show_contents(app, VaultManager, vault_name)
    else:
        app._show_toast(f"❌ {msg}", "error")


# ═══════════════════════════════════════════════════════════
# PAGE: SYNC
# ═══════════════════════════════════════════════════════════
def _page_sync(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    app._page_header(scroll, "СИНХРОНИЗАЦИЯ", "☁️",
                      "LWW (last-write-wins)")
    from nexus.integrations import SyncManager

    path_e = CTkEntry(scroll, placeholder_text="Путь к папке",
                       height=UI_FORM_ENTRY_HEIGHT,
                       fg_color=t.get("bg3"),
                       border_color=t.get("card_border"),
                       text_color=t.get("text"))
    path_e.pack(fill="x", pady=UI_PAD_INNER)
    path_e.insert(0, app.config_data.sync_path)

    def save_path() -> None:
        app.config_data.sync_path = str(
            path_e.get().strip())
        app.config_data.save()
        app._show_toast("💾 Сохранено", "success")

    CTkButton(scroll, text="💾 Сохранить путь",
              height=38, width=220,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=save_path
              ).pack(anchor="w", pady=(0, UI_PAD_INNER))

    def up() -> None:
        path = path_e.get().strip()
        if not path:
            return
        ok, msg = SyncManager.sync_to_local_folder(path)
        app._show_toast(
            f"{'✅' if ok else '❌'} {msg}",
            "success" if ok else "error")

    def down() -> None:
        path = path_e.get().strip()
        if not path or not messagebox.askyesno(
                "Sync", "Перезапишет данные? (LWW)"):
            return
        ok, msg = SyncManager.sync_from_local_folder(path)
        app._show_toast(
            f"{'✅' if ok else '❌'} {msg}",
            "success" if ok else "warning", 5000)

    bf = CTkFrame(scroll, fg_color="transparent")
    bf.pack(fill="x", pady=6)
    CTkButton(bf, text="⬆️ Выгрузить", height=40,
              width=180,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=up).pack(side="left", padx=4)
    CTkButton(bf, text="⬇️ Загрузить", height=40,
              width=180,
              fg_color=t.get("warning"),
              text_color=t.get("bg"),
              command=down).pack(side="left", padx=4)


# ═══════════════════════════════════════════════════════════
# PAGE: PLUGINS
# ═══════════════════════════════════════════════════════════
def _page_plugins(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    from nexus.integrations import (
        PluginMarketplace, plugin_manager,
    )
    plugins = PluginMarketplace.list_available()
    app._page_header(
        scroll, "ПЛАГИНЫ", "🔌",
        f"{len(plugins)} загружено",
        buttons=[("📦 Установить", t.get("accent"),
                  lambda: _install_plugin(app)),
                 ("🔄 Reload", t.get("warning"),
                  lambda: _reload_plugins(app))])

    if not plugins:
        empty = app._section_card(
            scroll, "🔌 Нет плагинов", t.get("card_border"))
        from nexus.core import PLUGINS_DIR
        CTkLabel(empty,
                 text=f"Папка: {PLUGINS_DIR}\n"
                      f"Плагин = класс, наследник PluginBase",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim"),
                 justify="center"
                 ).pack(pady=(0, UI_PAD_CARD))
        return
    for p in plugins:
        card = CTkFrame(scroll, fg_color=t.get("bg2"),
                         corner_radius=6)
        card.pack(fill="x", pady=4)
        CTkLabel(card, text=f"🔌 {p['name']} v{p['version']}",
                 font=CTkFont(size=13, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(6, 2))
        CTkLabel(card,
                 text=f"Автор: {p['author']} · {p['desc']}",
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(0, 6))


def _install_plugin(app: Any) -> None:
    from nexus.integrations import PluginMarketplace
    fp = filedialog.askopenfilename(
        filetypes=[("Plugin", "*.zip")])
    if not fp:
        return
    ok, msg = PluginMarketplace.install_from_file(fp)
    app._show_toast(
        f"{'✅' if ok else '❌'} {msg}",
        "success" if ok else "error")
    if ok:
        app._refresh_page()


def _reload_plugins(app: Any) -> None:
    from nexus.integrations import plugin_manager
    n = plugin_manager.reload_all()
    app._show_toast(f"🔌 Reload: {n} плагинов", "success")


# ═══════════════════════════════════════════════════════════
# PAGE: WEBHOOKS
# ═══════════════════════════════════════════════════════════
def _page_webhooks(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    from nexus.integrations import WebhookManager
    hooks = WebhookManager.get_webhooks()
    q_stats = WebhookManager.queue_stats()
    app._page_header(
        scroll, "WEBHOOKS", "🌐",
        f"всего: {len(hooks)} · SSRF-safe · "
        f"queue: {q_stats['pending']} в очереди, "
        f"{q_stats['delivered']} доставлено",
        buttons=[
            ("🔄 Обработать", t.get("accent"),
             lambda: (
                 WebhookManager._process_queue(),
                 app._show_toast("🔄 Очередь обработана",
                                   "info"))),
            ("🔄", t.get("success"), app._refresh_page)])

    url_e = CTkEntry(scroll, placeholder_text="https://...",
                      height=UI_FORM_ENTRY_HEIGHT,
                      fg_color=t.get("bg3"),
                      border_color=t.get("card_border"),
                      text_color=t.get("text"))
    url_e.pack(fill="x", pady=UI_PAD_INNER)

    def add() -> None:
        url = url_e.get().strip()
        if not url:
            return
        ok, msg = WebhookManager.add_webhook(url)
        app._show_toast(
            f"{'✅' if ok else '❌'} {msg}",
            "success" if ok else "error")
        if ok:
            app._refresh_page()

    CTkButton(scroll, text="＋ Добавить", height=38,
              width=200,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=add
              ).pack(anchor="w", pady=(0, UI_PAD_INNER))

    for wh in hooks:
        card = CTkFrame(scroll, fg_color=t.get("bg2"),
                         corner_radius=6)
        card.pack(fill="x", pady=3)
        CTkLabel(card, text=f"🌐 {wh.get('url', '')}",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(6, 2))
        CTkLabel(card,
                 text=f"События: "
                      f"{','.join(wh.get('events') or ['all'])}"
                      f" · Доставлено: {wh.get('deliveries', 0)}"
                      f" · Ошибок: {wh.get('failures', 0)}",
                 font=CTkFont(size=10),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(anchor="w", padx=10,
                                    pady=(0, 4))
        CTkButton(card, text="🗑️", width=32, height=26,
                  fg_color="transparent",
                  text_color=t.get("error"),
                  command=lambda wid=wh["id"]: (
                      WebhookManager.remove_webhook(wid),
                      app._refresh_page())
                  ).pack(anchor="e", padx=10, pady=(0, 6))


# ═══════════════════════════════════════════════════════════
# PAGE: API
# ═══════════════════════════════════════════════════════════
def _page_api(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    from nexus.integrations import api_server
    running = api_server.is_running()
    port = app.config_data.api_port
    app._page_header(
        scroll, "HTTP API", "📡",
        (f"🟢 работает на порту {api_server.port()}"
         if running else f"🔴 выключен · порт {port}"),
        buttons=[
            ("▶ Старт" if not running else "⏹ Стоп",
             (t.get("success") if not running
              else t.get("error")),
             lambda: _toggle_api(app)),
            ("🎫 Токены", t.get("accent"),
             lambda: _show_api_tokens_dialog(app)),
            ("🔄", t.get("primary"), app._refresh_page)])

    info = app._section_card(
        scroll, "📖 ENDPOINTS", t.get("primary"))
    endpoints = [
        ("GET /api/v1/health", "публичный · статус"),
        ("GET /api/v1/stats", "статистика"),
        ("GET /api/v1/tasks", "активные задачи"),
        ("GET /api/v1/notes?limit=N", "заметки"),
        ("GET /api/v1/qr?limit=N", "история QR"),
        ("GET /api/v1/blockchain", "цепь + Merkle"),
        ("POST /api/v1/tasks", "создать задачу"),
        ("POST /api/v1/notes", "создать заметку"),
        ("POST /api/v1/qr", "сгенерировать QR"),
        ("POST /api/v1/dynamic", "создать dynamic-QR"),
        ("GET /r/{id}", "редирект dynamic-QR"),
    ]
    for ep, desc in endpoints:
        row = CTkFrame(info, fg_color="transparent")
        row.pack(fill="x", padx=UI_PAD_CARD, pady=2)
        CTkLabel(row, text=ep,
                 font=CTkFont(family="Courier", size=11,
                               weight="bold"),
                 text_color=t.get("primary"),
                 width=280, anchor="w").pack(side="left")
        CTkLabel(row, text=desc,
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim"),
                 anchor="w").pack(side="left", fill="x",
                                    expand=True)

    auth_card = app._section_card(
        scroll, "🔑 АВТОРИЗАЦИЯ", t.get("warning"))
    CTkLabel(auth_card,
             text="Заголовок: Authorization: Bearer <token>\n"
                  "Или: X-API-Token: <token>\n"
                  "Rate-limit: 120 запросов / минуту",
             font=CTkFont(family="Courier", size=11),
             text_color=t.get("text"),
             anchor="w", justify="left",
             wraplength=UI_WRAP_WIDE
             ).pack(anchor="w", padx=UI_PAD_CARD,
                    pady=(0, UI_PAD_CARD))


def _toggle_api(app: Any) -> None:
    from nexus.integrations import api_server
    if api_server.is_running():
        api_server.stop()
        app.config_data.api_enabled = False
        app.config_data.save()
        app._show_toast("📡 API остановлен", "info")
    else:
        ok, msg = api_server.start(app.config_data.api_port)
        if ok:
            app.config_data.api_enabled = True
            app.config_data.save()
            app._show_toast(f"📡 {msg}", "success")
        else:
            app._show_toast(f"❌ {msg}", "error")
    app._refresh_page()


def _show_api_tokens_dialog(app: Any) -> None:
    t = _t()
    from nexus.security import APITokenManager
    win = CTkToplevel(app)
    win.title("🎫 API-токены")
    win.geometry("700x600")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())
    CTkLabel(win, text="🎫 API-ТОКЕНЫ",
             font=CTkFont(size=20, weight="bold"),
             text_color=t.get("primary")
             ).pack(pady=(15, 8))

    add_row = CTkFrame(win, fg_color="transparent")
    add_row.pack(fill="x", padx=20, pady=(0, 8))
    name_e = CTkEntry(add_row,
                       placeholder_text="Название токена",
                       height=36, font=CTkFont(size=12),
                       fg_color=t.get("bg3"),
                       border_color=t.get("card_border"),
                       text_color=t.get("text"))
    name_e.pack(side="left", fill="x", expand=True,
                padx=(0, 6))

    def create_token() -> None:
        ok, msg, raw = APITokenManager.create(
            app.current_user, name_e.get().strip())
        if ok and raw:
            name_e.delete(0, "end")
            app._show_toast("✅ Токен создан", "success",
                              4000)
            txt = CTkTextbox(
                win, height=60,
                font=CTkFont(family="Courier", size=12),
                fg_color=t.get("bg3"),
                text_color=t.get("primary"))
            txt.pack(fill="x", padx=20, pady=6)
            txt.insert("1.0", raw)
            app._safe_after(100, lambda: copy_to_clipboard(raw))
            win.destroy()
            _show_api_tokens_dialog(app)

    CTkButton(add_row, text="➕ Создать",
              width=140, height=36,
              font=CTkFont(size=11, weight="bold"),
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=create_token).pack(side="right")

    tokens = APITokenManager.list_for_user(app.current_user)
    scroll = CTkScrollableFrame(win, fg_color=t.get("bg2"))
    scroll.pack(fill="both", expand=True, padx=20,
                 pady=(8, 15))
    if not tokens:
        CTkLabel(scroll, text="Пока нет токенов",
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=20)
    else:
        for tok in tokens:
            card = CTkFrame(scroll, fg_color=t.get("bg3"),
                             corner_radius=6)
            card.pack(fill="x", pady=3)
            revoked = tok.get("revoked")
            CTkLabel(card,
                     text=f"{'🚫' if revoked else '🎫'} "
                          f"{tok.get('name', '')}",
                     font=CTkFont(size=12, weight="bold"),
                     text_color=(t.get("text_dim") if revoked
                                  else t.get("primary")),
                     anchor="w").pack(anchor="w", padx=10,
                                        pady=(6, 2))
            CTkLabel(card,
                     text=f"📅 Создан: "
                          f"{fmt_dt(tok.get('created_at', ''))}"
                          f" · Истекает: "
                          f"{fmt_dt(tok.get('expires_at', ''), '%d.%m.%Y')}",
                     font=CTkFont(size=10),
                     text_color=t.get("text_dim"),
                     anchor="w").pack(anchor="w", padx=10)
            if not revoked:
                CTkButton(card, text="🚫 Отозвать",
                          width=120, height=26,
                          font=CTkFont(size=10,
                                        weight="bold"),
                          fg_color=t.get("error"),
                          text_color=t.get("bg"),
                          command=lambda tid=tok.get("id"): (
                              APITokenManager.revoke(
                                  tid, app.current_user),
                              win.destroy(),
                              _show_api_tokens_dialog(app))
                          ).pack(anchor="e", padx=10,
                                 pady=(2, 6))


# ═══════════════════════════════════════════════════════════
# Импорт для json внутри модуля
# ═══════════════════════════════════════════════════════════
import json  # noqa: E402


# ═══════════════════════════════════════════════════════════
# PAGE: RECOVERY
# ═══════════════════════════════════════════════════════════
def _page_recovery(app: Any, parent: CTkFrame) -> None:
    t = _t()
    scroll = CTkScrollableFrame(parent, fg_color=t.get("bg"))
    scroll.pack(fill="both", expand=True)
    user = UserManager.get(app.current_user) or {}
    has_recovery = bool(user.get("recovery_phrase_created"))
    app._page_header(scroll, "ВОССТАНОВЛЕНИЕ", "🔑",
                      "12 слов + Shamir 2/3")

    info_card = app._section_card(
        scroll, "ℹ️ КАК ЭТО РАБОТАЕТ", t.get("primary"))
    for line in [
        "• При регистрации генерируется 12 слов",
        "• Фраза сохраняется в nexus_data/recovery/",
        "• Дополнительно — 3 Shamir-части "
        "(любые 2 восстановят фразу)",
        "• Храните фразу и части в надёжном месте!",
    ]:
        CTkLabel(info_card, text=line,
                 font=CTkFont(size=12),
                 text_color=t.get("text"),
                 anchor="w").pack(anchor="w",
                                    padx=UI_PAD_CARD,
                                    pady=2)

    status_card = app._section_card(
        scroll, "📋 СТАТУС",
        t.get("success") if has_recovery else t.get("warning"))
    CTkLabel(status_card,
             text=("✅ Recovery создана" if has_recovery
                   else "⚠️ Не создана"),
             font=CTkFont(size=12, weight="bold"),
             text_color=(t.get("success") if has_recovery
                          else t.get("warning")),
             anchor="w").pack(anchor="w",
                                padx=UI_PAD_CARD,
                                pady=(0, 6))

    def view_phrase() -> None:
        words = UserManager.get_recovery_phrase(
            app.current_user)
        if not words:
            app._show_toast("Фраза не найдена", "warning")
            return
        shards = UserManager.get_recovery_shards(
            app.current_user)
        win = CTkToplevel(app)
        win.title("🔑 Recovery-фраза")
        win.geometry("620x720")
        win.configure(fg_color=t.get("bg"))
        win.transient(app)
        win.grab_set()
        CTkLabel(win, text="🔑 RECOVERY-ФРАЗА",
                 font=CTkFont(size=20, weight="bold"),
                 text_color=t.get("warning")
                 ).pack(pady=(20, 8))
        grid = CTkFrame(win, fg_color="transparent")
        grid.pack(fill="x", padx=40)
        for i in range(2):
            grid.grid_columnconfigure(i, weight=1,
                                        uniform="rec")
        for i, w in enumerate(words):
            r, c = i // 2, i % 2
            cell = CTkFrame(grid, fg_color=t.get("bg3"),
                             corner_radius=6)
            cell.grid(row=r, column=c, padx=4, pady=4,
                        sticky="nsew")
            CTkLabel(cell, text=f"{i + 1}. {w}",
                     font=CTkFont(family="Courier", size=14,
                                   weight="bold"),
                     text_color=t.get("primary"),
                     anchor="w").pack(anchor="w", padx=10,
                                        pady=6)
        CTkButton(win, text="📋 Скопировать фразу",
                  height=38, width=220,
                  fg_color=t.get("primary"),
                  text_color=t.get("bg"),
                  command=lambda: (
                      copy_to_clipboard(" ".join(words)),
                      app._show_toast("📋 Скопировано",
                                        "success"))
                  ).pack(pady=(12, 8))
        if shards:
            sh_card = CTkFrame(win, fg_color=t.get("bg3"),
                                 corner_radius=6,
                                 border_width=1,
                                 border_color=t.get("accent"))
            sh_card.pack(fill="x", padx=40, pady=(8, 8))
            CTkLabel(sh_card,
                     text=f"🧩 SHAMIR 2/{SHAMIR_PARTS} — "
                          f"любые 2 части:",
                     font=CTkFont(size=11, weight="bold"),
                     text_color=t.get("accent"),
                     anchor="w").pack(anchor="w", padx=10,
                                        pady=(6, 2))
            for s in shards:
                CTkLabel(sh_card, text=truncate(s, 80),
                         font=CTkFont(family="Courier",
                                       size=9),
                         text_color=t.get("text_dim"),
                         anchor="w", wraplength=520,
                         justify="left"
                         ).pack(anchor="w", fill="x",
                                padx=10, pady=1)
            CTkButton(win, text="📋 Скопировать Shamir",
                      height=34, width=220,
                      fg_color=t.get("accent"),
                      text_color=t.get("bg"),
                      command=lambda: (
                          copy_to_clipboard(
                              "\n".join(shards)),
                          app._show_toast(
                              "📋 Shamir скопированы",
                              "success"))
                      ).pack(pady=(4, 16))

    CTkButton(status_card, text="👁️ Показать фразу",
              height=38, width=220,
              fg_color=t.get("primary"),
              text_color=t.get("bg"),
              command=view_phrase
              ).pack(anchor="w", padx=UI_PAD_CARD,
                     pady=(0, 6))

    # ── Shamir restore ───────────────────────────────
    shamir_card = app._section_card(
        scroll, "🧩 ВОССТАНОВЛЕНИЕ ИЗ SHAMIR",
        t.get("accent"))
    CTkLabel(shamir_card,
             text=f"Вставь {SHAMIR_THRESHOLD}+ частей "
                  f"(по одной на строку, "
                  f"формат `N:base64`):",
             font=CTkFont(size=11),
             text_color=t.get("text_dim"),
             anchor="w", justify="left",
             wraplength=UI_WRAP_WIDE
             ).pack(anchor="w", padx=UI_PAD_CARD,
                    pady=(0, 4))
    sh_in = CTkTextbox(
        shamir_card, height=100,
        font=CTkFont(family="Courier", size=10),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("text"), wrap="word")
    sh_in.pack(fill="x", padx=UI_PAD_CARD, pady=4)
    sh_out = CTkTextbox(
        shamir_card, height=60,
        font=CTkFont(family="Courier", size=11),
        fg_color=t.get("bg3"),
        border_color=t.get("card_border"),
        text_color=t.get("success"), wrap="word")
    sh_out.pack(fill="x", padx=UI_PAD_CARD, pady=4)

    def do_reconstruct() -> None:
        from nexus.crypto import reconstruct_recovery_phrase
        lines = [line.strip() for line in
                 sh_in.get("1.0", "end-1c").splitlines()
                 if ":" in line]
        if len(lines) < SHAMIR_THRESHOLD:
            app._show_toast(
                f"Нужно минимум {SHAMIR_THRESHOLD} части",
                "warning")
            return
        words = reconstruct_recovery_phrase(lines)
        if words:
            sh_out.delete("1.0", "end")
            sh_out.insert("1.0", " ".join(words))
            app._show_toast("✅ Фраза восстановлена",
                              "success")
        else:
            app._show_toast("❌ Не удалось восстановить",
                              "error")

    CTkButton(shamir_card,
              text="🔓 Восстановить фразу",
              height=38, width=220,
              fg_color=t.get("accent"),
              text_color=t.get("bg"),
              command=do_reconstruct
              ).pack(anchor="w", padx=UI_PAD_CARD,
                     pady=(6, UI_PAD_CARD))

def _delete_comment(app: Any, post: Dict[str, Any],
                     comment_id: str,
                     render_cb: Callable) -> None:
    """Удалить комментарий."""
    if not messagebox.askyesno("Удалить комментарий?",
                                 "Удалить этот комментарий?"):
        return
    ok = ForumManager.delete_comment(
        post["id"], comment_id, app.current_user)
    if ok:
        app._show_toast("🗑️ Удалено", "info", 1500)
        with suppress(Exception):
            render_cb()
    else:
        app._show_toast("❌ Ошибка", "error")


def _edit_comment_dialog(app: Any, post: Dict[str, Any],
                           comment_id: str, current_text: str,
                           render_cb: Callable) -> None:
    """Диалог редактирования комментария."""
    t = _t()
    win = CTkToplevel(app)
    win.title("✏️ Редактировать комментарий")
    win.geometry("500x300")
    win.configure(fg_color=t.get("bg"))
    win.transient(app)
    win.grab_set()
    win.bind("<Escape>", lambda e: win.destroy())

    CTkLabel(win, text="✏️ РЕДАКТИРОВАНИЕ",
             font=CTkFont(size=16, weight="bold"),
             text_color=t.get("warning")
             ).pack(pady=(15, 8))

    entry = CTkTextbox(win, height=120,
                        font=CTkFont(size=12),
                        fg_color=t.get("bg3"),
                        border_color=t.get("card_border"),
                        text_color=t.get("text"),
                        wrap="word")
    entry.pack(fill="both", expand=True, padx=20, pady=8)
    entry.insert("1.0", current_text)
    entry.focus()

    def save() -> None:
        new_text = entry.get("1.0", "end-1c").strip()
        if not new_text:
            return
        ok = ForumManager.update_comment(
            post["id"], comment_id, app.current_user, new_text)
        if ok:
            app._show_toast("✅ Обновлено", "success")
            win.destroy()
            with suppress(Exception):
                render_cb()
        else:
            app._show_toast("❌ Ошибка", "error")

    bf = CTkFrame(win, fg_color="transparent")
    bf.pack(fill="x", padx=20, pady=(6, 15))
    CTkButton(bf, text="💾 Сохранить", height=36, width=160,
              fg_color=t.get("success"),
              text_color=t.get("bg"),
              command=save).pack(side="left")
    CTkButton(bf, text="❌ Отмена", height=36, width=120,
              fg_color="transparent", border_width=1,
              border_color=t.get("error"),
              text_color=t.get("error"),
              command=win.destroy).pack(side="left", padx=6)


# ═══════════════════════════════════════════════════════════
# Helper: current theme
# ═══════════════════════════════════════════════════════════
def _t() -> Dict[str, str]:
    """Быстрый доступ к текущей теме."""
    from nexus.app import theme_manager
    return theme_manager.get_all()


__all__ = ["register"]