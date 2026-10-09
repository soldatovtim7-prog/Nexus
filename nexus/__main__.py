"""
Точка входа: python3 -m nexus

Запускает splash + boot worker + NexusApp.
"""
from __future__ import annotations

import sys
import time
import threading
import traceback
from typing import Any, Dict
from contextlib import suppress


# ═══════════════════════════════════════════════════════════
# ФИЛЬТР STDERR — временно ОТКЛЮЧЁН для отладки
# ═══════════════════════════════════════════════════════════
# Когда найдём баг в Code Studio — включим обратно,
# поменяв DEBUG_STDERR = False
DEBUG_STDERR = True   # ← True = показываем ВСЁ (для отладки)
                      # ← False = прячем мусор от Tk


def _install_stderr_filter() -> None:
    """
    Перехватывает stderr и молча отбрасывает
    сообщения Tkinter типа:
        invalid command name "...!label"
            while executing
        "..."
            ("after" script)

    Эти ошибки безопасны — они возникают, когда
    after-задачи выполняются после уничтожения виджета.
    """
    import io

    class _FilteredStderr:
        _SKIP_PATTERNS = (
            "invalid command name",
            "while executing",
            '("after" script)',
            '("after" idle)',
        )

        def __init__(self, real):
            self._real = real
            self._buf = ""

        def write(self, s):
            if not isinstance(s, str):
                s = str(s)
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if any(p in line
                        for p in self._SKIP_PATTERNS):
                    continue
                try:
                    self._real.write(line + "\n")
                except Exception:
                    pass
            return len(s)

        def flush(self):
            with suppress(Exception):
                if self._buf:
                    self._real.write(self._buf)
                    self._buf = ""
                self._real.flush()

    sys.stderr = _FilteredStderr(sys.stderr)


def _cancel_all_after(widget: Any) -> None:
    """Отменить pending after-задачи у виджета."""
    if widget is None:
        return
    with suppress(Exception):
        for aid in widget.tk.eval('after info').split():
            with suppress(Exception):
                widget.after_cancel(aid)


