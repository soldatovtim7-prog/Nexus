"""
NEXUS splash — экран загрузки с этапами.

Совместим с nexus/__main__.py.
Особенности:
  • Явный parent для CTkToplevel (иначе на Wayland
    окно не мапится на экран).
  • attributes("-type", "splash") вместо
    overrideredirect (Wayland-safe).
  • Принудительный deiconify+lift+update после показа.
  • Внутренний MIN_SHOW — не даёт закрыться раньше
    чем через N секунд.
"""
from __future__ import annotations

import sys
import time
import threading
from contextlib import suppress
from typing import Any, Dict, List, Optional

import tkinter as tk
import customtkinter as ctk
from customtkinter import (
    CTk, CTkFrame, CTkLabel, CTkProgressBar, CTkFont,
)

from nexus.core import (
    APP_ICON, APP_NAME, VERSION, VERSION_NAME,
    logger,
)


# Названия этапов (индексы 0..7)
STAGE_TITLES: List[str] = [
    "Запуск NEXUS...",
    "Проверка зависимостей...",
    "Загрузка UI-библиотек...",
    "Инициализация БД...",
    "Загрузка модулей...",
    "Сканирование плагинов...",
    "Регистрация страниц...",
    "Готово!",
]


class SplashScreen:
    """Экран загрузки NEXUS."""

    WIDTH = 620
    HEIGHT = 640
    MIN_SHOW = 3.0   # минимум секунд на экране

    # ────────────────────────────────────────────────
    # INIT
    # ────────────────────────────────────────────────
    def __init__(self, total_steps: int = 7) -> None:
        self._total_steps = total_steps
        self._root: Optional[Any] = None
        self._progress_bar: Optional[Any] = None
        self._percent_lbl: Optional[Any] = None
        self._status_lbl: Optional[Any] = None
        self._stage_lbls: List[Any] = []
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._start_time = time.time()

        self._build_ui()

    # ────────────────────────────────────────────────
    # UI
    # ────────────────────────────────────────────────
    def _build_ui(self) -> None:
        t = self._theme()

        # ── Явный parent ─────────────────────────────
        # На Wayland CTkToplevel() без parent может
        # не замапиться на экран. Берём _default_root
        # если он есть (его создаёт nexus/__main__.py).
        parent = tk._default_root
        try:
            if parent is not None:
                root = ctk.CTkToplevel(parent)
            else:
                root = ctk.CTkToplevel()
        except Exception:
            root = ctk.CTkToplevel()
        self._root = root

        root.title(f"{APP_ICON} {APP_NAME} — загрузка")
        root.configure(fg_color=t.get("bg", "#0a0a1a"))
        root.resizable(False, False)

        # ── Wayland-safe «без рамки» ─────────────────
        # overrideredirect(True) на GNOME/Wayland часто
        # делает окно невидимым. Используем -type=splash.
        with suppress(Exception):
            root.attributes("-type", "splash")
        with suppress(Exception):
            root.overrideredirect(True)
        with suppress(Exception):
            root.attributes("-topmost", True)

        # ── Центрирование ────────────────────────────
        try:
            root.update_idletasks()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            x = (screen_w - self.WIDTH) // 2
            y = (screen_h - self.HEIGHT) // 2
            root.geometry(
                f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except Exception:
            root.geometry(f"{self.WIDTH}x{self.HEIGHT}")

        # ── Логотип ──────────────────────────────────
        logo = ctk.CTkFrame(root, fg_color="transparent")
        logo.pack(pady=(40, 6))

        ctk.CTkLabel(
            logo, text="🧠",
            font=ctk.CTkFont(size=64)
        ).pack()

        ctk.CTkLabel(
            logo, text=APP_NAME,
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color=t.get("primary", "#00e5ff")
        ).pack(pady=(4, 0))

        ctk.CTkLabel(
            logo, text=f"{VERSION_NAME} · v{VERSION}",
            font=ctk.CTkFont(size=12),
            text_color=t.get("text_dim", "#8888bb")
        ).pack()

        # ── Прогресс-бар ─────────────────────────────
        pf = ctk.CTkFrame(root, fg_color="transparent")
        pf.pack(fill="x", padx=60, pady=(20, 4))

        self._progress_bar = ctk.CTkProgressBar(
            pf, height=10, corner_radius=5,
            fg_color=t.get("bg3", "#1a1a3a"),
            progress_color=t.get("primary", "#00e5ff"))
        self._progress_bar.pack(fill="x")
        self._progress_bar.set(0.0)

        info = ctk.CTkFrame(pf, fg_color="transparent")
        info.pack(fill="x", pady=(4, 0))

        self._status_lbl = ctk.CTkLabel(
            info, text="Запуск...",
            font=ctk.CTkFont(size=11),
            text_color=t.get("text_glow", "#aaaaff"),
            anchor="w")
        self._status_lbl.pack(side="left")

        self._percent_lbl = ctk.CTkLabel(
            info, text="0%",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t.get("primary", "#00e5ff"),
            anchor="e")
        self._percent_lbl.pack(side="right")

        # ── Список этапов ────────────────────────────
        stages = ctk.CTkFrame(
            root, fg_color=t.get("bg2", "#12122a"),
            corner_radius=10, border_width=1,
            border_color=t.get("card_border", "#3a3a6a"))
        stages.pack(fill="both", expand=True,
                     padx=50, pady=(12, 12))

        ctk.CTkLabel(
            stages, text="ЭТАПЫ ЗАГРУЗКИ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=t.get("text_dim", "#8888bb"),
            anchor="w"
        ).pack(anchor="w", padx=14, pady=(10, 4))

        for title in STAGE_TITLES[:self._total_steps + 1]:
            row = ctk.CTkFrame(stages, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=2)
            lbl = ctk.CTkLabel(
                row, text=f"⏳ {title}",
                font=ctk.CTkFont(size=11),
                text_color=t.get("text_dim", "#8888bb"),
                anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            self._stage_lbls.append(lbl)

        # ── Цитата ───────────────────────────────────
        ctk.CTkLabel(
            root, text='"QR — мост между мирами 🌉"',
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color=t.get("text_dim", "#8888bb")
        ).pack(pady=(0, 6))

        # ── Футер ────────────────────────────────────
        py_ver = sys.version.split()[0]
        ctk.CTkLabel(
            root,
            text=f"Python {py_ver} · Tk 8.6 · CustomTkinter",
            font=ctk.CTkFont(size=9),
            text_color=t.get("text_dim", "#8888bb")
        ).pack(pady=(0, 16))

        # ── ГАРАНТИРОВАННЫЙ ПОКАЗ ────────────────────
        # На Wayland/GNOME без этого окно может не
        # отрисоваться вообще.
        with suppress(Exception):
            root.update_idletasks()
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.update()

        # Дать WM время реально нарисовать окно
        try:
            time.sleep(0.35)
            root.update()
        except Exception:
            pass

    # ────────────────────────────────────────────────
    # PUBLIC API
    # ────────────────────────────────────────────────
    def set_step(self, idx: int, total: int,
                  text: str) -> None:
        """Установить текущий шаг + общий прогресс."""
        with self._lock:
            for i in range(idx):
                if i < len(self._stage_lbls):
                    self._mark_done(i)
            if idx < len(self._stage_lbls):
                self._mark_active(idx)

            pct = max(0.0, min(1.0, idx / max(1, total)))
            self._safe_ui(
                lambda: self._progress_bar.set(pct)
                if self._progress_bar else None)
            self._safe_ui(
                lambda: self._percent_lbl.configure(
                    text=f"{int(pct * 100)}%")
                if self._percent_lbl else None)
            self._safe_ui(
                lambda: self._status_lbl.configure(text=text)
                if self._status_lbl else None)

            with suppress(Exception):
                if self._root:
                    self._root.update_idletasks()
                    self._root.update()

    def set_status(self, text: str) -> None:
        """Дополнительный статус."""
        self._safe_ui(
            lambda: self._status_lbl.configure(text=text)
            if self._status_lbl else None)

    def close(self) -> None:
        """Закрыть сплэш (потокобезопасно)."""
        if self._closed.is_set():
            return
        self._closed.set()

        # ── Минимум времени на экране ────────────────
        try:
            elapsed = time.time() - self._start_time
            if elapsed < self.MIN_SHOW:
                time.sleep(self.MIN_SHOW - elapsed)
        except Exception:
            pass

        # ── Отменяем after-задачи ────────────────────
        self._cancel_all_after()

        # ── Плавное закрытие ─────────────────────────
        try:
            steps = 6
            for i in range(steps, -1, -1):
                with suppress(Exception):
                    self._root.attributes(
                        "-alpha", i / steps)
                    self._root.update()
                time.sleep(0.02)
        except Exception:
            pass

        # ── Гарантированное уничтожение ──────────────
        with suppress(Exception):
            if self._root:
                self._root.quit()
                self._root.destroy()
        self._root = None

    def run(self) -> None:
        """Mainloop сплэша (блокирует)."""
        if not self._root:
            return
        try:
            self._root.mainloop()
        except Exception as e:
            logger.debug(f"splash mainloop: {e}")

    # ────────────────────────────────────────────────
    # INTERNAL
    # ────────────────────────────────────────────────
    def _cancel_all_after(self) -> None:
        """Отменить все pending after-задачи."""
        if not self._root:
            return
        try:
            for aid in self._root.tk.eval(
                    'after info').split():
                with suppress(Exception):
                    self._root.after_cancel(aid)
            with suppress(Exception):
                self._root.tk.call(
                    'after', 'cancel', 'all')
        except Exception as e:
            logger.debug(f"_cancel_all_after: {e}")

    def _mark_active(self, idx: int) -> None:
        if idx >= len(self._stage_lbls):
            return
        t = self._theme()
        lbl = self._stage_lbls[idx]
        title = STAGE_TITLES[idx] if idx < len(
            STAGE_TITLES) else "..."
        self._safe_ui(lambda: lbl.configure(
            text=f"⏳ {title}",
            text_color=t.get("primary", "#00e5ff")))

    def _mark_done(self, idx: int) -> None:
        if idx >= len(self._stage_lbls):
            return
        t = self._theme()
        lbl = self._stage_lbls[idx]
        title = STAGE_TITLES[idx] if idx < len(
            STAGE_TITLES) else "..."
        self._safe_ui(lambda: lbl.configure(
            text=f"✅ {title}",
            text_color=t.get("success", "#39ff14")))

    def _safe_ui(self, fn: Any) -> None:
        if not self._root:
            return
        with suppress(Exception):
            self._root.after(0, fn)

    @staticmethod
    def _theme() -> Dict[str, str]:
        with suppress(Exception):
            from nexus.app import theme_manager
            return theme_manager.get_all()
        return {}


__all__ = ["SplashScreen"]