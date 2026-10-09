"""
NEXUS app — главное окно приложения.

Волна 4.2a: каркас NexusApp + auth + sidebar +
статус-бар + тосты + хоткеи.

Страницы (_page_*) и диалоги (_show_*) — в 4.2b/c.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import webbrowser
import time
import traceback
from contextlib import suppress
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from tkinter import (
    BooleanVar, StringVar, IntVar, DoubleVar,
    filedialog, messagebox,
)

from typing import (
    Any, Callable, Dict, List, Optional, Tuple,
)

from nexus.qr import (
    QRBatchProcessor, QRExporter, QRLogoManager,
    QRTemplates, qr_gen, qr_history, DynamicQRManager,
)

import customtkinter as ctk
from customtkinter import (
    CTk, CTkFrame, CTkLabel, CTkButton, CTkEntry, CTkTextbox,
    CTkScrollableFrame, CTkOptionMenu, CTkProgressBar,
    CTkCheckBox, CTkRadioButton, CTkSwitch, CTkSegmentedButton,
    CTkImage, CTkToplevel, CTkFont,
)
from PIL import Image, ImageTk  # noqa: F401

from nexus.core import (
    APP_ICON, APP_NAME, APP_TITLE, APP_YEAR, APP_AUTHOR,
    BUILD, VERSION, VERSION_NAME,
    UI_BUTTON_HEIGHT, UI_BUTTON_WIDTH, UI_HEADER_HEIGHT,
    UI_PAD_INNER, UI_PAD_OUTER, UI_SIDEBAR_WIDTH,
    UI_STATUS_BAR_HEIGHT, UI_TOAST_HEIGHT,
    UI_QUICK_BTN_MIN_W, UI_HOME_WIDGET_MIN_H,
    UI_HOME_WIDGET_MIN_W,
    UI_TOAST_DEBOUNCE_MS, STATUS_BAR_INTERVAL_MS,
    UI_QR_DEBOUNCE_MS,               # ← добавлено
    UI_SEARCH_DEBOUNCE_MS,           # ← добавлено
    UI_CODE_AUTOSAVE_MS,             # ← добавлено
    UI_CODE_HIGHLIGHT_MS,            # ← добавлено
    UI_WRAP_MED,                     # ← добавлено
    UI_WRAP_WIDE,                    # ← добавлено
    UI_VIRTUAL_BATCH_SIZE,           # ← добавлено
    UI_PAD_CARD,                     # ← добавлено
    UI_FORM_ENTRY_HEIGHT,            # ← добавлено
    UI_QR_PREVIEW_SIZE,              # ← добавлено
    CODE_MULTI_TAB_MAX,              # ← добавлено
    DAILY_QUESTS_COUNT,              # ← добавлено
    PASSWORD_MIN_LENGTH,             # ← добавлено
    SHAMIR_PARTS, SHAMIR_THRESHOLD,  # ← добавлено
    QR_HISTORY_LIMIT, QR_BATCH_LIMIT, # ← добавлено
    MAX_TAGS,                        # ← добавлено
    copy_to_clipboard,               # ← добавлено
    clipboard_image,                 # ← добавлено
    extract_tags,                    # ← добавлено
    fmt_size,                        # ← добавлено
    parse_iso,                       # ← добавлено
    sanitize_filename,               # ← добавлено
    ACTIVITY_THROTTLE_SEC, TOAST_STACK_MAX,
    SESSION_REFRESH_INTERVAL_MS,
    ACHIEVEMENT_BATCH_INTERVAL_MS,
    AUTO_BACKUP_INTERVAL, DRAFT_SAVE_INTERVAL,
    beep_async, detect_system_theme, fmt_dt, fmt_duration,
    fmt_mmss, gen_id, get_unhandled_errors, humanize_relative,
    logger, now_iso, quote, truncate,
    _tk_callback_excepthook,
)
from nexus.storage import (
    TimerPool, cache, db, event_bus, executor, toast_dedup,
    undo_stack,
)
from nexus.domain import (
    BlockchainManager, CodeDraftManager, CodeHistory,
    CodeTemplates, DailyQuests, DraftManager, ForumManager,
    NoteManager, PatternEngine, PomodoroManager, SnippetManager,
    TaskManager, TimerManager, pattern_engine,
    achievement_system, chat_bot, context_tracker,
    level_system, pomodoro, snippet_manager, timer_manager,
)
from nexus.security import (
    SessionManager, UserManager, vault_session,
)
from nexus.integrations import (
    APIServer, api_server, webhook_worker,
)
from nexus.qr import qr_gen, qr_history


# ═══════════════════════════════════════════════════════════
# I18N (минимальный, для sidebar)
# ═══════════════════════════════════════════════════════════
class I18n:
    _current: str = "ru"

    STRINGS: Dict[str, Dict[str, str]] = {
        "ru": {
            "Home": "ГЛАВНАЯ", "Notes": "ПАМЯТЬ",
            "Code": "КОД", "Tasks": "ЗАДАЧИ",
            "Forum": "ФОРУМ", "QR": "QR-СТУДИЯ",
            "Passwords": "ПАРОЛИ",
            "Encrypt": "ШИФРОВАНИЕ",
            "Recovery": "ВОССТАНОВЛЕНИЕ",
            "Blockchain": "БЛОКЧЕЙН",
            "Achievements": "ДОСТИЖЕНИЯ",
            "Stats": "СТАТИСТИКА", "Audit": "АУДИТ",
            "About": "О НАС", "Settings": "НАСТРОЙКИ",
            "Export": "ЭКСПОРТ", "Import": "ИМПОРТ",
            "Vaults": "ХРАНИЛИЩА", "Sync": "СИНХРОНИЗАЦИЯ",
            "Plugins": "ПЛАГИНЫ", "Webhooks": "WEBHOOKS",
            "API": "API-СЕРВЕР", "Graph": "ГРАФ ПАМЯТИ",
            "Snippets": "СНИППЕТЫ", "Timers": "ТАЙМЕРЫ",
        },
        "en": {
            "Home": "HOME", "Notes": "MEMORY",
            "Code": "CODE", "Tasks": "TASKS",
            "Forum": "FORUM", "QR": "QR STUDIO",
            "Passwords": "PASSWORDS", "Encrypt": "ENCRYPT",
            "Recovery": "RECOVERY",
            "Blockchain": "BLOCKCHAIN",
            "Achievements": "ACHIEVEMENTS",
            "Stats": "STATS", "Audit": "AUDIT",
            "About": "ABOUT", "Settings": "SETTINGS",
            "Export": "EXPORT", "Import": "IMPORT",
            "Vaults": "VAULTS", "Sync": "SYNC",
            "Plugins": "PLUGINS", "Webhooks": "WEBHOOKS",
            "API": "API SERVER", "Graph": "MEMORY GRAPH",
            "Snippets": "SNIPPETS", "Timers": "TIMERS",
        },
        "uk": {
            "Home": "ГОЛОВНА", "Notes": "ПАМ'ЯТЬ",
            "Code": "КОД", "Tasks": "ЗАВДАННЯ",
            "Forum": "ФОРУМ", "QR": "QR-СТУДІЯ",
            "Passwords": "ПАРОЛІ",
            "Encrypt": "ШИФРУВАННЯ",
            "Recovery": "ВІДНОВЛЕННЯ",
            "Blockchain": "БЛОКЧЕЙН",
            "Achievements": "ДОСЯГНЕННЯ",
            "Stats": "СТАТИСТИКА", "Audit": "АУДИТ",
            "About": "ПРО НАС",
            "Settings": "НАЛАШТУВАННЯ",
            "Export": "ЕКСПОРТ", "Import": "ІМПОРТ",
            "Vaults": "СХОВИЩА",
            "Sync": "СИНХРОНІЗАЦІЯ",
            "Plugins": "ПЛАГІНИ", "Webhooks": "WEBHOOKS",
            "API": "API-СЕРВЕР", "Graph": "ГРАФ ПАМ'ЯТІ",
            "Snippets": "СНІПЕТИ", "Timers": "ТАЙМЕРИ",
        },
        "pl": {
            "Home": "GŁÓWNA", "Notes": "PAMIĘĆ",
            "Code": "KOD", "Tasks": "ZADANIA",
            "Forum": "FORUM", "QR": "STUDIO QR",
            "Passwords": "HASŁA",
            "Encrypt": "SZYFROWANIE",
            "Recovery": "ODZYSKIWANIE",
            "Blockchain": "BLOCKCHAIN",
            "Achievements": "OSIĄGNIĘCIA",
            "Stats": "STATYSTYKI", "Audit": "AUDYT",
            "About": "O NAS",
            "Settings": "USTAWIENIA",
            "Export": "EKSPORT", "Import": "IMPORT",
            "Vaults": "SKARBCE",
            "Sync": "SYNCHRONIZACJA",
            "Plugins": "WTYCZKI",
            "Webhooks": "WEBHOOKI",
            "API": "SERWER API",
            "Graph": "GRAF PAMIĘCI",
            "Snippets": "FRAGMENTY", "Timers": "TIMERY",
        },
    }

    @classmethod
    def set_lang(cls, lang: str) -> None:
        if lang in cls.STRINGS:
            cls._current = lang

    @classmethod
    def auto_detect(cls) -> str:
        import locale
        import os as _os
        import sys as _sys
        try:
            if _sys.platform == "win32":
                loc = locale.getdefaultlocale()[0] or ""
                if loc.startswith("ru"):
                    return "ru"
                if loc.startswith("uk"):
                    return "uk"
                if loc.startswith("pl"):
                    return "pl"
                return "en"
            lang = (_os.environ.get("LC_ALL")
                    or _os.environ.get("LC_MESSAGES")
                    or _os.environ.get("LANG") or "")
            lang = lang.lower()
            if lang.startswith("ru"):
                return "ru"
            if lang.startswith("uk"):
                return "uk"
            if lang.startswith("pl"):
                return "pl"
            return "en"
        except Exception:
            return "ru"

    @classmethod
    def t(cls, key: str) -> str:
        return cls.STRINGS.get(cls._current, {}).get(key, key)

    @classmethod
    def available(cls) -> List[str]:
        return sorted(cls.STRINGS.keys())


# ═══════════════════════════════════════════════════════════
# THEME MANAGER (сокращённый — полный в core.py)
# ═══════════════════════════════════════════════════════════
class ThemeManager:
    _current: str = "dark"
    _lock = threading.RLock()

    BUILTIN: Dict[str, Dict[str, str]] = {
        "dark": {
            "bg": "#0a0a1a", "bg2": "#12122a",
            "bg3": "#1a1a3a", "card": "#1a1a2e",
            "card_border": "#3a3a6a",
            "text": "#e8e8ff", "text_dim": "#8888bb",
            "text_glow": "#aaaaff",
            "primary": "#00e5ff", "secondary": "#ff2d95",
            "accent": "#b026ff",
            "success": "#39ff14", "warning": "#ffaa00",
            "error": "#ff1744",
            "code_bg": "#0a0a15", "code_text": "#e8e8e8",
            "gradient1": "#00e5ff", "gradient2": "#b026ff",
        },
        "light": {
            "bg": "#f0f0ff", "bg2": "#e0e0f0",
            "bg3": "#d0d0e0", "card": "#e8e8f8",
            "card_border": "#b0b0d0",
            "text": "#1a1a2e", "text_dim": "#666688",
            "text_glow": "#444466",
            "primary": "#0088cc", "secondary": "#cc1166",
            "accent": "#7700cc",
            "success": "#00bb00", "warning": "#cc7700",
            "error": "#cc0033",
            "code_bg": "#f5f5f5", "code_text": "#1a1a1a",
            "gradient1": "#0088cc", "gradient2": "#7700cc",
        },
        "cyber": {
            "bg": "#000a0a", "bg2": "#001a1a",
            "bg3": "#002a2a", "card": "#001515",
            "card_border": "#004444",
            "text": "#00ffaa", "text_dim": "#00aa77",
            "text_glow": "#33ffcc",
            "primary": "#00ff88", "secondary": "#ff0044",
            "accent": "#00ccff",
            "success": "#00ff44", "warning": "#ffaa00",
            "error": "#ff0044",
            "code_bg": "#000505", "code_text": "#00ff88",
            "gradient1": "#00ff88", "gradient2": "#00ccff",
        },
        "sunset": {
            "bg": "#1a0a0a", "bg2": "#2a1212",
            "bg3": "#3a1a1a", "card": "#2e1a1a",
            "card_border": "#6a3a3a",
            "text": "#ffe8e8", "text_dim": "#bb8888",
            "text_glow": "#ffaaaa",
            "primary": "#ff6644", "secondary": "#ff2d95",
            "accent": "#ffaa44",
            "success": "#44ff66", "warning": "#ffcc00",
            "error": "#ff1744",
            "code_bg": "#150a0a", "code_text": "#ffe8e8",
            "gradient1": "#ff6644", "gradient2": "#ff2d95",
        },
        "forest": {
            "bg": "#0a1a0a", "bg2": "#122a12",
            "bg3": "#1a3a1a", "card": "#1a2e1a",
            "card_border": "#3a6a3a",
            "text": "#e8ffe8", "text_dim": "#88bb88",
            "text_glow": "#aaffaa",
            "primary": "#44ff44", "secondary": "#ff6644",
            "accent": "#44ffaa",
            "success": "#66ff44", "warning": "#ffcc44",
            "error": "#ff4444",
            "code_bg": "#0a150a", "code_text": "#e8ffe8",
            "gradient1": "#44ff44", "gradient2": "#44ffaa",
        },
        "ocean": {
            "bg": "#0a0a2a", "bg2": "#0a1a3a",
            "bg3": "#0a2a4a", "card": "#0a1a3a",
            "card_border": "#2a5a8a",
            "text": "#e8f0ff", "text_dim": "#88aacc",
            "text_glow": "#aaccff",
            "primary": "#4a9aff", "secondary": "#ff6a4a",
            "accent": "#6affc0",
            "success": "#44ff88", "warning": "#ffcc44",
            "error": "#ff4455",
            "code_bg": "#0a0a20", "code_text": "#e8f0ff",
            "gradient1": "#4a9aff", "gradient2": "#6affc0",
        },
        "matrix_green": {
            "bg": "#000800", "bg2": "#001200",
            "bg3": "#001c00", "card": "#001400",
            "card_border": "#003300",
            "text": "#00ff00", "text_dim": "#008800",
            "text_glow": "#44ff44",
            "primary": "#00ff00", "secondary": "#88ff00",
            "accent": "#00ff88",
            "success": "#00ff00", "warning": "#ccff00",
            "error": "#ff3300",
            "code_bg": "#000600", "code_text": "#00ff00",
            "gradient1": "#00ff00", "gradient2": "#00ff88",
        },
    }

    @classmethod
    def get(cls, key: str) -> str:
        with cls._lock:
            theme = cls.BUILTIN.get(cls._current) or {}
            return theme.get(key, "#ffffff")

    @classmethod
    def get_all(cls, name: Optional[str] = None
                 ) -> Dict[str, str]:
        with cls._lock:
            return dict(
                cls.BUILTIN.get(name or cls._current, {}))

    @classmethod
    def set(cls, name: str) -> bool:
        with cls._lock:
            if name not in cls.BUILTIN:
                return False
            cls._current = name
            return True

    @classmethod
    def current(cls) -> str:
        return cls._current

    @classmethod
    def list(cls) -> List[str]:
        return sorted(cls.BUILTIN.keys())

    @classmethod
    def auto_by_system(cls) -> str:
        return detect_system_theme()

    @classmethod
    def auto_by_time(cls) -> str:
        h = datetime.now().hour
        return "dark" if h < 7 or h >= 21 else "light"


theme_manager = ThemeManager()


# ═══════════════════════════════════════════════════════════
# APP CONFIG (минимальный)
# ═══════════════════════════════════════════════════════════
class AppConfig:
    """Конфиг приложения — сохранение в JSON."""

    def __init__(self, **kwargs: Any) -> None:
        from nexus.core import CONFIG_PATH
        self._path = CONFIG_PATH
        # Значения по умолчанию
        self.theme: str = "dark"
        self.font_size: int = 14
        self.auto_backup: bool = True
        self.notifications: bool = True
        self.remember_me: bool = False
        self.last_user: str = ""
        self.language: str = "ru"
        self.qr_default_style: str = "neon_cyan"
        self.qr_default_size: int = 400
        self.qr_live_preview: bool = True
        self.qr_last_logo: str = ""
        self.code_auto_save: bool = True
        self.code_font_size: int = 13
        self.code_last_language: str = "python"
        self.code_syntax_highlight: bool = True
        self.auto_theme: bool = False
        self.auto_theme_time: bool = False
        self.auto_detect_language: bool = False
        self.autosave_drafts: bool = True
        self.toast_duration: int = 3500
        self.sound_effects: bool = False
        self.session_timeout: int = 240
        self.auto_logout_idle: bool = False
        self.auto_lock_idle: bool = False
        self.auto_lock_timeout: int = 15
        self.session_auto_login: bool = True
        self.dev_mode: bool = False
        self.sync_auto: bool = False
        self.sync_path: str = ""
        self.markdown_render: bool = True
        self.memory_hints_enabled: bool = True
        self.notify_deadlines: bool = True
        self.hibp_check_enabled: bool = True
        self.api_enabled: bool = False
        self.api_port: int = 8765
        self.quests_enabled: bool = True
        self.pomodoro_auto_start: bool = False
        self.webhook_queue_enabled: bool = True
        self.dynamic_qr_enabled: bool = False
        self.export_count: int = 0
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    @classmethod
    def load(cls) -> "AppConfig":
        from nexus.core import CONFIG_PATH
        import json
        if not CONFIG_PATH.exists():
            return cls()
        try:
            with open(CONFIG_PATH, "r",
                      encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(**{k: v for k, v in data.items()
                       if hasattr(cls(), k)})

    def save(self) -> bool:
        import json
        from pathlib import Path as _Path

        def _default(obj: Any) -> Any:
            """Fallback для несериализуемых типов."""
            if isinstance(obj, _Path):
                return str(obj)
            if isinstance(obj, set):
                return list(obj)
            if isinstance(obj, tuple):
                return list(obj)
            if isinstance(obj, bytes):
                return obj.decode("utf-8", errors="replace")
            return str(obj)

        try:
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.__dict__, f, indent=2,
                          ensure_ascii=False,
                          default=_default)
            tmp.replace(self._path)
            return True
        except Exception as e:
            logger.error(f"Config.save: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# NEXUS APP
# ═══════════════════════════════════════════════════════════
class NexusApp(ctk.CTk):
    """
    Главное окно NEXUS.

    Жизненный цикл:
      1. __init__ → загрузка конфига, темы, splash готов
      2. _try_auto_login → если есть session.token — вход
      3. _show_auth → окно логина (если нет сессии)
      4. _init_app → после успешного входа
         - sidebar + content + statusbar
         - регистрация hotkeys
         - запуск watchdog'ов
    """

    MENU_STRUCTURE: List[Tuple[str, List[Tuple[str, str]]]] = [
        ("🌀 ОСНОВНОЕ", [("🏠", "Home"), ("📱", "QR")]),
        ("💻 РАЗРАБОТКА", [("💻", "Code"),
                            ("✂️", "Snippets")]),
        ("📋 ПРОДУКТИВНОСТЬ", [("✅", "Tasks"),
                                 ("⏱️", "Timers")]),
        ("💬 ОБЩЕНИЕ", [("💬", "Forum")]),
        ("🔐 БЕЗОПАСНОСТЬ", [("🔑", "Passwords"),
                                ("🔏", "Encrypt"),
                                ("🔑", "Recovery")]),
        ("⛓️ БЛОКЧЕЙН", [("⛓️", "Blockchain")]),
        ("⚙️ СИСТЕМА", [("🏆", "Achievements"),
                          ("📊", "Stats"), ("🔍", "Audit"),
                          ("ℹ️", "About"),
                          ("⚙️", "Settings")]),
        ("📦 ДАННЫЕ", [("📤", "Export"),
                        ("📥", "Import"),
                        ("💾", "Vaults"),
                        ("☁️", "Sync")]),
        ("🔌 РАСШИРЕНИЯ", [("🧩", "Plugins"),
                            ("🌐", "Webhooks"),
                            ("📡", "API")]),
    ]

    def __init__(self) -> None:
        super().__init__()

        # ── Конфиг ───────────────────────────────────
        self.config_data = AppConfig.load()

        if self.config_data.auto_detect_language:
            I18n.set_lang(I18n.auto_detect())
        else:
            I18n.set_lang(self.config_data.language)

        if self.config_data.auto_theme_time:
            theme_manager.set(theme_manager.auto_by_time())
        elif self.config_data.auto_theme:
            theme_manager.set(theme_manager.auto_by_system())
        else:
            theme_manager.set(self.config_data.theme)

        # ── Окно ─────────────────────────────────────
        self.title(f"{APP_ICON} {APP_TITLE}")
        self.geometry("1500x900")
        self.minsize(1200, 720)

        # ── Состояние ────────────────────────────────
        self.current_user: Optional[str] = None
        self.is_auth: bool = False
        self.pages: Dict[str, Callable] = {}
        self.sidebar_buttons: Dict[str, CTkButton] = {}
        self._btn_page_by_label: Dict[str, str] = {}
        self.current_page: str = "Home"
        self.toasts: List[Any] = []
        self._is_closing: bool = False
        self._app_initialized: bool = False
        self._session_start: float = time.time()
        self._last_activity: float = time.time()

        # Таймеры (после-id для отмены)
        self._timers: Dict[str, Optional[str]] = {
            "backup": None, "autosave": None, "status": None,
            "session": None, "session_refresh": None,
            "achievements": None, "search_debounce": None,
            "queue_pump": None, "timers_refresh": None,
            "settings_save": None,
            "code_autosave": None, "code_highlight": None,
            "qr_live_preview": None, "pomodoro_tick": None,
            "vault_autolock": None,
        }
        self._main_queue: "queue.Queue" = queue.Queue()
        self._timer_pool = TimerPool(self)

        # Окна (ленивая инициализация)
        self._auth_win: Optional[Any] = None
        self._search_window: Optional[Any] = None
        self._cmd_palette: Optional[Any] = None
        self._quick_note_win: Optional[Any] = None
        self._recall_win: Optional[Any] = None
        self._code_history_win: Optional[Any] = None
        self._qr_history_win: Optional[Any] = None
        self._qr_batch_win: Optional[Any] = None
        self._pomodoro_win: Optional[Any] = None
        self._quests_win: Optional[Any] = None
        self._api_tokens_win: Optional[Any] = None
        # PRO-модули (заполняется в _init_app)
        self._pro_modules: Dict[str, bool] = {}

        # Code Studio state
        self._code_out: Optional[Any] = None
        self._code_widget: Optional[Any] = None
        self._code_status_lbl: Optional[Any] = None
        self._code_lang_var: Optional[Any] = None
        self._code_template_var: Optional[Any] = None
        self._code_tabs: List[Dict[str, Any]] = []
        self._code_current_tab: int = 0
        self._code_tabs_container: Optional[Any] = None
        self._code_tabs_bar: Optional[Any] = None

        # QR Studio state
        self._qr_preview_image: Optional[Any] = None
        self._qr_preview_data: Optional[str] = None
        self._qr_label: Optional[Any] = None
        self._qr_mode_data: Dict[str, Any] = {}
        self._qr_current_mode: str = "text"
        self._qr_get_mode_data: Optional[Callable] = None
        self._qr_style_var: Optional[Any] = None
        self._qr_size_var: Optional[Any] = None
        self._qr_logo_path: Optional[str] = None
        self._qr_logo_lbl: Optional[Any] = None
        self._qr_info_lbl: Optional[Any] = None

        self._vault_items: List[Dict[str, Any]] = []
        self._tasks_view: str = "list"

        # Хоткеи (загружаются через встроенный класс ниже)
        self.hotkeys: Dict[str, str] = HotkeysManager.load()

        self._theme_history: List[str] = []
        self._qr_styles_used: set = set()
        self._code_templates_used: set = set()
        self._last_page_name: str = ""
        self._last_page_time: float = 0.0

        # ── Grid ─────────────────────────────────────
        self.grid_columnconfigure(0, weight=0,
                                    minsize=UI_SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Обработчики ──────────────────────────────
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind_all("<KeyPress>", self._mark_activity,
                        add="+")
        self.bind_all("<ButtonPress>", self._mark_activity,
                        add="+")


        try:
            self.report_callback_exception = _tk_callback_excepthook
        except Exception:
            pass

        # ── Setup ────────────────────────────────────
        self._setup_hotkeys()
        event_bus.subscribe(
            "achievement.unlocked",
            self._on_achievement_unlocked)
        self._start_main_queue_pump()
        self._check_db_health()

        # ── Фоновые сервисы ──────────────────────────
        webhook_worker.start()
        if self.config_data.api_enabled:
            with suppress(Exception):
                api_server.start(self.config_data.api_port)

                # Дополнительные хоткеи для Code Studio
        with suppress(Exception):
            self.bind_all(
                "<Control-Shift-F>",
                lambda e: (self._format_code(), "break")[1])
        with suppress(Exception):
            self.bind_all(
                "<Control-Shift-S>",
                lambda e: (self._save_code_as_file(),
                            "break")[1])
        with suppress(Exception):
            self.bind_all(
                "<Control-Shift-A>",
                lambda e: (self._run_all_tabs(), "break")[1])

        # ── Auth или Auto-login ──────────────────────
        if self._try_auto_login():
            return
        try:
            self._show_auth()
        except Exception as e:
            logger.error(f"_show_auth failed: {e}")
            from tkinter import messagebox
            messagebox.showerror(
                "NEXUS",
                f"Не удалось запустить: {e}\n"
                "Проверьте nexus_logs/")

    # ────────────────────────────────────────────────
    # AUTO LOGIN / DB HEALTH
    # ────────────────────────────────────────────────
    def _try_auto_login(self) -> bool:
        if not (self.config_data.session_auto_login
                or self.config_data.remember_me):
            return False
        try:
            session_user = SessionManager.load()
            if (not session_user
                    or not UserManager.exists(session_user)):
                return False
            self.current_user = session_user
            self.is_auth = True
            with suppress(Exception):
                db.execute(
                    "UPDATE users SET last_active = ? "
                    "WHERE username = ?",
                    (now_iso(), session_user))
            self.config_data.last_user = session_user
            self.config_data.remember_me = True
            self.config_data.session_auto_login = True
            self.config_data.save()
            level_system.update_streak(session_user)
            logger.info(f"Auto-login OK: {session_user}")
            self._init_app()
            return True
        except Exception as e:
            logger.warning(f"Auto-login failed: {e}")
            return False

    def _check_db_health(self) -> None:
        try:
            ok, msg = db.health_check()
            if ok:
                return
            logger.warning(f"DB health: {msg}")
            from nexus.integrations import BackupManager
            backups = BackupManager.list_backups()
            if not backups:
                return
            from tkinter import messagebox
            result = messagebox.askyesno(
                "Восстановление БД",
                f"БД повреждена: {msg}\n\n"
                f"Найден бэкап от "
                f"{backups[0]['mtime']:%d.%m.%Y %H:%M}.\n"
                "Восстановить?")
            if result:
                ok2, _ = BackupManager.restore_from(
                    backups[0]["path"])
                if ok2:
                    messagebox.showinfo(
                        "OK",
                        "Восстановлено. Перезапустите NEXUS.")
                    import sys
                    sys.exit(0)
        except Exception:
            pass

    # ────────────────────────────────────────────────
    # QUEUE PUMP / SAFE AFTER
    # ────────────────────────────────────────────────
    def _start_main_queue_pump(self) -> None:
        def pump() -> None:
            if self._is_closing:
                return
            try:
                processed = 0
                while processed < 50:
                    try:
                        fn, args, kwargs = (
                            self._main_queue.get_nowait())
                    except queue.Empty:
                        break
                    try:
                        fn(*args, **kwargs)
                    except Exception as e:
                        logger.error(f"main queue fn: {e}")
                    processed += 1
            except Exception as e:
                logger.error(f"queue pump: {e}")
            with suppress(Exception):
                self._timers["queue_pump"] = self.after(
                    40, pump)
        self._timers["queue_pump"] = self.after(40, pump)

    def _safe_after(self, ms: int, callback: Callable,
                     *args: Any,
                     **kwargs: Any) -> Optional[str]:
        """
        Thread-safe `after`.
        Из main thread — self.after.
        Из другого потока — Timer + main_queue.
        """
        if self._is_closing:
            return None
        if threading.current_thread() is \
                threading.main_thread():
            try:
                return self.after(
                    ms,
                    lambda: self._run_safe(callback, args,
                                            kwargs))
            except Exception:
                return None
        try:
            t = threading.Timer(
                max(0, ms) / 1000.0,
                lambda: self._main_queue.put(
                    (callback, args, kwargs)))
            t.daemon = True
            t.start()
        except Exception:
            pass
        return None

    @staticmethod
    def _run_safe(callback: Callable,
                   args: Tuple[Any, ...],
                   kwargs: Dict[str, Any]) -> None:
        with suppress(Exception):
            callback(*args, **kwargs)

    def _cancel_all_timers(self) -> None:
        for name, tid in list(self._timers.items()):
            if not tid:
                continue
            with suppress(Exception):
                self.after_cancel(tid)
            self._timers[name] = None
        self._timer_pool.cancel_all()

    def _mark_activity(self, _e: Any = None) -> None:
        now = time.time()
        if now - self._last_activity \
                < ACTIVITY_THROTTLE_SEC:
            return
        self._last_activity = now

    # ────────────────────────────────────────────────
    # ACHIEVEMENT LISTENER
    # ────────────────────────────────────────────────
    def _on_achievement_unlocked(self, user: str,
                                   achievement: str,
                                   **kw: Any) -> None:
        if user != self.current_user:
            return
        try:
            meta = achievement_system.get_all(
                include_hidden=True).get(achievement, {})
            name = meta.get("name", achievement)
            self._safe_after(0, self._show_toast,
                             f"🏆 {name}", "achievement", 4000)
            if self.config_data.sound_effects:
                self._safe_after(0, beep_async)
        except Exception:
            pass

    # ────────────────────────────────────────────────
    # INIT APP
    # ────────────────────────────────────────────────
    def _init_app(self) -> None:
        if self._app_initialized:
            self._cancel_all_timers()
        self._app_initialized = True
        self._session_start = time.time()
        self._last_activity = time.time()
        self._theme_history = []
        self._qr_styles_used = set()
        self._code_templates_used = set()

        for w in self.winfo_children():
            with suppress(Exception):
                w.destroy()

        mode = ("light"
                if theme_manager.current() == "light"
                else "dark")
        ctk.set_appearance_mode(mode)
        ctk.set_default_color_theme("dark-blue")

        self.grid_columnconfigure(0, weight=0,
                                    minsize=UI_SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._create_sidebar()
        self._create_content()
        self._create_status_bar()

        # Импортируем страницы из app_pages (4.2b)
        # ВАЖНО: ДО первого _show_page, иначе Home
        # отрисуется пустым (pages ещё нет)
        self._register_pages()

        # FTS state для UI-потока
        with suppress(Exception):
            if not db._fts_ready:
                rows = db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='notes_fts'")
                if rows:
                    db._fts_ready = True

        # ── PRO-модули (QR PRO, CODE PRO, Debugger) ────
        # Импортируем в try/except — если файлов нет,
        # приложение работает без них
        self._pro_modules: Dict[str, bool] = {
            "qr_pro": False,
            "code_pro": False,
            "debugger": False,
        }

        def _attach_pro() -> None:
            """Навесить PRO-модули. Вызывается в фоне."""
            # QR PRO
            try:
                import nexus_qr_pro
                nexus_qr_pro.attach(self, globals())
                self._pro_modules["qr_pro"] = True
            except ImportError:
                logger.info(
                    "QR PRO: модуль не найден (опционально)")
            except Exception as e:
                logger.warning(f"QR PRO attach: {e}")

            # CODE PRO
            try:
                import nexus_code_pro
                nexus_code_pro.attach(self, globals())
                self._pro_modules["code_pro"] = True
            except ImportError:
                logger.info(
                    "CODE PRO: модуль не найден (опционально)")
            except Exception as e:
                logger.warning(f"CODE PRO attach: {e}")

            # Debugger
            try:
                import nexus_debugger
                nexus_debugger.attach(self, globals())
                self._pro_modules["debugger"] = True
            except ImportError:
                logger.info(
                    "Debugger: модуль не найден (опционально)")
            except Exception as e:
                logger.warning(f"Debugger attach: {e}")

            # Связываем debugger с редактором (если есть)
            with suppress(Exception):
                if (self._pro_modules.get("debugger")
                        and self._code_widget is not None):
                    self.debugger_bind_editor(self._code_widget)

            # Связываем CODE PRO с редактором
            with suppress(Exception):
                if (self._pro_modules.get("code_pro")
                        and self._code_widget is not None):
                    self.code_pro_bind_widget()

            # Итоговый лог
            loaded = [k for k, v in self._pro_modules.items() if v]
            if loaded:
                logger.info(
                    f"✅ PRO modules loaded: {', '.join(loaded)}")

        # Активируем PRO в фоне — не блокируем UI
        executor.submit_safe(_attach_pro)

        # Отдельный хоткей для отладчика (Ctrl+Shift+D)
        with suppress(Exception):
            self.bind_all(
                "<Control-Shift-D>",
                lambda e: (self._open_debugger(), "break")[1])

        self._show_page("Home")


    def _register_pages(self) -> None:
        """
        Регистрация всех _page_* методов.
        В 4.2a — заглушки. В 4.2b — реальные.
        """
        # Импортируем реальные страницы, если они есть
        try:
            from nexus import app_pages
            app_pages.register(self)
        except ImportError:
            # Fallback — заглушки
            for name in ["Home", "Code", "Snippets", "Tasks",
                          "Timers", "Forum", "QR", "Passwords",
                          "Encrypt", "Recovery", "Blockchain",
                          "Achievements", "Stats", "Audit",
                          "About", "Settings", "Export",
                          "Import", "Vaults", "Sync",
                          "Plugins", "Webhooks", "API"]:
                self.pages[name] = self._make_stub_page(name)

    def _make_stub_page(self, name: str) -> Callable:
        def stub(parent: Any) -> None:
            self._page_header(parent, name.upper(), "🚧",
                               "страница в разработке")
            card = CTkFrame(parent,
                             fg_color=theme_manager.get("bg2"),
                             corner_radius=10,
                             border_width=2,
                             border_color=theme_manager.get(
                                 "warning"))
            card.pack(fill="both", expand=True,
                       pady=UI_PAD_INNER)
            CTkLabel(card,
                     text=f"🚧 Страница «{name}»\n\n"
                          f"Будет добавлена в волне 4.2b",
                     font=CTkFont(size=16, weight="bold"),
                     text_color=theme_manager.get("text_dim"),
                     justify="center").pack(expand=True)
        return stub

    # ────────────────────────────────────────────────
    # WATCHDOGS
    # ────────────────────────────────────────────────
    def _timers_watchdog(self) -> None:
        if self._is_closing or not self.is_auth:
            return
        try:
            if self.current_page == "Timers":
                self._refresh_page()
        except Exception:
            pass
        if not self._is_closing:
            self._timers["timers_refresh"] = self._safe_after(
                3000, self._timers_watchdog)

    def _vault_autolock_watchdog(self) -> None:
        if self._is_closing:
            return
        try:
            if vault_session.needs_auto_lock():
                vault_session.lock()
                self._show_toast(
                    "🔒 Хранилище заблокировано", "warning")
                if self.current_page == "Passwords":
                    self._refresh_page()
        except Exception:
            pass
        if not self._is_closing:
            self._timers["vault_autolock"] = self._safe_after(
                60000, self._vault_autolock_watchdog)

    def _on_pomodoro_tick(self, phase: str,
                            rem: int) -> None:
        if self._is_closing:
            return
        try:
            if (self._pomodoro_win
                    and self._pomodoro_win.winfo_exists()
                    and hasattr(self._pomodoro_win,
                                 "update_label")):
                with suppress(Exception):
                    self._pomodoro_win.update_label(phase, rem)
        except Exception:
            pass

    def _on_pomodoro_done(self, phase: str, user: str,
                            task_id: str) -> None:
        if self._is_closing:
            return
        label = {"work": "🍅 Работа",
                 "short": "☕ Перерыв",
                 "long": "🛋️ Длинный перерыв"}.get(
            phase, phase)
        self._safe_after(0, self._show_toast,
                         f"{label} завершён!", "success",
                         5000)
        self._safe_after(0, beep_async)
        if task_id and phase == "work":
            timer_manager.stop(task_id)

    def _auto_backup(self) -> None:
        def _work() -> None:
            try:
                db.backup()
                import random
                if random.random() < 0.2:
                    db.incremental_backup()
            except Exception as e:
                logger.debug(f"auto backup: {e}")
        executor.submit_safe(_work)
        if self.config_data.auto_backup \
                and not self._is_closing:
            self._timers["backup"] = self._safe_after(
                AUTO_BACKUP_INTERVAL * 1000,
                self._auto_backup)

    def _auto_save_drafts(self) -> None:
        if (not self.config_data.autosave_drafts
                or self._is_closing):
            return
        self._timers["autosave"] = self._safe_after(
            DRAFT_SAVE_INTERVAL * 1000,
            self._auto_save_drafts)

    def _session_watchdog(self) -> None:
        if self._is_closing or not self.is_auth:
            return
        if not self.config_data.auto_logout_idle:
            return
        try:
            timeout = max(
                4 * 3600,
                self.config_data.session_timeout * 60)
            if time.time() - self._last_activity > timeout:
                self._show_toast("⏱️ Сессия истекла",
                                  "warning", 3000)
                self._safe_after(1000, self._logout)
                return
        except Exception:
            pass
        self._timers["session"] = self._safe_after(
            60000, self._session_watchdog)

    def _auto_refresh_session(self) -> None:
        if self._is_closing or not self.is_auth:
            return
        try:
            if (self.current_user
                    and self.config_data.session_auto_login):
                SessionManager.refresh(self.current_user)
        except Exception:
            pass
        if not self._is_closing:
            self._timers["session_refresh"] = self._safe_after(
                SESSION_REFRESH_INTERVAL_MS,
                self._auto_refresh_session)

    def _batch_achievements_watchdog(self) -> None:
        if self._is_closing or not self.is_auth:
            return
        try:
            if self.current_user:
                with suppress(Exception):
                    NoteManager.recompute_decay(
                        self.current_user)
                new = achievement_system.batch_check(
                    self.current_user)
                if new:
                    logger.info(f"Achievements: {new}")
        except Exception:
            pass
        if not self._is_closing:
            self._timers["achievements"] = self._safe_after(
                ACHIEVEMENT_BATCH_INTERVAL_MS,
                self._batch_achievements_watchdog)

    # ────────────────────────────────────────────────
    # WELCOME TASK
    # ────────────────────────────────────────────────
    def _create_welcome_task(self) -> None:
        try:
            active = TaskManager.get_active()
            if active:
                return
            TaskManager.create(
                title="✅ Изучить возможности NEXUS v1.1.1",
                author=self.current_user, priority="high",
                tags=["#NEXUS", "#старт"],
                category="Обучение",
                description=(
                    "• 💻 Code Studio: 14 шаблонов, "
                    "подсветка синтаксиса, multi-tab\n"
                    "• 📱 QR-студия: 16 стилей, "
                    "live-preview, батч\n"
                    "• 🍅 Pomodoro и 🎯 ежедневные квесты\n"
                    "• ⛓️ Блокчейн с Merkle-root и "
                    "smart-contracts-lite\n"
                    "• 📡 HTTP API (Bearer + rate-limit)\n"
                    "• 🔐 Shamir's Secret Sharing для "
                    "recovery\n"
                    "• 🌐 Markdown в форуме"))
        except Exception as e:
            logger.debug(f"welcome task: {e}")

    # ────────────────────────────────────────────────
    # LOGOUT / CLOSING
    # ────────────────────────────────────────────────
    def _logout(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("Выход",
                                     "Выйти из аккаунта?"):
            return
        try:
            if self.current_user:
                with suppress(Exception):
                    self._save_code_draft()
                with suppress(Exception):
                    elapsed = int(
                        time.time() - self._session_start)
                    db.execute(
                        "UPDATE user_stats SET total_time = "
                        "COALESCE(total_time, 0) + ? "
                        "WHERE user = ?",
                        (elapsed, self.current_user))
                with suppress(Exception):
                    db.backup()
                SessionManager.clear(
                    specific_user=self.current_user)
            self.config_data.remember_me = False
            self.config_data.last_user = ""
            self.config_data.session_auto_login = False
            self.config_data.save()
        except Exception:
            pass
        self._cancel_all_timers()
        self.current_user = None
        self.is_auth = False
        self._app_initialized = False
        self._session_start = time.time()
        # Очистка state
        self._qr_preview_image = None
        self._qr_preview_data = None
        self._qr_label = None
        self._qr_mode_data.clear()
        self._qr_get_mode_data = None
        self._code_out = None
        self._code_widget = None
        self._code_status_lbl = None
        self._code_tabs = []
        self._code_current_tab = 0
        # Закрыть отладчик при logout
        with suppress(Exception):
            if hasattr(self, "debugger_close"):
                self.debugger_close()
        self._pro_modules = {}
        # Закрыть отладчик при logout
        with suppress(Exception):
            if hasattr(self, "debugger_close"):
                self.debugger_close()
        self._pro_modules = {}
        self._code_tabs = []
        self._code_current_tab = 0
        vault_session.lock()
        self._vault_items = []
        pomodoro.stop()
        vault_session.lock()
        self._vault_items = []
        pomodoro.stop()
        with suppress(Exception):
            pattern_engine.invalidate(self.current_user)
        for w in self.winfo_children():
            with suppress(Exception):
                w.destroy()
        self._show_auth()

    def _on_closing(self) -> None:
        if self._is_closing:
            return
        self._is_closing = True
        try:
            if (self.current_user
                    and self.config_data.session_auto_login):
                SessionManager.save(self.current_user,
                                      force=True)
        except Exception:
            pass
        try:
            if self.current_user:
                self.config_data.remember_me = True
                self.config_data.last_user = self.current_user
                self.config_data.save()
        except Exception:
            pass
        with suppress(Exception):
            self._save_code_draft()
        try:
            if self.current_user:
                elapsed = int(time.time() - self._session_start)
                db.execute(
                    "UPDATE user_stats SET total_time = "
                    "COALESCE(total_time, 0) + ? "
                    "WHERE user = ?",
                    (elapsed, self.current_user))
        except Exception:
            pass
        with suppress(Exception):
            timer_manager.stop_all()
        with suppress(Exception):
            pomodoro.stop()
        with suppress(Exception):
            webhook_worker.stop()
        with suppress(Exception):
            api_server.stop()
        if (self.config_data.sync_auto
                and self.config_data.sync_path):
            with suppress(Exception):
                from nexus.integrations import SyncManager
                SyncManager.sync_to_local_folder(
                    self.config_data.sync_path)
        self._cancel_all_timers()
        with suppress(Exception):
            self.config_data.save()
        with suppress(Exception):
            db.backup()
        with suppress(Exception):
            cache.close()
        with suppress(Exception):
            executor.shutdown(wait=False)
        with suppress(Exception):
            qr_gen.clear_cache()
        with suppress(Exception):
            vault_session.lock()
        with suppress(Exception):
            db.close()
        with suppress(Exception):
            import gc
            gc.collect()
        with suppress(Exception):
            self.destroy()

    # ────────────────────────────────────────────────
    # SAVE CODE DRAFT (заглушка для logout)
    # ────────────────────────────────────────────────
    def _save_code_draft(self) -> None:
        """Полная версия — в 4.2b."""
        w = self._code_widget
        if w is None:
            return
        try:
            if w.winfo_exists():
                code = w.get("1.0", "end-1c")
                lang = (self._code_lang_var.get()
                         if self._code_lang_var else "python")
                CodeDraftManager.save(code, lang)
        except Exception:
            pass

    # ────────────────────────────────────────────────
    # SIDEBAR
    # ────────────────────────────────────────────────
    def _create_sidebar(self) -> None:
        self.sidebar = CTkFrame(self,
                                  width=UI_SIDEBAR_WIDTH,
                                  corner_radius=0,
                                  fg_color=theme_manager.get(
                                      "bg2"))
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self._render_sidebar_logo()
        self._render_sidebar_progress()
        self._render_sidebar_quick_actions()
        self._render_sidebar_search()
        self._render_sidebar_menu()

    def _render_sidebar_logo(self) -> None:
        logo = CTkFrame(self.sidebar, fg_color="transparent")
        logo.pack(pady=(12, 8), padx=12, fill="x")
        inner = CTkFrame(logo, fg_color="transparent")
        inner.pack(anchor="center")
        lb = CTkFrame(inner, width=50, height=50,
                       fg_color="transparent",
                       border_width=3,
                       border_color=theme_manager.get("primary"),
                       corner_radius=25)
        lb.pack(pady=(0, 3))
        lb.pack_propagate(False)
        CTkLabel(lb, text=APP_ICON, font=CTkFont(size=28),
                 text_color=theme_manager.get("primary")
                 ).pack(expand=True)
        CTkLabel(inner, text=APP_NAME,
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack()
        CTkLabel(inner,
                 text=(self.current_user or "").upper(),
                 font=CTkFont(size=12),
                 text_color=theme_manager.get("success")
                 ).pack()
        CTkLabel(inner,
                 text=f"v{VERSION} // {VERSION_NAME}",
                 font=CTkFont(size=10),
                 text_color=theme_manager.get("text_dim")
                 ).pack()

    def _render_sidebar_progress(self) -> None:
        progress = level_system.get_progress(self.current_user)
        rank = level_system.get_rank(progress["level"])
        lf = CTkFrame(self.sidebar,
                       fg_color=theme_manager.get("bg3"),
                       corner_radius=8)
        lf.pack(fill="x", padx=12, pady=(5, 8))
        row = CTkFrame(lf, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(6, 0))
        CTkLabel(row, text=f"🏆 {rank}",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("primary"),
                 anchor="w").pack(side="left")
        CTkLabel(row,
                 text=f"⭐ {progress['xp']} XP",
                 font=CTkFont(size=10),
                 text_color=theme_manager.get("text_dim"),
                 anchor="e").pack(side="right")
        pbar = CTkProgressBar(
            lf, height=8, corner_radius=4,
            progress_color=theme_manager.get("gradient1"),
            fg_color=theme_manager.get("bg"))
        pbar.pack(fill="x", padx=8, pady=(2, 8))
        pbar.set(progress["progress"])

    def _render_sidebar_quick_actions(self) -> None:
        bf = CTkFrame(self.sidebar, fg_color="transparent")
        bf.pack(fill="x", padx=12, pady=(4, 4))
        for txt, color, cmd in [
            ("🚪", theme_manager.get("error"), self._logout),
            ("🍅", theme_manager.get("error"),
             self._show_pomodoro),
            ("🎯", theme_manager.get("warning"),
             self._show_quests),
            ("⌘", theme_manager.get("primary"),
             self._show_command_palette),
        ]:
            CTkButton(bf, text=txt, width=40, height=34,
                      font=CTkFont(size=15, weight="bold"),
                      fg_color=color,
                      text_color=theme_manager.get("bg"),
                      command=cmd).pack(side="left", padx=2)

    def _render_sidebar_search(self) -> None:
        CTkFrame(self.sidebar, height=2,
                 fg_color=theme_manager.get("primary")
                 ).pack(fill="x", padx=15, pady=5)
        self.menu_search = CTkEntry(
            self.sidebar, placeholder_text="🔍 Поиск...",
            height=32, font=CTkFont(size=12),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"))
        self.menu_search.pack(fill="x", padx=12, pady=(8, 4))
        self.menu_search.bind(
            "<KeyRelease>", lambda e: self._filter_menu())
        CTkFrame(self.sidebar, height=2,
                 fg_color=theme_manager.get("primary")
                 ).pack(fill="x", padx=15, pady=5)

    def _render_sidebar_menu(self) -> None:
        self.menu_container = CTkScrollableFrame(
            self.sidebar, fg_color="transparent")
        self.menu_container.pack(fill="both", expand=True,
                                    padx=5, pady=5)
        self._build_menu()

    def _build_menu(self) -> None:
        for w in self.menu_container.winfo_children():
            w.destroy()
        self.sidebar_buttons.clear()
        self._btn_page_by_label.clear()
        for cat, items in self.MENU_STRUCTURE:
            CTkLabel(self.menu_container, text=cat,
                     font=CTkFont(size=12, weight="bold"),
                     text_color=theme_manager.get("text_dim"),
                     anchor="w").pack(anchor="w", padx=8,
                                        pady=(8, 3))
            for icon, page_name in items:
                display = I18n.t(page_name)
                btn = CTkButton(
                    self.menu_container,
                    text=f"{icon}  {display}",
                    anchor="w", fg_color="transparent",
                    hover_color=theme_manager.get("bg3"),
                    text_color=theme_manager.get("text"),
                    font=CTkFont(size=13, weight="bold"),
                    height=34, corner_radius=5,
                    command=lambda pn=page_name:
                        self._show_page(pn))
                btn.pack(fill="x", padx=8, pady=2)
                self.sidebar_buttons[display] = btn
                self._btn_page_by_label[display] = page_name

    def _filter_menu(self) -> None:
        q = self.menu_search.get().strip().lower()
        for label, btn in self.sidebar_buttons.items():
            try:
                if not q or q in label.lower():
                    btn.pack_configure(fill="x", padx=8,
                                        pady=2)
                else:
                    btn.pack_forget()
            except Exception:
                pass

    # ────────────────────────────────────────────────
    # CONTENT AREA
    # ────────────────────────────────────────────────
    def _create_content(self) -> None:
        self.content = CTkFrame(
            self, fg_color=theme_manager.get("bg"),
            corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=0)
        self.content.grid_rowconfigure(2, weight=0)

        # Toast container
        self.toast_container = CTkFrame(
            self.content, fg_color="transparent")
        self.toast_container.grid(row=2, column=0, sticky="ew",
                                    padx=UI_PAD_OUTER,
                                    pady=(UI_PAD_OUTER, 0))
        self.toast_container.grid_columnconfigure(0, weight=1)
        self.toast_container.grid_remove()

        # Pages container
        self.page_container = CTkFrame(
            self.content, fg_color=theme_manager.get("bg"),
            corner_radius=0)
        self.page_container.grid(row=0, column=0, sticky="nsew",
                                    padx=UI_PAD_OUTER,
                                    pady=UI_PAD_OUTER)
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)

    def _show_page(self, name: str) -> None:
        if self._is_closing or not self.is_auth:
            return
        try:
            if not self.page_container.winfo_exists():
                return
        except Exception:
            return
        if (self.current_page == "Code"
                and self._code_widget is not None
                and self.config_data.code_auto_save):
            self._save_code_draft()

        # Дедуп перерисовки < 400ms
        now = time.time()
        last = self._last_page_time
        last_name = self._last_page_name
        if name == last_name and now - last < 0.4:
            self.current_page = name
            return
        self._last_page_name = name
        self._last_page_time = now
        self.current_page = name

        t0 = time.perf_counter()
        with suppress(Exception):
            context_tracker.record("page_view",
                                    {"page": name})

        # Сбросить ссылки на виджеты Code Studio
        # ДО уничтожения, чтобы не осталось
        # висячих ссылок.
        with suppress(Exception):
            self._reset_code_refs()

        # Сбросить ссылки на виджеты Code Studio
        # ДО уничтожения — иначе останутся висячие
        # ссылки на мёртвые виджеты.
        with suppress(Exception):
            self._reset_code_refs()

        # Уничтожить старые виджеты
        children = self.page_container.winfo_children()
        for w in children:
            try:
                w.destroy()
            except Exception:
                pass
        with suppress(Exception):
            self.page_container.update_idletasks()

                # Сбросить ссылки на виджеты Code Studio —
        # они уже уничтожены вместе со старыми
        # виджетами в page_container.
        # Иначе при возврате в Code получим
        # 'invalid command name ...!label'.
        with suppress(Exception):
            self._code_out = None
            self._code_widget = None
            self._code_status_lbl = None
            self._code_lang_var = None
            self._code_template_var = None
            self._code_tabs_container = None
            self._code_tabs_bar = None

        # Подсветить кнопку
        for label, btn in self.sidebar_buttons.items():
            page_name = self._btn_page_by_label.get(
                label, label)
            with suppress(Exception):
                if page_name == name:
                    btn.configure(
                        fg_color=theme_manager.get("bg3"),
                        text_color=theme_manager.get(
                            "primary"))
                else:
                    btn.configure(
                        fg_color="transparent",
                        text_color=theme_manager.get("text"))

        # Рендер страницы
        if name in self.pages:
            container = CTkFrame(
                self.page_container,
                fg_color=theme_manager.get("bg"))
            container.pack(fill="both", expand=True)
            try:
                self.pages[name](container)
            except Exception as e:
                logger.exception(f"Page '{name}' error")
                for w in container.winfo_children():
                    with suppress(Exception):
                        w.destroy()
                tb = traceback.format_exc()
                CTkLabel(
                    container,
                    text=f"❌ Ошибка «{name}»\n\n{tb[-800:]}",
                    font=CTkFont(family="Courier", size=10),
                    text_color=theme_manager.get("error"),
                    justify="left", wraplength=900,
                    anchor="w"
                ).pack(pady=20, padx=40, fill="both",
                        expand=True)

        dt = (time.perf_counter() - t0) * 1000
        logger.info(f"⏱️ Page '{name}' render: {dt:.0f}ms")

    def _refresh_page(self) -> None:
        if self.current_page:
            self._show_page(self.current_page)

    # ────────────────────────────────────────────────
    # STATUS BAR
    # ────────────────────────────────────────────────
    def _create_status_bar(self) -> None:
        self.status_bar = CTkFrame(
            self.content, height=UI_STATUS_BAR_HEIGHT,
            fg_color=theme_manager.get("bg2"),
            corner_radius=0)
        self.status_bar.grid(row=1, column=0, sticky="ew")
        self.status_bar.grid_propagate(False)
        self.status_bar.grid_columnconfigure(0, weight=0)
        self.status_bar.grid_columnconfigure(1, weight=1)
        self.status_bar.grid_columnconfigure(2, weight=0)
        self.status_bar.grid_rowconfigure(0, weight=1)

        self.status_left = CTkLabel(
            self.status_bar,
            text=f"👤 {self.current_user} | "
                 f"NEXUS {VERSION}",
            font=CTkFont(size=11),
            text_color=theme_manager.get("text_dim"),
            anchor="w")
        self.status_left.grid(row=0, column=0, sticky="w",
                                padx=12)

        self.status_center = CTkLabel(
            self.status_bar, text="", font=CTkFont(size=11),
            text_color=theme_manager.get("text_glow"),
            anchor="center")
        self.status_center.grid(row=0, column=1, sticky="ew")

        self.status_right = CTkLabel(
            self.status_bar, text="", font=CTkFont(size=11),
            text_color=theme_manager.get("text_dim"),
            anchor="e")
        self.status_right.grid(row=0, column=2, sticky="e",
                                 padx=12)

        self._update_status_bar()

    def _update_status_bar(self) -> None:
        if self._is_closing:
            return
        try:
            rows = db.execute(
                "SELECT "
                "  us.level, us.xp, us.streak, "
                "  (SELECT COUNT(*) FROM achievements "
                "   WHERE user = us.user) AS ach_count, "
                "  (SELECT COUNT(*) FROM qr_history "
                "   WHERE user = us.user) AS qr_count, "
                "  (SELECT COUNT(*) FROM code_history "
                "   WHERE user = us.user) AS code_count, "
                "  (SELECT COUNT(*) FROM tasks "
                "   WHERE author = us.user "
                "     AND status != 'done' "
                "     AND deleted_at = '') AS tasks_count "
                "FROM user_stats us WHERE us.user = ?",
                (self.current_user,))
            if rows:
                r = rows[0]
                level = r["level"] or 1
                xp = r["xp"] or 0
                streak = r["streak"] or 0
                ach_count = r["ach_count"] or 0
                qr_count = r["qr_count"] or 0
                code_count = r["code_count"] or 0
                tasks_count = r["tasks_count"] or 0
            else:
                level = xp = streak = 0
                ach_count = qr_count = 0
                code_count = tasks_count = 0

            elapsed = int(time.time() - self._session_start)
            h, m = divmod(elapsed, 3600)
            m, s = divmod(m, 60)
            session_str = f"{h:02d}:{m:02d}:{s:02d}"

            timer_count = timer_manager.active_count()
            timer_str = (f" | ⏱️ {timer_count}"
                          if timer_count > 0 else "")
            pomo_str = ""
            if pomodoro.is_active():
                pomo_str = (f" | 🍅 "
                             f"{fmt_mmss(pomodoro.remaining())}")

            active = executor.active_count()
            pulse = f" | 🔄 {active}" if active > 0 else ""

            fts = "FTS5" if db.fts_available() else "LIKE"
            vault_str = (" | 🔓 Vault"
                          if vault_session.is_unlocked else "")

            # FIX: "⭐ {xp} XP"
            self.status_center.configure(
                text=f"🏆 Ур.{level} | ⭐ {xp} XP | "
                     f"🔥 {streak}д | 🏅 {ach_count} | "
                     f"📱 {qr_count} | 💻 {code_count} | "
                     f"✅ {tasks_count} | "
                     f"⏱️ {session_str}"
                     f"{timer_str}{pomo_str}"
                     f"{vault_str}{pulse} | 🔍 {fts}")
            self.status_right.configure(
                text=datetime.now().strftime(
                    "%d.%m.%Y %H:%M:%S"))
        except Exception as e:
            logger.debug(f"status bar: {e}")
        if not self._is_closing:
            self._timers["status"] = self._safe_after(
                STATUS_BAR_INTERVAL_MS,
                self._update_status_bar)

    # ────────────────────────────────────────────────
    # TOASTS
    # ────────────────────────────────────────────────
    def _show_toast(self, msg: str, typ: str = "info",
                     duration: Optional[int] = None) -> None:
        if (not self.config_data.notifications
                or self._is_closing):
            return
        # Защита от мёртвого toast_container
        try:
            if (not hasattr(self, "toast_container")
                    or self.toast_container is None
                    or not self.toast_container.winfo_exists()):
                return
        except Exception:
            return
        # Защита: toast_container может быть невалиден,
        # если окно перерисовывается или toast_container
        # уже уничтожен — иначе падает с
        # 'invalid command name ...!label'
        try:
            if (not hasattr(self, "toast_container")
                    or not self.toast_container
                    or not self.toast_container.winfo_exists()):
                return
        except Exception:
            return
        # Фильтр пустых/мусорных ошибок
        # Защита: toast_container может быть невалиден
        # при запуске кода до полной инициализации UI
        try:
            if (not hasattr(self, "toast_container")
                    or not self.toast_container
                    or not self.toast_container.winfo_exists()):
                return
        except Exception:
            return
        # Фильтр пустых/мусорных ошибок
        if typ == "error":
            import re
            if re.fullmatch(r"[❌⚠️\s]*\d+\.\d+с\s*", msg):
                return
            if not msg.strip():
                return
        if (typ in ("info", "success", "warning")
                and not toast_dedup.should_show(msg)):
            return

        if duration is None:
            duration = self.config_data.toast_duration

        if len(self.toasts) >= TOAST_STACK_MAX:
            try:
                oldest = self.toasts.pop(0)
                if oldest.winfo_exists():
                    oldest.destroy()
            except Exception:
                pass
            with suppress(Exception):
                self.update_idletasks()

        colors = {
            "info": (theme_manager.get("primary"),
                     theme_manager.get("bg2")),
            "success": (theme_manager.get("success"),
                         "#003322"),
            "warning": (theme_manager.get("warning"),
                         "#332200"),
            "error": (theme_manager.get("error"),
                       "#440022"),
            "cyber": (theme_manager.get("primary"),
                       "#001a1a"),
            "achievement": (theme_manager.get("accent"),
                             "#220044"),
        }
        icons = {"info": "ℹ️", "success": "✅",
                 "warning": "⚠️", "error": "❌",
                 "cyber": "🧠", "achievement": "🏆"}

        color, bg = colors.get(typ, colors["info"])
        try:
            wrap_len = max(300, self.winfo_width() - 500)
        except Exception:
            wrap_len = 900

        try:
            self.toast_container.grid()
            toast = CTkFrame(
                self.toast_container, fg_color=bg,
                corner_radius=12,
                height=UI_TOAST_HEIGHT,
                border_width=2, border_color=color)
            toast.pack(fill="x", pady=(0, 6))
            toast.pack_propagate(False)
            self.toasts.append(toast)

            inner = CTkFrame(toast, fg_color="transparent")
            inner.pack(fill="both", expand=True,
                        padx=12, pady=8)

            CTkLabel(inner, text=icons.get(typ, "📌"),
                     font=CTkFont(size=18),
                     width=30).pack(side="left", padx=(0, 6))

            CTkLabel(inner, text=msg,
                     font=CTkFont(size=13, weight="bold"),
                     text_color=color, anchor="w",
                     justify="left",
                     wraplength=wrap_len
                     ).pack(side="left", fill="x", expand=True)

            CTkButton(inner, text="✕", width=28, height=28,
                      font=CTkFont(size=13, weight="bold"),
                      fg_color="transparent",
                      text_color=color,
                      command=lambda t=toast:
                          self._remove_toast(t)
                      ).pack(side="right", padx=(6, 0))

            if duration > 0:
                self._safe_after(duration,
                                  self._remove_toast, toast)
            if (self.config_data.sound_effects
                    and typ in ("success", "achievement",
                                 "error")):
                beep_async()
        except Exception as e:
            logger.error(f"_show_toast: {e}")

    def _remove_toast(self, toast: Any) -> None:
        try:
            if toast in self.toasts:
                self.toasts.remove(toast)
            if toast.winfo_exists():
                toast.destroy()
            if not self.toasts:
                with suppress(Exception):
                    if self.toast_container.winfo_exists():
                        self.toast_container.grid_remove()
        except Exception:
            pass

    # ────────────────────────────────────────────────
    # HELPERS
    # ────────────────────────────────────────────────
    def _page_header(
        self, parent: Any, title: str, icon: str = "",
        subtitle: str = "",
        buttons: Optional[List[Tuple[
            str, str, Callable]]] = None,
    ) -> Any:
        h = CTkFrame(parent, fg_color="transparent",
                       height=UI_HEADER_HEIGHT)
        h.pack(fill="x", pady=(0, UI_PAD_INNER))
        h.pack_propagate(False)

        left = CTkFrame(h, fg_color="transparent")
        left.pack(side="left", fill="y")
        CTkLabel(left, text=f"{icon} {title}".strip(),
                 font=CTkFont(size=22, weight="bold"),
                 text_color=theme_manager.get("primary"),
                 anchor="w").pack(anchor="w")
        if subtitle:
            CTkLabel(left, text=subtitle,
                     font=CTkFont(size=10),
                     text_color=theme_manager.get("text_dim"),
                     anchor="w").pack(anchor="w")

        if buttons:
            right = CTkFrame(h, fg_color="transparent")
            right.pack(side="right", fill="y")
            for txt, color, cmd in buttons:
                CTkButton(right, text=txt,
                          height=UI_BUTTON_HEIGHT,
                          width=UI_BUTTON_WIDTH,
                          font=CTkFont(size=11,
                                        weight="bold"),
                          fg_color=color,
                          text_color=theme_manager.get("bg"),
                          command=cmd).pack(side="right",
                                              padx=3)
        return h

    def _section_card(self, parent: Any, title: str,
                       border_color: str) -> Any:
        card = CTkFrame(parent, corner_radius=10,
                        border_width=2,
                        border_color=border_color,
                        fg_color=theme_manager.get("bg2"))
        card.pack(fill="x", pady=UI_PAD_INNER)
        CTkLabel(card, text=title,
                 font=CTkFont(size=14, weight="bold"),
                 text_color=border_color,
                 anchor="w"
                 ).pack(anchor="w", padx=UI_PAD_CARD,
                        pady=(12, 6))
        return card

    def _greeting(self) -> str:
        h = datetime.now().hour
        if 5 <= h < 12:
            base = "☀️ Доброе утро"
        elif 12 <= h < 18:
            base = "🌤️ Добрый день"
        elif 18 <= h < 23:
            base = "🌆 Добрый вечер"
        else:
            base = "🌙 Доброй ночи"
        progress = level_system.get_progress(self.current_user)
        streak = progress.get("streak", 0)
        if streak >= 30:
            suffix = f" 🔥 {streak} дней — впечатляет!"
        elif streak >= 7:
            suffix = f" 🔥 {streak} дней — отличный темп!"
        else:
            suffix = " ✨ Рад тебя видеть!"
        return f"{base}, {self.current_user}!{suffix}"

    # ────────────────────────────────────────────────
    # CODE STUDIO methods (заглушки для 4.2b-i)
    # ────────────────────────────────────────────────
    def _code_render_tabs(self) -> None:
        if not getattr(self, "_code_tabs_container", None):
            return
        try:
            if not self._code_tabs_container.winfo_exists():
                return
        except Exception:
            return
        for w in self._code_tabs_container.winfo_children():
            w.destroy()
        for i, tab in enumerate(self._code_tabs):
            active = (i == self._code_current_tab)
            btn = CTkButton(
                self._code_tabs_container,
                text=f"📄 {tab.get('name', f'tab{i+1}')}",
                height=30, width=100,
                font=CTkFont(
                    size=11,
                    weight="bold" if active else "normal"),
                fg_color=(theme_manager.get("primary")
                           if active else "transparent"),
                text_color=(theme_manager.get("bg")
                             if active
                             else theme_manager.get("text")),
                hover_color=theme_manager.get("bg3"),
                command=lambda idx=i:
                    self._code_switch_tab(idx))
            btn.pack(side="left", padx=2)
            if len(self._code_tabs) > 1:
                CTkButton(
                    self._code_tabs_container,
                    text="✕", width=24, height=30,
                    font=CTkFont(size=10, weight="bold"),
                    fg_color="transparent",
                    text_color=theme_manager.get("error"),
                    hover_color=theme_manager.get("bg3"),
                    command=lambda idx=i:
                        self._code_close_tab(idx)
                ).pack(side="left", padx=(0, 4))

    def _code_new_tab(self) -> None:
        if len(self._code_tabs) >= CODE_MULTI_TAB_MAX:
            self._show_toast(
                f"Максимум {CODE_MULTI_TAB_MAX} вкладок",
                "warning")
            return
        self._code_tabs.append({
            "name": f"tab{len(self._code_tabs) + 1}",
            "code": "",
            "lang": (self._code_lang_var.get()
                      if self._code_lang_var else "python"),
        })
        self._code_current_tab = len(self._code_tabs) - 1
        self._code_render_tabs()
        self._code_load_active_tab()
        self._show_toast("➕ Новая вкладка", "info", 1500)

    def _code_switch_tab(self, idx: int) -> None:
        if (self._code_widget is not None
                and 0 <= self._code_current_tab < len(
                    self._code_tabs)):
            try:
                self._code_tabs[
                    self._code_current_tab]["code"] = \
                    self._code_widget.get("1.0", "end-1c")
            except Exception:
                pass
        if idx < 0 or idx >= len(self._code_tabs):
            return
        self._code_current_tab = idx
        self._code_render_tabs()
        self._code_load_active_tab()

    def _code_close_tab(self, idx: int) -> None:
        if len(self._code_tabs) <= 1:
            self._show_toast(
                "Нельзя закрыть последнюю вкладку", "warning")
            return
        if idx == self._code_current_tab:
            if (self._code_widget is not None
                    and 0 <= idx < len(self._code_tabs)):
                try:
                    self._code_tabs[idx]["code"] = \
                        self._code_widget.get("1.0", "end-1c")
                except Exception:
                    pass
        self._code_tabs.pop(idx)
        if self._code_current_tab >= len(self._code_tabs):
            self._code_current_tab = len(self._code_tabs) - 1
        self._code_render_tabs()
        self._code_load_active_tab()

    def _code_load_active_tab(self) -> None:
        if not (0 <= self._code_current_tab < len(
                self._code_tabs)):
            return
        tab = self._code_tabs[self._code_current_tab]
        if self._code_lang_var is not None:
            with suppress(Exception):
                self._code_lang_var.set(tab.get("lang", "python"))
        if self._code_widget is not None:
            with suppress(Exception):
                self._code_widget.delete("1.0", "end")
                self._code_widget.insert(
                    "1.0", tab.get("code", ""))
                self._schedule_highlight(
                    self._code_widget,
                    tab.get("lang", "python"))

    def _on_code_lang_change(self, lang: str) -> None:
        """Смена языка кода. Устойчиво к мёртвым ссылкам."""
        try:
            self.config_data.code_last_language = lang
            self.config_data.save()
        except Exception:
            pass
        try:
            if (0 <= self._code_current_tab
                    < len(self._code_tabs)):
                self._code_tabs[
                    self._code_current_tab]["lang"] = lang
        except Exception:
            pass
        try:
            if (self._code_status_lbl is not None
                    and self._code_status_lbl.winfo_exists()):
                self._code_status_lbl.configure(
                    text=f"🗣️ {lang}")
        except Exception:
            pass
        try:
            if (self._code_widget is not None
                    and self._code_widget.winfo_exists()):
                self._schedule_highlight(
                    self._code_widget, lang)
        except Exception:
            pass

    def _on_font_size(self, value: str,
                       var: StringVar) -> None:
        try:
            size = int(value)
        except (TypeError, ValueError):
            size = 13
        size = max(10, min(20, size))
        self.config_data.code_font_size = size
        self.config_data.save()
        if (self._code_widget is not None
                and self._code_widget.winfo_exists()):
            with suppress(Exception):
                self._code_widget.configure(
                    font=CTkFont(family="Courier", size=size))

    def _on_template_selected(self, name: str) -> None:
        if name == "— не выбран —":
            return
        tid = CodeTemplates.get_id_by_name(name)
        if not tid:
            return
        tpl = CodeTemplates.get(tid)
        if not tpl:
            return
        w = self._code_widget
        if w is None or not w.winfo_exists():
            return
        w.delete("1.0", "end")
        w.insert("1.0", tpl.get("code", ""))
        self._code_templates_used.add(tid)
        with suppress(Exception):
            level_system.add_xp(self.current_user,
                                  "code_template_used")
        self._show_toast(
            f"📚 {tpl.get('name', '')}", "success")

    def _on_code_change(self) -> None:
        if self._code_status_lbl is not None:
            with suppress(Exception):
                self._code_status_lbl.configure(
                    text="✏️ Изменено")
        if (0 <= self._code_current_tab < len(
                self._code_tabs)
                and self._code_widget is not None):
            with suppress(Exception):
                self._code_tabs[
                    self._code_current_tab]["code"] = \
                    self._code_widget.get("1.0", "end-1c")
        if self.config_data.code_auto_save:
            old = self._timers.get("code_autosave")
            if old:
                with suppress(Exception):
                    self.after_cancel(old)
            self._timers["code_autosave"] = self._safe_after(
                UI_CODE_AUTOSAVE_MS, self._save_code_draft)

    def _toggle_code_autosave(self, var: BooleanVar) -> None:
        self.config_data.code_auto_save = bool(var.get())
        self.config_data.save()

    def _toggle_syntax_highlight(self, var: BooleanVar) -> None:
        self.config_data.code_syntax_highlight = bool(var.get())
        self.config_data.save()

    def _schedule_highlight(self, widget: Any,
                             language: str) -> None:
        if not self.config_data.code_syntax_highlight:
            return
        old = self._timers.get("code_highlight")
        if old:
            with suppress(Exception):
                self.after_cancel(old)
        self._timers["code_highlight"] = self._safe_after(
            UI_CODE_HIGHLIGHT_MS,
            self._apply_syntax_highlight, widget, language)

    def _apply_syntax_highlight(self, widget: Any,
                                  language: str = "python"
                                  ) -> None:
        if not self.config_data.code_syntax_highlight:
            return
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                return
        except Exception:
            return
        for tag in ("kw", "str", "comment", "num", "decor",
                    "builtin"):
            with suppress(Exception):
                widget.tag_remove(tag, "1.0", "end")
        try:
            text = widget.get("1.0", "end-1c")
        except Exception:
            return
        if not text:
            return
        tt = theme_manager
        try:
            widget.tag_config(
                "kw", foreground=tt.get("accent"),
                font=CTkFont(
                    family="Courier",
                    size=self.config_data.code_font_size,
                    weight="bold"))
            widget.tag_config("str",
                                foreground=tt.get("success"))
            widget.tag_config(
                "comment",
                foreground=tt.get("text_dim"),
                font=CTkFont(
                    family="Courier",
                    size=self.config_data.code_font_size,
                    slant="italic"))
            widget.tag_config("num",
                                foreground=tt.get("warning"))
            widget.tag_config("decor",
                                foreground=tt.get("secondary"))
            widget.tag_config("builtin",
                                foreground=tt.get("primary"))
        except Exception:
            return

        # ── Выбор правил для языка ──────────────────────
        if language == "python":
            kws = (r"\b(?:False|None|True|and|as|assert|async|"
                    r"await|break|class|continue|def|del|elif|"
                    r"else|except|finally|for|from|global|if|"
                    r"import|in|is|lambda|nonlocal|not|or|pass|"
                    r"raise|return|try|while|with|yield|"
                    r"match|case)\b")
            builtins = (r"\b(?:print|len|range|str|int|float|"
                         r"list|dict|set|tuple|bool|type|"
                         r"isinstance|enumerate|zip|map|filter|"
                         r"sorted|sum|min|max|abs|all|any|"
                         r"round|repr|hash|id|chr|ord|"
                         r"open|input|type|super|property|"
                         r"staticmethod|classmethod|dir|vars|"
                         r"getattr|setattr|hasattr|iter|next)\b")
        elif language == "shell":
            kws = (r"\b(?:if|then|else|fi|for|do|done|while|"
                    r"case|esac|function|echo|exit|return|"
                    r"export|local|readonly|read|source|cd|"
                    r"break|continue|test|alias|unalias|set|"
                    r"unset|shift|trap|wait|kill)\b")
            builtins = (r"\b(?:ls|cd|pwd|cat|grep|sed|awk|find|"
                         r"sort|uniq|head|tail|wc|chmod|chown|"
                         r"mkdir|rmdir|rm|cp|mv|ln|touch|"
                         r"du|df|ps|top|kill|which|whereis|"
                         r"man|sudo|apt|yum|dnf|pacman)\b")
        elif language in ("javascript", "typescript"):
            kws = (r"\b(?:const|let|var|function|return|if|"
                    r"else|for|while|do|switch|case|break|"
                    r"continue|new|this|class|extends|import|"
                    r"export|from|async|await|try|catch|"
                    r"finally|typeof|instanceof|in|of|delete|"
                    r"void|yield|throw|debugger|with|default|"
                    r"interface|type|enum|namespace|implements|"
                    r"private|public|protected|readonly|static)\b")
            builtins = (r"\b(?:console|document|window|Math|"
                         r"JSON|Array|Object|String|Number|"
                         r"Boolean|Promise|Set|Map|WeakMap|"
                         r"Symbol|Date|RegExp|Error|fetch|"
                         r"setTimeout|setInterval|clearTimeout|"
                         r"clearInterval|parseInt|parseFloat|"
                         r"isNaN|isFinite|encodeURI|decodeURI)\b")
        elif language == "json":
            kws = r"\b(?:true|false|null)\b"
            builtins = r"\b\w+\b"
        elif language == "html":
            kws = (r"</?(?:html|head|body|div|span|p|a|img|"
                    r"ul|ol|li|table|tr|td|th|form|input|"
                    r"button|script|style|link|meta|title|"
                    r"header|footer|nav|section|article|"
                    r"aside|main|h[1-6]|br|hr)\b")
            builtins = (r"\b(?:class|id|href|src|alt|title|"
                         r"type|name|value|placeholder|disabled|"
                         r"checked|selected|readonly|required|"
                         r"style|onclick|onload)\b")
        elif language == "css":
            kws = (r"@(?:media|import|font-face|keyframes|"
                    r"supports|charset|namespace|page)\b")
            builtins = (r"\b(?:color|background|border|margin|"
                         r"padding|width|height|display|position|"
                         r"top|left|right|bottom|font|text|"
                         r"flex|grid|transform|transition|"
                         r"animation|opacity|z-index|overflow|"
                         r"content|box-shadow|filter)\b")
        elif language == "sql":
            kws = (r"\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|"
                    r"UPDATE|SET|DELETE|CREATE|TABLE|DROP|ALTER|"
                    r"INDEX|VIEW|JOIN|INNER|LEFT|RIGHT|OUTER|ON|"
                    r"GROUP|BY|HAVING|ORDER|ASC|DESC|LIMIT|"
                    r"OFFSET|UNION|ALL|DISTINCT|AS|AND|OR|NOT|"
                    r"NULL|IS|IN|LIKE|BETWEEN|EXISTS|PRIMARY|"
                    r"KEY|FOREIGN|REFERENCES|CONSTRAINT|DEFAULT|"
                    r"CASCADE|IF|EXISTS|BEGIN|COMMIT|ROLLBACK)\b")
            builtins = (r"\b(?:COUNT|SUM|AVG|MIN|MAX|COALESCE|"
                         r"NULLIF|CAST|CONVERT|UPPER|LOWER|TRIM|"
                         r"SUBSTR|LENGTH|NOW|DATE|YEAR|MONTH|DAY)\b")
        elif language == "markdown":
            kws = r"^#{1,6}\s"
            builtins = r"\*\*[^*]+\*\*|_[^_]+_"
        elif language in ("yaml", "yml"):
            kws = r"^\s*-|\w+(?=:)"
            builtins = r"\b(?:true|false|null|yes|no|on|off)\b"
        elif language == "xml":
            kws = r"</?[\w:-]+"
            builtins = r"\b\w+="
        elif language == "c":
            kws = (r"\b(?:auto|break|case|char|const|continue|"
                    r"default|do|double|else|enum|extern|float|"
                    r"for|goto|if|inline|int|long|register|"
                    r"restrict|return|short|signed|sizeof|"
                    r"static|struct|switch|typedef|union|"
                    r"unsigned|void|volatile|while)\b")
            builtins = (r"\b(?:printf|scanf|malloc|free|"
                         r"memcpy|memset|strlen|strcpy|strcmp|"
                         r"fopen|fclose|fread|fwrite|exit|"
                         r"NULL|EOF|FILE)\b")
        elif language == "cpp":
            kws = (r"\b(?:auto|break|case|catch|char|class|"
                    r"const|constexpr|continue|default|delete|"
                    r"do|double|else|enum|explicit|extern|"
                    r"false|float|for|friend|if|inline|int|"
                    r"long|namespace|new|nullptr|operator|"
                    r"private|protected|public|return|short|"
                    r"signed|sizeof|static|struct|switch|"
                    r"template|this|throw|true|try|typedef|"
                    r"typename|union|unsigned|using|virtual|"
                    r"void|volatile|while|bool)\b")
            builtins = (r"\b(?:std|cout|cin|endl|vector|string|"
                         r"map|set|list|pair|make_pair|unique_ptr|"
                         r"shared_ptr|malloc|free|printf)\b")
        elif language == "rust":
            kws = (r"\b(?:as|async|await|break|const|continue|"
                    r"crate|dyn|else|enum|extern|false|fn|for|"
                    r"if|impl|in|let|loop|match|mod|move|mut|"
                    r"pub|ref|return|self|Self|static|struct|"
                    r"super|trait|true|type|unsafe|use|where|"
                    r"while)\b")
            builtins = (r"\b(?:String|Vec|Box|Option|Result|"
                         r"Some|None|Ok|Err|println|print|"
                         r"format|vec|panic|assert|assert_eq)\b")
        elif language == "go":
            kws = (r"\b(?:break|case|chan|const|continue|"
                    r"default|defer|else|fallthrough|for|func|"
                    r"go|goto|if|import|interface|map|package|"
                    r"range|return|select|struct|switch|type|"
                    r"var)\b")
            builtins = (r"\b(?:fmt|Println|Printf|Errorf|"
                         r"make|new|len|cap|append|copy|delete|"
                         r"panic|recover|nil|true|false)\b")
        elif language == "java":
            kws = (r"\b(?:abstract|assert|boolean|break|byte|"
                    r"case|catch|char|class|const|continue|"
                    r"default|do|double|else|enum|extends|"
                    r"final|finally|float|for|goto|if|"
                    r"implements|import|instanceof|int|"
                    r"interface|long|native|new|package|"
                    r"private|protected|public|return|short|"
                    r"static|strictfp|super|switch|synchronized|"
                    r"this|throw|throws|transient|try|void|"
                    r"volatile|while|true|false|null)\b")
            builtins = (r"\b(?:String|System|Math|List|Map|Set|"
                         r"ArrayList|HashMap|HashSet|Exception|"
                         r"RuntimeException|out|println|Integer|"
                         r"Double|Boolean|Character)\b")
        elif language == "ruby":
            kws = (r"\b(?:BEGIN|END|alias|and|begin|break|"
                    r"case|class|def|defined|do|else|elsif|end|"
                    r"ensure|false|for|if|in|module|next|nil|"
                    r"not|or|redo|rescue|retry|return|self|"
                    r"super|then|true|undef|unless|until|when|"
                    r"while|yield)\b")
            builtins = (r"\b(?:puts|print|gets|require|include|"
                         r"attr_accessor|attr_reader|attr_writer|"
                         r"new|class|lambda|proc)\b")
        elif language == "php":
            kws = (r"\b(?:abstract|and|array|as|break|callable|"
                    r"case|catch|class|clone|const|continue|"
                    r"declare|default|do|echo|else|elseif|empty|"
                    r"enddeclare|endfor|endforeach|endif|"
                    r"endswitch|endwhile|eval|exit|extends|"
                    r"final|finally|fn|for|foreach|function|"
                    r"global|goto|if|implements|include|"
                    r"include_once|instanceof|insteadof|"
                    r"interface|isset|list|match|namespace|new|"
                    r"or|print|private|protected|public|require|"
                    r"require_once|return|static|switch|throw|"
                    r"trait|try|unset|use|var|while|xor|yield)\b")
            builtins = (r"\b(?:strlen|str_replace|strpos|substr|"
                         r"explode|implode|count|array_push|"
                         r"array_pop|json_encode|json_decode|"
                         r"var_dump|print_r|file_get_contents|"
                         r"file_put_contents)\b")
        elif language == "kotlin":
            kws = (r"\b(?:as|break|class|continue|do|else|"
                    r"false|for|fun|if|in|interface|is|null|"
                    r"object|package|return|super|this|throw|"
                    r"true|try|typealias|typeof|val|var|when|"
                    r"while|by|catch|constructor|delegate|"
                    r"dynamic|field|file|finally|get|import|"
                    r"init|param|property|receiver|set|setparam|"
                    r"where|actual|abstract|annotation|"
                    r"companion|const|crossinline|data|enum|"
                    r"expect|external|final|infix|inline|"
                    r"inner|internal|lateinit|noinline|open|"
                    r"operator|out|override|private|protected|"
                    r"public|reified|sealed|suspend|tailrec|"
                    r"vararg)\b")
            builtins = (r"\b(?:println|print|readLine|listOf|"
                         r"mapOf|setOf|mutableListOf|arrayOf|"
                         r"Int|String|Double|Boolean|Any|Unit)\b")
        elif language == "swift":
            kws = (r"\b(?:associatedtype|class|deinit|enum|"
                    r"extension|fileprivate|func|import|init|"
                    r"inout|internal|let|open|operator|private|"
                    r"protocol|public|static|struct|subscript|"
                    r"typealias|var|break|case|continue|default|"
                    r"defer|do|else|fallthrough|for|guard|if|"
                    r"in|repeat|return|switch|where|while|as|"
                    r"catch|is|rethrows|super|self|throw|throws|"
                    r"try|await|async)\b")
            builtins = (r"\b(?:print|Int|String|Double|Bool|"
                         r"Array|Dictionary|Set|Optional|Any|"
                         r"Void|nil|true|false)\b")
        else:
            kws, builtins = r"\b\w+\b", r"\b\w+\b"

        # Комментарии для языка
        comment_patterns: List[str] = []
        if language == "python":
            comment_patterns.append(r"#[^\n]*")
        elif language == "shell":
            comment_patterns.append(r"#[^\n]*")
        elif language == "ruby":
            comment_patterns.append(r"#[^\n]*")
        elif language in ("javascript", "typescript", "java",
                            "c", "cpp", "rust", "go", "php",
                            "kotlin", "swift"):
            comment_patterns.append(r"//[^\n]*")
            comment_patterns.append(r"/\*[\s\S]*?\*/")
        elif language == "sql":
            comment_patterns.append(r"--[^\n]*")
            comment_patterns.append(r"/\*[\s\S]*?\*/")
        elif language in ("html", "xml"):
            comment_patterns.append(r"<!--[\s\S]*?-->")
        elif language == "css":
            comment_patterns.append(r"/\*[\s\S]*?\*/")

        # Универсальные: строки, числа, декораторы
        string_patterns = [
            r"(?:\"\"\"[\s\S]*?\"\"\")",  # Python triple
            r"(?:'''[\s\S]*?''')",         # Python triple
            r"`[^`]*`",                     # JS template literals
            r"\"(?:\\.|[^\"\\])*\"",
            r"'(?:\\.|[^'\\])*'",
        ]
        num_pattern = r"\b\d+(?:\.\d+)?\b"
        decor_pattern = r"@[\w.]+"

        # Применяем
        for pat in comment_patterns:
            _scan(pat, "comment")
        for pat in string_patterns:
            _scan(pat, "str")
        _scan(num_pattern, "num")
        _scan(decor_pattern, "decor")
        _scan(kws, "kw")
        _scan(builtins, "builtin")

    def _copy_code_current(self) -> None:
        if self._code_widget is None:
            return
        try:
            src = self._code_widget.get("1.0", "end-1c")
            if copy_to_clipboard(src):
                self._show_toast("📋 Код скопирован",
                                  "success", 1500)
        except Exception:
            pass

    def _copy_output(self) -> None:
        out = self._code_out
        if out is None:
            return
        try:
            if out.winfo_exists():
                txt = out.get("1.0", "end-1c")
                if copy_to_clipboard(txt):
                    self._show_toast("📋 Вывод скопирован",
                                      "success", 1500)
        except Exception:
            pass

    def _clear_code_output(self) -> None:
        out = self._code_out
        if out is None:
            return
        try:
            if out.winfo_exists():
                out.delete("1.0", "end")
                out.insert("1.0", "🗑️ Очищено")
        except Exception:
            pass

    def _save_as_snippet_current(self) -> None:
        w = self._code_widget
        if w is None:
            return
        try:
            code = w.get("1.0", "end-1c").strip()
            if not code:
                self._show_toast("Пустой код", "warning")
                return
            self._show_snippet_save_dialog(code)
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    def _show_snippet_save_dialog(self, code: str) -> None:
        """Заглушка — простая форма."""
        win = CTkToplevel(self)
        win.title("💾 Сохранить сниппет")
        win.geometry("520x320")
        win.configure(fg_color=theme_manager.get("bg"))
        win.transient(self)
        win.grab_set()
        CTkLabel(win, text="💾 НОВЫЙ СНИППЕТ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack(pady=(15, 8))
        CTkLabel(win, text="📝 Название:",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", padx=20,
                                    pady=(8, 3))
        name_e = CTkEntry(win, height=36,
                            font=CTkFont(size=13),
                            fg_color=theme_manager.get("bg3"),
                            border_color=theme_manager.get(
                                "card_border"),
                            text_color=theme_manager.get("text"))
        name_e.pack(fill="x", padx=20, pady=(0, 4))
        name_e.focus()

        def save() -> None:
            name = name_e.get().strip()
            if not name:
                return
            lang = (self._code_lang_var.get()
                     if self._code_lang_var else "python")
            s = snippet_manager.create(
                name=name, code=code,
                user=self.current_user, language=lang)
            if s:
                level_system.add_xp(self.current_user,
                                      "save_snippet")
                level_system.increment(self.current_user,
                                         "snippets_saved")
                self._show_toast("✂️ Сниппет сохранён!",
                                  "success")
                win.destroy()
        name_e.bind("<Return>", lambda e: save())
        CTkButton(win, text="💾 СОХРАНИТЬ", height=38,
                  width=180,
                  fg_color=theme_manager.get("success"),
                  text_color=theme_manager.get("bg"),
                  command=save).pack(pady=15)

    def _load_code_to_editor(self, code: str) -> None:
        w = self._code_widget
        if w is None or not w.winfo_exists():
            return
        w.delete("1.0", "end")
        w.insert("1.0", code)
        self._show_toast("📋 Загружено в редактор",
                          "success", 1500)


        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")

        # Preview-языки — открываем окно предпросмотра
        if lang in ("html", "css", "markdown"):
            self._open_preview(src, lang)
            return

        # Проверяем доступность интерпретатора
        try:
            from nexus.sandbox import lang_available
            ok, hint = lang_available(lang)
            if not ok:
                self._show_toast(
                    f"❌ {hint}", "error", 6000)
                return
        except Exception:
            pass

        out.delete("1.0", "end")
        out.insert("1.0",
                    f"⏳ Выполняется ({lang}) в песочнице...")

        def work() -> None:
            import time as _time
            from nexus.sandbox import MultiLangRunner
            start = _time.time()
            try:
                stdout, stderr, success = MultiLangRunner.run(
                    src, lang=lang, timeout=10)
            except Exception as e:
                stdout, stderr, success = "", f"❌ {e}", False
            elapsed = _time.time() - start
            with suppress(Exception):
                CodeHistory.add(
                    self.current_user, src,
                    stdout or "", success, elapsed,
                    stderr or "")
            self._safe_after(
                0, self._apply_code_result, out,
                stdout or "", stderr or "",
                elapsed, success)
        executor.submit_safe(work)

        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")

        # Preview-языки — открываем окно предпросмотра
        if lang in ("html", "css", "markdown"):
            self._open_preview(src, lang)
            return

        # Проверяем доступность интерпретатора
        try:
            from nexus.sandbox import lang_available
            ok, hint = lang_available(lang)
            if not ok:
                self._show_toast(
                    f"❌ {hint}", "error", 6000)
                return
        except Exception:
            pass

        out.delete("1.0", "end")
        out.insert("1.0",
                    f"⏳ Выполняется ({lang}) в песочнице...")

        def work() -> None:
            import time as _time
            from nexus.sandbox import MultiLangRunner
            start = _time.time()
            try:
                stdout, stderr, success = MultiLangRunner.run(
                    src, lang=lang, timeout=10)
            except Exception as e:
                stdout, stderr, success = "", f"❌ {e}", False
            elapsed = _time.time() - start
            with suppress(Exception):
                CodeHistory.add(
                    self.current_user, src,
                    stdout or "", success, elapsed,
                    stderr or "")
            self._safe_after(
                0, self._apply_code_result, out,
                stdout or "", stderr or "",
                elapsed, success)
        executor.submit_safe(work)

    def _run_all_tabs(self) -> None:
        """Запустить код всех вкладок по очереди (мультиязычно)."""
        if not self._code_tabs:
            self._show_toast("Нет вкладок", "warning")
            return
        out = self._code_out
        if out is None:
            return

        # Синхронизировать текущую вкладку
        if (self._code_widget is not None
                and 0 <= self._code_current_tab < len(
                    self._code_tabs)):
            with suppress(Exception):
                self._code_tabs[
                    self._code_current_tab]["code"] = \
                    self._code_widget.get("1.0", "end-1c")

        # Собираем непустые вкладки (с языком каждой)
        tabs_to_run: List[Tuple[str, str, str]] = []
        for i, tab in enumerate(self._code_tabs):
            code = (tab.get("code") or "").strip()
            if code:
                name = tab.get("name", f"tab{i+1}")
                tab_lang = tab.get("lang", "python")
                tabs_to_run.append((name, code, tab_lang))

        if not tabs_to_run:
            self._show_toast("Все вкладки пусты", "warning")
            return

        out.delete("1.0", "end")
        out.insert("1.0",
                    f"▶▶ Запуск {len(tabs_to_run)} вкладок...\n\n")
        if self._code_status_lbl is not None:
            with suppress(Exception):
                self._code_status_lbl.configure(
                    text=f"▶▶ 0/{len(tabs_to_run)}")

        def work() -> None:
            from nexus.sandbox import MultiLangRunner
            total = len(tabs_to_run)
            for idx, (name, code, tab_lang) in enumerate(
                    tabs_to_run, 1):
                self._safe_after(
                    0, self._update_batch_progress,
                    idx, total, name)
                try:
                    stdout, stderr, ok = MultiLangRunner.run(
                        code, lang=tab_lang, timeout=10)
                except Exception as e:
                    stdout, stderr, ok = "", str(e), False
                self._safe_after(
                    0, self._append_tab_result,
                    name, stdout, stderr, ok)
            self._safe_after(
                0, self._finish_batch_run, total)

        executor.submit_safe(work)
        

    def _run_code_current(self) -> None:
        """Запуск кода в песочнице (мультиязычный)."""
        w = self._code_widget
        if w is None:
            return
        src = w.get("1.0", "end-1c").strip()
        out = self._code_out
        if not src or out is None:
            self._show_toast("Пустой код", "warning")
            return

        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")

        # Preview-языки — открываем окно предпросмотра
        if lang in ("html", "css", "markdown"):
            self._open_preview(src, lang)
            return

        # Проверяем доступность интерпретатора
        try:
            from nexus.sandbox import lang_available
            ok, hint = lang_available(lang)
            if not ok:
                self._show_toast(
                    f"❌ {hint}", "error", 6000)
                return
        except Exception:
            pass

        out.delete("1.0", "end")
        out.insert("1.0",
                    f"⏳ Выполняется ({lang}) в песочнице...")

        def work() -> None:
            import time as _time
            from nexus.sandbox import MultiLangRunner
            start = _time.time()
            try:
                stdout, stderr, success = MultiLangRunner.run(
                    src, lang=lang, timeout=10)
            except Exception as e:
                stdout, stderr, success = "", f"❌ {e}", False
            elapsed = _time.time() - start
            with suppress(Exception):
                CodeHistory.add(
                    self.current_user, src,
                    stdout or "", success, elapsed,
                    stderr or "")
            self._safe_after(
                0, self._apply_code_result, out,
                stdout or "", stderr or "",
                elapsed, success)
        executor.submit_safe(work)

    def _apply_code_result(self, out: Any, stdout: str,
                            stderr: str, elapsed: float,
                            success: bool) -> None:
        try:
            if not out.winfo_exists():
                return
    
            out.delete("1.0", "end")
            with suppress(Exception):
                    self._show_toast(
                        f"✅ Выполнено за {elapsed:.2f}с",
                        "success", 2500)
            # Preview-маркер (HTML/CSS/MD)
            if stdout.startswith("__HTML__"):
                html_len = len(stdout) - len("__HTML__")
                out.insert(
                    "1.0",
                    f"🌐 HTML готов ({html_len} символов)\n\n"
                    f"Окно preview открыто отдельно.\n"
                    f"Кнопки: 🌐 браузер · 📋 копия · 💾 файл")
                # Тост может упасть, если UI не инициализирован
                with suppress(Exception):
                    self._show_toast(
                        "🌐 HTML preview", "success", 2000)
                return

            parts: List[str] = []
            if stdout.strip():
                parts.append(stdout)
            elif success:
                parts.append("✅ OK (нет вывода)")
            if stderr.strip():
                if parts:
                    parts.append("\n--- stderr ---\n")
                parts.append(stderr)
            if not parts:
                parts.append("✅ OK")
            out.insert("1.0", "".join(parts))
            if success:
                with suppress(Exception):
                    level_system.add_xp(self.current_user,
                                          "execute_code")
                    level_system.increment(
                        self.current_user, "code_executed")
                self._show_toast(
                    f"✅ Выполнено за {elapsed:.2f}с",
                    "success", 2500)
            else:
                first_err = ""
                for line in stderr.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith(("File \"", "Traceback")):
                        continue
                    if "--- stderr ---" in line:
                        continue
                    first_err = line
                    break
                self._show_toast(
                    f"❌ {first_err[:80] or 'Ошибка'}",
                    "error", 4000)
        except Exception:
            pass

    def _open_preview(self, src: str, lang: str) -> None:
        """Открыть окно предпросмотра HTML/CSS/Markdown."""
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        win.title(f"🖼️ Предпросмотр · {lang}")
        win.geometry("900x700")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 900) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 700) // 2)
        win.geometry(f"+{x}+{y}")

        head = CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=15, pady=(15, 8))
        CTkLabel(head,
                 text=f"🖼️ ПРЕДПРОСМОТР · {lang.upper()}",
                 font=CTkFont(size=17, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(side="left")
        CTkLabel(head, text=f"{len(src)} символов",
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim")).pack(side="right")

        btns = CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=15, pady=(0, 8))

        CTkButton(btns, text="🌐 Открыть в браузере",
                  height=34, width=200,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._preview_in_browser(src, lang)
                  ).pack(side="left", padx=2)
        CTkButton(btns, text="📋 Копировать HTML",
                  height=34, width=180,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("accent"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._copy_preview_html(src, lang)
                  ).pack(side="left", padx=2)
        CTkButton(btns, text="💾 Сохранить HTML",
                  height=34, width=180,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("primary"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._save_preview_html(src, lang)
                  ).pack(side="left", padx=2)

        tabs = ctk.CTkTabview(
            win, fg_color=t.get("bg2"),
            segmented_button_fg_color=t.get("bg3"),
            segmented_button_selected_color=t.get("primary"),
            segmented_button_selected_hover_color=t.get(
                "primary"),
            text_color=t.get("text"))
        tabs.pack(fill="both", expand=True, padx=15,
                   pady=(0, 15))

        tab_html = tabs.add("📄 HTML")
        tab_src = tabs.add("📝 Исходник")

        html_code = self._prepare_html(src, lang)
        html_box = CTkTextbox(
            tab_html,
            font=CTkFont(family="Courier", size=11),
            fg_color=t.get("code_bg"),
            text_color=t.get("code_text"),
            wrap="word")
        html_box.pack(fill="both", expand=True, padx=5,
                       pady=5)
        html_box.insert("1.0", html_code)
        html_box.configure(state="disabled")

        src_box = CTkTextbox(
            tab_src,
            font=CTkFont(family="Courier", size=11),
            fg_color=t.get("code_bg"),
            text_color=t.get("code_text"),
            wrap="word")
        src_box.pack(fill="both", expand=True, padx=5,
                      pady=5)
        src_box.insert("1.0", src)
        src_box.configure(state="disabled")

        self._safe_after(
            400, lambda:
            self._preview_in_browser(src, lang))

    def _prepare_html(self, src: str, lang: str) -> str:
        if lang == "html":
            return src
        if lang == "css":
            return (f"<!DOCTYPE html><html><head>"
                    f"<meta charset='utf-8'>"
                    f"<style>{src}</style></head>"
                    f"<body><h1>CSS Preview</h1>"
                    f"<p>Пример текста.</p>"
                    f"<button>Кнопка</button></body></html>")
        if lang == "markdown":
            try:
                from nexus.sandbox import MultiLangRunner
                body = MultiLangRunner._markdown_to_html(src)
            except Exception:
                body = f"<pre>{src}</pre>"
            return (
                "<!DOCTYPE html><html><head>"
                "<meta charset='utf-8'>"
                "<style>body{font-family:system-ui;"
                "max-width:800px;margin:40px auto;"
                "padding:20px;line-height:1.6;color:#333}"
                "code{background:#f4f4f4;padding:2px 6px;"
                "border-radius:3px}"
                "pre{background:#f4f4f4;padding:12px;"
                "border-radius:6px;overflow:auto}"
                "</style></head><body>" + body +
                "</body></html>")
        return src

    def _preview_in_browser(self, src: str,
                             lang: str) -> None:
        try:
            import tempfile
            html = self._prepare_html(src, lang)
            with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".html",
                    delete=False, encoding="utf-8") as f:
                f.write(html)
                path = f.name
            webbrowser.open(f"file://{path}")
            with suppress(Exception):
                self._show_toast("🌐 Открыто в браузере",
                                  "success", 2000)
        except Exception as e:
            with suppress(Exception):
                self._show_toast(f"❌ {e}", "error")

    def _copy_preview_html(self, src: str,
                            lang: str) -> None:
        html = self._prepare_html(src, lang)
        if copy_to_clipboard(html):
            with suppress(Exception):
                self._show_toast("📋 HTML скопирован",
                                  "success", 2000)

    def _save_preview_html(self, src: str,
                            lang: str) -> None:
        from tkinter import filedialog as _fd
        from datetime import datetime as _dt
        html = self._prepare_html(src, lang)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fp = _fd.asksaveasfilename(
            parent=self,
            defaultextension=".html",
            initialfile=f"preview_{ts}.html",
            filetypes=[("HTML", "*.html")])
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(html)
            with suppress(Exception):
                self._show_toast(
                    f"💾 {os.path.basename(fp)}",
                    "success", 3000)
            self._open_file(Path(fp))
        except Exception as e:
            with suppress(Exception):
                self._show_toast(f"❌ {e}", "error")

        # ────────────────────────────────────────────────
    # PREVIEW для HTML / CSS / Markdown
    # ────────────────────────────────────────────────
    def _open_preview(self, src: str, lang: str) -> None:
        """Открыть окно предпросмотра."""
        t = theme_manager.get_all()

        win = CTkToplevel(self)
        win.title(f"🖼️ Предпросмотр · {lang}")
        win.geometry("900x700")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 900) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 700) // 2)
        win.geometry(f"+{x}+{y}")

        head = CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=15, pady=(15, 8))
        CTkLabel(head,
                 text=f"🖼️ ПРЕДПРОСМОТР · {lang.upper()}",
                 font=CTkFont(size=17, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(side="left")
        CTkLabel(head, text=f"{len(src)} символов",
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim")).pack(side="right")

        btns = CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=15, pady=(0, 8))

        CTkButton(btns, text="🌐 Открыть в браузере",
                  height=34, width=200,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._preview_in_browser(src, lang)
                  ).pack(side="left", padx=2)
        CTkButton(btns, text="📋 Копировать HTML",
                  height=34, width=180,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("accent"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._copy_preview_html(src, lang)
                  ).pack(side="left", padx=2)
        CTkButton(btns, text="💾 Сохранить HTML",
                  height=34, width=180,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("primary"),
                  text_color=t.get("bg"),
                  command=lambda:
                      self._save_preview_html(src, lang)
                  ).pack(side="left", padx=2)

        tabs = ctk.CTkTabview(
            win, fg_color=t.get("bg2"),
            segmented_button_fg_color=t.get("bg3"),
            segmented_button_selected_color=t.get("primary"),
            segmented_button_selected_hover_color=t.get(
                "primary"),
            text_color=t.get("text"))
        tabs.pack(fill="both", expand=True, padx=15,
                   pady=(0, 15))

        tab_html = tabs.add("📄 HTML")
        tab_src = tabs.add("📝 Исходник")

        html_code = self._prepare_html(src, lang)
        html_box = CTkTextbox(
            tab_html,
            font=CTkFont(family="Courier", size=11),
            fg_color=t.get("code_bg"),
            text_color=t.get("code_text"),
            wrap="word")
        html_box.pack(fill="both", expand=True, padx=5,
                       pady=5)
        html_box.insert("1.0", html_code)
        html_box.configure(state="disabled")

        src_box = CTkTextbox(
            tab_src,
            font=CTkFont(family="Courier", size=11),
            fg_color=t.get("code_bg"),
            text_color=t.get("code_text"),
            wrap="word")
        src_box.pack(fill="both", expand=True, padx=5,
                      pady=5)
        src_box.insert("1.0", src)
        src_box.configure(state="disabled")

        # Авто-открытие в браузере через 400мс
        self._safe_after(
            400, lambda:
            self._preview_in_browser(src, lang))

    def _prepare_html(self, src: str, lang: str) -> str:
        """Обернуть исходник в полноценный HTML."""
        if lang == "html":
            return src
        if lang == "css":
            return (f"<!DOCTYPE html><html><head>"
                    f"<meta charset='utf-8'>"
                    f"<style>{src}</style></head>"
                    f"<body><h1>CSS Preview</h1>"
                    f"<p>Пример текста для просмотра "
                    f"стилей.</p>"
                    f"<button>Кнопка</button></body></html>")
        if lang == "markdown":
            try:
                from nexus.sandbox import MultiLangRunner
                body = MultiLangRunner._markdown_to_html(src)
            except Exception:
                body = f"<pre>{src}</pre>"
            return (
                "<!DOCTYPE html><html><head>"
                "<meta charset='utf-8'>"
                "<style>body{font-family:system-ui;"
                "max-width:800px;margin:40px auto;"
                "padding:20px;line-height:1.6;color:#333}"
                "code{background:#f4f4f4;padding:2px 6px;"
                "border-radius:3px}"
                "pre{background:#f4f4f4;padding:12px;"
                "border-radius:6px;overflow:auto}"
                "</style></head><body>" + body +
                "</body></html>")
        return src

    def _preview_in_browser(self, src: str,
                             lang: str) -> None:
        """Открыть HTML в системном браузере."""
        try:
            import tempfile
            html = self._prepare_html(src, lang)
            with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".html",
                    delete=False, encoding="utf-8") as f:
                f.write(html)
                path = f.name
            webbrowser.open(f"file://{path}")
            self._show_toast("🌐 Открыто в браузере",
                              "success", 2000)
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    def _copy_preview_html(self, src: str,
                            lang: str) -> None:
        html = self._prepare_html(src, lang)
        if copy_to_clipboard(html):
            self._show_toast("📋 HTML скопирован",
                              "success", 2000)

    def _save_preview_html(self, src: str,
                            lang: str) -> None:
        from tkinter import filedialog as _fd
        from datetime import datetime as _dt
        html = self._prepare_html(src, lang)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fp = _fd.asksaveasfilename(
            parent=self,
            defaultextension=".html",
            initialfile=f"preview_{ts}.html",
            filetypes=[("HTML", "*.html")])
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(html)
            self._show_toast(
                f"💾 {os.path.basename(fp)}",
                "success", 3000)
            self._open_file(Path(fp))
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    # ────────────────────────────────────────────────
    # CODE: сохранение в файл
    # ────────────────────────────────────────────────
    def _save_code_as_file(self) -> None:
        """Сохранить текущую вкладку в файл с правильным расширением."""
        w = self._code_widget
        if w is None or not w.winfo_exists():
            return
        code = w.get("1.0", "end-1c")
        if not code.strip():
            self._show_toast("Пустой код", "warning")
            return

        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")

        ext_map = {
            "python": (".py", "Python"),
            "shell": (".sh", "Shell"),
            "javascript": (".js", "JavaScript"),
            "typescript": (".ts", "TypeScript"),
            "json": (".json", "JSON"),
            "html": (".html", "HTML"),
            "css": (".css", "CSS"),
            "sql": (".sql", "SQL"),
            "markdown": (".md", "Markdown"),
            "yaml": (".yml", "YAML"),
            "xml": (".xml", "XML"),
            "c": (".c", "C"),
            "cpp": (".cpp", "C++"),
            "rust": (".rs", "Rust"),
            "go": (".go", "Go"),
            "java": (".java", "Java"),
            "ruby": (".rb", "Ruby"),
            "php": (".php", "PHP"),
            "kotlin": (".kt", "Kotlin"),
            "swift": (".swift", "Swift"),
        }
        ext, label = ext_map.get(lang, (".txt", "Text"))

        from tkinter import filedialog as _fd
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = _fd.asksaveasfilename(
            defaultextension=ext,
            initialfile=f"script_{ts}{ext}",
            title=f"Сохранить {label} файл",
            filetypes=[
                (label, f"*{ext}"),
                ("Все файлы", "*.*"),
            ])
        if not fp:
            return

        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(code)
            self._show_toast(
                f"💾 {os.path.basename(fp)}", "success", 3000)
            with suppress(Exception):
                achievement_system.check(
                    self.current_user, "code_exporter")
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    # ────────────────────────────────────────────────
    # CODE: форматирование через black / shfmt / prettier
    # ────────────────────────────────────────────────
    def _format_code(self) -> None:
        """Форматирование кода. Python → black, Shell → shfmt,
        JS → prettier. Если нет — тост с командой установки."""
        w = self._code_widget
        if w is None or not w.winfo_exists():
            return
        code = w.get("1.0", "end-1c")
        if not code.strip():
            self._show_toast("Пустой код", "warning")
            return

        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")

        # Выбираем форматтер
        if lang == "python":
            cmd = ["python3", "-m", "black", "--quiet", "-"]
            pip_hint = "pip install black"
        elif lang == "shell":
            cmd = ["shfmt", "-i", "4"]
            pip_hint = "sudo apt install shfmt"
        elif lang == "javascript":
            cmd = ["prettier", "--stdin-filepath", "file.js"]
            pip_hint = "sudo apt install node-prettier"
        else:
            self._show_toast(
                f"🎨 Форматтер для «{lang}» не поддержан",
                "info", 2500)
            return

        # Запускаем в фоне
        def work() -> None:
            try:
                result = subprocess.run(
                    cmd, input=code,
                    capture_output=True, text=True,
                    timeout=15)
                if result.returncode == 0 and result.stdout:
                    self._safe_after(
                        0, self._apply_format_result,
                        w, result.stdout.strip())
                else:
                    err_msg = (result.stderr or "").strip()[:100]
                    self._safe_after(
                        0, self._show_toast,
                        f"❌ {pip_hint}" if "not found" in err_msg.lower()
                        or "no module" in err_msg.lower()
                        else f"❌ Формат не удался: {err_msg}",
                        "warning", 5000)
            except FileNotFoundError:
                self._safe_after(
                    0, self._show_toast,
                    f"❌ {pip_hint}", "error", 5000)
            except subprocess.TimeoutExpired:
                self._safe_after(
                    0, self._show_toast,
                    "⏱️ Форматирование зависло (>15с)",
                    "error", 3000)
            except Exception as e:
                self._safe_after(
                    0, self._show_toast,
                    f"❌ {e}", "error", 3000)

        executor.submit_safe(work)

    def _apply_format_result(self, widget: Any,
                              formatted: str) -> None:
        """Применить отформатированный код в редактор."""
        if widget is None or not widget.winfo_exists():
            return
        try:
            # Сохранить позицию курсора
            old_index = widget.index("insert")
            old_line = old_index.split(".")[0]

            widget.delete("1.0", "end")
            widget.insert("1.0", formatted)

            # Восстановить примерно ту же позицию
            try:
                widget.mark_set("insert", f"{old_line}.0")
                widget.see("insert")
            except Exception:
                pass

            self._show_toast("🎨 Отформатировано",
                              "success", 1500)
            with suppress(Exception):
                level_system.add_xp(self.current_user,
                                      "format_code")
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    # ────────────────────────────────────────────────
    # CODE: запуск всех вкладок
    # ────────────────────────────────────────────────
    def _run_all_tabs(self) -> None:
        """Запустить код всех открытых вкладок по очереди."""
        if not self._code_tabs:
            self._show_toast("Нет вкладок", "warning")
            return

        # FIX: только Python
        lang = (self._code_lang_var.get()
                 if self._code_lang_var else "python")
        if lang != "python":
            self._show_toast(
                f"⚠️ Batch-запуск только для Python",
                "warning", 3000)
            return
        out = self._code_out
        if out is None:
            return

        # Синхронизировать текущую вкладку перед запуском
        if (self._code_widget is not None
                and 0 <= self._code_current_tab < len(
                    self._code_tabs)):
            with suppress(Exception):
                self._code_tabs[
                    self._code_current_tab]["code"] = \
                    self._code_widget.get("1.0", "end-1c")

        # Собираем все непустые вкладки
        tabs_to_run: List[Tuple[str, str]] = []
        for i, tab in enumerate(self._code_tabs):
            code = (tab.get("code") or "").strip()
            if code:
                name = tab.get("name", f"tab{i+1}")
                tabs_to_run.append((name, code))

        if not tabs_to_run:
            self._show_toast("Все вкладки пусты", "warning")
            return

        # Очищаем вывод
        out.delete("1.0", "end")
        out.insert("1.0",
                    f"▶▶ Запуск {len(tabs_to_run)} вкладок...\n\n")
        if self._code_status_lbl is not None:
            with suppress(Exception):
                self._code_status_lbl.configure(
                    text=f"▶▶ 0/{len(tabs_to_run)}")

        # Запускаем в фоне
        def work() -> None:
            from nexus.sandbox import SandboxRunner
            total = len(tabs_to_run)
            for idx, (name, code) in enumerate(tabs_to_run, 1):
                self._safe_after(
                    0, self._update_batch_progress,
                    idx, total, name)
                try:
                    stdout, stderr, ok = SandboxRunner.run(
                        code, timeout=10)
                except Exception as e:
                    stdout, stderr, ok = "", str(e), False
                self._safe_after(
                    0, self._append_tab_result,
                    name, stdout, stderr, ok)
            self._safe_after(
                0, self._finish_batch_run, total)

        executor.submit_safe(work)

    def _update_batch_progress(self, idx: int,
                                 total: int,
                                 name: str) -> None:
        """Обновить статус при batch-запуске."""
        if self._code_status_lbl is not None:
            with suppress(Exception):
                self._code_status_lbl.configure(
                    text=f"▶▶ {idx}/{total} · {name}")

    def _append_tab_result(self, name: str, stdout: str,
                             stderr: str, ok: bool) -> None:
        """Добавить результат вкладки в вывод."""
        out = self._code_out
        if out is None or not out.winfo_exists():
            return
        try:
            out.insert("end", f"─── 📄 {name} ───\n")
            if stdout.strip():
                out.insert("end", stdout)
                if not stdout.endswith("\n"):
                    out.insert("end", "\n")
            if stderr.strip():
                out.insert("end", f"⚠️ {stderr}\n")
            if not stdout.strip() and not stderr.strip():
                if ok:
                    out.insert("end", "✅ OK (нет вывода)\n")
                else:
                    out.insert("end", "❌ Ошибка (нет stderr)\n")
            out.insert("end", f"{'✅' if ok else '❌'} "
                               f"код {name} завершён\n\n")
            out.see("end")
        except Exception:
            pass

    def _finish_batch_run(self, total: int) -> None:
        """Завершение batch-запуска."""
        if self._code_status_lbl is not None:
            with suppress(Exception):
                self._code_status_lbl.configure(
                    text=f"✅ Batch: {total} вкладок")
        self._show_toast(
            f"✅ {total} вкладок выполнено",
            "success", 3000)
        with suppress(Exception):
            level_system.add_xp(
                self.current_user, "code_batch")

    # ────────────────────────────────────────────────
    # QR STUDIO methods
    # ────────────────────────────────────────────────
    def _qr_schedule_preview(self) -> None:
        if not self.config_data.qr_live_preview:
            return
        old = self._timers.get("qr_live_preview")
        if old:
            with suppress(Exception):
                self.after_cancel(old)
        self._timers["qr_live_preview"] = self._safe_after(
            UI_QR_DEBOUNCE_MS, self._qr_generate_click)

    def _qr_on_style_change(self, style_label: str) -> None:
        m = re.search(r"\((\w+)\)$", style_label)
        if m:
            style_key = m.group(1)
            self.config_data.qr_default_style = style_key
            self.config_data.save()
            self._qr_styles_used.add(style_key)
        self._qr_schedule_preview()

    def _qr_generate_click(self) -> None:
        if not self._qr_get_mode_data:
            return
        data = self._qr_get_mode_data()
        if not data:
            if (self._qr_label
                    and self._qr_label.winfo_exists()):
                with suppress(Exception):
                    self._qr_label.configure(
                        image="", text="📱 Введите данные...")
            return
        try:
            size = int(self._qr_size_var.get()
                        if self._qr_size_var else "400")
        except (TypeError, ValueError):
            size = 400
        style_key = "neon_cyan"
        if self._qr_style_var:
            m = re.search(r"\((\w+)\)$",
                          self._qr_style_var.get())
            if m:
                style_key = m.group(1)
        logo_path = self._qr_logo_path

        def work() -> None:
            img = qr_gen.generate(
                data, size=size, style=style_key,
                logo_path=logo_path)
            if img:
                self._safe_after(0, self._qr_apply_preview,
                                  img, data, style_key, size)
        executor.submit_safe(work)

        # ── Начисление XP, истории, достижений ──────
        with suppress(Exception):
            level_system.add_xp(self.current_user,
                                  "generate_qr")
            level_system.increment(self.current_user,
                                     "qr_generated")
            achievement_system.check(self.current_user,
                                      "first_qr")
            achievement_system.check(self.current_user,
                                      "qr_master")
            DailyQuests.complete(self.current_user,
                                   "generate_qr")
            qr_history.add(
                self.current_user, data, style_key, size,
                mode=self._qr_current_mode)

        # Тост с результатом
        with suppress(Exception):
            self._show_toast(
                f"🎨 QR · {size}px · {len(data)} симв. "
                f"· +15 XP",
                "success", 2000)

    def _qr_apply_preview(self, img: Any, data: str,
                            style_key: str, size: int) -> None:
        if self._is_closing:
            return
        self._qr_preview_image = img
        self._qr_preview_data = data
        try:
            if (self._qr_label
                    and self._qr_label.winfo_exists()):
                sz = min(UI_QR_PREVIEW_SIZE, img.size[0])
                cimg = CTkImage(light_image=img,
                                 dark_image=img, size=(sz, sz))
                self._qr_label.configure(image=cimg, text="")
                self._qr_label.image = cimg
            if self._qr_info_lbl is not None:
                with suppress(Exception):
                    self._qr_info_lbl.configure(
                        text=f"✅ {style_key} · {size}px · "
                             f"{len(data)} символов")
        except Exception:
            pass

    def _qr_copy_data(self) -> None:
        data = self._qr_preview_data
        if not data:
            self._show_toast("Нечего копировать", "warning")
            return
        if copy_to_clipboard(data):
            self._show_toast("📋 Данные скопированы",
                              "success", 1500)

    def _qr_save_as(self, fmt: str) -> None:
        img = self._qr_preview_image
        if img is None:
            self._show_toast("Сначала создайте QR", "warning")
            return
        if fmt == "SVG":
            data = self._qr_preview_data
            if not data:
                return
            fp = filedialog.asksaveasfilename(
                parent=self,
                defaultextension=".svg",
                initialfile=f"qr_"
                            f"{datetime.now():%Y%m%d_%H%M%S}.svg",
                filetypes=[("SVG", "*.svg")])
            if not fp:
                return
            style_key = "neon_cyan"
            if self._qr_style_var:
                m = re.search(r"\((\w+)\)$",
                              self._qr_style_var.get())
                if m:
                    style_key = m.group(1)
            cfg = qr_gen.STYLES.get(
                style_key, qr_gen.STYLES["neon_cyan"])
            ok, msg = QRExporter.export_svg(
                data, Path(fp),
                front=cfg.get("color", "#000000"),
                back=cfg.get("bg", "#ffffff"))
            self._show_toast(
                f"{'✅' if ok else '❌'} {os.path.basename(fp)}"
                if ok else f"❌ {msg}",
                "success" if ok else "error")
            return
        ext = {"PNG": ".png", "JPEG": ".jpg"}.get(fmt, ".png")
        fp = filedialog.asksaveasfilename(
            defaultextension=ext,
            initialfile=f"qr_"
                        f"{datetime.now():%Y%m%d_%H%M%S}{ext}",
            filetypes=[(fmt, f"*{ext}")])
        if not fp:
            return
        try:
            out_path = Path(fp)
            if fmt == "JPEG":
                ok, msg = QRExporter.export_jpg(img, out_path)
            else:
                ok, msg = QRExporter.export_png(img, out_path)
            self._show_toast(
                f"✅ {os.path.basename(fp)}" if ok
                else f"❌ {msg}",
                "success" if ok else "error")
        except Exception as e:
            self._show_toast(f"❌ {e}", "error")

    def _qr_save_to_history(self) -> None:
        data = self._qr_preview_data
        if not data:
            self._show_toast("Сначала создайте QR", "warning")
            return
        style_key = "neon_cyan"
        if self._qr_style_var:
            m = re.search(r"\((\w+)\)$",
                          self._qr_style_var.get())
            if m:
                style_key = m.group(1)
        try:
            size = int(self._qr_size_var.get()
                        if self._qr_size_var else "400")
        except (TypeError, ValueError):
            size = 400
        qr_history.add(self.current_user, data, style_key,
                        size, mode=self._qr_current_mode)
        self._show_toast("💾 Сохранено в историю", "success")

    def _qr_choose_logo(self) -> None:
        fp = filedialog.askopenfilename(
            parent=self,
            title="Выберите логотип",
            filetypes=[("Изображения",
                        "*.png *.jpg *.jpeg *.gif *.bmp")])
        if not fp:
            return
        ok, msg, dst = QRLogoManager.import_logo(fp)
        if ok and dst:
            self._qr_logo_path = str(dst)
            self.config_data.qr_last_logo = str(dst)
            self.config_data.save()
            if (self._qr_logo_lbl
                    and self._qr_logo_lbl.winfo_exists()):
                with suppress(Exception):
                    self._qr_logo_lbl.configure(
                        text=f"✅ {dst.name}")
            self._show_toast("🖼️ Логотип загружен", "success")
            self._qr_schedule_preview()
        else:
            self._show_toast(f"❌ {msg}", "error")

    def _qr_clear_logo(self) -> None:
        self._qr_logo_path = None
        self.config_data.qr_last_logo = ""
        self.config_data.save()
        if (self._qr_logo_lbl
                and self._qr_logo_lbl.winfo_exists()):
            with suppress(Exception):
                self._qr_logo_lbl.configure(
                    text="— не выбран —")
        self._qr_schedule_preview()

    def _qr_scan_from_clipboard(self) -> None:
        def work() -> None:
            try:
                img = clipboard_image()
                if img is None:
                    self._safe_after(
                        0, self._show_toast,
                        "❌ В буфере нет изображения",
                        "warning")
                    return
                result = qr_gen.decode_from_pil(img)
                if result == "__NO_LIB__":
                    self._safe_after(
                        0, self._show_toast,
                        "❌ pip install pyzbar", "error")
                elif result:
                    self._safe_after(
                        0, self._show_toast,
                        f"📥 {truncate(result, 60)}",
                        "success", 5000)
                else:
                    self._safe_after(
                        0, self._show_toast,
                        "❌ QR не найден", "warning")
            except Exception as e:
                self._safe_after(0, self._show_toast,
                                  f"❌ {e}", "error")
        executor.submit_safe(work)

    def _qr_scan_from_file(self) -> None:
        fp = filedialog.askopenfilename(
            filetypes=[("Изображения",
                        "*.png *.jpg *.jpeg *.gif")])
        if not fp:
            return

        def work() -> None:
            result = qr_gen.decode_qr(fp)
            if result == "__NO_LIB__":
                self._safe_after(0, self._show_toast,
                                  "❌ pip install pyzbar",
                                  "error")
            elif result:
                self._safe_after(
                    0, self._show_toast,
                    f"📥 {truncate(result, 60)}",
                    "success", 5000)
            else:
                self._safe_after(0, self._show_toast,
                                  "❌ QR не найден", "warning")
        executor.submit_safe(work)

    # ────────────────────────────────────────────────
    # CODE: история, шаблоны, поиск
    # ────────────────────────────────────────────────
    def _show_code_history(self) -> None:
        if (self._code_history_win
                and self._code_history_win.winfo_exists()):
            self._code_history_win.focus()
            return
        t = theme_manager.get_all()
        history = CodeHistory.get_user_history(
            self.current_user, limit=50)
        win = CTkToplevel(self)
        self._code_history_win = win
        win.title("📜 История кода")
        win.geometry("800x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())

        header = CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 8))
        CTkLabel(header,
                 text=f"📜 ИСТОРИЯ ({len(history)})",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(side="left")

        def clear_hist() -> None:
            if not messagebox.askyesno(
                    "Очистить", "Удалить всю историю?"):
                return
            n = CodeHistory.clear(self.current_user)
            self._show_toast(f"🗑️ Удалено: {n}", "info")
            win.destroy()

        CTkButton(header, text="🗑️ Очистить", width=120,
                  height=30,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=clear_hist).pack(side="right")

        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=15,
                     pady=(0, 15))
        if not history:
            CTkLabel(scroll, text="Пока пусто",
                     font=CTkFont(size=14),
                     text_color=t.get("text_dim")
                     ).pack(pady=20)
            return
        for h in history:
            item = CTkFrame(scroll, fg_color=t.get("bg3"),
                             corner_radius=6)
            item.pack(fill="x", pady=3)
            top = CTkFrame(item, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 2))
            ok = h.get("success", 0)
            CTkLabel(top,
                     text=f"{'✅' if ok else '❌'} "
                          f"{fmt_dt(h.get('created_at', ''))} "
                          f"· {h.get('duration', 0):.2f}с",
                     font=CTkFont(size=10, weight="bold"),
                     text_color=(t.get("success") if ok
                                  else t.get("error")),
                     anchor="w").pack(side="left")
            code_preview = h.get("code", "")[:200]
            if len(h.get("code", "")) > 200:
                code_preview += "\n..."
            CTkLabel(item, text=code_preview,
                     font=CTkFont(family="Courier", size=10),
                     text_color=t.get("text"),
                     anchor="w", justify="left",
                     wraplength=UI_WRAP_MED
                     ).pack(anchor="w", fill="x", padx=10,
                            pady=(2, 4))
            br = CTkFrame(item, fg_color="transparent")
            br.pack(fill="x", padx=10, pady=(2, 6))
            CTkButton(br, text="📋 В редактор",
                      width=140, height=26,
                      font=CTkFont(size=10, weight="bold"),
                      fg_color=t.get("primary"),
                      text_color=t.get("bg"),
                      command=lambda c=h.get("code", ""),
                          w=win: (
                          self._load_code_to_editor(c),
                          self._show_page("Code"),
                          w.destroy())
                      ).pack(side="left", padx=2)
            CTkButton(br, text="📄 Копировать",
                      width=140, height=26,
                      font=CTkFont(size=10, weight="bold"),
                      fg_color=t.get("accent"),
                      text_color=t.get("bg"),
                      command=lambda c=h.get("code", ""): (
                          copy_to_clipboard(c),
                          self._show_toast("📋 Скопировано",
                                            "success", 1500))
                      ).pack(side="left", padx=2)
            CTkButton(br, text="🗑️", width=32, height=26,
                      font=CTkFont(size=10),
                      fg_color="transparent",
                      text_color=t.get("error"),
                      command=lambda eid=h.get("id"): (
                          CodeHistory.delete(
                              eid, self.current_user),
                          win.destroy(),
                          self._show_code_history())
                      ).pack(side="right", padx=2)

    def _show_templates(self) -> None:
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        win.title("📚 Библиотека шаблонов")
        win.geometry("800x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="📚 БИБЛИОТЕКА ШАБЛОНОВ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 8))
        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=15,
                     pady=(0, 15))

        for cat in CodeTemplates.get_categories():
            CTkLabel(scroll, text=f"▸ {cat}",
                     font=CTkFont(size=14, weight="bold"),
                     text_color=t.get("accent"),
                     anchor="w").pack(anchor="w", padx=8,
                                        pady=(12, 4))
            for tid, tpl in CodeTemplates.get_by_category(cat):
                card = CTkFrame(scroll,
                                 fg_color=t.get("bg3"),
                                 corner_radius=6)
                card.pack(fill="x", pady=3)
                top = CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=10, pady=(6, 2))
                CTkLabel(top,
                         text=f"{tpl.get('icon', '📄')} "
                              f"{tpl.get('name', '')}",
                         font=CTkFont(size=13, weight="bold"),
                         text_color=t.get("primary"),
                         anchor="w").pack(side="left")
                CTkLabel(card,
                         text=tpl.get("description", ""),
                         font=CTkFont(size=10),
                         text_color=t.get("text_dim"),
                         anchor="w").pack(anchor="w",
                                            padx=10,
                                            pady=(0, 4))
                br = CTkFrame(card, fg_color="transparent")
                br.pack(fill="x", padx=10, pady=(0, 6))
                CTkButton(
                    br, text="📂 Загрузить", width=140,
                    height=26,
                    font=CTkFont(size=10, weight="bold"),
                    fg_color=t.get("success"),
                    text_color=t.get("bg"),
                    command=lambda c=tpl.get("code", ""),
                        n=tpl.get("name", ""),
                        tid2=tid,
                        w=win: (
                            self._load_code_to_editor(c),
                            self._code_templates_used.add(tid2),
                            self._show_toast(
                                f"📚 {n}", "success", 1500),
                            self._show_page("Code"),
                            w.destroy())
                ).pack(side="left", padx=2)
                CTkButton(
                    br, text="📄 Копировать", width=140,
                    height=26,
                    font=CTkFont(size=10, weight="bold"),
                    fg_color=t.get("accent"),
                    text_color=t.get("bg"),
                    command=lambda c=tpl.get("code", ""): (
                        copy_to_clipboard(c),
                        self._show_toast("📋 Скопировано",
                                          "success", 1500))
                ).pack(side="left", padx=2)

    def _show_code_search(self) -> None:
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        win.title("🔍 Поиск в истории кода")
        win.geometry("700x500")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="🔍 ПОИСК ПО КОДУ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 8))
        entry = CTkEntry(win, placeholder_text="Запрос...",
                          height=42, font=CTkFont(size=13),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(fill="x", padx=20, pady=8)
        entry.focus()
        results = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        results.pack(fill="both", expand=True, padx=20,
                     pady=(0, 15))

        def do_search() -> None:
            q = entry.get().strip()
            for w in results.winfo_children():
                w.destroy()
            if not q:
                return
            items = CodeHistory.search(
                self.current_user, q, limit=30)
            if not items:
                CTkLabel(results, text="😕 Ничего",
                         font=CTkFont(size=13),
                         text_color=t.get("text_dim")
                         ).pack(pady=20)
                return
            for h in items:
                item = CTkFrame(results,
                                 fg_color=t.get("bg3"),
                                 corner_radius=6)
                item.pack(fill="x", pady=3)
                CTkLabel(item,
                         text=f"✅ "
                              f"{fmt_dt(h.get('created_at', ''))}",
                         font=CTkFont(size=10, weight="bold"),
                         text_color=t.get("accent"),
                         anchor="w").pack(anchor="w", padx=10,
                                            pady=(6, 2))
                CTkLabel(item,
                         text=h.get("code", "")[:300],
                         font=CTkFont(family="Courier", size=10),
                         text_color=t.get("text"),
                         anchor="w", justify="left",
                         wraplength=UI_WRAP_MED
                         ).pack(anchor="w", fill="x", padx=10,
                                pady=(2, 4))
                CTkButton(item, text="📋 В редактор",
                          width=140, height=26,
                          font=CTkFont(size=10, weight="bold"),
                          fg_color=t.get("primary"),
                          text_color=t.get("bg"),
                          command=lambda c=h.get("code", ""),
                              w=win: (
                              self._load_code_to_editor(c),
                              self._show_page("Code"),
                              w.destroy())
                          ).pack(anchor="e", padx=10,
                                 pady=(0, 6))

        def debounced(e: Any = None) -> None:
            self._timer_pool.schedule(
                f"code_search_{id(win)}",
                UI_SEARCH_DEBOUNCE_MS, do_search)

        entry.bind("<KeyRelease>", debounced)
        entry.bind("<Return>", lambda e: do_search())

    # ────────────────────────────────────────────────
    # QR: история + батч
    # ────────────────────────────────────────────────
    def _show_qr_history(self) -> None:
        if (self._qr_history_win
                and self._qr_history_win.winfo_exists()):
            self._qr_history_win.focus()
            return
        t = theme_manager.get_all()
        history = qr_history.get_user_history(
            self.current_user, limit=80)
        win = CTkToplevel(self)
        self._qr_history_win = win
        win.title("📚 История QR")
        win.geometry("800x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        header = CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 8))
        CTkLabel(header,
                 text=f"📚 ИСТОРИЯ ({len(history)})",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(side="left")

        def clear_hist() -> None:
            if not messagebox.askyesno(
                    "Очистить", "Удалить всю историю?"):
                return
            n = qr_history.clear(self.current_user)
            self._show_toast(f"🗑️ Удалено: {n}", "info")
            win.destroy()

        CTkButton(header, text="🗑️ Очистить", width=120,
                  height=30,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=clear_hist).pack(side="right")

        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=15,
                     pady=(0, 15))
        if not history:
            CTkLabel(scroll, text="Пока пусто",
                     font=CTkFont(size=14),
                     text_color=t.get("text_dim")
                     ).pack(pady=20)
            return
        for h in history:
            item = CTkFrame(scroll, fg_color=t.get("bg3"),
                             corner_radius=6)
            item.pack(fill="x", pady=3)
            top = CTkFrame(item, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 2))
            CTkLabel(top,
                     text=f"📱 "
                          f"{fmt_dt(h.get('created_at', ''))}"
                          f" · {h.get('style', '?')} · "
                          f"{h.get('size', '?')}px",
                     font=CTkFont(size=10, weight="bold"),
                     text_color=t.get("accent"),
                     anchor="w").pack(side="left")
            data = h.get("data", "")
            CTkLabel(item, text=truncate(data, 120),
                     font=CTkFont(family="Courier", size=10),
                     text_color=t.get("text"),
                     anchor="w", justify="left",
                     wraplength=UI_WRAP_MED
                     ).pack(anchor="w", fill="x", padx=10,
                            pady=(2, 4))
            br = CTkFrame(item, fg_color="transparent")
            br.pack(fill="x", padx=10, pady=(2, 6))
            CTkButton(br, text="🔁 Перегенерировать",
                      width=180, height=26,
                      font=CTkFont(size=10, weight="bold"),
                      fg_color=t.get("primary"),
                      text_color=t.get("bg"),
                      command=lambda d=data, w=win: (
                          self._qr_load_from_history(d),
                          self._show_page("QR"),
                          w.destroy())
                      ).pack(side="left", padx=2)
            CTkButton(br, text="📋 Копировать",
                      width=140, height=26,
                      font=CTkFont(size=10, weight="bold"),
                      fg_color=t.get("accent"),
                      text_color=t.get("bg"),
                      command=lambda d=data: (
                          copy_to_clipboard(d),
                          self._show_toast("📋 Скопировано",
                                            "success", 1500))
                      ).pack(side="left", padx=2)
            CTkButton(br, text="🗑️", width=32, height=26,
                      font=CTkFont(size=10),
                      fg_color="transparent",
                      text_color=t.get("error"),
                      command=lambda eid=h.get("id"): (
                          qr_history.delete(
                              eid, self.current_user),
                          win.destroy(),
                          self._show_qr_history())
                      ).pack(side="right", padx=2)

    def _qr_load_from_history(self, data: str) -> None:
        mode = self._qr_mode_data.get("text")
        if mode is not None and hasattr(mode, "delete"):
            with suppress(Exception):
                mode.delete("1.0", "end")
                mode.insert("1.0", data)
        self._qr_schedule_preview()
        self._show_toast("🔁 Данные загружены", "success")

    def _show_qr_batch(self) -> None:
        if (self._qr_batch_win
                and self._qr_batch_win.winfo_exists()):
            self._qr_batch_win.focus()
            return
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._qr_batch_win = win
        win.title("📦 Батч-генерация QR")
        win.geometry("700x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="📦 БАТЧ-ГЕНЕРАЦИЯ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 4))
        CTkLabel(win,
                 text="CSV или TXT: одна строка = один QR.\n"
                      "CSV: данные,метка",
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim"),
                 justify="center").pack(pady=(0, 12))

        file_row = CTkFrame(win, fg_color="transparent")
        file_row.pack(fill="x", padx=20, pady=(0, 8))
        file_lbl = CTkLabel(file_row,
                             text="📁 Файл не выбран",
                             font=CTkFont(size=11),
                             text_color=t.get("text_dim"),
                             anchor="w")
        file_lbl.pack(side="left", fill="x", expand=True)
        parsed_items: List[Tuple[str, str]] = []
        def choose_file() -> None:
            nonlocal parsed_items
            # FIX: filedialog без parent= — у CTkToplevel
            # _w может быть строкой, что ломает tkinter
            fp = filedialog.askopenfilename(
                title="Выберите CSV или TXT",
                filetypes=[("Data", "*.csv *.txt *.md")])
            if not fp:
                return
            ok, items, err = QRBatchProcessor.parse_input_file(
                fp)
            if ok and items:
                parsed_items = items
                file_lbl.configure(
                    text=f"✅ {len(items)} элементов",
                    text_color=t.get("success"))
            else:
                parsed_items = []
                file_lbl.configure(
                    text=f"❌ {err or 'нет данных'}",
                    text_color=t.get("error"))

        CTkButton(file_row, text="📂 Выбрать",
                  width=140, height=36,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("primary"),
                  text_color=t.get("bg"),
                  command=choose_file).pack(side="right")

        opts = CTkFrame(win, fg_color="transparent")
        opts.pack(fill="x", padx=20, pady=(8, 4))
        CTkLabel(opts, text="🎨 Стиль:",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=t.get("text_dim")
                 ).pack(side="left")
        style_names = [
            f"{qr_gen.get_style_name(k)} ({k})"
            for k in qr_gen.get_styles()]
        style_var = StringVar(
            value=(self._qr_style_var.get()
                   if self._qr_style_var
                   else style_names[0]))
        CTkOptionMenu(opts, values=style_names,
                       variable=style_var,
                       width=200, height=30,
                       font=CTkFont(size=11),
                       fg_color=t.get("bg3"),
                       button_color=t.get("primary"),
                       text_color=t.get("text")
                       ).pack(side="left", padx=(6, 20))
        CTkLabel(opts, text="📐 Формат:",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=t.get("text_dim")
                 ).pack(side="left")
        fmt_var = StringVar(value="PNG")
        CTkOptionMenu(opts, values=["PNG", "JPEG"],
                       variable=fmt_var,
                       width=100, height=30,
                       font=CTkFont(size=11),
                       fg_color=t.get("bg3"),
                       button_color=t.get("primary"),
                       text_color=t.get("text")
                       ).pack(side="left", padx=(6, 0))

        progress_lbl = CTkLabel(win, text="",
                                 font=CTkFont(size=11,
                                               weight="bold"),
                                 text_color=t.get("accent"))
        progress_lbl.pack(pady=(8, 4))
        pbar = CTkProgressBar(
            win, height=10, corner_radius=5,
            progress_color=t.get("gradient1"),
            fg_color=t.get("bg3"))
        pbar.pack(fill="x", padx=20, pady=(0, 12))
        pbar.set(0)

        def start_batch() -> None:
            if not parsed_items:
                self._show_toast("Сначала выберите файл",
                                  "warning")
                return
            m = re.search(r"\((\w+)\)$", style_var.get())
            style_key = m.group(1) if m else "neon_cyan"
            fmt = fmt_var.get()
            logo_p = self._qr_logo_path
            from nexus.core import QR_EXPORT_DIR
            out_dir = (QR_EXPORT_DIR /
                       datetime.now().strftime(
                           "batch_%Y%m%d_%H%M%S"))

            def prog(i: int, total: int) -> None:
                self._safe_after(0, lambda: (
                    pbar.set(i / max(1, total)),
                    progress_lbl.configure(
                        text=f"⏳ {i}/{total}")))

            def work() -> None:
                ok_c, err_c, errors = QRBatchProcessor.process(
                    parsed_items, style=style_key,
                    size=400, out_dir=out_dir, fmt=fmt,
                    user=self.current_user,
                    logo_path=logo_p,
                    progress_cb=prog)
                self._safe_after(0, self._qr_batch_done,
                                  ok_c, err_c, errors,
                                  out_dir, fmt, win)
            executor.submit_safe(work)

        bf = CTkFrame(win, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(8, 15))
        CTkButton(bf, text="▶ СТАРТ", height=42, width=200,
                  font=CTkFont(size=13, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=start_batch).pack(side="left")
        CTkButton(bf, text="❌ Отмена", height=42, width=140,
                  fg_color="transparent", border_width=1,
                  border_color=t.get("error"),
                  text_color=t.get("error"),
                  command=win.destroy).pack(side="left",
                                              padx=6)

    def _qr_batch_done(self, ok_count: int, err_count: int,
                        errors: List[str], out_dir: Path,
                        fmt: str, win: Any) -> None:
        msg = f"✅ Успешно: {ok_count} · ❌ Ошибок: {err_count}"
        self._show_toast(msg, "success" if err_count == 0
                          else "warning", 6000)
        if errors:
            self._show_toast(
                f"⚠️ Первая ошибка: {errors[0]}",
                "warning", 5000)
        if ok_count > 0 and messagebox.askyesno(
                "Батч завершён",
                f"Создано QR: {ok_count}\n"
                f"Папка: {out_dir.name}\n\n"
                f"Создать ZIP-архив?"):
            try:
                zip_path = QRBatchProcessor.export_zip(out_dir)
                self._show_toast(f"📦 {zip_path.name}",
                                  "success", 5000)
                self._safe_after(100, self._open_file, zip_path)
            except Exception as e:
                self._show_toast(f"❌ {e}", "error")
        with suppress(Exception):
            win.destroy()

    # ────────────────────────────────────────────────
    # SNIPPET: диалог создания
    # ────────────────────────────────────────────────
    def _new_snippet_dialog(self) -> None:
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        win.title("✂️ Новый сниппет")
        win.geometry("640x560")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="✂️ НОВЫЙ СНИППЕТ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 8))
        CTkLabel(win, text="📝 Название:",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=t.get("text_glow"),
                 anchor="w").pack(anchor="w", padx=20,
                                    pady=(8, 3))
        name_e = CTkEntry(win, height=36,
                           font=CTkFont(size=13),
                           fg_color=t.get("bg3"),
                           border_color=t.get("card_border"),
                           text_color=t.get("text"))
        name_e.pack(fill="x", padx=20, pady=(0, 4))
        name_e.focus()
        CTkLabel(win, text="💻 Код:",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=t.get("text_glow"),
                 anchor="w").pack(anchor="w", padx=20,
                                    pady=(8, 3))
        code_e = CTkTextbox(
            win, height=260,
            font=CTkFont(family="Courier", size=12),
            fg_color=t.get("code_bg"),
            border_color=t.get("card_border"),
            text_color=t.get("code_text"),
            wrap="none")
        code_e.pack(fill="both", expand=True, padx=20,
                     pady=(0, 8))
        msg = CTkLabel(win, text="", font=CTkFont(size=11),
                        text_color=t.get("error"))
        msg.pack(pady=2)

        def save() -> None:
            name = name_e.get().strip()
            code = code_e.get("1.0", "end-1c").strip()
            if not name or not code:
                msg.configure(text="Имя и код обязательны")
                return
            s = snippet_manager.create(
                name=name, code=code,
                user=self.current_user)
            if s:
                level_system.add_xp(self.current_user,
                                      "save_snippet")
                level_system.increment(self.current_user,
                                         "snippets_saved")
                achievement_system.check(self.current_user,
                                          "snippet_master")
                self._show_toast("✂️ Сохранён! +10 XP",
                                  "success")
                win.destroy()
                self._refresh_page()
            else:
                msg.configure(text="❌ Ошибка")

        bf = CTkFrame(win, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(6, 15))
        CTkButton(bf, text="💾 СОХРАНИТЬ", height=38,
                  width=180,
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=save).pack(side="left")
        CTkButton(bf, text="❌ Отмена", height=38, width=140,
                  fg_color="transparent", border_width=1,
                  border_color=t.get("error"),
                  text_color=t.get("error"),
                  command=win.destroy).pack(side="left",
                                              padx=6)

    def _show_device_list(self) -> None:
        t = theme_manager.get_all()
        devices = SessionManager.list_devices(self.current_user)
        win = CTkToplevel(self)
        win.title("📱 Мои устройства")
        win.geometry("700x500")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        CTkLabel(win, text=f"📱 УСТРОЙСТВА ({len(devices)})",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 8))
        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=15,
                     pady=(0, 15))
        if not devices:
            CTkLabel(scroll, text="Только текущее устройство",
                     font=CTkFont(size=13),
                     text_color=t.get("text_dim")
                     ).pack(pady=30)
        else:
            for d in devices:
                card = CTkFrame(scroll,
                                 fg_color=t.get("bg3"),
                                 corner_radius=6)
                card.pack(fill="x", pady=3)
                CTkLabel(card,
                         text=f"🖥️ "
                              f"{truncate(d.get('user_agent', '?'), 50)}",
                         font=CTkFont(size=12, weight="bold"),
                         text_color=t.get("primary"),
                         anchor="w").pack(anchor="w", padx=10,
                                            pady=(6, 2))
                CTkLabel(card,
                         text=f"📅 "
                              f"{fmt_dt(d.get('created_at', ''))}"
                              f" · Истекает: "
                              f"{fmt_dt(d.get('expires_at', ''), '%d.%m.%Y')}",
                         font=CTkFont(size=10),
                         text_color=t.get("text_dim"),
                         anchor="w").pack(anchor="w", padx=10,
                                            pady=(0, 6))

        def revoke_all() -> None:
            if not messagebox.askyesno(
                    "Отозвать все",
                    "Разлогинить на всех устройствах "
                    "(кроме текущего)?"):
                return
            n = SessionManager.revoke_all(self.current_user)
            self._show_toast(
                f"🚪 Отозвано сессий: {n}", "success")
            with suppress(Exception):
                achievement_system.check(
                    self.current_user, "device_manager")
            win.destroy()

        CTkButton(win, text="🚪 Отозвать все", height=38,
                  width=220,
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=revoke_all
                  ).pack(pady=(0, 15))

    def _show_api_tokens(self) -> None:
        if (self._api_tokens_win
                and self._api_tokens_win.winfo_exists()):
            self._api_tokens_win.focus()
            return
        t = theme_manager.get_all()
        from nexus.security import APITokenManager
        win = CTkToplevel(self)
        self._api_tokens_win = win
        win.title("🎫 API-токены")
        win.geometry("700x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="🎫 API-ТОКЕНЫ",
                 font=CTkFont(size=20, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 4))
        running = api_server.is_running()
        CTkLabel(win,
                 text=f"API-сервер: "
                      f"{'🟢 работает на порту ' + str(api_server.port()) if running else '🔴 выключен'}",
                 font=CTkFont(size=11),
                 text_color=t.get("success" if running
                                   else "warning")
                 ).pack(pady=(0, 8))
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
                self.current_user, name_e.get().strip())
            if ok and raw:
                name_e.delete(0, "end")
                self._show_toast(
                    "✅ Токен создан (скопируйте!)",
                    "success", 4000)
                win_tok = CTkToplevel(self)
                win_tok.title("🎫 Новый токен")
                win_tok.geometry("600x300")
                win_tok.configure(fg_color=t.get("bg"))
                win_tok.transient(self)
                CTkLabel(win_tok,
                         text="⚠️ Токен показывается ОДИН РАЗ!",
                         font=CTkFont(size=12, weight="bold"),
                         text_color=t.get("error")
                         ).pack(pady=(15, 4))
                txt = CTkTextbox(
                    win_tok, height=60,
                    font=CTkFont(family="Courier", size=12),
                    fg_color=t.get("bg3"),
                    text_color=t.get("primary"))
                txt.pack(fill="x", padx=20, pady=6)
                txt.insert("1.0", raw)
                CTkButton(win_tok, text="📋 Копировать",
                          height=38, width=200,
                          fg_color=t.get("primary"),
                          text_color=t.get("bg"),
                          command=lambda: (
                              copy_to_clipboard(raw),
                              self._show_toast(
                                  "📋 Скопировано",
                                  "success"))
                          ).pack(pady=8)
                win.destroy()
                self._show_api_tokens()

        CTkButton(add_row, text="➕ Создать",
                  width=140, height=36,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=create_token).pack(side="right")

        tokens = APITokenManager.list_for_user(
            self.current_user)
        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=20,
                     pady=(8, 15))
        if not tokens:
            CTkLabel(scroll, text="Пока нет токенов",
                     font=CTkFont(size=12),
                     text_color=t.get("text_dim")
                     ).pack(pady=20)
        else:
            for tok in tokens:
                card = CTkFrame(scroll,
                                 fg_color=t.get("bg3"),
                                 corner_radius=6)
                card.pack(fill="x", pady=3)
                revoked = tok.get("revoked")
                CTkLabel(card,
                         text=f"{'🚫' if revoked else '🎫'} "
                              f"{tok.get('name', '')}",
                         font=CTkFont(size=12, weight="bold"),
                         text_color=(t.get("text_dim")
                                      if revoked
                                      else t.get("primary")),
                         anchor="w").pack(anchor="w", padx=10,
                                            pady=(6, 2))
                CTkLabel(card,
                         text=f"📅 Создан: "
                              f"{fmt_dt(tok.get('created_at', ''))}"
                              f" · Истекает: "
                              f"{fmt_dt(tok.get('expires_at', ''), '%d.%m.%Y')}"
                              f" · Использован: "
                              f"{fmt_dt(tok.get('last_used', '')) or '—'}",
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
                                      tid, self.current_user),
                                  win.destroy(),
                                  self._show_api_tokens())
                              ).pack(anchor="e", padx=10,
                                     pady=(2, 6))
                else:
                    CTkFrame(card, height=4,
                              fg_color="transparent").pack()

    def _show_subtasks_dialog(self, task: Dict[str, Any]) -> None:
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        win.title(f"🪜 Подзадачи: {task['title']}")
        win.geometry("560x520")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())

        CTkLabel(win, text="🪜 ПОДЗАДАЧИ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 4))
        CTkLabel(win, text=truncate(task["title"], 50),
                 font=CTkFont(size=12),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, 10))

        add_row = CTkFrame(win, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(0, 8))
        new_e = CTkEntry(add_row,
                          placeholder_text="Новая подзадача...",
                          height=36, font=CTkFont(size=13),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        new_e.pack(side="left", fill="x", expand=True,
                    padx=(0, 6))

        scroll = CTkScrollableFrame(win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=20,
                     pady=(0, 15))

        def render_list() -> None:
            for w in scroll.winfo_children():
                w.destroy()
            items = SubtaskManager.get_for_task(task["id"])
            if not items:
                CTkLabel(scroll, text="Пока нет подзадач",
                         font=CTkFont(size=12),
                         text_color=t.get("text_dim")
                         ).pack(pady=20)
                return
            for st in items:
                row = CTkFrame(scroll, fg_color=t.get("bg3"),
                                 corner_radius=4)
                row.pack(fill="x", pady=2)
                done = bool(st.get("done"))
                prefix = "✅" if done else "⬜"
                CTkButton(
                    row,
                    text=f"{prefix} {st.get('title', '')}",
                    anchor="w", height=32,
                    font=CTkFont(size=12),
                    fg_color="transparent",
                    text_color=(t.get("text_dim") if done
                                 else t.get("text")),
                    command=lambda sid=st["id"]: (
                        SubtaskManager.toggle(
                            sid, self.current_user),
                        render_list())
                ).pack(side="left", fill="x", expand=True,
                        padx=4, pady=4)
                CTkButton(row, text="🗑️", width=32,
                          height=28, font=CTkFont(size=10),
                          fg_color="transparent",
                          text_color=t.get("error"),
                          command=lambda sid=st["id"]: (
                              SubtaskManager.delete(sid),
                              render_list())
                          ).pack(side="right", padx=4)

        def add_st() -> None:
            title = new_e.get().strip()
            if not title:
                return
            SubtaskManager.create(task["id"], title)
            new_e.delete(0, "end")
            render_list()

        CTkButton(add_row, text="➕", width=44, height=36,
                  font=CTkFont(size=14, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=add_st).pack(side="right")
        new_e.bind("<Return>", lambda e: add_st())
        render_list()


    # ────────────────────────────────────────────────
    # STUBS (переопределяются в 4.2b/c)
    # ────────────────────────────────────────────────
    def _setup_hotkeys(self) -> None:
        """Регистрация горячих клавиш."""
        actions: Dict[str, Callable] = {
            "save_all": self._save_all,
            "search": self._toggle_search,
            "qr_studio": lambda: self._show_page("QR"),
            "code_studio": lambda: self._show_page("Code"),
            "command_palette": self._show_command_palette,
            "quick_note": self._show_quick_note,
            "screenshot": self._take_screenshot,
            "panic": self._panic_hide,
            "memory_recall": self._show_memory_recall,
            "refresh": self._refresh_page,
            "dev_mode": self._toggle_dev_mode,
            "passwords": lambda: self._show_page("Passwords"),
            "snippets": lambda: self._show_page("Snippets"),
            "timers": lambda: self._show_page("Timers"),
            "pomodoro": self._show_pomodoro,
            "quests": self._show_quests,
            "close_window": self._on_closing,
        }
        for name, action in actions.items():
            key = self.hotkeys.get(name, "")
            if not key:
                continue
            with suppress(Exception):
                self.bind_all(
                    key,
                    lambda e, a=action: (a(), "break")[1])
        with suppress(Exception):
            self.bind_all(
                "<Control-z>",
                lambda e: (self._do_undo(), "break")[1])
        quick_pages = [
            "Home", "Code", "Tasks", "Forum",
            "QR", "Blockchain", "Stats", "Timers"]
        for i, page in enumerate(quick_pages, start=1):
            with suppress(Exception):
                self.bind_all(
                    f"<Control-Key-{i}>",
                    lambda e, p=page: (
                        self._show_page(p), "break")[1])

    def _save_all(self) -> None:
        if not self.current_user:
            return
        db.backup()
        self._save_code_draft()
        self._show_toast("💾 Сохранено", "success")

    def _reset_code_refs(self) -> None:
        """Сбросить ссылки на виджеты Code Studio.
        Вызывается перед уничтожением страницы."""
        self._code_out = None
        self._code_widget = None
        self._code_status_lbl = None
        self._code_lang_var = None
        self._code_template_var = None
        self._code_tabs_container = None
        self._code_tabs_bar = None

    # ────────────────────────────────────────────────
    # DIALOG: POMODORO
    # ────────────────────────────────────────────────
    def _show_pomodoro(self) -> None:
        if (self._pomodoro_win
                and self._pomodoro_win.winfo_exists()):
            self._pomodoro_win.focus()
            return
        from nexus.domain import (
            POMODORO_LONG_MIN, POMODORO_SHORT_MIN,
            POMODORO_WORK_MIN,
        )
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._pomodoro_win = win
        win.title("🍅 Pomodoro")
        win.geometry("400x500")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 400) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 500) // 2)
        win.geometry(f"+{x}+{y}")

        CTkLabel(win, text="🍅 POMODORO",
                 font=CTkFont(size=24, weight="bold"),
                 text_color=t.get("error")
                 ).pack(pady=(20, 4))
        phase_lbl = CTkLabel(win, text="Готов к работе",
                               font=CTkFont(size=14),
                               text_color=t.get("text_dim"))
        phase_lbl.pack(pady=4)
        time_lbl = CTkLabel(win, text="25:00",
                             font=CTkFont(size=64,
                                           weight="bold"),
                             text_color=t.get("primary"))
        time_lbl.pack(pady=10)
        sessions_lbl = CTkLabel(
            win,
            text=f"Сессий: {pomodoro.sessions_done()}",
            font=CTkFont(size=11),
            text_color=t.get("text_dim"))
        sessions_lbl.pack(pady=2)

        def update_label(phase: str, rem: int) -> None:
            with suppress(Exception):
                if not win.winfo_exists():
                    return
                phase_map = {
                    "work": "🍅 Работа",
                    "short": "☕ Короткий перерыв",
                    "long": "🛋️ Длинный перерыв",
                    "idle": "Готов к работе",
                }
                phase_lbl.configure(
                    text=phase_map.get(phase, phase))
                time_lbl.configure(text=fmt_mmss(rem))
                sessions_lbl.configure(
                    text=f"Сессий: "
                         f"{pomodoro.sessions_done()}")

        win.update_label = update_label

        btn_row = CTkFrame(win, fg_color="transparent")
        btn_row.pack(pady=20)

        def start_work() -> None:
            if pomodoro.start_work(self.current_user):
                update_label("work", POMODORO_WORK_MIN * 60)
            else:
                self._show_toast("Уже запущен", "warning")

        def start_short() -> None:
            if pomodoro.start_break(self.current_user, False):
                update_label("short",
                              POMODORO_SHORT_MIN * 60)

        def start_long() -> None:
            if pomodoro.start_break(self.current_user, True):
                update_label("long",
                              POMODORO_LONG_MIN * 60)

        def stop() -> None:
            pomodoro.stop()
            update_label("idle", 0)
            time_lbl.configure(text="25:00")

        CTkButton(btn_row, text="▶ РАБОТА", width=110,
                  height=38,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("error"),
                  text_color=t.get("bg"),
                  command=start_work).pack(side="left",
                                              padx=3)
        CTkButton(btn_row, text="☕ ПЕРЕРЫВ", width=110,
                  height=38,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=start_short).pack(side="left",
                                               padx=3)
        CTkButton(btn_row, text="🛋️ ДЛИННЫЙ", width=110,
                  height=38,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("accent"),
                  text_color=t.get("bg"),
                  command=start_long).pack(side="left",
                                              padx=3)
        CTkButton(win, text="⏹ СТОП", height=36, width=200,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color="transparent", border_width=1,
                  border_color=t.get("error"),
                  text_color=t.get("error"),
                  command=stop).pack(pady=10)

        if pomodoro.is_active():
            update_label(pomodoro.phase(),
                          pomodoro.remaining())

    # ────────────────────────────────────────────────
    # DIALOG: DAILY QUESTS
    # ────────────────────────────────────────────────
    def _show_quests(self) -> None:
        if (self._quests_win
                and self._quests_win.winfo_exists()):
            self._quests_win.focus()
            return
        from nexus.domain import DAILY_QUESTS_COUNT
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._quests_win = win
        win.title("🎯 Ежедневные квесты")
        win.geometry("560x520")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        CTkLabel(win, text="🎯 ЕЖЕДНЕВНЫЕ КВЕСТЫ",
                 font=CTkFont(size=22, weight="bold"),
                 text_color=t.get("warning")
                 ).pack(pady=(20, 4))
        today = datetime.now().strftime("%d.%m.%Y")
        CTkLabel(win, text=f"Обновление: {today}",
                 font=CTkFont(size=11),
                 text_color=t.get("text_dim")
                 ).pack(pady=(0, 12))
        quests = DailyQuests.get_user_quests(self.current_user)
        done_count = sum(1 for q in quests if q.get("done"))
        CTkLabel(win,
                 text=f"Прогресс: {done_count}/{len(quests)}",
                 font=CTkFont(size=14, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=4)
        scroll = CTkScrollableFrame(win, fg_color=t.get("bg2"))
        scroll.pack(fill="both", expand=True, padx=20,
                     pady=(8, 15))
        for q in quests:
            card = CTkFrame(
                scroll,
                fg_color=(t.get("bg3") if q.get("done")
                           else t.get("card")),
                corner_radius=8, border_width=2,
                border_color=(t.get("success")
                               if q.get("done")
                               else t.get("warning")))
            card.pack(fill="x", pady=4)
            top = CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 4))
            icon = "✅" if q.get("done") else "⬜"
            CTkLabel(top,
                     text=f"{icon} {q.get('name', '')}",
                     font=CTkFont(size=13, weight="bold"),
                     text_color=(t.get("text_dim")
                                  if q.get("done")
                                  else t.get("text")),
                     anchor="w").pack(side="left")
            if q.get("done"):
                CTkLabel(top, text="+20 XP",
                         font=CTkFont(size=11,
                                       weight="bold"),
                         text_color=t.get("success")
                         ).pack(side="right")
            CTkLabel(card, text=q.get("desc", ""),
                     font=CTkFont(size=11),
                     text_color=t.get("text_dim"),
                     anchor="w", wraplength=480,
                     justify="left").pack(anchor="w",
                                            padx=12,
                                            pady=(0, 8))

    # ────────────────────────────────────────────────
    # DIALOG: COMMAND PALETTE
    # ────────────────────────────────────────────────
    def _show_command_palette(self) -> None:
        if (self._cmd_palette
                and self._cmd_palette.winfo_exists()):
            self._cmd_palette.focus()
            return
        from nexus.core import fuzzy_match
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._cmd_palette = win
        win.title("⌘ Command Palette")
        win.geometry("640x520")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 640) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 520) // 3)
        win.geometry(f"+{x}+{y}")
        CTkLabel(win, text="⌘ Command Palette",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(12, 4))
        entry = CTkEntry(win,
                          placeholder_text="Введите команду...",
                          height=42, font=CTkFont(size=14),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(fill="x", padx=20, pady=8)
        entry.focus()
        results_frame = CTkScrollableFrame(
            win, fg_color=t.get("bg2"))
        results_frame.pack(fill="both", expand=True,
                            padx=20, pady=(0, 12))

        # Собираем команды
        commands: List[Tuple[str, str, Callable, str]] = []
        for cat, items in self.MENU_STRUCTURE:
            for icon, page_name in items:
                if page_name in self.pages:
                    commands.append((
                        "Навигация",
                        I18n.t(page_name),
                        (lambda p=page_name:
                            self._show_page(p)),
                        icon))
        commands += [
            ("Действие", "Code Studio",
             lambda: self._show_page("Code"), "💻"),
            ("Действие", "QR-студия",
             lambda: self._show_page("QR"), "📱"),
            ("Действие", "Батч QR",
             lambda: self._stub_dialog(
                 "📦 Батч QR", "в 4.2c-ii"),
             "📦"),
            ("Действие", "История QR",
             lambda: self._stub_dialog(
                 "📚 QR", "в 4.2c-ii"),
             "📚"),
            ("Действие", "Библиотека шаблонов",
             lambda: self._stub_dialog(
                 "📚 Шаблоны", "в 4.2c-ii"),
             "📚"),
            ("Действие", "Что я забыл?",
             self._show_memory_recall, "💭"),
            ("Действие", "Быстрая запись",
             self._show_quick_note, "🧠"),
            ("Действие", "Pomodoro",
             self._show_pomodoro, "🍅"),
            ("Действие", "Квесты",
             self._show_quests, "🎯"),
            ("Действие", "Panic", self._panic_hide, "🙈"),
            ("Действие", "Скриншот",
             self._take_screenshot, "📸"),
            ("Действие", "Поиск",
             self._toggle_search, "🔍"),
            ("Действие", "Выход", self._logout, "🚪"),
        ]
        from nexus.storage import mru
        mru_items = set(mru.get_all())
        state: Dict[str, Any] = {
            "buttons": [], "selected": 0}

        def render(query: str = "") -> None:
            state["buttons"].clear()
            state["selected"] = 0
            for w in results_frame.winfo_children():
                w.destroy()
            q = query.lower().strip()
            scored: List[Tuple[float, Tuple]] = []
            for c in commands:
                ok, score = fuzzy_match(q, f"{c[1]} {c[0]}")
                if ok:
                    if c[1] in mru_items:
                        score += 0.15
                    scored.append((score, c))
            scored.sort(key=lambda x: -x[0])
            for _, (cat, name, action, icon) in scored[:60]:
                def mk(a: Callable = action,
                        n: str = name) -> Callable:
                    def fn() -> None:
                        with suppress(Exception):
                            mru.push(n)
                        with suppress(Exception):
                            win.destroy()
                        self._safe_after(30, a)
                    return fn
                btn = CTkButton(
                    results_frame,
                    text=f"{icon}  {name}   [{cat}]",
                    anchor="w", height=36,
                    font=CTkFont(size=13),
                    fg_color="transparent",
                    hover_color=t.get("bg3"),
                    text_color=t.get("text"),
                    command=mk())
                btn.pack(fill="x", pady=1)
                state["buttons"].append(btn)
            refresh_sel()

        def refresh_sel() -> None:
            btns = state["buttons"]
            sel = state["selected"]
            for i, btn in enumerate(btns):
                with suppress(Exception):
                    btn.configure(
                        fg_color=(t.get("bg3") if i == sel
                                   else "transparent"))
            if btns and 0 <= sel < len(btns):
                with suppress(Exception):
                    btns[sel].focus()

        def on_key(event: Any) -> Optional[str]:
            btns = state["buttons"]
            if not btns:
                return None
            if event.keysym == "Down":
                state["selected"] = min(
                    len(btns) - 1, state["selected"] + 1)
                refresh_sel()
                return "break"
            if event.keysym == "Up":
                state["selected"] = max(
                    0, state["selected"] - 1)
                refresh_sel()
                return "break"
            return None

        def on_enter(e: Any = None) -> None:
            btns = state["buttons"]
            sel = state["selected"]
            if btns and 0 <= sel < len(btns):
                btns[sel].invoke()

        entry.bind("<KeyRelease>",
                    lambda e: render(entry.get()))
        entry.bind("<Down>", on_key)
        entry.bind("<Up>", on_key)
        entry.bind("<Return>", on_enter)
        win.bind("<Escape>", lambda e: win.destroy())
        render()

    # ────────────────────────────────────────────────
    # DIALOG: QUICK NOTE
    # ────────────────────────────────────────────────
    def _show_quick_note(self) -> None:
        if (self._quick_note_win
                and self._quick_note_win.winfo_exists()):
            self._quick_note_win.focus()
            return
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._quick_note_win = win
        win.title("🧠 Быстрая запись")
        win.geometry("500x440")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 500) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 440) // 3)
        win.geometry(f"+{x}+{y}")
        CTkLabel(win, text="🧠 БЫСТРАЯ ЗАПИСЬ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=t.get("primary"),
                 anchor="w").pack(anchor="w", padx=20,
                                    pady=(15, 4))
        draft = DraftManager.load("quick_note",
                                    self.current_user) or {}
        title_e = CTkEntry(
            win, placeholder_text="📌 Заголовок (#теги)",
            height=UI_FORM_ENTRY_HEIGHT,
            font=CTkFont(size=14),
            fg_color=t.get("bg3"),
            border_color=t.get("card_border"),
            text_color=t.get("text"))
        title_e.pack(fill="x", padx=20, pady=6)
        if draft.get("title"):
            title_e.insert(0, draft["title"])
        content_e = CTkTextbox(
            win, height=150, font=CTkFont(size=14),
            fg_color=t.get("bg3"),
            border_color=t.get("card_border"),
            text_color=t.get("text"), wrap="word")
        content_e.pack(fill="both", expand=True, padx=20,
                        pady=6)
        if draft.get("content"):
            content_e.insert("1.0", draft["content"])
        title_e.focus()

        def save_draft(*_a: Any) -> None:
            if self.config_data.autosave_drafts:
                DraftManager.save(
                    "quick_note", self.current_user, {
                        "title": title_e.get(),
                        "content": content_e.get(
                            "1.0", "end-1c")})

        title_e.bind("<KeyRelease>", save_draft)
        content_e.bind("<KeyRelease>", save_draft)

        def save() -> None:
            ttl = title_e.get().strip()
            c = content_e.get("1.0", "end-1c").strip()
            if not ttl:
                self._show_toast("Введите заголовок",
                                  "warning")
                return
            if not c:
                c = ttl
            if NoteManager.create(ttl, c, self.current_user,
                                    extract_tags(ttl)):
                self._show_toast("🧠 Запомнено! +10 XP",
                                  "success")
                DraftManager.clear("quick_note",
                                     self.current_user)
                win.destroy()

        CTkButton(win, text="💾 ЗАПОМНИТЬ", height=40,
                  width=200,
                  fg_color=t.get("success"),
                  text_color=t.get("bg"),
                  command=save).pack(pady=12)
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-Return>", lambda e: save())

    # ────────────────────────────────────────────────
    # DIALOG: MEMORY RECALL
    # ────────────────────────────────────────────────
    def _show_memory_recall(self) -> None:
        if (self._recall_win
                and self._recall_win.winfo_exists()):
            self._recall_win.focus()
            return
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._recall_win = win
        win.title("💭 Что я забыл?")
        win.geometry("760x640")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 760) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 640) // 2)
        win.geometry(f"+{x}+{y}")
        CTkLabel(win, text="💭 ЧТО Я ЗАБЫЛ?",
                 font=CTkFont(size=22, weight="bold"),
                 text_color=t.get("accent")
                 ).pack(pady=(15, 4))
        search_e = CTkEntry(
            win, placeholder_text="🔍 Ключевые слова...",
            height=40, font=CTkFont(size=13),
            fg_color=t.get("bg3"),
            border_color=t.get("card_border"),
            text_color=t.get("text"))
        search_e.pack(fill="x", padx=20, pady=(12, 6))
        fb = CTkFrame(win, fg_color="transparent")
        fb.pack(fill="x", padx=20, pady=(0, 6))
        scroll = CTkScrollableFrame(
            win, fg_color=t.get("bg2"),
            corner_radius=8, border_width=2,
            border_color=t.get("card_border"))
        scroll.pack(fill="both", expand=True, padx=20,
                     pady=(0, 15))

        def render_items(items: List[Dict[str, Any]],
                          empty_text: str = "Ничего 😌"
                          ) -> None:
            for w in scroll.winfo_children():
                w.destroy()
            if not items:
                CTkLabel(scroll, text=empty_text,
                         font=CTkFont(size=13),
                         text_color=t.get("text_dim")
                         ).pack(pady=30)
                return
            for n in items:
                card = CTkFrame(scroll, fg_color=t.get("bg3"),
                                 corner_radius=6)
                card.pack(fill="x", pady=3)
                top = CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=10, pady=(6, 2))
                CTkLabel(
                    top,
                    text=f"🧠 {truncate(n.get('title', ''), 50)}",
                    font=CTkFont(size=13, weight="bold"),
                    text_color=t.get("primary"),
                    anchor="w").pack(side="left", fill="x",
                                        expand=True)
                age = n.get("age_days")
                if age is not None:
                    CTkLabel(top, text=f"{age}д",
                             font=CTkFont(size=10),
                             text_color=t.get("text_dim"),
                             width=50).pack(side="right",
                                              padx=4)
                body = (n.get("content") or "")[:200]
                if body:
                    CTkLabel(card, text=body,
                             font=CTkFont(size=11),
                             text_color=t.get("text"),
                             anchor="w", justify="left",
                             wraplength=UI_WRAP_MED
                             ).pack(anchor="w", fill="x",
                                    padx=10, pady=(0, 4))
                br = CTkFrame(card, fg_color="transparent")
                br.pack(fill="x", padx=10, pady=(2, 6))
                CTkButton(br, text="📖 Отметить",
                          width=100, height=24,
                          font=CTkFont(size=10),
                          fg_color="transparent",
                          border_width=1,
                          border_color=t.get("primary"),
                          text_color=t.get("primary"),
                          command=lambda nid=n["id"]: (
                              NoteManager.mark_viewed(nid),
                              self._show_toast(
                                  "📖 Просмотрено",
                                  "info", 1500),
                              win.destroy())
                          ).pack(side="left", padx=2)
                CTkButton(br, text="💫 Оживить",
                          width=100, height=24,
                          font=CTkFont(size=10),
                          fg_color=t.get("warning"),
                          text_color=t.get("bg"),
                          command=lambda nid=n["id"]: (
                              NoteManager.resurrect(
                                  nid, self.current_user),
                              self._show_toast(
                                  "💫 Оживлено +25 XP",
                                  "success"),
                              win.destroy())
                          ).pack(side="left", padx=2)

        def show_forgotten() -> None:
            items = NoteManager.recall(
                self.current_user, limit=30)
            render_items(
                items, "Память пуста — ты всё помнишь 😌")

        def show_search() -> None:
            q = search_e.get().strip()
            if not q:
                self._show_toast("Введите слова", "warning")
                return
            items = NoteManager.search(
                self.current_user, q, limit=30)
            render_items(items, f"Ничего по «{q}» 😕")

        CTkButton(fb, text="🌱 Забытое", width=140,
                  height=30,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("warning"),
                  text_color=t.get("bg"),
                  command=show_forgotten).pack(side="left",
                                                  padx=2)
        CTkButton(fb, text="🔍 Найти по теме", width=160,
                  height=30,
                  font=CTkFont(size=11, weight="bold"),
                  fg_color=t.get("accent"),
                  text_color=t.get("bg"),
                  command=show_search).pack(side="left",
                                               padx=2)
        search_e.bind("<Return>", lambda e: show_search())
        with suppress(Exception):
            level_system.add_xp(self.current_user,
                                  "recall_used")
            achievement_system.check(self.current_user,
                                      "memory_recall")
        show_forgotten()

    # ────────────────────────────────────────────────
    # DIALOG: SEARCH
    # ────────────────────────────────────────────────
    def _toggle_search(self) -> None:
        if (self._search_window
                and self._search_window.winfo_exists()):
            with suppress(Exception):
                self._search_window.destroy()
            self._search_window = None
            return
        from nexus.core import fuzzy_match
        t = theme_manager.get_all()
        win = CTkToplevel(self)
        self._search_window = win
        win.title("🔍 Поиск")
        win.geometry("700x600")
        win.configure(fg_color=t.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 700) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 600) // 2)
        win.geometry(f"+{x}+{y}")
        CTkLabel(win, text="🔍 ПОИСК ПО ВСЕМУ",
                 font=CTkFont(size=20, weight="bold"),
                 text_color=t.get("primary")
                 ).pack(pady=(15, 4))
        entry = CTkEntry(win, placeholder_text="Запрос...",
                          height=42, font=CTkFont(size=14),
                          fg_color=t.get("bg3"),
                          border_color=t.get("card_border"),
                          text_color=t.get("text"))
        entry.pack(fill="x", padx=20, pady=8)
        entry.focus()
        results = CTkScrollableFrame(
            win, fg_color=t.get("bg2"),
            corner_radius=8, border_width=2,
            border_color=t.get("card_border"))
        results.pack(fill="both", expand=True, padx=20,
                      pady=8)

        def do_search() -> None:
            q = entry.get().strip().lower()
            for w in results.winfo_children():
                w.destroy()
            if not q:
                return
            found: List[Tuple[float, str, str, str]] = []
            for task in TaskManager.get_all():
                ok, sc = fuzzy_match(
                    q, task.get("title", "") + " "
                    + task.get("description", ""))
                if ok:
                    found.append((sc, "✅",
                                    truncate(task["title"], 50),
                                    "Tasks"))
            for snip in snippet_manager.get_all():
                ok, sc = fuzzy_match(
                    q, snip.get("name", "") + " "
                    + snip.get("code", ""))
                if ok:
                    found.append((sc, "✂️",
                                    truncate(snip["name"], 50),
                                    "Snippets"))
            for h in CodeHistory.search(
                    self.current_user, q, limit=20):
                found.append((0.5, "💻",
                                truncate(h.get("code", ""), 50),
                                "Code"))
            for n in NoteManager.search(
                    self.current_user, q, limit=20):
                found.append((0.6, "🧠",
                                truncate(n.get("title", ""), 50),
                                "Home"))
            for b in BlockchainManager.get_all():
                ok, sc = fuzzy_match(q, b.get("data", ""))
                if ok:
                    found.append((sc, "⛓️",
                                    truncate(
                                        b.get("data", ""), 50),
                                    "Blockchain"))
            found.sort(key=lambda x: -x[0])
            if not found:
                CTkLabel(results, text="😕 Ничего",
                         font=CTkFont(size=14),
                         text_color=t.get("text_dim")
                         ).pack(pady=20)
                return
            for _, icon, title, page in found[:60]:
                item = CTkFrame(results, corner_radius=6,
                                 fg_color=t.get("bg3"))
                item.pack(fill="x", pady=2)
                CTkLabel(item, text=f"{icon} {title}",
                         font=CTkFont(size=12),
                         text_color=t.get("text"),
                         anchor="w").pack(side="left",
                                            padx=10, pady=6)
                CTkButton(item, text="📂", width=30,
                          height=24,
                          fg_color=t.get("primary"),
                          text_color=t.get("bg"),
                          command=lambda p=page: (
                              self._show_page(p),
                              win.destroy())
                          ).pack(side="right", padx=6,
                                 pady=6)

        def debounced(e: Any = None) -> None:
            self._timer_pool.schedule(
                f"search_{id(win)}",
                UI_SEARCH_DEBOUNCE_MS, do_search)

        entry.bind("<KeyRelease>", debounced)
        entry.bind("<Return>", lambda e: do_search())

    def _open_file(self, path: Any) -> None:
        """Открыть файл/папку системным приложением."""
        try:
            from pathlib import Path as _P
            p = _P(str(path))
            if not p.exists():
                self._show_toast(
                    f"❌ Файл не найден: {p.name}", "error")
                return
            import subprocess as _sp
            import sys as _sys
            if _sys.platform == "win32":
                os.startfile(str(p))
            elif _sys.platform == "darwin":
                _sp.Popen(["open", str(p)])
            else:
                _sp.Popen(["xdg-open", str(p)])
        except Exception as e:
            self._show_toast(f"❌ Не удалось открыть: {e}",
                              "error")

    def _open_debugger(self) -> None:
        """Открыть отладочную панель (если debugger attached)."""
        with suppress(Exception):
            if hasattr(self, "debugger_open"):
                self.debugger_open()
                return
        self._show_toast(
            "🐛 Отладчик не загружен (модуль не установлен)",
            "warning", 3000)

    # ────────────────────────────────────────────────
    # ACTION: SCREENSHOT
    # ────────────────────────────────────────────────

    def _toggle_dev_mode(self) -> None:
        self.config_data.dev_mode = \
            not self.config_data.dev_mode
        self.config_data.save()
        self._show_toast(
            f"🔧 Dev "
            f"{'ВКЛ' if self.config_data.dev_mode else 'ВЫКЛ'}",
            "info", 2000)

    def _take_screenshot(self) -> None:
        try:
            from PIL import ImageGrab
            from nexus.core import EXPORT_DIR
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = EXPORT_DIR / f"nexus_screenshot_{ts}.png"
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            x, y = self.winfo_x(), self.winfo_y()
            w, h = self.winfo_width(), self.winfo_height()
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            img.save(out)
            self._show_toast(f"📸 {out.name}", "success")
            with suppress(Exception):
                achievement_system.check(
                    self.current_user, "photographer")
        except Exception as e:
            self._show_toast(f"❌ Скриншот: {e}", "error")

    def _panic_hide(self) -> None:
        try:
            self.withdraw()
            self._safe_after(5000, self._panic_show)
        except Exception:
            pass

    def _panic_show(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _do_undo(self) -> None:
        item = undo_stack.pop_valid()
        if not item:
            self._show_toast("Нечего отменять", "info", 1500)
            return
        try:
            item["fn"]()
            self._show_toast("↶ Отменено", "success", 2000)
        except Exception as e:
            logger.error(f"undo: {e}")

    def _stub_dialog(self, title: str, msg: str) -> None:
        """Временный диалог-заглушка."""
        win = CTkToplevel(self)
        win.title(title)
        win.geometry("400x220")
        win.configure(fg_color=theme_manager.get("bg"))
        win.transient(self)
        win.grab_set()
        win.bind("<Escape>", lambda e: win.destroy())
        self.update_idletasks()
        x = self.winfo_x() + max(
            0, (self.winfo_width() - 400) // 2)
        y = self.winfo_y() + max(
            0, (self.winfo_height() - 220) // 2)
        win.geometry(f"+{x}+{y}")

        CTkLabel(win, text=title,
                 font=CTkFont(size=20, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack(pady=(30, 8))
        CTkLabel(win, text="🚧",
                 font=CTkFont(size=40)
                 ).pack()
        CTkLabel(win, text=msg,
                 font=CTkFont(size=12),
                 text_color=theme_manager.get("text_dim")
                 ).pack(pady=(8, 16))
        CTkButton(win, text="OK", width=120, height=36,
                  fg_color=theme_manager.get("primary"),
                  text_color=theme_manager.get("bg"),
                  command=win.destroy).pack()

    def _show_auth(self) -> None:
        """Полная версия — ниже в этом файле."""
        self._show_auth_full()

    # ────────────────────────────────────────────────
    # AUTH WINDOW (полная версия)
    # ────────────────────────────────────────────────
    def _show_auth_full(self) -> None:
        if self._auth_win and self._auth_win.winfo_exists():
            self._auth_win.focus()
            return

        win = CTkToplevel(self)
        self._auth_win = win
        win.title(f"{APP_ICON} АВТОРИЗАЦИЯ v{VERSION}")
        win.geometry("520x900")
        win.configure(fg_color=theme_manager.get("bg"))
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        x = self.winfo_screenwidth() // 2 - 260
        y = self.winfo_screenheight() // 2 - 450
        win.geometry(f"+{x}+{y}")

        def close_auth() -> None:
            with suppress(Exception):
                win.destroy()
            self._auth_win = None
            with suppress(Exception):
                self.destroy()
        win.protocol("WM_DELETE_WINDOW", close_auth)

        # Logo
        logo = CTkFrame(win, fg_color="transparent")
        logo.pack(pady=(18, 8))
        lb = CTkFrame(logo, width=72, height=72,
                       fg_color="transparent",
                       border_width=3,
                       border_color=theme_manager.get("primary"),
                       corner_radius=36)
        lb.pack(pady=(0, 5))
        lb.pack_propagate(False)
        CTkLabel(lb, text=APP_ICON, font=CTkFont(size=38),
                 text_color=theme_manager.get("primary")
                 ).pack(expand=True)
        CTkLabel(logo, text=APP_NAME,
                 font=CTkFont(size=30, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack()
        CTkLabel(logo, text=f"{VERSION_NAME} v{VERSION}",
                 font=CTkFont(size=13),
                 text_color=theme_manager.get("secondary")
                 ).pack()
        CTkLabel(logo, text=f'"{quote("qr")}"',
                 font=CTkFont(size=11, slant="italic"),
                 text_color=theme_manager.get("text_dim"),
                 wraplength=420,
                 justify="center").pack(pady=(4, 0))

        # Toast
        self.auth_toast = CTkFrame(
            win, fg_color="transparent", height=40)
        self.auth_toast.pack(fill="x", padx=40, pady=(0, 4))

        # Card
        card = CTkFrame(
            win, corner_radius=14, border_width=2,
            border_color=theme_manager.get("card_border"),
            fg_color=theme_manager.get("bg2"))
        card.pack(pady=5, padx=40, fill="both", expand=True)

        # Tabs
        tabs = CTkFrame(card, fg_color="transparent")
        tabs.pack(pady=(12, 8))
        self.login_btn = CTkButton(
            tabs, text="ВХОД", width=110, height=38,
            font=CTkFont(size=15, weight="bold"),
            fg_color=theme_manager.get("primary"),
            text_color=theme_manager.get("bg"),
            command=lambda: self._switch_auth("login"))
        self.login_btn.pack(side="left", padx=5)
        self.register_btn = CTkButton(
            tabs, text="РЕГИСТРАЦИЯ", width=140, height=38,
            font=CTkFont(size=15, weight="bold"),
            fg_color="transparent", border_width=2,
            border_color=theme_manager.get("text_dim"),
            text_color=theme_manager.get("text"),
            command=lambda: self._switch_auth("register"))
        self.register_btn.pack(side="left", padx=5)

        self.auth_container = CTkFrame(card,
                                         fg_color="transparent")
        self.auth_container.pack(fill="both", expand=True,
                                    padx=15, pady=8)

        self._create_login_form()
        self._create_register_form()
        self._create_forgot_form()
        self.login_form.pack(fill="both", expand=True)

    def _create_login_form(self) -> None:
        self.login_form = CTkFrame(self.auth_container,
                                     fg_color="transparent")
        CTkLabel(self.login_form, text="◈ ВХОД",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack(pady=(0, 12))

        self.login_user = CTkEntry(
            self.login_form,
            placeholder_text="👤 ИМЯ ИЛИ EMAIL",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"))
        self.login_user.pack(fill="x", pady=4)

        pwd_row = CTkFrame(self.login_form,
                             fg_color="transparent")
        pwd_row.pack(fill="x", pady=4)
        self.login_pass = CTkEntry(
            pwd_row, placeholder_text="🔑 ПАРОЛЬ",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"), show="•")
        self.login_pass.pack(side="left", fill="x",
                               expand=True, padx=(0, 4))

        self._login_pwd_visible = ctk.BooleanVar(value=False)

        def toggle_pwd() -> None:
            self.login_pass.configure(
                show="" if self._login_pwd_visible.get()
                else "•")

        CTkButton(pwd_row, text="👁", width=40, height=38,
                  font=CTkFont(size=14),
                  fg_color=theme_manager.get("bg3"),
                  text_color=theme_manager.get("text"),
                  command=lambda: (
                      self._login_pwd_visible.set(
                          not self._login_pwd_visible.get()),
                      toggle_pwd())
                  ).pack(side="right")
        self.login_pass.bind("<Return>",
                              lambda e: self._do_login())

        self.remember_var = ctk.BooleanVar(
            value=bool(self.config_data.remember_me))
        remember_row = CTkFrame(self.login_form,
                                  fg_color="transparent")
        remember_row.pack(fill="x", pady=(8, 4))
        CTkCheckBox(
            remember_row,
            text="💾 Запомнить меня",
            variable=self.remember_var,
            font=CTkFont(size=12),
            text_color=theme_manager.get("text"),
            fg_color=theme_manager.get("primary"),
            hover_color=theme_manager.get("primary"),
            border_color=theme_manager.get("card_border")
        ).pack(side="left")
        CTkLabel(remember_row,
                 text="(сессия 90 дней · machine-bound)",
                 font=CTkFont(size=10),
                 text_color=theme_manager.get("text_dim")
                 ).pack(side="left", padx=(6, 0))

        CTkButton(self.login_form, text="▸ ВОЙТИ",
                  height=46,
                  font=CTkFont(size=16, weight="bold"),
                  fg_color=theme_manager.get("success"),
                  text_color=theme_manager.get("bg"),
                  command=self._do_login
                  ).pack(pady=(12, 4))

        CTkButton(
            self.login_form,
            text="🔑 Забыли пароль?",
            height=30, font=CTkFont(size=12),
            fg_color="transparent",
            hover_color=theme_manager.get("bg3"),
            text_color=theme_manager.get("warning"),
            command=lambda: self._switch_auth("forgot")
        ).pack(pady=(4, 0))

        CTkLabel(self.login_form,
                 text="🔒 AES-256 + Argon2id · "
                      "Rolling-сессия · machine-bound",
                 font=CTkFont(size=9),
                 text_color=theme_manager.get("text_dim")
                 ).pack(pady=(8, 0))

    def _create_register_form(self) -> None:
        self.register_form = CTkFrame(self.auth_container,
                                        fg_color="transparent")
        scroll = CTkScrollableFrame(
            self.register_form, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        CTkLabel(scroll, text="◈ РЕГИСТРАЦИЯ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme_manager.get("primary")
                 ).pack(pady=(0, 12))

        CTkLabel(scroll, text="👤 Имя пользователя",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.reg_user = CTkEntry(
            scroll, placeholder_text="мин. 3 символа",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"))
        self.reg_user.pack(fill="x", pady=(0, 8))

        CTkLabel(scroll, text="📧 Email",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.reg_email = CTkEntry(
            scroll, placeholder_text="you@example.com",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"))
        self.reg_email.pack(fill="x", pady=(0, 8))

        CTkLabel(scroll, text="🔑 Пароль",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        reg_pwd_row = CTkFrame(scroll, fg_color="transparent")
        reg_pwd_row.pack(fill="x", pady=(0, 4))
        self.reg_pass = CTkEntry(
            reg_pwd_row, placeholder_text="мин. 8",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"), show="•")
        self.reg_pass.pack(side="left", fill="x", expand=True,
                             padx=(0, 4))

        self._reg_pwd_visible = ctk.BooleanVar(value=False)
        CTkButton(reg_pwd_row, text="👁", width=40, height=38,
                  font=CTkFont(size=14),
                  fg_color=theme_manager.get("bg3"),
                  text_color=theme_manager.get("text"),
                  command=lambda: (
                      self._reg_pwd_visible.set(
                          not self._reg_pwd_visible.get()),
                      self.reg_pass.configure(
                          show=""
                          if self._reg_pwd_visible.get()
                          else "•"))
                  ).pack(side="right")

        self.strength_lbl = CTkLabel(
            scroll,
            text="Пароль: 8+, A-Z, a-z, 0-9, спецсимвол",
            font=CTkFont(size=10),
            text_color=theme_manager.get("text_dim"),
            anchor="w", justify="left")
        self.strength_lbl.pack(anchor="w", fill="x",
                                 pady=(0, 4))

        self.strength_bar = CTkProgressBar(
            scroll, height=6, corner_radius=3,
            fg_color=theme_manager.get("bg3"),
            progress_color=theme_manager.get("error"))
        self.strength_bar.pack(fill="x", pady=(0, 8))
        self.strength_bar.set(0)
        self.reg_pass.bind(
            "<KeyRelease>",
            lambda e: self._update_strength())

        CTkLabel(scroll, text="✅ Подтверждение пароля",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.reg_confirm = CTkEntry(
            scroll, placeholder_text="повторите пароль",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"), show="•")
        self.reg_confirm.pack(fill="x", pady=(0, 8))

        self.agree_var = ctk.BooleanVar(value=False)
        agree_card = CTkFrame(
            scroll, fg_color=theme_manager.get("bg3"),
            corner_radius=8, border_width=1,
            border_color=theme_manager.get("card_border"))
        agree_card.pack(fill="x", pady=(6, 4))
        agree_row = CTkFrame(agree_card,
                               fg_color="transparent")
        agree_row.pack(fill="x", padx=10, pady=(10, 6))
        CTkCheckBox(
            agree_row,
            text="✅ Я согласен с условиями",
            variable=self.agree_var,
            font=CTkFont(size=12, weight="bold"),
            text_color=theme_manager.get("text"),
            fg_color=theme_manager.get("success"),
            hover_color=theme_manager.get("success"),
            border_color=theme_manager.get("card_border"),
            command=self._check_agree
        ).pack(anchor="w")

        CTkLabel(
            agree_card,
            text=(
                "• NEXUS хранит данные локально "
                "(nexus_data/)\n"
                "• Пароль в виде Argon2id-хеша с pepper\n"
                "• Recovery-фраза (12 слов) + Shamir 2/3\n"
                "• При утере фразы и пароля данные "
                "восстановить невозможно\n"
                "• Никакие данные не отправляются в "
                "интернет без согласия"),
            font=CTkFont(size=10),
            text_color=theme_manager.get("text_dim"),
            anchor="w", justify="left", wraplength=380
        ).pack(anchor="w", fill="x", padx=10, pady=(0, 10))

        self.register_submit_btn = CTkButton(
            scroll, text="▸ ЗАРЕГИСТРИРОВАТЬСЯ",
            height=46,
            font=CTkFont(size=16, weight="bold"),
            fg_color=theme_manager.get("text_dim"),
            text_color=theme_manager.get("bg"),
            state="disabled",
            command=self._do_register)
        self.register_submit_btn.pack(fill="x",
                                        pady=(12, 4))
        CTkLabel(scroll,
                 text="🛡️ Argon2id + pepper + HIBP + Shamir",
                 font=CTkFont(size=9),
                 text_color=theme_manager.get("text_dim")
                 ).pack(pady=(6, 20))

    def _check_agree(self) -> None:
        try:
            if self.agree_var.get():
                self.register_submit_btn.configure(
                    state="normal",
                    fg_color=theme_manager.get("secondary"))
            else:
                self.register_submit_btn.configure(
                    state="disabled",
                    fg_color=theme_manager.get("text_dim"))
        except Exception:
            pass

    def _update_strength(self) -> None:
        try:
            from nexus.core import password_strength
            p = self.reg_pass.get()
            score, label = password_strength(p)
            if score < 30:
                color = theme_manager.get("error")
            elif score < 50:
                color = theme_manager.get("warning")
            elif score < 70:
                color = theme_manager.get("accent")
            elif score < 85:
                color = theme_manager.get("primary")
            else:
                color = theme_manager.get("success")
            icons = {0: "😰", 1: "😟", 2: "😐",
                     3: "🙂", 4: "💪", 5: "🔥"}
            idx = min(5, score // 20)
            self.strength_lbl.configure(
                text=f"{icons[idx]} {score}/100 — {label}",
                text_color=color)
            self.strength_bar.configure(progress_color=color)
            self.strength_bar.set(score / 100.0)
        except Exception:
            pass

    def _create_forgot_form(self) -> None:
        self.forgot_form = CTkFrame(self.auth_container,
                                      fg_color="transparent")
        CTkLabel(self.forgot_form,
                 text="🔑 ВОССТАНОВЛЕНИЕ",
                 font=CTkFont(size=18, weight="bold"),
                 text_color=theme_manager.get("warning")
                 ).pack(pady=(0, 8))
        CTkLabel(self.forgot_form,
                 text="Введите имя пользователя и\n"
                      "recovery-фразу (12 слов)\n"
                      "ИЛИ 2 из 3 Shamir-частей",
                 font=CTkFont(size=11),
                 text_color=theme_manager.get("text_dim"),
                 justify="center").pack(pady=(0, 12))

        CTkLabel(self.forgot_form, text="👤 Имя",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.forgot_user = CTkEntry(
            self.forgot_form,
            placeholder_text="ваш username",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"))
        self.forgot_user.pack(fill="x", pady=(0, 8))

        CTkLabel(self.forgot_form,
                 text="🗝️ Recovery-фраза (12 слов)",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.forgot_phrase = CTkTextbox(
            self.forgot_form, height=80,
            font=CTkFont(size=12),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"),
            wrap="word")
        self.forgot_phrase.pack(fill="x", pady=(0, 4))
        CTkLabel(self.forgot_form,
                 text="Слова через пробел. Или 2 строки "
                      "вида `1:base64` / `2:base64`",
                 font=CTkFont(size=10),
                 text_color=theme_manager.get("text_dim"),
                 anchor="w").pack(anchor="w", pady=(0, 8))

        CTkLabel(self.forgot_form, text="🔑 Новый пароль",
                 font=CTkFont(size=11, weight="bold"),
                 text_color=theme_manager.get("text_glow"),
                 anchor="w").pack(anchor="w", fill="x",
                                    pady=(4, 2))
        self.forgot_new_pass = CTkEntry(
            self.forgot_form,
            placeholder_text="новый пароль",
            height=38, font=CTkFont(size=14),
            fg_color=theme_manager.get("bg3"),
            border_color=theme_manager.get("card_border"),
            text_color=theme_manager.get("text"), show="•")
        self.forgot_new_pass.pack(fill="x", pady=(0, 8))

        CTkButton(
            self.forgot_form,
            text="🔄 ВОССТАНОВИТЬ ПАРОЛЬ",
            height=42,
            font=CTkFont(size=14, weight="bold"),
            fg_color=theme_manager.get("warning"),
            text_color=theme_manager.get("bg"),
            command=self._handle_forgot_password
        ).pack(fill="x", pady=(8, 4))

        CTkButton(
            self.forgot_form,
            text="← Назад к входу",
            height=30, font=CTkFont(size=11),
            fg_color="transparent",
            text_color=theme_manager.get("text_dim"),
            command=lambda: self._switch_auth("login")
        ).pack(pady=(4, 0))

        CTkLabel(
            self.forgot_form,
            text="⚠️ Без recovery-фразы восстановление "
                 "невозможно",
            font=CTkFont(size=9),
            text_color=theme_manager.get("error"),
            wraplength=380
        ).pack(pady=(12, 0))

    def _handle_forgot_password(self) -> None:
        import re
        from nexus.crypto import (
            validate_recovery_words,
            reconstruct_recovery_phrase,
        )
        from nexus.core import SHAMIR_THRESHOLD
        username = self.forgot_user.get().strip()
        phrase_text = self.forgot_phrase.get(
            "1.0", "end-1c").strip()
        new_pass = self.forgot_new_pass.get().strip()
        if not username or not phrase_text or not new_pass:
            self._show_auth_toast("Заполните все поля",
                                    "warning")
            return

        words: Optional[List[str]] = None
        if ":" in phrase_text and "base64" not in phrase_text:
            shards = [line.strip() for line in
                      phrase_text.splitlines()
                      if ":" in line]
            if len(shards) >= SHAMIR_THRESHOLD:
                reconstructed = reconstruct_recovery_phrase(
                    shards)
                if reconstructed:
                    words = reconstructed
                    with suppress(Exception):
                        achievement_system.check(
                            username, "shamir_keeper")
        if words is None:
            words = [w.lower().strip() for w in
                     re.split(r"[\s,]+", phrase_text)
                     if w.strip()]

        ok, msg = validate_recovery_words(words)
        if not ok:
            self._show_auth_toast(f"⚠️ {msg}", "warning")
            return

        success, message = UserManager.reset_with_recovery(
            username, words, new_pass)
        if success:
            self._show_auth_toast(
                "✅ Пароль восстановлен! Войдите с новым "
                "паролем", "success")
            self._safe_after(
                1500, lambda: self._switch_auth("login"))
        else:
            self._show_auth_toast(f"❌ {message}", "error")

    def _switch_auth(self, tab: str) -> None:
        self.login_btn.configure(
            fg_color="transparent", border_width=2,
            border_color=theme_manager.get("text_dim"),
            text_color=theme_manager.get("text"))
        self.register_btn.configure(
            fg_color="transparent", border_width=2,
            border_color=theme_manager.get("text_dim"),
            text_color=theme_manager.get("text"))
        for form in (self.login_form, self.register_form,
                      self.forgot_form):
            with suppress(Exception):
                form.pack_forget()

        if tab == "login":
            self.login_btn.configure(
                fg_color=theme_manager.get("primary"),
                text_color=theme_manager.get("bg"))
            self.login_form.pack(fill="both", expand=True)
            with suppress(Exception):
                self.login_user.focus()
        elif tab == "register":
            self.register_btn.configure(
                fg_color=theme_manager.get("primary"),
                text_color=theme_manager.get("bg"))
            self.register_form.pack(fill="both", expand=True)
            with suppress(Exception):
                self.reg_user.focus()
        elif tab == "forgot":
            self.register_btn.configure(
                fg_color=theme_manager.get("warning"),
                text_color=theme_manager.get("bg"))
            self.forgot_form.pack(fill="both", expand=True)
            with suppress(Exception):
                self.forgot_user.focus()

        with suppress(Exception):
            for w in self.auth_toast.winfo_children():
                w.destroy()

    def _show_auth_toast(self, msg: str,
                          typ: str = "info") -> None:
        with suppress(Exception):
            for w in self.auth_toast.winfo_children():
                w.destroy()
        colors = {
            "info": (theme_manager.get("primary"),
                     theme_manager.get("bg2")),
            "success": (theme_manager.get("success"),
                         "#003322"),
            "warning": (theme_manager.get("warning"),
                         "#332200"),
            "error": (theme_manager.get("error"),
                       "#440022"),
        }
        icons = {"info": "ℹ️", "success": "✅",
                 "warning": "⚠️", "error": "❌"}
        color, bg = colors.get(typ, colors["info"])
        with suppress(Exception):
            toast = CTkFrame(self.auth_toast, fg_color=bg,
                              corner_radius=10,
                              border_width=2,
                              border_color=color, height=35)
            toast.pack(fill="x", pady=(0, 4))
            CTkLabel(toast,
                     text=f"{icons.get(typ, '📌')} {msg}",
                     font=CTkFont(size=13, weight="bold"),
                     text_color=color, wraplength=380,
                     justify="left", anchor="w"
                     ).pack(padx=12, pady=6, fill="x")
            self._safe_after(
                3500,
                lambda: toast.destroy()
                if toast.winfo_exists() else None)

    def _do_login(self) -> None:
        login = self.login_user.get().strip()
        password = self.login_pass.get().strip()
        if not login or not password:
            self._show_auth_toast("Заполните поля", "warning")
            return
        success, message, username = UserManager.verify(
            login, password)
        if not success or not username:
            self._show_auth_toast(f"{message}", "error")
            return
        remember = bool(self.remember_var.get())
        self.current_user = username
        self.is_auth = True
        self.config_data.remember_me = remember
        self.config_data.last_user = (username if remember
                                        else "")
        self.config_data.session_auto_login = remember
        if remember:
            SessionManager.save(username, force=True)
        else:
            SessionManager.clear(specific_user=username)
        self.config_data.save()
        level_system.update_streak(username)
        level_system.add_xp(username, "daily_login")
        self._show_auth_toast(
            f"✅ Добро пожаловать, {username}!", "success")
        self._safe_after(800, self._close_auth_and_init)

    def _do_register(self) -> None:
        if not self.agree_var.get():
            self._show_auth_toast(
                "❌ Необходимо согласие с условиями",
                "error")
            return
        u = self.reg_user.get().strip()
        e = self.reg_email.get().strip()
        p = self.reg_pass.get().strip()
        c = self.reg_confirm.get().strip()
        if p != c:
            self._show_auth_toast("Пароли не совпадают",
                                    "error")
            return
        success, message = UserManager.create(u, e, p)
        if not success:
            self._show_auth_toast(f"❌ {message}", "error")
            return
        self._show_auth_toast(f"✅ {message}", "success")
        self.current_user = u
        self.is_auth = True
        self.config_data.remember_me = True
        self.config_data.last_user = u
        self.config_data.session_auto_login = True
        SessionManager.save(u, force=True)
        self.config_data.save()
        self._safe_after(900,
                          self._show_recovery_after_register)

    def _show_recovery_after_register(self) -> None:
        words = UserManager.get_recovery_phrase(
            self.current_user or "")
        if not words:
            self._close_auth_and_init()
            return
        shards = UserManager.get_recovery_shards(
            self.current_user or "")

        win = CTkToplevel(self)
        win.title("🔑 Ваша recovery-фраза")
        win.geometry("620x720")
        win.configure(fg_color=theme_manager.get("bg"))
        win.transient(self)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        CTkLabel(win, text="🔑 RECOVERY-ФРАЗА",
                 font=CTkFont(size=22, weight="bold"),
                 text_color=theme_manager.get("warning")
                 ).pack(pady=(20, 4))
        CTkLabel(win,
                 text="⚠️ Сохраните эти 12 слов в надёжном "
                      "месте!",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=theme_manager.get("error"),
                 wraplength=560).pack(pady=(0, 4))
        CTkLabel(win,
                 text="Они — ЕДИНСТВЕННЫЙ способ "
                      "восстановить пароль.",
                 font=CTkFont(size=11),
                 text_color=theme_manager.get("text_dim"),
                 wraplength=560).pack(pady=(0, 12))

        grid = CTkFrame(win, fg_color="transparent")
        grid.pack(fill="x", padx=30)
        for i in range(2):
            grid.grid_columnconfigure(i, weight=1,
                                        uniform="rec")
        for i, w in enumerate(words):
            r, c = i // 2, i % 2
            cell = CTkFrame(
                grid, fg_color=theme_manager.get("bg3"),
                corner_radius=6, border_width=1,
                border_color=theme_manager.get("card_border"))
            cell.grid(row=r, column=c, padx=4, pady=4,
                        sticky="nsew")
            CTkLabel(cell, text=f"{i + 1}. {w}",
                     font=CTkFont(family="Courier", size=14,
                                   weight="bold"),
                     text_color=theme_manager.get("primary"),
                     anchor="w").pack(anchor="w", padx=10,
                                        pady=6)

        if shards:
            sh_card = CTkFrame(
                win, fg_color=theme_manager.get("bg3"),
                corner_radius=6, border_width=1,
                border_color=theme_manager.get("accent"))
            sh_card.pack(fill="x", padx=30, pady=(12, 4))
            CTkLabel(sh_card,
                     text="🧩 SHAMIR 2/3 — любые 2 части "
                          "восстановят фразу:",
                     font=CTkFont(size=10, weight="bold"),
                     text_color=theme_manager.get("accent"),
                     anchor="w").pack(anchor="w", padx=10,
                                        pady=(6, 2))
            for s in shards:
                CTkLabel(sh_card, text=truncate(s, 70),
                         font=CTkFont(family="Courier",
                                       size=9),
                         text_color=theme_manager.get("text_dim"),
                         anchor="w", wraplength=520,
                         justify="left"
                         ).pack(anchor="w", fill="x",
                                padx=10, pady=1)

        self._recovery_saved_check = ctk.BooleanVar(
            value=False)
        check_row = CTkFrame(win, fg_color="transparent")
        check_row.pack(pady=(16, 4))
        CTkCheckBox(
            check_row,
            text="✅ Я сохранил(а) фразу и Shamir-части",
            variable=self._recovery_saved_check,
            font=CTkFont(size=12, weight="bold"),
            text_color=theme_manager.get("text"),
            fg_color=theme_manager.get("success"),
            hover_color=theme_manager.get("success"),
            border_color=theme_manager.get("card_border"),
            command=lambda: self._continue_after_recovery(win)
        ).pack()

        bf = CTkFrame(win, fg_color="transparent")
        bf.pack(fill="x", padx=30, pady=(12, 20))
        CTkButton(
            bf, text="📋 Скопировать фразу",
            height=40, width=200,
            font=CTkFont(size=12, weight="bold"),
            fg_color=theme_manager.get("accent"),
            text_color=theme_manager.get("bg"),
            command=lambda: (
                __import__("nexus.core",
                            fromlist=["copy_to_clipboard"]
                            ).copy_to_clipboard(
                                " ".join(words)),
                self._show_auth_toast("📋 Скопировано",
                                        "success"))
        ).pack(side="left")

        self._recovery_continue_btn = CTkButton(
            bf, text="➡️ Продолжить",
            height=40, width=180,
            font=CTkFont(size=12, weight="bold"),
            fg_color=theme_manager.get("text_dim"),
            text_color=theme_manager.get("bg"),
            state="disabled",
            command=lambda: (
                win.destroy(),
                self._close_auth_and_init())
        )
        self._recovery_continue_btn.pack(side="right")

    def _continue_after_recovery(self, win: Any) -> None:
        try:
            if self._recovery_saved_check.get():
                self._recovery_continue_btn.configure(
                    state="normal",
                    fg_color=theme_manager.get("success"))
            else:
                self._recovery_continue_btn.configure(
                    state="disabled",
                    fg_color=theme_manager.get("text_dim"))
        except Exception:
            pass

    def _close_auth_and_init(self) -> None:
        with suppress(Exception):
            if (self._auth_win
                    and self._auth_win.winfo_exists()):
                self._auth_win.destroy()
        self._auth_win = None
        self._init_app()

# ═══════════════════════════════════════════════════════════
# HOTKEYS MANAGER
# ═══════════════════════════════════════════════════════════
class HotkeysManager:
    """Управление горячими клавишами. Сохраняет в JSON."""

    DEFAULT_HOTKEYS: Dict[str, str] = {
        "save_all": "<Control-s>",
        "search": "<Control-f>",
        "qr_studio": "<Control-q>",
        "debugger": "<Control-Shift-B>",
        "command_palette": "<Control-k>",
        "quick_note": "<Control-Shift-N>",
        "screenshot": "<Control-Shift-S>",
        "panic": "<Control-Shift-H>",
        "memory_recall": "<Control-Shift-M>",
        "refresh": "<F5>",
        # FIX: конфликт с pomodoro устранён —
        # dev_mode на Ctrl+Shift+D
        "dev_mode": "<Control-Shift-D>",
        "passwords": "<Control-Shift-L>",
        "snippets": "<Control-Shift-K>",
        "timers": "<Control-Shift-T>",
        "code_studio": "<Control-Shift-C>",
        "close_window": "<Control-w>",
        "pomodoro": "<Control-Shift-P>",
        "quests": "<Control-Shift-Q>",
    }

    @staticmethod
    def load() -> Dict[str, str]:
        from nexus.core import HOTKEYS_PATH
        result = dict(HotkeysManager.DEFAULT_HOTKEYS)
        try:
            if HOTKEYS_PATH.exists():
                import json
                with open(HOTKEYS_PATH, "r",
                          encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if (isinstance(k, str)
                                and isinstance(v, str)
                                and k in HotkeysManager
                                .DEFAULT_HOTKEYS):
                            result[k] = v
        except Exception:
            pass
        return result

    @staticmethod
    def save(hotkeys: Dict[str, str]) -> bool:
        from nexus.core import HOTKEYS_PATH
        if not isinstance(hotkeys, dict):
            return False
        try:
            import json
            clean = {
                k: v for k, v in hotkeys.items()
                if (k in HotkeysManager.DEFAULT_HOTKEYS
                    and isinstance(v, str))
            }
            tmp = HOTKEYS_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False,
                          indent=2)
            tmp.replace(HOTKEYS_PATH)
            return True
        except Exception:
            return False

    @staticmethod
    def reset() -> bool:
        from nexus.core import HOTKEYS_PATH
        try:
            if HOTKEYS_PATH.exists():
                HOTKEYS_PATH.unlink()
            return True
        except Exception:
            return False

__all__ = [
    "NexusApp", "I18n", "ThemeManager", "theme_manager",
    "AppConfig", "HotkeysManager",
]