def main() -> int:
    """Финальный bootstrap NEXUS."""
    # ── 0) Перехват мусорных stderr-сообщений ────────
    # ⚠️ ВРЕМЕННО ОТКЛЮЧЕНО для отладки
    if not DEBUG_STDERR:
        _install_stderr_filter()

    # ── 1) Проверка Python ───────────────────────────
    if sys.version_info < (3, 10):
        sys.stderr.write(
            f"❌ Требуется Python 3.10+.\n"
            f"У вас {sys.version_info.major}."
            f"{sys.version_info.minor}\n")
        return 1

    # ── 2) Скрытый Tk-корень ─────────────────────────
    import tkinter as tk
    import customtkinter as ctk

    hidden_root = ctk.CTk()
    hidden_root.withdraw()
    hidden_root.update_idletasks()
    hidden_root.update()

    # ── 3) Splash показываем СРАЗУ ───────────────────
    from nexus.nexus_splash import SplashScreen
    splash = SplashScreen(total_steps=7)
    splash.set_step(0, 7, "Запуск NEXUS...")

    # ── 4) Boot в фоне ───────────────────────────────
    result: Dict[str, Any] = {
        "ok": False, "err": None, "app": None,
    }

    def boot_worker() -> None:
        try:
            splash.set_step(1, 7,
                            "Проверка зависимостей...")
            from nexus.deps import ensure_deps
            deps = ensure_deps(
                progress=lambda msg:
                    splash.set_status(msg))

            splash.set_step(2, 7,
                            "Загрузка UI-библиотек...")
            import customtkinter  # noqa: F401
            with suppress(Exception):
                import nexus_ctk_patch
                nexus_ctk_patch.ensure_patched()
            from PIL import Image, ImageDraw  # noqa: F401
            import qrcode  # noqa: F401

            splash.set_step(3, 7,
                            "Инициализация базы данных...")
            from nexus.storage import db
            db.initialize()

            splash.set_step(4, 7, "Загрузка модулей...")
            from nexus import (  # noqa: F401
                crypto, domain, security, qr,
                integrations, sandbox,
            )

            splash.set_step(5, 7,
                            "Сканирование плагинов...")
            from nexus.integrations import plugin_manager
            plugins_count = len(
                plugin_manager.get_plugins())
            if plugins_count:
                splash.set_status(
                    f"Найдено плагинов: {plugins_count}")

            splash.set_step(6, 7,
                            "Регистрация страниц...")

            splash.set_step(7, 7, "Готово!")
            result["ok"] = True

        except SystemExit as e:
            result["err"] = (
                f"Критическая ошибка bootstrap: {e}")
            splash.set_status("❌ Ошибка")
        except Exception as e:
            result["err"] = str(e)
            splash.set_status("❌ Ошибка")
            try:
                sys.stderr.write(
                    "\n" + "=" * 60 + "\n")
                sys.stderr.write("❌ BOOTSTRAP FAILED:\n")
                sys.stderr.write("=" * 60 + "\n")
                traceback.print_exc()
                sys.stderr.write("=" * 60 + "\n\n")
            except Exception:
                pass

    thread = threading.Thread(
        target=boot_worker, daemon=True,
        name="nexus-boot")
    thread.start()

    # ── 5) Автозакрытие сплэша ───────────────────────
    def close_splash_when_ready() -> None:
        thread.join(timeout=120)
        MIN_SHOW = 3.0
        elapsed = time.time() - splash._start_time
        if elapsed < MIN_SHOW:
            time.sleep(MIN_SHOW - elapsed)
        time.sleep(0.3)
        splash.close()

    close_thread = threading.Thread(
        target=close_splash_when_ready, daemon=True)
    close_thread.start()

    # ── 6) Сплэш mainloop с таймаутом ────────────────
    splash_thread = threading.Thread(
        target=splash.run, daemon=True,
        name="nexus-splash-mainloop")
    splash_thread.start()
    splash_thread.join(timeout=10)

    with suppress(Exception):
        if splash._root:
            _cancel_all_after(splash._root)
            splash._root.quit()
            splash._root.destroy()
            splash._root = None

    # ── 7) Ошибки boot ───────────────────────────────
    if result.get("err"):
        print(
            f"\n❌ Не удалось запустить NEXUS: "
            f"{result['err']}",
            file=sys.stderr)
        print(f"   Логи: nexus_logs/", file=sys.stderr)
        with suppress(Exception):
            _cancel_all_after(hidden_root)
            hidden_root.destroy()
        return 1

    # ── 8) Health-check БД ───────────────────────────
    try:
        from nexus.storage import db
        ok, msg = db.health_check()
        if not ok:
            sys.stderr.write(f"⚠️ DB health: {msg}\n")
    except Exception:
        pass

    # ── 9) Уничтожаем скрытый корень + сброс Tk ─────
    with suppress(Exception):
        _cancel_all_after(hidden_root)
    with suppress(Exception):
        hidden_root.destroy()
    with suppress(Exception):
        tk._default_root = None

    # ── 10) Плашка ───────────────────────────────────
    print()
    print("  " + "=" * 56)
    print(f"  🧠 NEXUS v1.1.1 CORE++ FIX  —  ready")
    print("  " + "=" * 56)
    print()

    # ── 11) NexusApp ─────────────────────────────────
    try:
        from nexus.app import NexusApp
        app = NexusApp()
        result["app"] = app
        app.mainloop()
        return 0
    except KeyboardInterrupt:
        print("\n👋 До свидания!")
        return 0
    except Exception as e:
        sys.stderr.write(
            f"\n❌ Фатальная ошибка приложения: {e}\n")
        traceback.print_exc()
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "NEXUS — Фатальная ошибка",
                f"Приложение остановлено:\n\n{e}\n\n"
                f"Логи: nexus_logs/")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())