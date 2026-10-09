"""
Точка входа: python3 -m nexus

Запускает splash + boot worker + NexusApp.
"""
from __future__ import annotations

import sys
import threading
import traceback
from typing import Any, Dict, Optional
from contextlib import suppress


def main() -> int:
    """Финальный bootstrap NEXUS."""
    # 1) Проверка Python
    if sys.version_info < (3, 10):
        sys.stderr.write(
            f"❌ Требуется Python 3.10+.\n"
            f"У вас {sys.version_info.major}."
            f"{sys.version_info.minor}\n")
        return 1

    # 2) Splash показываем СРАЗУ
    from nexus.nexus_splash import SplashScreen
    splash = SplashScreen(total_steps=7)
    splash.set_step(0, 7, "Запуск NEXUS...")

    # 3) Boot в фоне
    result: Dict[str, Any] = {
        "ok": False, "err": None, "app": None,
    }

    def boot_worker() -> None:
        try:
            # ── Шаг 1: зависимости ────────────────────
            splash.set_step(1, 7,
                            "Проверка зависимостей...")
            from nexus.deps import ensure_deps
            deps = ensure_deps(
                progress=lambda msg:
                    splash.set_status(msg))

            # ── Шаг 2: UI-библиотеки ──────────────────
            splash.set_step(2, 7,
                            "Загрузка UI-библиотек...")
            import customtkinter  # noqa: F401
            # Патч customtkinter (фикс 'str' has no 'master')
            with suppress(Exception):
                import nexus_ctk_patch
                nexus_ctk_patch.ensure_patched()
            from PIL import Image, ImageDraw  # noqa: F401
            import qrcode  # noqa: F401

            # ── Шаг 3: БД ─────────────────────────────
            splash.set_step(3, 7,
                            "Инициализация базы данных...")
            from nexus.storage import db
            db.initialize()

            # ── Шаг 4: Core модули ────────────────────
            splash.set_step(4, 7,
                            "Загрузка модулей...")
            from nexus import (  # noqa: F401
                crypto, domain, security, qr,
                integrations, sandbox,
            )

            # ── Шаг 5: Плагины ────────────────────────
            splash.set_step(5, 7,
                            "Сканирование плагинов...")
            from nexus.integrations import plugin_manager
            plugins_count = len(
                plugin_manager.get_plugins())
            if plugins_count:
                splash.set_status(
                    f"Найдено плагинов: {plugins_count}")

            # ── Шаг 6: Регистрация хоткеев и страниц ──
            splash.set_step(6, 7,
                            "Регистрация страниц...")

            # ── Шаг 7: App ────────────────────────────
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
                sys.stderr.write(
                    "❌ BOOTSTRAP FAILED:\n")
                sys.stderr.write("=" * 60 + "\n")
                traceback.print_exc()
                sys.stderr.write(
                    "=" * 60 + "\n\n")
            except Exception:
                pass

    thread = threading.Thread(
        target=boot_worker, daemon=True,
        name="nexus-boot")
    thread.start()

    @staticmethod
    def _patch_ctk_scrollable() -> None:
        """Monkey-patch CTkScrollableFrame."""
        try:
            from customtkinter.windows.widgets import (
                ctk_scrollable_frame as _mod,
            )
            cls = _mod.CTkScrollableFrame
            orig_wheel = getattr(cls, "_mouse_wheel_all", None)

            def _safe_wheel(self, event):
                try:
                    if hasattr(event, "widget") and \
                            isinstance(event.widget, str):
                        with suppress(Exception):
                            event.widget = self
                    if orig_wheel:
                        return orig_wheel(self, event)
                except Exception:
                    pass
            cls._mouse_wheel_all = _safe_wheel
        except Exception:
            pass

    # 4) Splash mainloop — блокируется до close()
    # Дадим splash немного поработать (проверки в фоне)
    def close_splash_when_ready() -> None:
        # Ждём завершения boot (или таймаута)
        thread.join(timeout=120)
        # Небольшая задержка для читаемости
        import time as _t
        _t.sleep(0.4)
        splash.close()

    close_thread = threading.Thread(
        target=close_splash_when_ready, daemon=True)
    close_thread.start()
    splash.run()  # блокирует, пока splash.close()

    # 5) Проверка результата
    if result.get("err"):
        print(
            f"\n❌ Не удалось запустить NEXUS: "
            f"{result['err']}",
            file=sys.stderr)
        print(
            f"   Логи: nexus_logs/",
            file=sys.stderr)
        return 1

    # 6) Финальная проверка что всё OK
    try:
        from nexus.storage import db
        ok, msg = db.health_check()
        if not ok:
            sys.stderr.write(
                f"⚠️ DB health: {msg}\n")
    except Exception:
        pass

    # 7) Создаём и запускаем NexusApp
    print()
    print("  " + "=" * 56)
    print(f"  🧠 NEXUS v1.1.1 CORE++ FIX  —  ready")
    print("  " + "=" * 56)
    print()

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