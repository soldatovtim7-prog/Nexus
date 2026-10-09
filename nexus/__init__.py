"""
NEXUS package.

Модульная версия NEXUS — каждый компонент в своём модуле.

Структура:
    nexus.core         — константы, пути, логгер, утилиты, simhash
    nexus.deps         — проверка и установка зависимостей
    nexus.crypto       — Argon2id, AES-GCM, Shamir, HIBP
    nexus.storage      — Database, Store, TTLCache, EventBus, MRU
    nexus.domain       — NoteManager, TaskManager, ForumManager,
                         BlockchainManager, AchievementSystem,
                         LevelSystem, DailyQuests, Pomodoro,
                         CodeTemplates, PatternEngine, ChatBot,
                         ContextTracker, EmailSender, ...
    nexus.security     — UserManager, SessionManager, RollingToken,
                         PasswordVault, VaultSession, APITokenManager
    nexus.sandbox      — CodeSandbox, SandboxRunner
    nexus.qr           — QRGenerator, QRTemplates, QRHistory,
                         QRBatchProcessor, QRLogoManager,
                         DynamicQRManager
    nexus.integrations — HTTP API, Webhooks, Plugins, Backups,
                         Export/Import, HTML Reports, Sync, Vaults
    nexus.app          — NexusApp (главное окно Tk)
    nexus.app_pages    — Реализации всех 23 страниц
    nexus.nexus_splash — Splash-окно загрузки
    nexus.__main__     — Точка входа

Запуск:
    python3 -m nexus
"""
from __future__ import annotations

# ── Версия ──────────────────────────────────────────
__version__: str = "1.1.1"
__version_name__: str = "CORE++ FIX"

# ── Re-export самых используемых констант ───────────
# (чтобы можно было делать `from nexus import APP_NAME`)
from nexus.core import (
    APP_AUTHOR,
    APP_CHANNEL,
    APP_ICON,
    APP_NAME,
    APP_TITLE,
    APP_YEAR,
    BUILD,
    BUILD_INFO,
    SCHEMA_VERSION,
    VERSION,
    VERSION_INFO,
    VERSION_NAME,
)

__all__ = [
    # Version
    "VERSION",
    "VERSION_NAME",
    "VERSION_INFO",
    "SCHEMA_VERSION",
    "APP_CHANNEL",
    "BUILD",
    "BUILD_INFO",
    # App identity
    "APP_NAME",
    "APP_ICON",
    "APP_TITLE",
    "APP_AUTHOR",
    "APP_YEAR",
    # Dunder
    "__version__",
    "__version_name__",
]