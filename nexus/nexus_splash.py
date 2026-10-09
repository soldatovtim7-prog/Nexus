"""
NEXUS Splash Screen — лёгкое окно загрузки.
Использует только чистый tkinter (НЕ customtkinter),
поэтому работает ДО установки зависимостей.
"""
from __future__ import annotations

import queue
import tkinter as tk
from typing import Optional

_SPLASH_W = 560
_SPLASH_H = 340


class SplashScreen:
    """
    Thread-safe splash-окно с прогресс-баром.

    Пример:
        splash = SplashScreen()
        splash.set_step(1, 5, "Проверка...")
        splash.run()   # блокирует до close()
    """

    def __init__(self, total_steps: int = 6) -> None:
        self._total = max(1, total_steps)
        self._current = 0
        self._closed = False
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg="#0a0a1a")
        self._center()
        self._build_ui()

    # ──────────────────────────────────────────────
    # Публичный API (можно вызывать из любого потока)
    # ──────────────────────────────────────────────
    def set_step(self, current: int, total: int,
                  text: str = "") -> None:
        self._q.put(("step", int(current), int(total), text))

    def set_status(self, text: str) -> None:
        self._q.put(("status", text))

    def inc(self, text: str = "") -> None:
        """Увеличить на 1 шаг."""
        self._current += 1
        self._q.put(("step", self._current,
                      self._total, text))

    def close(self) -> None:
        self._q.put(("close",))

    def run(self) -> None:
        """Запустить mainloop splash. Блокирует до close()."""
        try:
            self._root.mainloop()
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Внутреннее
    # ──────────────────────────────────────────────
    def _center(self) -> None:
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = (sw - _SPLASH_W) // 2
        y = (sh - _SPLASH_H) // 2 - 40
        self._root.geometry(
            f"{_SPLASH_W}x{_SPLASH_H}+{x}+{y}")

    def _build_ui(self) -> None:
        c = tk.Canvas(
            self._root, width=_SPLASH_W, height=_SPLASH_H,
            bg="#0a0a1a", highlightthickness=0)
        c.pack(fill="both", expand=True)
        self._canvas = c

        # Иконка + название
        c.create_text(_SPLASH_W // 2, 70,
                       text="🧠", font=("Segoe UI Emoji", 52))
        c.create_text(_SPLASH_W // 2, 138,
                       text="N E X U S",
                       font=("Segoe UI", 28, "bold"),
                       fill="#00e5ff")
        c.create_text(_SPLASH_W // 2, 168,
                       text="CORE++ FIX · v1.1.1",
                       font=("Segoe UI", 11), fill="#8888bb")

        # Прогресс-бар (canvas, не ttk — лёгкий)
        bar_x1, bar_y1 = 70, 235
        bar_x2, bar_y2 = _SPLASH_W - 70, 251
        c.create_rectangle(bar_x1, bar_y1, bar_x2, bar_y2,
                            fill="#1a1a3a", outline="")
        self._bar_coords = (bar_x1, bar_y1, bar_x2, bar_y2)
        self._bar = c.create_rectangle(
            bar_x1, bar_y1, bar_x1, bar_y2,
            fill="#00e5ff", outline="")

        # Статус-текст
        self._status_id = c.create_text(
            _SPLASH_W // 2, 285,
            text="Инициализация...",
            font=("Segoe UI", 10),
            fill="#aaaaff",
            width=_SPLASH_W - 80)

        # Нижняя подпись
        c.create_text(_SPLASH_W // 2, _SPLASH_H - 18,
                       text="python · tkinter · customtkinter",
                       font=("Consolas", 8), fill="#444466")

        self._pump()

    def _pump(self) -> None:
        if self._closed:
            return
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "step":
                    _, cur, total, text = msg
                    self._current = cur
                    self._total = max(1, total)
                    self._render_bar()
                    if text:
                        self._canvas.itemconfig(
                            self._status_id, text=text)
                elif kind == "status":
                    self._canvas.itemconfig(
                        self._status_id, text=msg[1])
                elif kind == "close":
                    self._closed = True
                    try:
                        self._root.destroy()
                    except Exception:
                        pass
                    return
        except queue.Empty:
            pass
        try:
            self._root.after(40, self._pump)
        except Exception:
            pass

    def _render_bar(self) -> None:
        x1, y1, x2, y2 = self._bar_coords
        ratio = min(1.0, self._current / self._total)
        x_end = x1 + int((x2 - x1) * ratio)
        try:
            self._canvas.coords(self._bar, x1, y1, x_end, y2)
        except Exception:
            pass