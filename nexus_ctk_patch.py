"""
Monkey-patch для customtkinter — фикс бага
'str' object has no attribute 'master' в CTkScrollableFrame.

Проблема:
  В ctk_scrollable_frame.py:302
      elif widget.master is not None:
  падает когда widget — это str (id виджета), а не объект.

Решение:
  Оборачиваем _check_if_valid_scroll в try/except и
  делаем дополнительную проверку isinstance(widget, str).

Подключение:
  import nexus_ctk_patch  # перед созданием любого CTk
"""
from __future__ import annotations

from contextlib import suppress
import sys


def apply_patch() -> bool:
    """Применить патч. Возвращает True если сработало."""
    try:
        from customtkinter.windows.widgets import (
            ctk_scrollable_frame as _mod,
        )
        cls = _mod.CTkScrollableFrame
    except ImportError:
        print("⚠️ CTK patch: customtkinter не найден")
        return False

    # Оригиналы
    original_check = getattr(cls, "_check_if_valid_scroll", None)
    original_wheel = getattr(cls, "_mouse_wheel_all", None)

    if original_check is None:
        print("⚠️ CTK patch: _check_if_valid_scroll не найден")
        return False

    # ── Патч 1: _check_if_valid_scroll — безопасная версия ─
    def _safe_check(self, widget):
        """Безопасная проверка. Возвращает False при ошибках."""
        try:
            # Если widget — строка (id), не считаем его валидным
            if isinstance(widget, str):
                return False
            # Стандартные проверки customtkinter
            if widget is None:
                return False
            if not hasattr(widget, "master"):
                return False
            if widget.master is None:
                return False
            # Проверяем что widget — внутри этого scrollframe
            try:
                parent_str = str(self)
                widget_str = str(widget)
                while widget is not None:
                    if widget == self:
                        return True
                    if not hasattr(widget, "master"):
                        return False
                    widget = widget.master
            except Exception:
                return False
            return False
        except Exception:
            return False

    # ── Патч 2: _mouse_wheel_all — оборачиваем в try ──────
    def _safe_wheel(self, event):
        try:
            if hasattr(event, "widget"):
                w = event.widget
                if isinstance(w, str):
                    # Проблема здесь — подменяем на self
                    try:
                        event.widget = self
                    except Exception:
                        return
            return original_wheel(self, event)
        except Exception:
            # Игнорируем ошибки скролла — они не критичны
            pass

    # Устанавливаем
    cls._check_if_valid_scroll = _safe_check
    if original_wheel is not None:
        cls._mouse_wheel_all = _safe_wheel

    print("✅ CTK patch: applied")
    return True


def apply_bind_all_patch() -> None:
    """
    Дополнительный патч — фикс bind_all на
    CTkScrollableFrame и его дочерних элементах.
    """
    try:
        from customtkinter.windows.widgets.ctk_scrollable_frame import (
            CTkScrollableFrame,
        )
    except ImportError:
        return
    # Просто повторно применяем
    apply_patch()


# Применяем при импорте
_APPLIED = False


def ensure_patched() -> None:
    """Гарантированно применить патч (один раз)."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = apply_patch()


# Автоприменение
ensure_patched()