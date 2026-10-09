"""
NEXUS domain — бизнес-логика.
Импортирует: core, storage.
Не импортирует: security, qr (избегаем циклов).
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import secrets
import threading
import time
import traceback
from collections import Counter, defaultdict, deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (
    Any, Callable, Dict, Final, FrozenSet, Iterable, List,
    Optional, Set, Tuple,
)

from nexus.core import (
    ACHIEVEMENT_BATCH_INTERVAL_MS,
    AUDIT_MAX_ENTRIES,
    AUTOLINK_MIN_SIM,
    BLOCKCHAIN_MAX_EDIT_HISTORY,
    DECAY_BASE_HALF_LIFE,
    DECAY_FAVORITE_MULT,
    DECAY_PINNED_MULT,
    DAILY_QUESTS_COUNT,
    DRAFTS_DIR,
    EXPORT_DIR,
    MEMORY_DECAY_LOW_VIEWS,
    MEMORY_PATTERN_WINDOW_DAYS,
    NOTES_MERGE_THRESHOLD,
    POMODORO_LONG_INTERVAL,
    POMODORO_LONG_MIN,
    POMODORO_SHORT_MIN,
    POMODORO_WORK_MIN,
    SNIPPET_LIMIT,
    TAG_CLOUD_LIMIT,
    TIMER_FLUSH_INTERVAL,
    extract_tags,
    fmt_dt,
    fmt_duration,
    gen_id,
    gen_short_id,
    logger,
    now_iso,
    parse_iso,
    sanitize_filename,
    sanitize_html,
    simhash,
    simhash_similarity,
    truncate,
)
from nexus.storage import Store, cache, db, event_bus, executor


# ═══════════════════════════════════════════════════════════
# LEVEL SYSTEM
# ═══════════════════════════════════════════════════════════
class LevelSystem:
    _instance: Optional["LevelSystem"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "LevelSystem":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self.xp_actions: Dict[str, int] = {
            "format_code": 2,
            "code_batch": 5,
            "code_exporter": 3,
            "create_note": 10, "create_task": 10,
            "complete_task": 15, "create_post": 15,
            "create_block": 20, "edit_block": 15,
            "delete_block": 5, "generate_qr": 15,
            "execute_code": 20, "add_attachment": 5,
            "daily_login": 25, "edit_note": 5,
            "like_post": 2, "comment_post": 3,
            "save_snippet": 10, "password_saved": 5,
            "merge_notes": 15, "recall_used": 5,
            "resurrect_note": 25,
            "subtask_completed": 3,
            "timer_started": 2,
            "qr_batch": 30,
            "code_template_used": 5,
            "pomodoro_done": 25,
            "quest_done": 20,
            "clipboard_scan": 8,
            "dynamic_qr": 12,
            "markdown_post": 8,
        }
        self.ranks: Dict[int, str] = {
            0: "🌀 Путешественник", 5: "🌱 Новичок",
            15: "📈 Ученик", 30: "🌟 Профи", 50: "💪 Эксперт",
            75: "🚀 Мастер", 100: "⭐ Архимаг",
            150: "👑 Легенда", 200: "🌈 Бог кода",
            300: "♾️ Бесконечность",
        }
        self._lock = threading.RLock()
        self._incrementable: FrozenSet[str] = frozenset({
            "tasks_completed", "notes_created", "posts_created",
            "blocks_created", "blocks_edited", "blocks_deleted",
            "qr_generated", "code_executed",
            "attachments_added", "likes_received",
            "comments_count", "sessions_count",
            "notes_resurrected", "memory_hints_shown",
            "snippets_saved", "subtasks_completed",
            "timers_started", "blocks_signed",
            "quests_completed", "pomodoro_sessions",
            "clipboard_scans", "code_compiles",
        })

    def get_stats(self, user: str) -> Dict[str, Any]:
        ck = f"stats:{user}"
        cached = cache.get(ck)
        if cached is not None:
            return dict(cached)
        rows = db.execute(
            "SELECT * FROM user_stats WHERE user = ?", (user,))
        if rows:
            stats: Dict[str, Any] = dict(rows[0])
            for key in ("level", "xp", "xp_next", "streak",
                        "tasks_completed", "notes_created",
                        "posts_created", "blocks_created",
                        "blocks_edited", "blocks_deleted",
                        "qr_generated", "code_executed",
                        "attachments_added", "likes_received",
                        "comments_count", "total_time",
                        "sessions_count", "memory_hints_shown",
                        "notes_resurrected", "snippets_saved",
                        "subtasks_completed", "timers_started",
                        "blocks_signed", "quests_completed",
                        "pomodoro_sessions", "clipboard_scans",
                        "code_compiles"):
                if stats.get(key) is None:
                    stats[key] = 0
            if stats["level"] < 1:
                stats["level"] = 1
            if stats["xp_next"] < 1:
                stats["xp_next"] = 100
        else:
            stats = {"user": user, "level": 1, "xp": 0,
                     "xp_next": 100, "streak": 0,
                     "last_visit": now_iso(),
                     "tasks_completed": 0, "notes_created": 0,
                     "posts_created": 0, "blocks_created": 0,
                     "blocks_edited": 0, "blocks_deleted": 0,
                     "qr_generated": 0, "code_executed": 0,
                     "attachments_added": 0, "likes_received": 0,
                     "comments_count": 0, "total_time": 0,
                     "sessions_count": 0,
                     "memory_hints_shown": 0,
                     "notes_resurrected": 0,
                     "snippets_saved": 0,
                     "subtasks_completed": 0,
                     "timers_started": 0, "blocks_signed": 0,
                     "quests_completed": 0,
                     "pomodoro_sessions": 0,
                     "clipboard_scans": 0,
                     "code_compiles": 0}
            db.execute(
                "INSERT OR REPLACE INTO user_stats "
                "(user, level, xp, xp_next, last_visit) "
                "VALUES (?, ?, ?, ?, ?)",
                (user, 1, 0, 100, stats["last_visit"]))
        cache.set(ck, stats, 60)
        return dict(stats)

    def add_xp(self, user: str, action: str) -> Tuple[int, bool]:
        with self._lock:
            amount = self.xp_actions.get(action, 5)
            stats = self.get_stats(user)
            new_xp = int(stats.get("xp") or 0) + amount
            new_level = max(1, int(stats.get("level") or 1))
            xp_next = max(1, int(stats.get("xp_next") or 100))
            level_up = False
            safety = 0
            while new_xp >= xp_next and safety < 1000:
                new_xp -= xp_next
                new_level += 1
                xp_next = int(xp_next * 1.15)
                level_up = True
                safety += 1
            db.execute(
                "UPDATE user_stats SET level = ?, xp = ?, "
                "xp_next = ?, last_visit = ? WHERE user = ?",
                (new_level, new_xp, xp_next, now_iso(), user))
            cache.delete(f"stats:{user}")
        if level_up:
            with suppress(Exception):
                achievement_system.check(user, f"level_{new_level}")
        return new_level, level_up

    def increment(self, user: str, counter: str,
                  amount: int = 1) -> None:
        if counter not in self._incrementable or amount == 0:
            return
        with self._lock:
            db.execute(
                f"UPDATE user_stats SET {counter} = "
                f"COALESCE({counter}, 0) + ? WHERE user = ?",
                (amount, user))
            cache.delete(f"stats:{user}")

    def update_streak(self, user: str) -> None:
        new_streak = 1
        with self._lock:
            stats = self.get_stats(user)
            last = parse_iso(stats.get("last_visit", ""))
            today = datetime.now(timezone.utc).date()
            if last:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                last_date = last.date()
                if last_date == today:
                    return
                if last_date == today - timedelta(days=1):
                    new_streak = int(stats.get("streak") or 0) + 1
            db.execute(
                "UPDATE user_stats SET streak = ?, last_visit = ?, "
                "sessions_count = COALESCE(sessions_count, 0) + 1 "
                "WHERE user = ?",
                (new_streak, now_iso(), user))
            cache.delete(f"stats:{user}")
        if new_streak in (7, 30, 100, 365):
            with suppress(Exception):
                achievement_system.check(user, f"streak_{new_streak}")

    def get_progress(self, user: str) -> Dict[str, Any]:
        s = self.get_stats(user)
        xp_next = max(1, int(s.get("xp_next") or 100))
        return {
            "level": int(s.get("level") or 1),
            "xp": int(s.get("xp") or 0),
            "xp_next": xp_next,
            "progress": min(1.0, int(s.get("xp") or 0) / xp_next),
            "streak": int(s.get("streak") or 0),
        }

    def get_rank(self, level: int) -> str:
        for lvl, rank in sorted(self.ranks.items(),
                                 key=lambda x: x[0], reverse=True):
            if level >= lvl:
                return rank
        return self.ranks[0]


level_system = LevelSystem()


# ═══════════════════════════════════════════════════════════
# ACHIEVEMENT SYSTEM
# ═══════════════════════════════════════════════════════════
class AchievementSystem:
    _instance: Optional["AchievementSystem"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "AchievementSystem":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self.achievements: Dict[str, Dict[str, Any]] = {
            "first_note": {"name": "🧠 Первое воспоминание",
                           "desc": "Запиши первую мысль!",
                           "points": 10, "rarity": "common"},
            "first_task": {"name": "✅ Первая задача",
                           "desc": "Поставь первую цель!",
                           "points": 10, "rarity": "common"},
            "first_code": {"name": "💻 Первый код",
                           "desc": "Запусти программу!",
                           "points": 15, "rarity": "common"},
            "first_qr": {"name": "📱 Первый QR",
                         "desc": "Создай цифровой след!",
                         "points": 15, "rarity": "common"},
            "qr_master": {"name": "📱 Мастер QR",
                          "desc": "100 QR!",
                          "points": 40, "rarity": "uncommon"},
            "qr_batch_master": {
                "name": "📦 Батч-генератор",
                "desc": "Сгенерируй 10+ QR за раз!",
                "points": 50, "rarity": "rare"},
            "qr_stylist": {
                "name": "🎨 QR-стилист",
                "desc": "Используй 6 разных стилей!",
                "points": 35, "rarity": "uncommon"},
            "qr_scanner": {
                "name": "📥 QR-сканер",
                "desc": "Сканируй QR из буфера!",
                "points": 20, "rarity": "common"},
            "first_block": {"name": "🔗 Первый блок",
                            "desc": "Впиши себя в блокчейн!",
                            "points": 20, "rarity": "uncommon"},
            "block_master": {"name": "⛓️ Мастер цепи",
                             "desc": "50 блоков!",
                             "points": 50, "rarity": "rare"},
            "first_forum": {"name": "💬 Первое сообщение",
                            "desc": "Поделись мыслью!",
                            "points": 10, "rarity": "common"},
            "first_attachment": {"name": "📎 Первое вложение",
                                 "desc": "Прикрепи файл!",
                                 "points": 15, "rarity": "common"},
            "first_like": {"name": "❤️ Первый лайк",
                           "desc": "Кто-то оценил!",
                           "points": 10, "rarity": "common"},
            "photographer": {"name": "📸 Фотограф",
                             "desc": "Скриншот!",
                             "points": 10, "rarity": "common"},
            "task_master": {"name": "🎯 Мастер задач",
                            "desc": "50 задач!",
                            "points": 30, "rarity": "uncommon"},
            "note_master": {"name": "📚 Мастер памяти",
                            "desc": "50 записей!",
                            "points": 30, "rarity": "uncommon"},
            "code_master": {"name": "💻 Мастер кода",
                            "desc": "50 запусков!",
                            "points": 40, "rarity": "uncommon"},
            "code_architect": {
                "name": "🏗️ Архитектор кода",
                "desc": "Используй 10 шаблонов",
                "points": 40, "rarity": "rare"},
            "pomodoro_master": {
                "name": "🍅 Помидорный лорд",
                "desc": "20 помидоров!",
                "points": 40, "rarity": "uncommon"},
            "quest_hunter": {
                "name": "🎯 Охотник за квестами",
                "desc": "Выполни 30 квестов!",
                "points": 50, "rarity": "rare"},
            "merge_master": {"name": "🔀 Мастер слияния",
                             "desc": "Объедини 5 заметок!",
                             "points": 25, "rarity": "uncommon"},
            "kanban_user": {"name": "🗂 Канбан-практик",
                            "desc": "Открой Kanban в задачах!",
                            "points": 10, "rarity": "common"},
            "memory_recall": {"name": "💭 Вспоминающий",
                              "desc": "Используй «Что я забыл?»",
                              "points": 15, "rarity": "common"},
            "memory_keeper": {"name": "🌱 Хранитель памяти",
                              "desc": "Оживи 5 увядших заметок!",
                              "points": 40, "rarity": "rare"},
            "decay_master": {"name": "🌿 Проводник увядания",
                             "desc": "Оживи 25 увядших заметок!",
                             "points": 80, "rarity": "epic"},
            "simhash_master": {"name": "🔬 Мастер связей",
                               "desc": "Найди 10 похожих пар!",
                               "points": 30, "rarity": "rare"},
            "autolink_master": {
                "name": "🔗 Связной",
                "desc": "Автолинковка заметок",
                "points": 25, "rarity": "uncommon"},
            "nexus_master": {"name": "🌀 Мастер NEXUS",
                             "desc": "1000 очков!",
                             "points": 50, "rarity": "epic"},
            "god_master": {"name": "👑 Бог кода",
                           "desc": "5000 очков!",
                           "points": 100, "rarity": "legendary"},
            "streak_7": {"name": "📅 Неделя",
                         "desc": "7 дней подряд!",
                         "points": 25, "rarity": "uncommon"},
            "streak_30": {"name": "📅 Месяц",
                          "desc": "30 дней!",
                          "points": 50, "rarity": "rare"},
            "streak_100": {"name": "📅 Сто дней",
                           "desc": "100 дней подряд!",
                           "points": 100, "rarity": "legendary"},
            "level_5": {"name": "🌱 Уровень 5",
                        "desc": "5 уровень!",
                        "points": 10, "rarity": "common"},
            "level_10": {"name": "🌟 Уровень 10",
                         "desc": "10 уровень!",
                         "points": 20, "rarity": "uncommon"},
            "level_25": {"name": "⭐ Уровень 25",
                         "desc": "25 уровень!",
                         "points": 50, "rarity": "rare"},
            "level_50": {"name": "👑 Уровень 50",
                         "desc": "50 уровень!",
                         "points": 100, "rarity": "epic"},
            "level_100": {"name": "♾️ Уровень 100",
                          "desc": "100 уровень!",
                          "points": 200, "rarity": "legendary"},
            "password_keeper": {"name": "🔑 Хранитель паролей",
                                "desc": "Сохрани 10 паролей!",
                                "points": 25, "rarity": "uncommon"},
            "sandbox_survivor": {
                "name": "🛡️ Выживший в песочнице",
                "desc": "Запусти 10 безопасных программ",
                "points": 20, "rarity": "uncommon"},
            "snippet_master": {
                "name": "✂️ Мастер сниппетов",
                "desc": "Сохрани 20 сниппетов!",
                "points": 30, "rarity": "uncommon"},
            "subtask_warrior": {
                "name": "🪜 Покоритель подзадач",
                "desc": "Выполни 50 подзадач!",
                "points": 40, "rarity": "rare"},
            "time_lord": {
                "name": "⏱️ Повелитель времени",
                "desc": "Запусти таймер 25 раз!",
                "points": 35, "rarity": "uncommon"},
            "recovery_guardian": {
                "name": "🛡️ Хранитель фразы",
                "desc": "Сохрани recovery-фразу!",
                "points": 20, "rarity": "common"},
            "shamir_keeper": {
                "name": "🧩 Хранитель осколков",
                "desc": "Используй Shamir-схему!",
                "points": 30, "rarity": "rare"},
            "device_manager": {
                "name": "📱 Менеджер устройств",
                "desc": "Управляй сессиями!",
                "points": 15, "rarity": "common"},
            "api_explorer": {
                "name": "🌐 API-исследователь",
                "desc": "Создай первый API-токен!",
                "points": 25, "rarity": "uncommon"},
            "polyglot": {
                "name": "🌍 Полиглот",
                "desc": "Смени язык 3 раза!",
                "points": 15, "rarity": "common"},
            "theme_collector": {
                "name": "🎨 Коллекционер тем",
                "desc": "Попробуй 5 разных тем!",
                "points": 25, "rarity": "uncommon"},
            "exporter": {
                "name": "📤 Экспортёр",
                "desc": "Экспортируй данные 3 раза!",
                "points": 20, "rarity": "common"},
            "night_owl": {
                "name": "🦉 Ночная сова",
                "desc": "Запиши заметку после 2:00",
                "points": 20, "rarity": "uncommon"},
            "early_bird": {
                "name": "🐦 Ранняя пташка",
                "desc": "Запиши заметку до 6:00",
                "points": 20, "rarity": "uncommon"},
            "hidden_333": {
                "name": "🕒 Полночь",
                "desc": "Запусти код в 03:33",
                "points": 50, "rarity": "epic",
                "hidden": True},
            "hidden_leap": {
                "name": "🐸 Високосный",
                "desc": "Записал 29 февраля",
                "points": 40, "rarity": "rare",
                "hidden": True},
            "hidden_void": {
                "name": "🌑 Пустота",
                "desc": "Пустая заметка",
                "points": 10, "rarity": "common",
                "hidden": True},
            "hidden_1337": {
                "name": "💀 Leet",
                "desc": "1337 очков",
                "points": 15, "rarity": "uncommon",
                "hidden": True},
            "hidden_night_shift": {
                "name": "🌙 Ночная смена",
                "desc": "Активен 5 часов после полуночи",
                "points": 30, "rarity": "rare",
                "hidden": True},
            "hidden_hoarder": {
                "name": "🗄️ Собиратель",
                "desc": "100 заметок",
                "points": 60, "rarity": "epic",
                "hidden": True},
            "hidden_monk": {
                "name": "🧘 Монах",
                "desc": "1 день без создания контента",
                "points": 25, "rarity": "uncommon",
                "hidden": True},
            "hidden_phoenix": {
                "name": "🔥 Феникс",
                "desc": "Восстанови 100 заметок",
                "points": 80, "rarity": "legendary",
                "hidden": True},
        }
        self.rarity_colors: Dict[str, str] = {
            "common": "#888888", "uncommon": "#00e5ff",
            "rare": "#b026ff", "epic": "#ff2d95",
            "legendary": "#ffd700",
        }

    def get_all(self, include_hidden: bool = False
                 ) -> Dict[str, Dict[str, Any]]:
        if include_hidden:
            return self.achievements
        return {k: v for k, v in self.achievements.items()
                if not v.get("hidden")}

    def get_rarity_color(self, rarity: str) -> str:
        return self.rarity_colors.get(rarity, "#888888")

    def get_user_achievements(self, user: str) -> List[str]:
        ck = f"user_ach:{user}"
        cached = cache.get(ck)
        if cached is not None:
            return list(cached)
        rows = db.execute(
            "SELECT achievement FROM achievements WHERE user = ?",
            (user,))
        result = [r["achievement"] for r in rows] if rows else []
        cache.set(ck, result, 300)
        return list(result)

    def get_points(self, user: str) -> int:
        unlocked = self.get_user_achievements(user)
        return sum(self.achievements.get(a, {}).get("points", 0)
                   for a in unlocked)

    def get_progress(self, user: str) -> Tuple[int, int, int]:
        total = len(self.achievements)
        unlocked = len(self.get_user_achievements(user))
        return total, unlocked, self.get_points(user)

    def _is_unlocked(self, user: str, ach: str) -> bool:
        ck = f"ach:{user}:{ach}"
        cached = cache.get(ck)
        if cached is not None:
            return cached
        rows = db.execute(
            "SELECT 1 FROM achievements "
            "WHERE user = ? AND achievement = ?", (user, ach))
        unlocked = bool(rows)
        cache.set(ck, unlocked, 300)
        return unlocked

    def _unlock(self, user: str, ach: str) -> None:
        try:
            inserted = False
            with db.transaction() as cur:
                cur.execute(
                    "INSERT OR IGNORE INTO achievements "
                    "(id, user, achievement, unlocked_at) "
                    "VALUES (?, ?, ?, ?)",
                    (gen_id(), user, ach, now_iso()))
                inserted = cur.rowcount > 0
            cache.delete(f"ach:{user}:{ach}")
            cache.delete(f"user_ach:{user}")
            if inserted:
                event_bus.emit_async(
                    "achievement.unlocked",
                    user=user, achievement=ach)
        except Exception:
            pass

    def check(self, user: str, action: str) -> Optional[str]:
        try:
            if (action in self.achievements
                    and self._is_unlocked(user, action)):
                return None
            if action.startswith("level_"):
                try:
                    lvl = int(action.split("_", 1)[1])
                except (ValueError, IndexError):
                    return None
                stats = db.execute(
                    "SELECT level FROM user_stats WHERE user = ?",
                    (user,))
                if stats and (stats[0]["level"] or 0) >= lvl:
                    self._unlock(user, action)
                    return action
            elif action.startswith("streak_"):
                try:
                    sk = int(action.split("_", 1)[1])
                except (ValueError, IndexError):
                    return None
                stats = db.execute(
                    "SELECT streak FROM user_stats WHERE user = ?",
                    (user,))
                if stats and (stats[0]["streak"] or 0) >= sk:
                    self._unlock(user, action)
                    return action
            return None
        except Exception:
            return None

    def batch_check(self, user: str) -> List[str]:
        try:
            rows = db.execute(
                "SELECT level, streak, tasks_completed, "
                "notes_created, posts_created, blocks_created, "
                "blocks_edited, blocks_deleted, "
                "qr_generated, code_executed, attachments_added, "
                "likes_received, notes_resurrected, "
                "snippets_saved, subtasks_completed, "
                "timers_started, quests_completed, "
                "pomodoro_sessions "
                "FROM user_stats WHERE user = ?", (user,))
            if not rows:
                return []
            s = dict(rows[0])
            for k, v in list(s.items()):
                if v is None:
                    s[k] = 0
            points = self.get_points(user)
            unlocked = set(self.get_user_achievements(user))
            conditions = {
                "first_note": s["notes_created"] >= 1,
                "first_task": s["tasks_completed"] >= 1,
                "first_code": s["code_executed"] >= 1,
                "first_qr": s["qr_generated"] >= 1,
                "first_block": s["blocks_created"] >= 1,
                "block_master": s["blocks_created"] >= 50,
                "first_forum": s["posts_created"] >= 1,
                "first_like": s["likes_received"] >= 1,
                "first_attachment": s["attachments_added"] >= 1,
                "qr_master": s["qr_generated"] >= 100,
                "task_master": s["tasks_completed"] >= 50,
                "note_master": s["notes_created"] >= 50,
                "code_master": s["code_executed"] >= 50,
                "memory_keeper": s["notes_resurrected"] >= 5,
                "decay_master": s["notes_resurrected"] >= 25,
                "snippet_master": s["snippets_saved"] >= 20,
                "subtask_warrior": s["subtasks_completed"] >= 50,
                "time_lord": s["timers_started"] >= 25,
                "nexus_master": points >= 1000,
                "god_master": points >= 5000,
                "level_5": s["level"] >= 5,
                "level_10": s["level"] >= 10,
                "level_25": s["level"] >= 25,
                "level_50": s["level"] >= 50,
                "level_100": s["level"] >= 100,
                "streak_7": s["streak"] >= 7,
                "streak_30": s["streak"] >= 30,
                "streak_100": s["streak"] >= 100,
                "quest_hunter": s["quests_completed"] >= 30,
                "pomodoro_master": s["pomodoro_sessions"] >= 20,
                "hidden_1337": 1300 <= points <= 1400,
                "hidden_hoarder": s["notes_created"] >= 100,
                "hidden_phoenix": s["notes_resurrected"] >= 100,
            }
            to_unlock = [k for k, cond in conditions.items()
                         if cond and k not in unlocked]
            if not to_unlock:
                return []
            with db.transaction() as cur:
                for ach in to_unlock:
                    cur.execute(
                        "INSERT OR IGNORE INTO achievements "
                        "(id, user, achievement, unlocked_at) "
                        "VALUES (?, ?, ?, ?)",
                        (gen_id(), user, ach, now_iso()))
            for ach in to_unlock:
                cache.delete(f"ach:{user}:{ach}")
            cache.delete(f"user_ach:{user}")
            for ach in to_unlock:
                event_bus.emit_async(
                    "achievement.unlocked",
                    user=user, achievement=ach)
            return to_unlock
        except Exception as e:
            logger.error(f"batch_check: {e}")
            return []


achievement_system = AchievementSystem()


# ═══════════════════════════════════════════════════════════
# DAILY QUESTS
# ═══════════════════════════════════════════════════════════
class DailyQuests:
    QUESTS: Final[Dict[str, Dict[str, Any]]] = {
        "q_notes_3": {
            "name": "📝 Записать 3 заметки",
            "desc": "Сегодня напиши 3 заметки",
            "action": "create_note", "target": 3},
        "q_tasks_2": {
            "name": "✅ Завершить 2 задачи",
            "desc": "Закрой 2 задачи",
            "action": "complete_task", "target": 2},
        "q_code_1": {
            "name": "💻 Запустить код",
            "desc": "Запусти хотя бы одну программу",
            "action": "execute_code", "target": 1},
        "q_qr_3": {
            "name": "📱 Создать 3 QR",
            "desc": "Сгенерируй 3 QR-кода",
            "action": "generate_qr", "target": 3},
        "q_pomodoro_1": {
            "name": "🍅 Помидор",
            "desc": "Заверши 1 Pomodoro-сессию",
            "action": "pomodoro_done", "target": 1},
        "q_block_1": {
            "name": "⛓️ Блок в цепь",
            "desc": "Добавь блок в блокчейн",
            "action": "create_block", "target": 1},
        "q_snippet_1": {
            "name": "✂️ Сниппет",
            "desc": "Сохрани сниппет",
            "action": "save_snippet", "target": 1},
    }

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @classmethod
    def get_user_quests(cls, user: str
                         ) -> List[Dict[str, Any]]:
        day = cls._today()
        rows = db.execute(
            "SELECT quest_id, done FROM daily_quests "
            "WHERE user = ? AND day = ?", (user, day))
        existing = {r["quest_id"]: bool(r["done"])
                    for r in (rows or [])}
        result: List[Dict[str, Any]] = []
        rng = random.Random(f"{user}:{day}")
        keys = list(cls.QUESTS.keys())
        rng.shuffle(keys)
        picked = keys[:DAILY_QUESTS_COUNT]
        for qid in picked:
            meta = cls.QUESTS[qid]
            done = existing.get(qid, False)
            if qid not in existing:
                with suppress(Exception):
                    db.execute(
                        "INSERT OR IGNORE INTO daily_quests "
                        "(user, day, quest_id, done) "
                        "VALUES (?, ?, ?, 0)", (user, day, qid))
            result.append({
                "id": qid, "done": done, **meta})
        return result

    @classmethod
    def complete(cls, user: str, action: str) -> int:
        day = cls._today()
        done_count = 0
        for qid, meta in cls.QUESTS.items():
            if meta["action"] != action:
                continue
            rows = db.execute(
                "SELECT done FROM daily_quests "
                "WHERE user = ? AND day = ? AND quest_id = ?",
                (user, day, qid))
            if rows and rows[0]["done"]:
                continue
            db.execute(
                "INSERT OR REPLACE INTO daily_quests "
                "(user, day, quest_id, done, done_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (user, day, qid, now_iso()))
            level_system.add_xp(user, "quest_done")
            level_system.increment(user, "quests_completed")
            done_count += 1
        return done_count


# ═══════════════════════════════════════════════════════════
# NOTE MANAGER
# ═══════════════════════════════════════════════════════════
class NoteManager:
    _FLAG_MAP: Final[Dict[str, str]] = {
        "favorite": "favorite",
        "pinned": "pinned",
    }

    @staticmethod
    def _row_to_note(row: Any) -> Dict[str, Any]:
        note = dict(row)
        for key in ("tags", "autolinks"):
            try:
                note[key] = json.loads(note.get(key) or "[]")
            except Exception:
                note[key] = []
            if not isinstance(note[key], list):
                note[key] = []
        return note

    @staticmethod
    def _invalidate(note_id: Optional[str] = None,
                     author: Optional[str] = None) -> None:
        cache.delete("notes:all")
        if note_id:
            cache.delete(f"note:{note_id}")
        if author:
            cache.delete(f"notes:user:{author}")

    @staticmethod
    def _compute_decay(note: Dict[str, Any]) -> float:
        try:
            created = parse_iso(note.get("created_at"))
            if not created:
                return 1.0
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            last_viewed = parse_iso(note.get("last_viewed"))
            ref = last_viewed if last_viewed else created
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            age_days = max(
                0.0,
                (datetime.now(timezone.utc)
                 - ref).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / DECAY_BASE_HALF_LIFE)
            if note.get("favorite"):
                decay *= DECAY_FAVORITE_MULT
            if note.get("pinned"):
                decay *= DECAY_PINNED_MULT
            return min(2.0, max(0.0, decay))
        except Exception:
            return 1.0

    @staticmethod
    def _compute_autolinks(note: Dict[str, Any],
                            user: str) -> List[str]:
        if not note.get("simhash"):
            return []
        try:
            rows = db.execute(
                "SELECT id, simhash FROM notes "
                "WHERE author = ? AND id != ? "
                "  AND deleted_at = '' "
                "  AND simhash != 0",
                (user, note.get("id", "")))
            if not rows:
                return []
            links: List[Tuple[float, str]] = []
            h = int(note["simhash"])
            for r in rows:
                s = simhash_similarity(
                    h, int(r["simhash"] or 0))
                if s >= AUTOLINK_MIN_SIM:
                    links.append((s, r["id"]))
            links.sort(reverse=True)
            return [nid for _, nid in links[:5]]
        except Exception:
            return []

    @staticmethod
    def create(title: str, content: str, author: str,
               tags: Optional[List[str]] = None,
               category: str = "Общее"
               ) -> Optional[Dict[str, Any]]:
        if not title or not author:
            return None
        note_id = gen_id()
        sh = simhash(f"{title} {content[:2000]}")
        now = now_iso()
        autolinks = NoteManager._compute_autolinks(
            {"id": note_id, "simhash": sh}, author)
        r = db.execute(
            "INSERT INTO notes (id, title, content, author, tags, "
            "category, created_at, updated_at, simhash, "
            "decay_score, autolinks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (note_id, title, content, author,
             json.dumps(tags or [], ensure_ascii=False),
             category, now, now, sh, 1.0,
             json.dumps(autolinks, ensure_ascii=False)))
        if r is None:
            return None
        if autolinks:
            try:
                with db.transaction() as cur:
                    for nid in autolinks:
                        rows = cur.execute(
                            "SELECT autolinks FROM notes "
                            "WHERE id = ?", (nid,)).fetchone()
                        if not rows:
                            continue
                        try:
                            back = json.loads(
                                rows["autolinks"] or "[]")
                        except Exception:
                            back = []
                        if not isinstance(back, list):
                            back = []
                        if note_id not in back:
                            back.append(note_id)
                            cur.execute(
                                "UPDATE notes SET autolinks = ? "
                                "WHERE id = ?",
                                (json.dumps(back[:10]), nid))
            except Exception as e:
                logger.debug(f"backlinks: {e}")
        level_system.add_xp(author, "create_note")
        level_system.increment(author, "notes_created")
        with suppress(Exception):
            achievement_system.check(author, "first_note")
            h = datetime.now().hour
            if 2 <= h < 6:
                achievement_system.check(author, "night_owl")
            elif 0 <= h < 6:
                achievement_system.check(author, "early_bird")
            if autolinks:
                achievement_system.check(
                    author, "autolink_master")
            if (datetime.now().month == 2
                    and datetime.now().day == 29):
                achievement_system.check(author, "hidden_leap")
        with suppress(Exception):
            DailyQuests.complete(author, "create_note")
        db.audit(author, "note.create", title)
        NoteManager._invalidate(note_id, author)
        event_bus.emit_async("note.created",
                              note_id=note_id, author=author)
        return {"id": note_id, "title": title,
                "autolinks": autolinks}

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        c = cache.get("notes:all")
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM notes WHERE status != 'deleted' "
            "  AND deleted_at = '' "
            "ORDER BY pinned DESC, created_at DESC")
        result = [NoteManager._row_to_note(r)
                   for r in (rows or [])]
        cache.set("notes:all", result, 60)
        return [dict(x) for x in result]

    @staticmethod
    def get_user_notes(user: str) -> List[Dict[str, Any]]:
        ck = f"notes:user:{user}"
        c = cache.get(ck)
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM notes WHERE author = ? "
            "  AND status = 'active' "
            "  AND deleted_at = '' "
            "ORDER BY pinned DESC, created_at DESC", (user,))
        result = [NoteManager._row_to_note(r)
                   for r in (rows or [])]
        cache.set(ck, result, 60)
        return [dict(x) for x in result]

    @staticmethod
    def get_by_id(nid: str) -> Optional[Dict[str, Any]]:
        if not nid:
            return None
        ck = f"note:{nid}"
        c = cache.get(ck)
        if c is not None:
            return dict(c)
        rows = db.execute(
            "SELECT * FROM notes WHERE id = ?", (nid,))
        if not rows:
            return None
        note = NoteManager._row_to_note(rows[0])
        cache.set(ck, note, 300)
        return dict(note)

    @staticmethod
    def update(nid: str, title: str, content: str,
               tags: Optional[List[str]] = None) -> bool:
        if not nid:
            return False
        before = NoteManager.get_by_id(nid)
        if not before:
            return False
        sh = simhash(f"{title} {content[:2000]}")
        autolinks = NoteManager._compute_autolinks(
            {"id": nid, "simhash": sh},
            before.get("author", ""))
        r = db.execute(
            "UPDATE notes SET title = ?, content = ?, tags = ?, "
            "updated_at = ?, simhash = ?, autolinks = ? "
            "WHERE id = ?",
            (title, content,
             json.dumps(tags or [], ensure_ascii=False),
             now_iso(), sh,
             json.dumps(autolinks, ensure_ascii=False), nid))
        if r is None:
            return False
        NoteManager._invalidate(nid, before.get("author"))
        return True

    @staticmethod
    def mark_viewed(nid: str) -> None:
        if not nid:
            return
        try:
            db.execute(
                "UPDATE notes SET views = COALESCE(views, 0) + 1, "
                "last_viewed = ?, decay_score = ? "
                "WHERE id = ?",
                (now_iso(), 1.0, nid))
            cache.delete(f"note:{nid}")
        except Exception:
            pass

    @staticmethod
    def recompute_decay(user: str) -> int:
        try:
            notes = NoteManager.get_user_notes(user)
            updated = 0
            with db.transaction() as cur:
                for n in notes:
                    d = NoteManager._compute_decay(n)
                    if abs(d - float(n.get("decay_score") or 0)) \
                            > 0.01:
                        cur.execute(
                            "UPDATE notes SET decay_score = ? "
                            "WHERE id = ?", (d, n["id"]))
                        updated += 1
            if updated:
                cache.delete(f"notes:user:{user}")
                cache.delete("notes:all")
            return updated
        except Exception:
            return 0

    @staticmethod
    def delete(nid: str, hard: bool = False) -> bool:
        if not nid:
            return False
        note = NoteManager.get_by_id(nid)
        if not note:
            return False
        author = note.get("author")
        if hard:
            with suppress(Exception):
                for att in AttachmentManager.get_by_ref(
                        "note", nid):
                    AttachmentManager.delete(
                        att["id"], att["filename"])
            db.execute("DELETE FROM notes WHERE id = ?", (nid,))
        else:
            db.execute(
                "UPDATE notes SET deleted_at = ?, "
                "status = 'deleted' WHERE id = ?",
                (now_iso(), nid))
        NoteManager._invalidate(nid, author)
        return True

    @staticmethod
    def restore(nid: str) -> bool:
        if not nid:
            return False
        db.execute(
            "UPDATE notes SET deleted_at = '', status = 'active' "
            "WHERE id = ?", (nid,))
        note = NoteManager.get_by_id(nid)
        NoteManager._invalidate(
            nid, note.get("author") if note else None)
        return True

    @staticmethod
    def _toggle_flag(nid: str, flag: str) -> bool:
        col = NoteManager._FLAG_MAP.get(flag)
        if not col:
            return False
        rows = db.execute(
            f"SELECT {col} AS v FROM notes WHERE id = ?",
            (nid,))
        if not rows:
            return False
        new = 1 - int(rows[0]["v"] or 0)
        db.execute(
            f"UPDATE notes SET {col} = ? WHERE id = ?",
            (new, nid))
        note = NoteManager.get_by_id(nid)
        NoteManager._invalidate(
            nid, note.get("author") if note else None)
        return True

    @staticmethod
    def toggle_favorite(nid: str) -> bool:
        return NoteManager._toggle_flag(nid, "favorite")

    @staticmethod
    def toggle_pinned(nid: str) -> bool:
        return NoteManager._toggle_flag(nid, "pinned")

    @staticmethod
    def get_tag_cloud(user: str,
                       limit: int = TAG_CLOUD_LIMIT
                       ) -> List[Tuple[str, int]]:
        notes = NoteManager.get_user_notes(user)
        counter: Counter = Counter()
        for n in notes:
            for t in (n.get("tags") or []):
                counter[t] += 1
        return counter.most_common(limit)

    @staticmethod
    def find_similar(user: str,
                     threshold: float = NOTES_MERGE_THRESHOLD
                     ) -> List[Tuple[float, Dict[str, Any],
                                     Dict[str, Any]]]:
        notes = NoteManager.get_user_notes(user)
        if len(notes) < 2:
            return []
        for n in notes:
            if not n.get("simhash"):
                h = simhash(
                    f"{n.get('title', '')} "
                    f"{n.get('content', '')[:2000]}")
                n["simhash"] = h
                with suppress(Exception):
                    db.execute(
                        "UPDATE notes SET simhash = ? "
                        "WHERE id = ?", (h, n["id"]))
        pairs: List[Tuple[float, Dict[str, Any],
                           Dict[str, Any]]] = []
        for i, a in enumerate(notes):
            ha = int(a.get("simhash") or 0)
            if not ha:
                continue
            for b in notes[i + 1:]:
                hb = int(b.get("simhash") or 0)
                if not hb:
                    continue
                s = simhash_similarity(ha, hb)
                if s >= threshold:
                    pairs.append((s, a, b))
        pairs.sort(key=lambda x: -x[0])
        return pairs[:50]

    @staticmethod
    def merge(src_id: str, dst_id: str, user: str) -> bool:
        src = NoteManager.get_by_id(src_id)
        dst = NoteManager.get_by_id(dst_id)
        if not src or not dst:
            return False
        if (src.get("author") != user
                or dst.get("author") != user):
            return False
        merged_content = (str(dst.get("content", ""))
                          + "\n\n---\n\n"
                          + str(src.get("content", "")))
        merged_tags = list(set((dst.get("tags") or [])
                                + (src.get("tags") or [])))
        ok = NoteManager.update(
            dst_id, dst.get("title", ""),
            merged_content, merged_tags)
        if not ok:
            return False
        NoteManager.delete(src_id, hard=False)
        with suppress(Exception):
            level_system.add_xp(user, "merge_notes")
            achievement_system.check(user, "merge_master")
        db.audit(user, "note.merge",
                 f"{src_id[:8]} → {dst_id[:8]}")
        return True

    @staticmethod
    def recall(user: str, limit: int = 20
                ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        try:
            NoteManager.recompute_decay(user)
            rows = db.execute(
                "SELECT * FROM notes "
                "WHERE author = ? "
                "  AND status IN ('active', 'faded') "
                "  AND deleted_at = '' "
                "  AND favorite = 0 AND pinned = 0 "
                "ORDER BY decay_score ASC LIMIT ?",
                (user, limit * 3))
            if not rows:
                return []
            now = datetime.now(timezone.utc)
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for row in rows:
                n = NoteManager._row_to_note(row)
                dt = parse_iso(n.get("created_at"))
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                n["age_days"] = ((now - dt).days if dt else 0)
                views = int(n.get("views") or 0)
                decay = float(n.get("decay_score") or 1.0)
                priority = (n["age_days"] * 0.5
                              + (1.0 - decay) * 100
                              + max(0, 5 - views) * 3)
                scored.append((priority, n))
            scored.sort(key=lambda x: -x[0])
            result = [n for _p, n in scored[:limit]]
        except Exception as e:
            logger.debug(f"recall: {e}")
        return result

    @staticmethod
    def search(user: str, query: str,
               limit: int = 30) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        fts = db.search_notes_fts(
            query, author=user, limit=limit)
        if fts:
            return [NoteManager._row_to_note(dict(r))
                     for r in fts]
        return db._search_notes_like(query, user, limit)

    @staticmethod
    def fade(user: str) -> int:
        count = 0
        try:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=90)).isoformat()
            rows = db.execute(
                "SELECT id FROM notes "
                "WHERE author = ? AND status = 'active' "
                "  AND deleted_at = '' "
                "  AND created_at < ? "
                "  AND (views IS NULL OR views <= ?) "
                "  AND favorite = 0 AND pinned = 0",
                (user, cutoff, MEMORY_DECAY_LOW_VIEWS))
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            with db.transaction() as cur:
                for nid in ids:
                    cur.execute(
                        "UPDATE notes SET status = 'faded' "
                        "WHERE id = ?", (nid,))
            count = len(ids)
            if count > 0:
                db.audit(user, "memory.fade",
                         f"{count} заметок")
                cache.delete("notes:all")
                cache.delete(f"notes:user:{user}")
        except Exception as e:
            logger.debug(f"fade: {e}")
        return count

    @staticmethod
    def resurrect(nid: str, user: str) -> bool:
        try:
            db.execute(
                "UPDATE notes SET status = 'active', "
                "views = 0, created_at = ?, "
                "resurrected_at = ?, decay_score = 1.0 "
                "WHERE id = ?",
                (now_iso(), now_iso(), nid))
            NoteManager._invalidate(nid, user)
            db.audit(user, "memory.resurrect", nid[:8])
            level_system.add_xp(user, "resurrect_note")
            level_system.increment(user, "notes_resurrected")
            with suppress(Exception):
                achievement_system.check(user, "memory_keeper")
                achievement_system.check(user, "decay_master")
                achievement_system.check(user, "hidden_phoenix")
            return True
        except Exception:
            return False

    @staticmethod
    def empty_trash(user: str) -> int:
        try:
            rows = db.execute(
                "SELECT id FROM notes "
                "WHERE author = ? AND deleted_at != ''", (user,))
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            with db.transaction() as cur:
                for nid in ids:
                    cur.execute(
                        "DELETE FROM notes WHERE id = ?", (nid,))
            cache.delete("notes:all")
            cache.delete(f"notes:user:{user}")
            return len(ids)
        except Exception:
            return 0

    @staticmethod
    def trash_count(user: str) -> int:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM notes "
                "WHERE author = ? AND deleted_at != ''", (user,))
            return int(rows[0]["c"] or 0) if rows else 0
        except Exception:
            return 0

    @staticmethod
    def get_autolink_graph(user: str
                            ) -> Dict[str, List[str]]:
        notes = NoteManager.get_user_notes(user)
        graph: Dict[str, List[str]] = {}
        for n in notes:
            links = n.get("autolinks") or []
            if links:
                graph[n["id"]] = [
                    l for l in links
                    if any(x["id"] == l for x in notes)]
        return graph


# ═══════════════════════════════════════════════════════════
# SUBTASK MANAGER
# ═══════════════════════════════════════════════════════════
class SubtaskManager:
    @staticmethod
    def create(parent_id: str, title: str,
               order_idx: int = 0
               ) -> Optional[Dict[str, Any]]:
        if not parent_id or not title:
            return None
        st_id = gen_id()
        r = db.execute(
            "INSERT INTO subtasks "
            "(id, parent_task_id, title, done, order_idx, "
            "created_at) VALUES (?, ?, ?, 0, ?, ?)",
            (st_id, parent_id, title, order_idx, now_iso()))
        if r is None:
            return None
        return {"id": st_id, "title": title, "done": 0}

    @staticmethod
    def get_for_task(parent_id: str) -> List[Dict[str, Any]]:
        if not parent_id:
            return []
        rows = db.execute(
            "SELECT * FROM subtasks WHERE parent_task_id = ? "
            "ORDER BY order_idx, created_at", (parent_id,))
        return [dict(r) for r in (rows or [])]

    @staticmethod
    def toggle(st_id: str, user: str) -> bool:
        rows = db.execute(
            "SELECT done FROM subtasks WHERE id = ?", (st_id,))
        if not rows:
            return False
        new = 1 - int(rows[0]["done"] or 0)
        db.execute(
            "UPDATE subtasks SET done = ?, done_at = ? "
            "WHERE id = ?",
            (new, now_iso() if new else "", st_id))
        if new:
            level_system.add_xp(user, "subtask_completed")
            level_system.increment(user, "subtasks_completed")
            with suppress(Exception):
                achievement_system.check(
                    user, "subtask_warrior")
        return True

    @staticmethod
    def delete(st_id: str) -> bool:
        db.execute("DELETE FROM subtasks WHERE id = ?", (st_id,))
        return True

    @staticmethod
    def delete_all_for_task(parent_id: str) -> None:
        db.execute(
            "DELETE FROM subtasks WHERE parent_task_id = ?",
            (parent_id,))

    @staticmethod
    def progress(parent_id: str) -> Tuple[int, int]:
        rows = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done "
            "FROM subtasks WHERE parent_task_id = ?",
            (parent_id,))
        if not rows:
            return 0, 0
        r = rows[0]
        return int(r["done"] or 0), int(r["total"] or 0)


# ═══════════════════════════════════════════════════════════
# TASK MANAGER
# ═══════════════════════════════════════════════════════════
class TaskManager:
    _VALID_PRIORITIES: Final[FrozenSet[str]] = frozenset(
        {"low", "normal", "high", "critical"})
    _VALID_STATUSES: Final[FrozenSet[str]] = frozenset(
        {"pending", "in_progress", "done"})
    _VALID_REPEATS: Final[FrozenSet[str]] = frozenset(
        {"", "daily", "weekly", "monthly"})

    @staticmethod
    def _row_to_task(row: Any) -> Dict[str, Any]:
        task = dict(row)
        try:
            task["tags"] = json.loads(task.get("tags") or "[]")
        except Exception:
            task["tags"] = []
        if not isinstance(task["tags"], list):
            task["tags"] = []
        return task

    @staticmethod
    def _invalidate(task_id: Optional[str] = None) -> None:
        cache.delete("tasks:all")
        cache.delete("tasks:active")
        if task_id:
            cache.delete(f"task:{task_id}")

    @staticmethod
    def create(title: str, author: str,
               priority: str = "normal",
               tags: Optional[List[str]] = None,
               category: str = "Общее",
               description: str = "",
               deadline: Optional[str] = None,
               repeat: str = ""
               ) -> Optional[Dict[str, Any]]:
        if not title or not author:
            return None
        if priority not in TaskManager._VALID_PRIORITIES:
            priority = "normal"
        if repeat not in TaskManager._VALID_REPEATS:
            repeat = ""
        task_id = gen_id()
        r = db.execute(
            "INSERT INTO tasks (id, title, description, priority, "
            "status, author, tags, category, deadline, "
            "created_at, updated_at, repeat) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, description, priority, author,
             json.dumps(tags or [], ensure_ascii=False),
             category, deadline, now_iso(), now_iso(), repeat))
        if r is None:
            return None
        level_system.add_xp(author, "create_task")
        with suppress(Exception):
            achievement_system.check(author, "first_task")
        TaskManager._invalidate(task_id)
        return {"id": task_id, "title": title}

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        c = cache.get("tasks:all")
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM tasks WHERE deleted_at = '' "
            "ORDER BY "
            "CASE priority WHEN 'critical' THEN 0 "
            "WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
            "CASE status WHEN 'pending' THEN 0 "
            "WHEN 'in_progress' THEN 1 ELSE 2 END, "
            "created_at DESC")
        result = [TaskManager._row_to_task(r)
                   for r in (rows or [])]
        cache.set("tasks:all", result, 60)
        return [dict(x) for x in result]

    @staticmethod
    def get_active() -> List[Dict[str, Any]]:
        c = cache.get("tasks:active")
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM tasks WHERE status != 'done' "
            "  AND deleted_at = '' "
            "ORDER BY CASE priority WHEN 'critical' THEN 0 "
            "WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
            "created_at DESC")
        result = [TaskManager._row_to_task(r)
                   for r in (rows or [])]
        cache.set("tasks:active", result, 30)
        return [dict(x) for x in result]

    @staticmethod
    def get_by_id(tid: str) -> Optional[Dict[str, Any]]:
        if not tid:
            return None
        rows = db.execute(
            "SELECT * FROM tasks WHERE id = ?", (tid,))
        return TaskManager._row_to_task(rows[0]) if rows else None

    @staticmethod
    def complete(tid: str, user: str) -> bool:
        if not tid or not user:
            return False
        before = TaskManager.get_by_id(tid)
        if not before:
            return False
        rows = db.execute(
            "SELECT status FROM tasks WHERE id = ?", (tid,))
        if not rows or rows[0]["status"] == "done":
            return False
        r = db.execute(
            "UPDATE tasks SET status = 'done', done_at = ?, "
            "progress = 100, updated_at = ? "
            "WHERE id = ? AND status != 'done'",
            (now_iso(), now_iso(), tid))
        if r is None:
            return False
        after = db.execute(
            "SELECT status FROM tasks WHERE id = ?", (tid,))
        if not after or after[0]["status"] != "done":
            return False
        timer_manager.stop(tid)
        level_system.add_xp(user, "complete_task")
        level_system.increment(user, "tasks_completed")
        with suppress(Exception):
            achievement_system.check(user, "task_master")
            DailyQuests.complete(user, "complete_task")
        db.audit(user, "task.complete", tid)
        if before.get("repeat") in ("daily", "weekly",
                                      "monthly"):
            try:
                TaskManager._create_next_repeat(before, user)
            except Exception:
                pass
        TaskManager._invalidate(tid)
        return True

    @staticmethod
    def _create_next_repeat(task: Dict[str, Any],
                             user: str) -> None:
        rp = task.get("repeat", "")
        if not rp:
            return
        delta = {"daily": timedelta(days=1),
                 "weekly": timedelta(days=7),
                 "monthly": timedelta(days=30)}.get(rp)
        if not delta:
            return
        new_deadline = None
        dl = parse_iso(task.get("deadline") or "")
        if dl:
            new_deadline = (dl + delta).isoformat()
        TaskManager.create(
            title=task.get("title", ""),
            author=user,
            priority=task.get("priority", "normal"),
            tags=task.get("tags") or [],
            category=task.get("category", "Общее"),
            description=task.get("description", ""),
            deadline=new_deadline,
            repeat=rp)

    @staticmethod
    def set_status(tid: str, status: str, user: str) -> bool:
        if status not in TaskManager._VALID_STATUSES:
            return False
        if status == "done":
            return TaskManager.complete(tid, user)
        r = db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? "
            "WHERE id = ?",
            (status, now_iso(), tid))
        if r is None:
            return False
        TaskManager._invalidate(tid)
        return True

    @staticmethod
    def delete(tid: str, hard: bool = False) -> bool:
        if not tid:
            return False
        timer_manager.stop(tid)
        SubtaskManager.delete_all_for_task(tid)
        if hard:
            db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        else:
            db.execute(
                "UPDATE tasks SET deleted_at = ? WHERE id = ?",
                (now_iso(), tid))
        TaskManager._invalidate(tid)
        return True

    @staticmethod
    def restore(tid: str) -> bool:
        try:
            db.execute(
                "UPDATE tasks SET deleted_at = '' WHERE id = ?",
                (tid,))
            TaskManager._invalidate(tid)
            return True
        except Exception:
            return False

    @staticmethod
    def stats_for(user: str) -> Dict[str, int]:
        rows = db.execute(
            "SELECT "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) "
            "  AS done, "
            "SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) "
            "  AS pending, "
            "SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 "
            "END) AS in_progress "
            "FROM tasks WHERE author = ? AND deleted_at = ''",
            (user,))
        if not rows:
            return {"total": 0, "done": 0,
                    "pending": 0, "in_progress": 0}
        r = rows[0]
        return {
            "total": int(r["total"] or 0),
            "done": int(r["done"] or 0),
            "pending": int(r["pending"] or 0),
            "in_progress": int(r["in_progress"] or 0),
        }

    @staticmethod
    def get_by_deadline(user: str) -> List[Dict[str, Any]]:
        rows = db.execute(
            "SELECT * FROM tasks "
            "WHERE author = ? AND deleted_at = '' "
            "  AND deadline != '' AND deadline IS NOT NULL "
            "ORDER BY deadline ASC", (user,))
        return [TaskManager._row_to_task(r)
                 for r in (rows or [])]


# ═══════════════════════════════════════════════════════════
# MARKDOWN RENDERER + FORUM
# ═══════════════════════════════════════════════════════════
class MarkdownRenderer:
    @staticmethod
    def render(text: str) -> str:
        if not text:
            return ""
        s = sanitize_html(text)

        def cb(m: Any) -> str:
            return f'<pre class="md-code">{m.group(1)}</pre>'

        s = re.sub(r"```([\s\S]*?)```", cb, s)
        s = re.sub(r"`([^`]+)`",
                    r'<code class="md-inline">\1</code>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)",
                    r"<i>\1</i>", s)
        s = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", s)
        s = re.sub(r"^### (.+)$", r"<h3>\1</h3>", s, flags=re.M)
        s = re.sub(r"^## (.+)$", r"<h2>\1</h2>", s, flags=re.M)
        s = re.sub(r"^# (.+)$", r"<h1>\1</h1>", s, flags=re.M)
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s\)]+)\)",
            r'<a href="\2" target="_blank">\1</a>', s)
        s = s.replace("\n", "<br>")
        return s


class ForumManager:
    @staticmethod
    def _row_to_post(row: Any) -> Dict[str, Any]:
        post = dict(row)
        for key in ("tags", "liked_by", "comments"):
            try:
                post[key] = json.loads(post.get(key) or "[]")
            except Exception:
                post[key] = []
            if not isinstance(post[key], list):
                post[key] = []
        return post

    @staticmethod
    def _invalidate(post_id: Optional[str] = None) -> None:
        cache.delete("forum:all")
        if post_id:
            cache.delete(f"forum:{post_id}")

    @staticmethod
    def create_post(title: str, content: str, author: str,
                    tags: Optional[List[str]] = None,
                    category: str = "Общее"
                    ) -> Optional[Dict[str, Any]]:
        if not title or not content or not author:
            return None
        post_id = gen_id()
        r = db.execute(
            "INSERT INTO forum_posts "
            "(id, title, content, author, tags, category, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (post_id, title, content, author,
             json.dumps(tags or [], ensure_ascii=False),
             category, now_iso(), now_iso()))
        if r is None:
            return None
        level_system.add_xp(author, "create_post")
        level_system.increment(author, "posts_created")
        with suppress(Exception):
            achievement_system.check(author, "first_forum")
            if re.search(r"(\*\*|##|```)", content):
                level_system.add_xp(author, "markdown_post")
        ForumManager._invalidate(post_id)
        return ForumManager.get_by_id(post_id)

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        c = cache.get("forum:all")
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM forum_posts "
            "WHERE status != 'deleted' "
            "ORDER BY pinned DESC, created_at DESC")
        result = [ForumManager._row_to_post(r)
                   for r in (rows or [])]
        cache.set("forum:all", result, 60)
        return [dict(x) for x in result]

    @staticmethod
    def get_by_id(pid: str) -> Optional[Dict[str, Any]]:
        if not pid:
            return None
        ck = f"forum:{pid}"
        c = cache.get(ck)
        if c is not None:
            return dict(c)
        rows = db.execute(
            "SELECT * FROM forum_posts WHERE id = ?", (pid,))
        if not rows:
            return None
        post = ForumManager._row_to_post(rows[0])
        cache.set(ck, post, 300)
        return dict(post)

    @staticmethod
    def toggle_like(pid: str, user: str) -> Optional[bool]:
        if not pid or not user:
            return None
        rows = db.execute(
            "SELECT liked_by, likes, author FROM forum_posts "
            "WHERE id = ?", (pid,))
        if not rows:
            return None
        row = rows[0]
        author = row["author"]
        likes = int(row["likes"] or 0)
        try:
            liked = json.loads(row["liked_by"] or "[]")
        except Exception:
            liked = []
        if not isinstance(liked, list):
            liked = []
        if user in liked:
            liked.remove(user)
            likes = max(0, likes - 1)
            is_liked = False
        else:
            liked.append(user)
            likes += 1
            is_liked = True
            level_system.add_xp(user, "like_post")
            if author != user:
                level_system.increment(
                    author, "likes_received")
                with suppress(Exception):
                    achievement_system.check(
                        author, "first_like")
        db.execute(
            "UPDATE forum_posts SET liked_by = ?, likes = ? "
            "WHERE id = ?",
            (json.dumps(liked), likes, pid))
        ForumManager._invalidate(pid)
        return is_liked

    @staticmethod
    def add_comment(pid: str, user: str, text: str) -> bool:
        if not pid or not user or not text:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        comments = post.get("comments") or []
        comments.append({
            "id": gen_id(),
            "author": user,
            "text": text[:1000],
            "created_at": now_iso(),
        })
        db.execute(
            "UPDATE forum_posts SET comments = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(comments, ensure_ascii=False),
             now_iso(), pid))
        level_system.add_xp(user, "comment_post")
        level_system.increment(user, "comments_count")
        ForumManager._invalidate(pid)
        return True

    @staticmethod
    def delete_post(pid: str, user: str) -> bool:
        """Удалить пост (только автор)."""
        if not pid or not user:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        if post.get("author") != user:
            return False
        # Мягкое удаление
        db.execute(
            "UPDATE forum_posts SET status = 'deleted', "
            "updated_at = ? WHERE id = ?",
            (now_iso(), pid))
        ForumManager._invalidate(pid)
        db.audit(user, "forum.post.delete", truncate(
            post.get("title", ""), 60))
        return True

    @staticmethod
    def hard_delete_post(pid: str, user: str) -> bool:
        """Жёстко удалить пост (только автор)."""
        if not pid or not user:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        if post.get("author") != user:
            return False
        db.execute("DELETE FROM forum_posts WHERE id = ?", (pid,))
        ForumManager._invalidate(pid)
        db.audit(user, "forum.post.hard_delete")
        return True

    @staticmethod
    def update_post(pid: str, user: str,
                     title: str = None,
                     content: str = None,
                     tags: Optional[List[str]] = None,
                     category: str = None) -> bool:
        """Редактировать пост (только автор)."""
        if not pid or not user:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        if post.get("author") != user:
            return False

        updates: List[str] = []
        params: List[Any] = []
        if title is not None and title.strip():
            updates.append("title = ?")
            params.append(title.strip())
        if content is not None and content.strip():
            updates.append("content = ?")
            params.append(content.strip())
            # Проверка markdown
            if re.search(r"(\*\*|##|```)", content):
                with suppress(Exception):
                    level_system.add_xp(user, "markdown_post")
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if category is not None:
            updates.append("category = ?")
            params.append(category)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(pid)

        result = db.execute(
            f"UPDATE forum_posts SET {', '.join(updates)} "
            f"WHERE id = ?", tuple(params))
        if result is None:
            return False
        ForumManager._invalidate(pid)
        db.audit(user, "forum.post.update", truncate(
            post.get("title", ""), 60))
        return True

    @staticmethod
    def toggle_pin(pid: str, user: str) -> Optional[bool]:
        """Закрепить / открепить пост. Возвращает новое состояние."""
        if not pid or not user:
            return None
        post = ForumManager.get_by_id(pid)
        if not post:
            return None
        if post.get("author") != user:
            return None
        new_pin = 0 if post.get("pinned") else 1
        db.execute(
            "UPDATE forum_posts SET pinned = ?, updated_at = ? "
            "WHERE id = ?",
            (new_pin, now_iso(), pid))
        ForumManager._invalidate(pid)
        return bool(new_pin)

    @staticmethod
    def delete_comment(pid: str, comment_id: str,
                        user: str) -> bool:
        """Удалить комментарий (только его автор)."""
        if not pid or not comment_id or not user:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        comments = post.get("comments") or []
        new_comments = []
        deleted = False
        for cm in comments:
            if (cm.get("id") == comment_id
                    and cm.get("author") == user):
                deleted = True
                continue
            new_comments.append(cm)
        if not deleted:
            return False
        db.execute(
            "UPDATE forum_posts SET comments = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(new_comments, ensure_ascii=False),
             now_iso(), pid))
        ForumManager._invalidate(pid)
        db.audit(user, "forum.comment.delete")
        return True

    @staticmethod
    def update_comment(pid: str, comment_id: str,
                        user: str, text: str) -> bool:
        """Редактировать комментарий (только автор)."""
        if not pid or not comment_id or not user or not text:
            return False
        post = ForumManager.get_by_id(pid)
        if not post:
            return False
        comments = post.get("comments") or []
        updated = False
        for cm in comments:
            if (cm.get("id") == comment_id
                    and cm.get("author") == user):
                cm["text"] = text[:1000]
                cm["edited_at"] = now_iso()
                updated = True
                break
        if not updated:
            return False
        db.execute(
            "UPDATE forum_posts SET comments = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(comments, ensure_ascii=False),
             now_iso(), pid))
        ForumManager._invalidate(pid)
        db.audit(user, "forum.comment.update")
        return True

    @staticmethod
    def render_markdown(text: str) -> str:
        return MarkdownRenderer.render(text)


# ═══════════════════════════════════════════════════════════
# ATTACHMENT MANAGER
# ═══════════════════════════════════════════════════════════
class AttachmentManager:
    _VALID_REF_TYPES: Final[FrozenSet[str]] = frozenset(
        {"note", "task", "forum"})

    @staticmethod
    def _invalidate(ref_type: Optional[str] = None,
                     ref_id: Optional[str] = None) -> None:
        cache.delete("att:all")
        if ref_type and ref_id:
            cache.delete(f"att:{ref_type}:{ref_id}")

    @staticmethod
    def _make_unique_name(original: str) -> str:
        name, ext = os.path.splitext(
            os.path.basename(original))
        safe_name = sanitize_filename(name)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rand = gen_short_id(4)
        return f"{safe_name}_{stamp}_{rand}{ext}"

    @staticmethod
    def _safe_attachment_path(
        filename: str
    ) -> Optional[Path]:
        from nexus.core import ATTACHMENTS_DIR
        try:
            fp = (ATTACHMENTS_DIR / filename).resolve()
            base = ATTACHMENTS_DIR.resolve()
            if fp.parent != base:
                return None
            return fp
        except Exception:
            return None

    @staticmethod
    def save_file(file_path: str, owner: str,
                  ref_type: str, ref_id: str,
                  description: str = ""
                  ) -> Optional[Tuple[str, str]]:
        from nexus.core import (
            ATTACHMENTS_DIR as _ATT,
            MAX_FILE_SIZE as _MFS,
        )
        import shutil
        if not file_path or not owner or not ref_type \
                or not ref_id:
            return None
        if not os.path.exists(file_path):
            return None
        if ref_type not in AttachmentManager._VALID_REF_TYPES:
            return None
        try:
            size = os.path.getsize(file_path)
        except OSError:
            return None
        if size > _MFS:
            return None
        unique = AttachmentManager._make_unique_name(
            file_path)
        dest = _ATT / unique
        try:
            shutil.copy2(file_path, dest)
        except Exception as e:
            logger.error(f"attachment copy: {e}")
            return None
        att_id = gen_id()
        _, ext = os.path.splitext(unique)
        mime = ext[1:].lower() if ext else "file"
        r = db.execute(
            "INSERT INTO attachments "
            "(id, filename, path, owner, size, type, "
            " ref_type, ref_id, created_at, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (att_id, unique, str(dest), owner, size,
             mime, ref_type, ref_id, now_iso(), description))
        if r is None:
            with suppress(Exception):
                dest.unlink()
            return None
        level_system.add_xp(owner, "add_attachment")
        level_system.increment(owner, "attachments_added")
        with suppress(Exception):
            achievement_system.check(owner, "first_attachment")
        AttachmentManager._invalidate(ref_type, ref_id)
        return att_id, unique

    @staticmethod
    def get_by_ref(ref_type: str,
                    ref_id: str) -> List[Dict[str, Any]]:
        ck = f"att:{ref_type}:{ref_id}"
        c = cache.get(ck)
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM attachments "
            "WHERE ref_type = ? AND ref_id = ? "
            "ORDER BY created_at DESC", (ref_type, ref_id))
        result = [dict(r) for r in (rows or [])]
        cache.set(ck, result, 120)
        return [dict(x) for x in result]

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        c = cache.get("att:all")
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM attachments "
            "ORDER BY created_at DESC")
        result = [dict(r) for r in (rows or [])]
        cache.set("att:all", result, 60)
        return [dict(x) for x in result]

    @staticmethod
    def delete(att_id: str, filename: str) -> bool:
        if not att_id:
            return False
        if filename:
            fp = AttachmentManager._safe_attachment_path(
                filename)
            if fp is not None and fp.exists():
                with suppress(Exception):
                    fp.unlink()
        db.execute(
            "DELETE FROM attachments WHERE id = ?", (att_id,))
        AttachmentManager._invalidate()
        return True


# ═══════════════════════════════════════════════════════════
# DRAFT MANAGER
# ═══════════════════════════════════════════════════════════
class DraftManager:
    @staticmethod
    def _path(kind: str, user: str) -> Path:
        safe_kind = sanitize_filename(kind, max_len=32)
        safe_user = sanitize_filename(user, max_len=64)
        return DRAFTS_DIR / f"{safe_user}_{safe_kind}.json"

    @staticmethod
    def save(kind: str, user: str,
             data: Dict[str, Any]) -> bool:
        if not kind or not user or not isinstance(data, dict):
            return False
        path = DraftManager._path(kind, user)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"data": data, "saved_at": now_iso()},
                          f, ensure_ascii=False)
            tmp.replace(path)
            return True
        except Exception:
            return False

    @staticmethod
    def load(kind: str, user: str
              ) -> Optional[Dict[str, Any]]:
        path = DraftManager._path(kind, user)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return None
            data = obj.get("data")
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def clear(kind: str, user: str) -> bool:
        path = DraftManager._path(kind, user)
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════
# CODE HISTORY
# ═══════════════════════════════════════════════════════════
class CodeHistory:
    _MAX_PREVIEW = 5000
    _MAX_ITEMS = 200

    @staticmethod
    def _invalidate(user: Optional[str] = None) -> None:
        if user:
            cache.delete(f"code:user:{user}")

    @staticmethod
    def add(user: str, code: str, output: str,
            success: bool, duration: float,
            stderr: str = "") -> Optional[str]:
        if not user:
            return None
        try:
            entry_id = gen_id()
            db.execute(
                "INSERT INTO code_history "
                "(id, user, code, output, success, duration, "
                "created_at, stderr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, user,
                 truncate(code, CodeHistory._MAX_PREVIEW),
                 truncate(output, CodeHistory._MAX_PREVIEW),
                 1 if success else 0, float(duration),
                 now_iso(),
                 truncate(stderr, CodeHistory._MAX_PREVIEW)))
            CodeHistory._invalidate(user)
            CodeHistory._trim(user)
            return entry_id
        except Exception:
            return None

    @staticmethod
    def _trim(user: str) -> None:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM code_history "
                "WHERE user = ?", (user,))
            count = rows[0]["c"] if rows else 0
            if count > CodeHistory._MAX_ITEMS:
                excess = count - CodeHistory._MAX_ITEMS
                db.execute(
                    "DELETE FROM code_history WHERE id IN ("
                    "  SELECT id FROM code_history "
                    "  WHERE user = ? "
                    "  ORDER BY created_at ASC LIMIT ?)",
                    (user, excess))
        except Exception:
            pass

    @staticmethod
    def get_user_history(user: str, limit: int = 20
                          ) -> List[Dict[str, Any]]:
        limit = max(1, int(limit))
        ck = f"code:user:{user}:{limit}"
        c = cache.get(ck)
        if c is not None:
            return [dict(x) for x in c]
        rows = db.execute(
            "SELECT * FROM code_history WHERE user = ? "
            "ORDER BY created_at DESC LIMIT ?", (user, limit))
        result = [dict(r) for r in (rows or [])]
        cache.set(ck, result, 30)
        return [dict(x) for x in result]

    @staticmethod
    def search(user: str, query: str,
               limit: int = 30) -> List[Dict[str, Any]]:
        try:
            like = f"%{query}%"
            rows = db.execute(
                "SELECT * FROM code_history WHERE user = ? "
                "  AND (LOWER(code) LIKE LOWER(?) "
                "       OR LOWER(output) LIKE LOWER(?)) "
                "ORDER BY created_at DESC LIMIT ?",
                (user, like, like, int(limit)))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    @staticmethod
    def delete(entry_id: str, user: str) -> bool:
        try:
            db.execute(
                "DELETE FROM code_history "
                "WHERE id = ? AND user = ?", (entry_id, user))
            CodeHistory._invalidate(user)
            return True
        except Exception:
            return False

    @staticmethod
    def clear(user: str) -> int:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM code_history "
                "WHERE user = ?", (user,))
            n = rows[0]["c"] if rows else 0
            db.execute(
                "DELETE FROM code_history WHERE user = ?",
                (user,))
            CodeHistory._invalidate(user)
            return int(n or 0)
        except Exception:
            return 0


# ═══════════════════════════════════════════════════════════
# SNIPPET MANAGER
# ═══════════════════════════════════════════════════════════
class SnippetManager:
    _lock = threading.RLock()

    @staticmethod
    def _load() -> List[Dict[str, Any]]:
        data = Store.load("code_snippets/snippets", [])
        return data if isinstance(data, list) else []

    @staticmethod
    def _save(items: List[Dict[str, Any]]) -> bool:
        return Store.save("code_snippets/snippets", items)

    @staticmethod
    def create(name: str, code: str, user: str,
               language: str = "python",
               description: str = "",
               tags: Optional[List[str]] = None
               ) -> Optional[Dict[str, Any]]:
        if not name or not code or not user:
            return None
        with SnippetManager._lock:
            items = SnippetManager._load()
            if len(items) >= SNIPPET_LIMIT:
                items = items[-SNIPPET_LIMIT + 1:]
            snippet = {
                "id": gen_id(),
                "name": name,
                "code": code,
                "language": language,
                "description": description,
                "tags": list(tags or []),
                "author": user,
                "created_at": now_iso(),
                "uses": 0,
            }
            items.append(snippet)
            if not SnippetManager._save(items):
                return None
        return snippet

    @staticmethod
    def get_all(user: Optional[str] = None
                 ) -> List[Dict[str, Any]]:
        items = SnippetManager._load()
        if user:
            items = [s for s in items
                     if s.get("author") == user]
        return sorted(items, key=lambda x: -x.get("uses", 0))

    @staticmethod
    def increment_uses(snippet_id: str) -> None:
        with SnippetManager._lock:
            items = SnippetManager._load()
            for s in items:
                if s.get("id") == snippet_id:
                    s["uses"] = int(s.get("uses", 0)) + 1
                    break
            SnippetManager._save(items)

    @staticmethod
    def delete(snippet_id: str) -> bool:
        with SnippetManager._lock:
            items = SnippetManager._load()
            before = len(items)
            items = [s for s in items
                     if s.get("id") != snippet_id]
            if len(items) == before:
                return False
            return SnippetManager._save(items)

    @staticmethod
    def search(query: str,
                user: Optional[str] = None
                ) -> List[Dict[str, Any]]:
        items = SnippetManager.get_all(user)
        if not query:
            return items
        q = query.lower()
        return [s for s in items
                if q in (s.get("name", "")
                          + s.get("code", "")
                          + s.get("description", "")).lower()]


snippet_manager = SnippetManager()


# ═══════════════════════════════════════════════════════════
# TIMER MANAGER + POMODORO
# ═══════════════════════════════════════════════════════════
class TimerManager:
    _instance: Optional["TimerManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TimerManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._active: Dict[
                    str, Dict[str, Any]] = {}
                cls._instance._rlock = threading.RLock()
                cls._instance._flusher: Optional[
                    threading.Thread] = None
            return cls._instance

    def start(self, task_id: str, user: str) -> bool:
        with self._rlock:
            if task_id in self._active:
                return False
            self._active[task_id] = {
                "started": time.time(),
                "user": user,
                "flushed_at": time.time(),
            }
            self._ensure_flusher()
            return True

    def stop(self, task_id: str) -> Optional[int]:
        with self._rlock:
            entry = self._active.pop(task_id, None)
            if not entry:
                return None
            elapsed = int(time.time() - entry["started"])
            try:
                db.execute(
                    "UPDATE tasks SET time_spent = "
                    "COALESCE(time_spent, 0) + ? WHERE id = ?",
                    (elapsed, task_id))
            except Exception:
                pass
            return elapsed

    def elapsed(self, task_id: str) -> int:
        with self._rlock:
            entry = self._active.get(task_id)
            if not entry:
                return 0
            return int(time.time() - entry["started"])

    def is_running(self, task_id: str) -> bool:
        with self._rlock:
            return task_id in self._active

    def stop_all(self) -> None:
        with self._rlock:
            for tid in list(self._active.keys()):
                self.stop(tid)

    def active_count(self) -> int:
        with self._rlock:
            return len(self._active)

    def get_active(self) -> Dict[str, Dict[str, Any]]:
        with self._rlock:
            return {k: dict(v)
                    for k, v in self._active.items()}

    def _ensure_flusher(self) -> None:
        if self._flusher and self._flusher.is_alive():
            return
        self._flusher = threading.Thread(
            target=self._flush_loop, daemon=True,
            name="timer-flusher")
        self._flusher.start()

    def _flush_loop(self) -> None:
        while True:
            time.sleep(TIMER_FLUSH_INTERVAL)
            try:
                with self._rlock:
                    entries = list(self._active.items())
                for tid, entry in entries:
                    now = time.time()
                    delta = int(now - entry["flushed_at"])
                    if delta > 0:
                        with suppress(Exception):
                            db.execute(
                                "UPDATE tasks SET time_spent = "
                                "COALESCE(time_spent, 0) + ? "
                                "WHERE id = ?",
                                (delta, tid))
                        entry["flushed_at"] = now
            except Exception:
                pass


timer_manager = TimerManager()


class PomodoroManager:
    _instance: Optional["PomodoroManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "PomodoroManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._state: Dict[str, Any] = {
                    "phase": "idle",
                    "started": 0.0,
                    "duration": 0,
                    "task_id": "",
                    "user": "",
                    "sessions_done": 0,
                }
                cls._instance._rlock = threading.RLock()
                cls._instance._on_tick: Optional[Callable] = None
                cls._instance._on_done: Optional[Callable] = None
                cls._instance._ticker: Optional[
                    threading.Thread] = None
                cls._instance._stop_flag = threading.Event()
            return cls._instance

    def set_callbacks(self,
                       on_tick: Optional[Callable] = None,
                       on_done: Optional[Callable] = None
                       ) -> None:
        with self._rlock:
            self._on_tick = on_tick
            self._on_done = on_done

    def start_work(self, user: str, task_id: str = "",
                    minutes: int = POMODORO_WORK_MIN) -> bool:
        with self._rlock:
            if self._state["phase"] != "idle":
                return False
            self._state.update({
                "phase": "work",
                "started": time.time(),
                "duration": max(1, int(minutes)) * 60,
                "task_id": task_id,
                "user": user,
            })
        self._start_ticker()
        return True

    def start_break(self, user: str,
                     long: bool = False) -> bool:
        with self._rlock:
            if self._state["phase"] != "idle":
                return False
            minutes = (POMODORO_LONG_MIN if long
                        else POMODORO_SHORT_MIN)
            self._state.update({
                "phase": "long" if long else "short",
                "started": time.time(),
                "duration": minutes * 60,
                "task_id": "",
                "user": user,
            })
        self._start_ticker()
        return True

    def stop(self) -> None:
        with self._rlock:
            self._state["phase"] = "idle"
            self._state["started"] = 0.0
            self._state["duration"] = 0
        self._stop_flag.set()

    def is_active(self) -> bool:
        with self._rlock:
            return self._state["phase"] != "idle"

    def remaining(self) -> int:
        with self._rlock:
            if self._state["phase"] == "idle":
                return 0
            elapsed = time.time() - self._state["started"]
            rem = int(self._state["duration"] - elapsed)
            return max(0, rem)

    def phase(self) -> str:
        with self._rlock:
            return self._state["phase"]

    def sessions_done(self) -> int:
        with self._rlock:
            return int(self._state.get("sessions_done") or 0)

    def _start_ticker(self) -> None:
        self._stop_flag.clear()
        if self._ticker and self._ticker.is_alive():
            return
        self._ticker = threading.Thread(
            target=self._tick_loop, daemon=True,
            name="pomodoro-ticker")
        self._ticker.start()

    def _tick_loop(self) -> None:
        while not self._stop_flag.is_set():
            with self._rlock:
                if self._state["phase"] == "idle":
                    return
                rem = self.remaining()
                phase = self._state["phase"]
                user = self._state.get("user", "")
                task_id = self._state.get("task_id", "")
            cb = self._on_tick
            if cb:
                with suppress(Exception):
                    cb(phase, rem)
            if rem <= 0:
                with self._rlock:
                    finished_phase = self._state["phase"]
                    if finished_phase == "work":
                        self._state["sessions_done"] = (
                            int(self._state.get(
                                "sessions_done") or 0) + 1)
                    self._state["phase"] = "idle"
                done_cb = self._on_done
                if done_cb:
                    with suppress(Exception):
                        done_cb(finished_phase, user, task_id)
                if finished_phase == "work":
                    with suppress(Exception):
                        level_system.add_xp(
                            user, "pomodoro_done")
                        level_system.increment(
                            user, "pomodoro_sessions")
                        achievement_system.check(
                            user, "pomodoro_master")
                        DailyQuests.complete(
                            user, "pomodoro_done")
                return
            time.sleep(1)


pomodoro = PomodoroManager()


# ═══════════════════════════════════════════════════════════
# AUDIT TRAIL / AUDIT MANAGER
# ═══════════════════════════════════════════════════════════
class AuditTrail:
    _instance: Optional["AuditTrail"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "AuditTrail":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    @staticmethod
    def record(entity_type: str, entity_id: str, action: str,
               before: Optional[Dict[str, Any]] = None,
               after: Optional[Dict[str, Any]] = None,
               user: Optional[str] = None) -> None:
        try:
            diff = AuditTrail._diff(before, after)
            db.execute(
                "INSERT INTO audit_trail "
                "(id, entity_type, entity_id, action, diff, "
                "user, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gen_id(), entity_type, entity_id, action,
                 json.dumps(diff, ensure_ascii=False,
                             default=str),
                 user or "system", now_iso()))
        except Exception as e:
            logger.debug(f"AuditTrail: {e}")

    @staticmethod
    def _diff(before: Optional[Dict[str, Any]],
               after: Optional[Dict[str, Any]]
               ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if before is None and after is None:
            return result
        if before is None:
            return {"after": after}
        if after is None:
            return {"before": before}
        try:
            keys = set(before.keys()) | set(after.keys())
        except Exception:
            return {"error": "invalid input"}
        for k in keys:
            try:
                b = before.get(k)
                a = after.get(k)
                if b != a:
                    result[k] = {"from": b, "to": a}
            except Exception:
                result[k] = {"error": "compare failed"}
        return result

    @staticmethod
    def get_history(entity_type: Optional[str] = None,
                     entity_id: Optional[str] = None,
                     limit: int = 100
                     ) -> List[Dict[str, Any]]:
        try:
            if entity_type and entity_id:
                rows = db.execute(
                    "SELECT * FROM audit_trail "
                    "WHERE entity_type = ? AND entity_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (entity_type, entity_id, limit))
            elif entity_type:
                rows = db.execute(
                    "SELECT * FROM audit_trail "
                    "WHERE entity_type = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (entity_type, limit))
            else:
                rows = db.execute(
                    "SELECT * FROM audit_trail "
                    "ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []


audit_trail = AuditTrail()


class AuditManager:
    @staticmethod
    def get_recent(user: Optional[str] = None,
                    limit: int = 200
                    ) -> List[Dict[str, Any]]:
        limit = max(1, min(5000, int(limit)))
        try:
            if user:
                rows = db.execute(
                    "SELECT * FROM audit_log WHERE user = ? "
                    "ORDER BY id DESC LIMIT ?", (user, limit))
            else:
                rows = db.execute(
                    "SELECT * FROM audit_log "
                    "ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    @staticmethod
    def clear_old(days: int = 90) -> int:
        days = max(1, int(days))
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=days)).isoformat()
        rows = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log "
            "WHERE created_at < ?", (cutoff,))
        count = rows[0]["c"] if rows else 0
        if count:
            db.execute(
                "DELETE FROM audit_log WHERE created_at < ?",
                (cutoff,))
        return count

    @staticmethod
    def export_csv(user: Optional[str],
                    out_path: Path) -> bool:
        import csv
        try:
            entries = AuditManager.get_recent(
                user, limit=10000)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            from nexus.core import safe_csv_cell
            with open(out_path, "w", newline="",
                      encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["created_at", "user", "action",
                            "details"])
                for e in entries:
                    w.writerow([
                        safe_csv_cell(e.get("created_at", "")),
                        safe_csv_cell(e.get("user", "")),
                        safe_csv_cell(e.get("action", "")),
                        safe_csv_cell(e.get("details", "")),
                    ])
            return True
        except Exception as e:
            logger.error(f"Audit export: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# BLOCKCHAIN
# ═══════════════════════════════════════════════════════════
class BlockchainManager:
    _lock = threading.RLock()
    _STORE_NAME = "blockchain"
    _GENESIS_HASH = "0" * 64

    @staticmethod
    def _load() -> List[Dict[str, Any]]:
        data = Store.load(
            BlockchainManager._STORE_NAME, [])
        if not isinstance(data, list):
            return []
        cleaned: List[Dict[str, Any]] = []
        for b in data:
            if not isinstance(b, dict):
                continue
            b.setdefault("version", 1)
            b.setdefault("updated_at", b.get("timestamp", ""))
            b.setdefault("deleted", False)
            b.setdefault("edit_history", [])
            b.setdefault("tags", [])
            b.setdefault("contract", "")
            cleaned.append(b)
        return cleaned

    @staticmethod
    def _save(chain: List[Dict[str, Any]]) -> bool:
        return Store.save(BlockchainManager._STORE_NAME, chain)

    @staticmethod
    def _invalidate_caches() -> None:
        cache.delete("blockchain:all:0")
        cache.delete("blockchain:all:1")
        cache.delete("blockchain:stats")
        cache.delete("blockchain:merkle")

    @staticmethod
    def _compute_hash(block_id: str, prev_hash: str,
                       data: str, timestamp: str,
                       contract: str = "") -> str:
        import hashlib
        raw = (f"{block_id}{prev_hash}{data}"
               f"{timestamp}{contract}")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _recompute_chain(
        chain: List[Dict[str, Any]]
    ) -> None:
        prev = BlockchainManager._GENESIS_HASH
        for block in chain:
            block["prev_hash"] = prev
            block["hash"] = BlockchainManager._compute_hash(
                block.get("id", ""), prev,
                block.get("data", ""),
                block.get("timestamp", ""),
                block.get("contract", ""))
            prev = block["hash"]

    @staticmethod
    def _merkle_root(hashes: List[str]) -> str:
        import hashlib
        if not hashes:
            return BlockchainManager._GENESIS_HASH
        level = list(hashes)
        while len(level) > 1:
            nxt: List[str] = []
            for i in range(0, len(level), 2):
                if i + 1 < len(level):
                    nxt.append(hashlib.sha256(
                        (level[i] + level[i + 1]).encode()
                    ).hexdigest())
                else:
                    nxt.append(hashlib.sha256(
                        (level[i] + level[i]).encode()
                    ).hexdigest())
            level = nxt
        return level[0]

    @staticmethod
    def merkle_root() -> str:
        cached = cache.get("blockchain:merkle")
        if cached:
            return cached
        chain = BlockchainManager._load()
        hashes = [b.get("hash", "")
                   for b in chain if not b.get("deleted")]
        root = BlockchainManager._merkle_root(hashes)
        cache.set("blockchain:merkle", root, 30)
        return root

    @staticmethod
    def add_block(data: str, sender: str,
                  tags: Optional[List[str]] = None,
                  contract: str = ""
                  ) -> Optional[Dict[str, Any]]:
        if not data or not sender:
            return None
        with BlockchainManager._lock:
            chain = BlockchainManager._load()
            prev_hash = (
                chain[-1].get(
                    "hash", BlockchainManager._GENESIS_HASH)
                if chain
                else BlockchainManager._GENESIS_HASH)
            block_id = gen_id()
            ts = now_iso()
            contract_clean = (contract or "").strip()[:1000]
            block_hash = BlockchainManager._compute_hash(
                block_id, prev_hash, data, ts,
                contract_clean)
            block = {
                "id": block_id, "sender": sender, "data": data,
                "timestamp": ts, "updated_at": ts,
                "prev_hash": prev_hash,
                "hash": block_hash,
                "tags": list(tags or []),
                "version": 1,
                "deleted": False,
                "edit_history": [],
                "contract": contract_clean,
            }
            chain.append(block)
            if not BlockchainManager._save(chain):
                return None
        level_system.add_xp(sender, "create_block")
        level_system.increment(sender, "blocks_created")
        with suppress(Exception):
            achievement_system.check(sender, "first_block")
            DailyQuests.complete(sender, "create_block")
        BlockchainManager._invalidate_caches()
        db.audit(sender, "blockchain.add", block_id[:8])
        return block

    @staticmethod
    def get_block(block_id: str
                   ) -> Optional[Dict[str, Any]]:
        if not block_id:
            return None
        for b in BlockchainManager._load():
            if b.get("id") == block_id:
                return dict(b)
        return None

    @staticmethod
    def get_all(include_deleted: bool = False
                 ) -> List[Dict[str, Any]]:
        ck = f"blockchain:all:{int(include_deleted)}"
        cached = cache.get(ck)
        if cached is not None:
            return [dict(x) for x in cached]
        chain = BlockchainManager._load()
        if not include_deleted:
            chain = [b for b in chain
                     if not b.get("deleted")]
        cache.set(ck, chain, 30)
        return [dict(x) for x in chain]

    @staticmethod
    def delete_block(block_id: str, user: str,
                      hard: bool = False
                      ) -> Tuple[bool, str]:
        if not block_id:
            return False, "Пустой ID"
        with BlockchainManager._lock:
            chain = BlockchainManager._load()
            idx = next(
                (i for i, b in enumerate(chain)
                 if b.get("id") == block_id), -1)
            if idx < 0:
                return False, "Блок не найден"
            block = chain[idx]
            if block.get("sender") != user and not hard:
                return False, "Только автор может удалить"
            if hard:
                chain.pop(idx)
                BlockchainManager._recompute_chain(chain)
            else:
                block["deleted"] = True
                block["updated_at"] = now_iso()
            if not BlockchainManager._save(chain):
                return False, "Ошибка сохранения"
        level_system.add_xp(user, "delete_block")
        level_system.increment(user, "blocks_deleted")
        BlockchainManager._invalidate_caches()
        return True, "Удалено" if hard else "Помечено"

    @staticmethod
    def verify_chain() -> Tuple[bool, str]:
        chain = BlockchainManager._load()
        if not chain:
            return True, "Цепь пуста"
        prev = BlockchainManager._GENESIS_HASH
        checked = 0
        skipped = 0
        for i, block in enumerate(chain):
            if block.get("deleted"):
                skipped += 1
                continue
            expected = BlockchainManager._compute_hash(
                block.get("id", ""), prev,
                block.get("data", ""),
                block.get("timestamp", ""),
                block.get("contract", ""))
            if block.get("hash") != expected:
                return False, f"Неверный хэш: блок #{i}"
            prev = block.get("hash", expected)
            checked += 1
        root = BlockchainManager.merkle_root()
        msg = (f"✅ Цепь верифицирована ({checked} активных"
               f"{', ' + str(skipped) + ' удалено' if skipped else ''})"
               f" · Merkle: {root[:12]}…")
        return True, msg

    @staticmethod
    def stats() -> Dict[str, Any]:
        cached = cache.get("blockchain:stats")
        if cached is not None:
            return dict(cached)
        chain = BlockchainManager._load()
        active = [b for b in chain if not b.get("deleted")]
        deleted = [b for b in chain if b.get("deleted")]
        senders = Counter(
            b.get("sender", "?") for b in active)
        tags: Counter = Counter()
        for b in active:
            for t in (b.get("tags") or []):
                tags[t] += 1
        contracts = sum(
            1 for b in active if b.get("contract"))
        result = {
            "total": len(chain),
            "active": len(active),
            "deleted": len(deleted),
            "contracts": contracts,
            "top_senders": senders.most_common(5),
            "top_tags": tags.most_common(10),
            "last_block_at": (active[-1].get("timestamp", "")
                               if active else ""),
            "merkle_root": BlockchainManager.merkle_root(),
        }
        cache.set("blockchain:stats", result, 30)
        return dict(result)

    @staticmethod
    def evaluate_contract(
        block: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        expr = (block.get("contract") or "").strip()
        if not expr:
            return True, "no contract"
        if len(expr) > 500:
            return False, "contract too long"
        allowed = {"amount", "tag", "date", "sender", "count"}
        for bad in ("__", "import", "lambda", "eval", "exec",
                     "open", "[", "]", "{", "}", ";", "\n"):
            if bad in expr:
                return False, f"forbidden token: {bad}"
        names = set(re.findall(
            r"[a-zA-Z_][a-zA-Z0-9_]*", expr))
        extra = names - allowed - {
            "and", "or", "not", "True", "False", "None", "in"}
        if extra:
            return False, f"unknown names: {extra}"
        ctx = dict(context or {})
        ctx.setdefault("amount", 0)
        ctx.setdefault("tag", "")
        ctx.setdefault("date", "")
        ctx.setdefault("sender", block.get("sender", ""))
        ctx.setdefault("count", 1)
        try:
            result = bool(eval(  # noqa: S307
                expr, {"__builtins__": {}}, ctx))
            return result, "ok"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def export_chain(include_history: bool = True
                      ) -> Dict[str, Any]:
        chain = BlockchainManager._load()
        if not include_history:
            for b in chain:
                b.pop("edit_history", None)
        ok, _msg = BlockchainManager.verify_chain()
        return {
            "version": VERSION if False else "1.1.1",
            "exported_at": now_iso(),
            "blocks": chain,
            "verified": ok,
            "merkle_root": BlockchainManager.merkle_root(),
        }

    @staticmethod
    def import_chain(data: Dict[str, Any], user: str,
                      mode: str = "merge"
                      ) -> Tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "Неверный формат"
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            return False, "Нет поля 'blocks'"

        def _normalize(b: Any) -> Optional[Dict[str, Any]]:
            if not isinstance(b, dict):
                return None
            if not b.get("id"):
                return None
            b.setdefault("version", 1)
            b.setdefault("deleted", False)
            b.setdefault("edit_history", [])
            b.setdefault("tags", [])
            b.setdefault("data", "")
            b.setdefault("sender", "imported")
            b.setdefault("contract", "")
            if not b.get("timestamp"):
                b["timestamp"] = now_iso()
            if not b.get("updated_at"):
                b["updated_at"] = b["timestamp"]
            return b

        with BlockchainManager._lock:
            if mode == "replace":
                new_chain: List[Dict[str, Any]] = []
                for b in blocks:
                    nb = _normalize(b)
                    if nb is not None:
                        new_chain.append(nb)
                if not new_chain:
                    return False, "Нет валидных блоков"
                new_chain.sort(
                    key=lambda x: x.get("timestamp", ""))
                BlockchainManager._recompute_chain(new_chain)
                if not BlockchainManager._save(new_chain):
                    return False, "Ошибка сохранения"
                n = len(new_chain)
            else:
                chain = BlockchainManager._load()
                existing = {b.get("id") for b in chain}
                added = 0
                for b in blocks:
                    nb = _normalize(b)
                    if nb is None or nb["id"] in existing:
                        continue
                    chain.append(nb)
                    existing.add(nb["id"])
                    added += 1
                if added == 0:
                    return False, "Нечего импортировать"
                chain.sort(
                    key=lambda x: x.get("timestamp", ""))
                BlockchainManager._recompute_chain(chain)
                if not BlockchainManager._save(chain):
                    return False, "Ошибка сохранения"
                n = added

        db.audit(user, "blockchain.import",
                 f"mode={mode} count={n}")
        BlockchainManager._invalidate_caches()
        return True, f"Импортировано: {n}"


# ═══════════════════════════════════════════════════════════
# TIMELINE
# ═══════════════════════════════════════════════════════════
class Timeline:
    @staticmethod
    def get_today_events(user: str
                          ) -> List[Dict[str, Any]]:
        if not user:
            return []
        today_start = (datetime.now()
                        .replace(hour=0, minute=0, second=0,
                                  microsecond=0)
                        .astimezone(timezone.utc).isoformat())
        events: List[Dict[str, Any]] = []
        try:
            for n in db.execute(
                    "SELECT title, created_at FROM notes "
                    "WHERE author = ? AND created_at > ? "
                    "ORDER BY created_at DESC LIMIT 30",
                    (user, today_start)) or []:
                events.append({"icon": "🧠", "kind": "note",
                               "title": n["title"],
                               "ts": n["created_at"]})
            for t in db.execute(
                    "SELECT title, created_at FROM tasks "
                    "WHERE author = ? AND created_at > ? "
                    "ORDER BY created_at DESC LIMIT 30",
                    (user, today_start)) or []:
                events.append({"icon": "✅", "kind": "task",
                               "title": t["title"],
                               "ts": t["created_at"]})
            rows = db.execute(
                "SELECT COUNT(*) AS c, "
                "MAX(created_at) AS last_ts "
                "FROM code_history "
                "WHERE user = ? AND created_at > ?",
                (user, today_start))
            if rows and rows[0]["c"] > 0:
                events.append({
                    "icon": "💻", "kind": "code",
                    "title": f"Запусков кода: {rows[0]['c']}",
                    "ts": rows[0]["last_ts"] or now_iso()})
            events.sort(key=lambda e: e["ts"], reverse=True)
            return events[:100]
        except Exception:
            return []

    @staticmethod
    def get_heatmap(user: str, weeks: int = 12
                     ) -> Dict[str, int]:
        if not user:
            return {}
        weeks = max(1, min(52, int(weeks)))
        days = weeks * 7
        start = (datetime.now(timezone.utc)
                 - timedelta(days=days)).date()
        try:
            rows = db.execute(
                "SELECT day, count FROM daily_activity "
                "WHERE user = ? AND day >= ?",
                (user, start.isoformat()))
            return {r["day"]: int(r["count"] or 0)
                    for r in (rows or [])}
        except Exception:
            return {}


# ═══════════════════════════════════════════════════════════
# PATTERN ENGINE
# ═══════════════════════════════════════════════════════════
class PatternEngine:
    _instance: Optional["PatternEngine"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "PatternEngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._locks: Dict[
                    str, threading.RLock] = {}
                cls._instance._locks_lock = threading.Lock()
                cls._instance._cache: Dict[str, Any] = {}
                cls._instance._last_compute: Dict[
                    str, float] = {}
            return cls._instance

    def _lock_for(self, user: str) -> threading.RLock:
        with self._locks_lock:
            if user not in self._locks:
                self._locks[user] = threading.RLock()
            return self._locks[user]

    def compute_user_patterns(self, user: str
                               ) -> Dict[str, Any]:
        """
        FIX: вычисления БЕЗ удержания lock. Сначала
        проверяем кэш, считаем (потенциально долго) вне
        lock, потом публикуем под lock.
        """
        lock = self._lock_for(user)
        with lock:
            cached = self._cache.get(user)
            last = self._last_compute.get(user, 0)
            if time.time() - last < 300 and cached:
                return dict(cached)

        # Считаем БЕЗ lock — иначе блокируем других
        patterns: Dict[str, Any] = {
            "hourly_note": [0] * 24,
            "hourly_task": [0] * 24,
            "weekday_note": [0] * 7,
            "favorite_hour": 0,
            "favorite_weekday": 0,
            "streak_days": 0,
            "total_notes": 0,
            "total_tasks": 0,
            "active_days": 0,
        }
        try:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(
                          days=MEMORY_PATTERN_WINDOW_DAYS)
                      ).isoformat()
            for row in db.execute(
                    "SELECT created_at FROM notes "
                    "WHERE author = ? AND created_at > ?",
                    (user, cutoff)) or []:
                dt = parse_iso(row["created_at"])
                if not dt:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_local = dt.astimezone()
                patterns["hourly_note"][dt_local.hour] += 1
                patterns["weekday_note"][
                    dt_local.weekday()] += 1
                patterns["total_notes"] += 1
            for row in db.execute(
                    "SELECT created_at FROM tasks "
                    "WHERE author = ? AND created_at > ?",
                    (user, cutoff)) or []:
                dt = parse_iso(row["created_at"])
                if not dt:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_local = dt.astimezone()
                patterns["hourly_task"][dt_local.hour] += 1
                patterns["total_tasks"] += 1
            rows = db.execute(
                "SELECT COUNT(DISTINCT day) AS d "
                "FROM daily_activity "
                "WHERE user = ? AND day > ?",
                (user, cutoff[:10]))
            if rows:
                patterns["active_days"] = int(
                    rows[0]["d"] or 0)
            if patterns["total_notes"] > 0:
                patterns["favorite_hour"] = int(
                    max(range(24),
                        key=lambda h: patterns["hourly_note"][h]))
                patterns["favorite_weekday"] = int(
                    max(range(7),
                        key=lambda w: patterns[
                            "weekday_note"][w]))
            stats = level_system.get_stats(user)
            patterns["streak_days"] = int(
                stats.get("streak") or 0)
        except Exception as e:
            logger.debug(f"PatternEngine: {e}")

        with lock:
            self._cache[user] = patterns
            self._last_compute[user] = time.time()
        return dict(patterns)

    def invalidate(self, user: Optional[str] = None) -> None:
        if user:
            lock = self._lock_for(user)
            with lock:
                self._cache.pop(user, None)
                self._last_compute.pop(user, None)
        else:
            with self._locks_lock:
                users = list(self._locks.keys())
            for u in users:
                self.invalidate(u)


pattern_engine = PatternEngine()


# ═══════════════════════════════════════════════════════════
# CHATBOT
# ═══════════════════════════════════════════════════════════
class ChatBot:
    _instance: Optional["ChatBot"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "ChatBot":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self._triggers: Dict[str, List[str]] = {
            r"\b(привет|здравствуй|хай|hi|hello)\b":
                ["Привет! 👋", "Здравствуйте! 🌟",
                 "Хай! 😊"],
            r"\b(как дела|как ты)\b":
                ["Отлично! 😊", "Супер! 🚀", "Всё круто! 💪"],
            r"\b(помощь|помоги)\b":
                ["Чем помочь? 🤝", "Спрашивайте! 💡",
                 "Готов! 🎯"],
            r"\b(спасибо|благодар)\b":
                ["Пожалуйста! 😊", "Рад помочь! 🌀"],
            r"\b(nexus|нексус)\b":
                ["NEXUS — твоя цифровая вселенная! 🌀",
                 "Теперь я помню всё! 🧠"],
            r"парол":
                ["Пароли в AES-GCM! 🔐",
                 "VaultSession — единый API! 🔑"],
            r"\b(код|программ)\b":
                ["Код — поэзия! 🐍",
                 "14 шаблонов в Code Studio!"],
            r"\b(qr|куар)\b":
                ["16 стилей и live-preview! 🎨",
                 "Сканер QR — из буфера! 📱"],
            r"\b(pomodoro|помидор)\b":
                ["🍅 25 минут работы, 5 отдыха!"],
            r"\b(quest|квест)\b":
                ["🎯 Ежедневные квесты в меню!"],
        }
        self._fallbacks: List[str] = [
            "Интересно! 🤔", "Отличный вопрос! 💡",
            "Попробуйте: «помощь», «QR», «код» 💡",
        ]
        self._compiled: List[Tuple[Any, List[str]]] = []
        for pattern, responses in self._triggers.items():
            try:
                self._compiled.append(
                    (re.compile(pattern, re.IGNORECASE),
                     responses))
            except re.error:
                continue

    def get_response(
        self, message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not message or not message.strip():
            return "Скажите что-нибудь! 👀"
        m = message.lower().strip()
        if context and any(
                kw in m for kw in ("сколько", "статистик",
                                     "прогресс")):
            stats = context.get("stats", {}) or {}
            return (f"📊 Твоя статистика:\n"
                    f"• Уровень: {stats.get('level', 1)}\n"
                    f"• Опыт: {stats.get('xp', 0)} XP\n"
                    f"• Стрик: {stats.get('streak', 0)} дней\n"
                    f"• QR: {context.get('qr', 0)}\n"
                    f"• Задач: {context.get('tasks', 0)}\n\n"
                    f"Продолжай! 🚀")
        for regex, responses in self._compiled:
            if regex.search(m):
                return random.choice(responses)
        return random.choice(self._fallbacks)


chat_bot = ChatBot()


# ═══════════════════════════════════════════════════════════
# CONTEXT TRACKER
# ═══════════════════════════════════════════════════════════
class ContextTracker:
    _instance: Optional["ContextTracker"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "ContextTracker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._events: deque = deque(
                    maxlen=500)
                cls._instance._lock = threading.RLock()
            return cls._instance

    def record(self, kind: str,
                data: Dict[str, Any]) -> None:
        with suppress(Exception):
            with self._lock:
                self._events.append({
                    "kind": kind,
                    "ts": now_iso(),
                    "data": data,
                })

    def recent(self, kind: Optional[str] = None,
               limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if kind:
            events = [e for e in events
                      if e["kind"] == kind]
        return events[-limit:]


context_tracker = ContextTracker()


# ═══════════════════════════════════════════════════════════
# EMAIL SENDER (SMTP)
# ═══════════════════════════════════════════════════════════
class EmailSender:
    _TIMEOUT = 15

    @staticmethod
    def is_configured(cfg: Optional[Any] = None) -> bool:
        if cfg is None:
            return False
        return bool(cfg.smtp_server and cfg.smtp_user
                    and cfg.smtp_password)

    @staticmethod
    def send_report(cfg: Any, subject: str,
                     body: str) -> Tuple[bool, str]:
        import smtplib
        import ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        if not EmailSender.is_configured(cfg):
            return False, "SMTP не настроен"
        try:
            msg = MIMEMultipart()
            msg["From"] = cfg.smtp_user
            msg["To"] = cfg.smtp_to or cfg.smtp_user
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            port = int(cfg.smtp_port or 587)
            ctx = ssl.create_default_context()
            if port == 465:
                # FIX: SMTPS — SSL с самого начала
                with smtplib.SMTP_SSL(
                    cfg.smtp_server, port,
                    timeout=EmailSender._TIMEOUT,
                    context=ctx,
                ) as s:
                    s.login(cfg.smtp_user, cfg.smtp_password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(
                    cfg.smtp_server, port,
                    timeout=EmailSender._TIMEOUT,
                ) as s:
                    s.ehlo()
                    if port == 587:
                        # FIX: STARTTLS обязателен на 587
                        s.starttls(context=ctx)
                        s.ehlo()
                    else:
                        # Порт 25 — попытка STARTTLS, но
                        # не критично если сервер не умеет
                        try:
                            s.starttls(context=ctx)
                            s.ehlo()
                        except smtplib.SMTPNotSupportedError:
                            pass
                    s.login(cfg.smtp_user, cfg.smtp_password)
                    s.send_message(msg)
            return True, "Отправлено"
        except smtplib.SMTPAuthenticationError:
            return False, "Ошибка авторизации SMTP"
        except smtplib.SMTPException as e:
            return False, f"SMTP: {e}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def send_test(cfg: Any) -> Tuple[bool, str]:
        body = (f"NEXUS test email\n\n"
                f"Сервер: {cfg.smtp_server}:{cfg.smtp_port}\n"
                f"Пользователь: {cfg.smtp_user}\n"
                f"Версия: 1.1.1\n"
                f"Время: {now_iso()}")
        return EmailSender.send_report(
            cfg, "NEXUS · test", body)


# ═══════════════════════════════════════════════════════════
# CODE TEMPLATES
# ═══════════════════════════════════════════════════════════
class CodeTemplates:
    """
    Все шаблоны: только импорты в начале файла.
    НЕ используем __import__ — он запрещён в песочнице.
    """
    BUILTIN: Final[Dict[str, Dict[str, Any]]] = {
        "hello_world": {
            "name": "👋 Привет, мир",
            "icon": "👋",
            "description": "Классический hello world",
            "category": "Основы",
            "code": (
                "# NEXUS Sandbox — Привет, мир!\n"
                "# Все импорты — в самом начале файла\n"
                "import sys\n"
                "\n"
                "print('👋 Привет, NEXUS!')\n"
                "print('Python', sys.version.split()[0])\n"
                "\n"
                "for i in range(1, 6):\n"
                "    print(f'  Строка {i}')\n"
            ),
        },
        "math_playground": {
            "name": "🔢 Математика",
            "icon": "🔢",
            "description": "Числа, формулы, статистика",
            "category": "Наука",
            "code": (
                "# Математическая песочница\n"
                "import math\n"
                "import random\n"
                "import statistics as st\n"
                "\n"
                "random.seed(42)\n"
                "numbers = [random.randint(1, 100)\n"
                "           for _ in range(10)]\n"
                "print('Числа:', numbers)\n"
                "print('Сумма:', sum(numbers))\n"
                "print('Среднее:', st.mean(numbers))\n"
                "print('Медиана:', st.median(numbers))\n"
                "print('Стд.откл:', round(st.stdev(numbers), 2))\n"
                "print('Максимум:', max(numbers))\n"
                "print('Минимум:', min(numbers))\n"
                "\n"
                "print(f'\\nπ = {math.pi:.10f}')\n"
                "print(f'e = {math.e:.10f}')\n"
                "print(f'√2 = {math.sqrt(2):.10f}')\n"
                "print(f'φ = {(1 + math.sqrt(5)) / 2:.10f}')\n"
                "\n"
                "print('\\nПростые числа до 50:')\n"
                "primes = [n for n in range(2, 51)\n"
                "          if all(n % d for d in range(2, n))]\n"
                "print(primes)\n"
            ),
        },
        "string_workshop": {
            "name": "📝 Строки",
            "icon": "📝",
            "description": "Работа со строками",
            "category": "Основы",
            "code": (
                "# Мастерская строк\n"
                "from collections import Counter\n"
                "\n"
                "text = 'NEXUS — твоя цифровая вселенная!'\n"
                "print('Исходный:', text)\n"
                "print('Длина:', len(text))\n"
                "print('Верхний:', text.upper())\n"
                "print('Нижний:', text.lower())\n"
                "print('Перевёрнутый:', text[::-1])\n"
                "print('Слова:', text.split())\n"
                "\n"
                "def is_palindrome(s):\n"
                "    s = ''.join(c.lower() for c in s "
                "if c.isalnum())\n"
                "    return s == s[::-1]\n"
                "\n"
                "for test in ('шалаш', 'привет', "
                "'а роза упала на лапу Азора'):\n"
                "    print(f'{test!r}: {is_palindrome(test)}')\n"
                "\n"
                "words = text.lower().replace('—', '').split()\n"
                "print('\\nТоп-слова:', "
                "Counter(words).most_common(3))\n"
            ),
        },
        "fibonacci": {
            "name": "🌀 Фибоначчи",
            "icon": "🌀",
            "description": "Числа Фибоначчи: 3 способа",
            "category": "Алгоритмы",
            "code": (
                "# Числа Фибоначчи — 3 подхода\n"
                "\n"
                "def fib_recursive(n):\n"
                "    if n < 2:\n"
                "        return n\n"
                "    return fib_recursive(n - 1) + "
                "fib_recursive(n - 2)\n"
                "\n"
                "def fib_iterative(n):\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        a, b = b, a + b\n"
                "    return a\n"
                "\n"
                "def fib_generator(n):\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        yield a\n"
                "        a, b = b, a + b\n"
                "\n"
                "n = 15\n"
                "print(f'Рекурсивно: "
                "{[fib_recursive(i) for i in range(n)]}')\n"
                "print(f'Итеративно: "
                "{[fib_iterative(i) for i in range(n)]}')\n"
                "print(f'Генератор:  {list(fib_generator(n))}')\n"
                "print(f'\\nЗолотое сечение: "
                "{fib_iterative(30) / fib_iterative(29):.10f}')\n"
            ),
        },
        "sorting_demo": {
            "name": "📊 Сортировки",
            "icon": "📊",
            "description": "Сравнение алгоритмов сортировки",
            "category": "Алгоритмы",
            "code": (
                "# Демонстрация сортировок\n"
                "import random\n"
                "\n"
                "def bubble_sort(arr):\n"
                "    a = arr[:]\n"
                "    n = len(a)\n"
                "    for i in range(n):\n"
                "        for j in range(0, n - i - 1):\n"
                "            if a[j] > a[j + 1]:\n"
                "                a[j], a[j + 1] = "
                "a[j + 1], a[j]\n"
                "    return a\n"
                "\n"
                "def quick_sort(arr):\n"
                "    if len(arr) <= 1:\n"
                "        return arr\n"
                "    p = arr[len(arr) // 2]\n"
                "    left = [x for x in arr if x < p]\n"
                "    mid = [x for x in arr if x == p]\n"
                "    right = [x for x in arr if x > p]\n"
                "    return (quick_sort(left) + mid "
                "+ quick_sort(right))\n"
                "\n"
                "random.seed(7)\n"
                "data = [random.randint(1, 100) "
                "for _ in range(15)]\n"
                "print('Исходные:', data)\n"
                "print('Bubble:  ', bubble_sort(data))\n"
                "print('Quick:   ', quick_sort(data))\n"
                "print('Built-in:', sorted(data))\n"
            ),
        },
        "qr_helper": {
            "name": "📱 QR-помощник",
            "icon": "📱",
            "description": "Генерация данных для QR",
            "category": "Утилиты",
            "code": (
                "# Помощник для QR-студии\n"
                "print('=' * 40)\n"
                "print('📶 WIFI QR')\n"
                "print('=' * 40)\n"
                "ssid = 'MyWiFi'\n"
                "password = 'secret123'\n"
                "print(f'WIFI:T:WPA;S:{ssid};P:{password};;')\n"
                "\n"
                "print()\n"
                "print('=' * 40)\n"
                "print('👤 VCARD QR')\n"
                "print('=' * 40)\n"
                "name = 'Иванов Иван'\n"
                "phone = '+79991234567'\n"
                "email = 'ivan@nexus.app'\n"
                "print('BEGIN:VCARD')\n"
                "print('VERSION:3.0')\n"
                "print(f'FN:{name}')\n"
                "print(f'TEL:{phone}')\n"
                "print(f'EMAIL:{email}')\n"
                "print('END:VCARD')\n"
                "\n"
                "print()\n"
                "print('=' * 40)\n"
                "print('🔗 URL QR')\n"
                "print('=' * 40)\n"
                "print('https://nexus.app/user/ivanov')\n"
            ),
        },
        "date_time": {
            "name": "📅 Дата и время",
            "icon": "📅",
            "description": "Работа с датами",
            "category": "Утилиты",
            "code": (
                "# Мастерская дат\n"
                "from datetime import datetime, timedelta\n"
                "\n"
                "now = datetime.now()\n"
                "print('Сейчас:', "
                "now.strftime('%d.%m.%Y %H:%M:%S'))\n"
                "days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']\n"
                "print('День недели:', days[now.weekday()])\n"
                "print('Номер недели:', now.isocalendar()[1])\n"
                "print()\n"
                "\n"
                "new_year = datetime(now.year + 1, 1, 1)\n"
                "delta = new_year - now\n"
                "print(f'До Нового года: {delta.days} дней')\n"
                "\n"
                "print('\\nПопулярные форматы:')\n"
                "for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%H:%M', "
                "'%A, %d %B %Y'):\n"
                "    print(f'  {fmt}: {now.strftime(fmt)}')\n"
            ),
        },
        "json_toolkit": {
            "name": "📋 JSON",
            "icon": "📋",
            "description": "Работа с JSON",
            "category": "Данные",
            "code": (
                "# JSON-инструментарий\n"
                "import json\n"
                "\n"
                "data = {\n"
                "    'user': 'ivan',\n"
                "    'level': 42,\n"
                "    'tags': ['python', 'nexus', 'code'],\n"
                "    'stats': {'qr': 15, 'code': 8, 'tasks': 23}\n"
                "}\n"
                "\n"
                "print('JSON с отступами:')\n"
                "print(json.dumps(data, indent=2, "
                "ensure_ascii=False))\n"
                "\n"
                "compact = json.dumps(data, "
                "separators=(',', ':'))\n"
                "print('\\nКомпактно:', compact)\n"
                "print('Размер:', len(compact), 'байт')\n"
                "\n"
                "parsed = json.loads(compact)\n"
                "print('\\nРаспарсено, ключи:', "
                "list(parsed.keys()))\n"
                "print('Level:', parsed['level'])\n"
                "print('Tags:', ', '.join(parsed['tags']))\n"
            ),
        },
        "hash_demo": {
            "name": "🔐 Хэши",
            "icon": "🔐",
            "description": "SHA, MD5, хэш-функции",
            "category": "Безопасность",
            "code": (
                "# Демонстрация хэш-функций\n"
                "import hashlib\n"
                "import secrets\n"
                "\n"
                "text = 'NEXUS v1.1.1'\n"
                "print('Исходный:', text)\n"
                "print()\n"
                "\n"
                "for algo in ('md5', 'sha1', 'sha256', 'sha512',\n"
                "             'sha3_256', 'blake2b'):\n"
                "    h = hashlib.new(algo)\n"
                "    h.update(text.encode())\n"
                "    print(f'{algo:10} "
                "{h.hexdigest()[:32]}...')\n"
                "\n"
                "print('\\nБезопасная соль:')\n"
                "for _ in range(3):\n"
                "    print('  ', secrets.token_hex(16))\n"
                "\n"
                "print('\\nКонтрольная сумма:')\n"
                "digest = hashlib.sha256(text.encode()).hexdigest()\n"
                "print(f'  {digest}')\n"
                "print(f'  Длина: {len(digest)} символов')\n"
            ),
        },
        "pomodoro_tracker": {
            "name": "🍅 Pomodoro",
            "icon": "🍅",
            "description": "Простой Pomodoro-трекер",
            "category": "Продуктивность",
            "code": (
                "# Pomodoro-трекер (симуляция)\n"
                "sessions = [\n"
                "    {'task': 'Писать код', 'min': 25},\n"
                "    {'task': 'Проверить PR', 'min': 25},\n"
                "    {'task': 'Отдохнуть', 'min': 5},\n"
                "]\n"
                "total_work = 0\n"
                "total_break = 0\n"
                "for s in sessions:\n"
                "    icon = '🍅' if s['min'] >= 25 else '☕'\n"
                "    print(f\"{icon} {s['task']}: "
                "{s['min']} мин\")\n"
                "    if s['min'] >= 25:\n"
                "        total_work += s['min']\n"
                "    else:\n"
                "        total_break += s['min']\n"
                "print()\n"
                "print(f'Работа: {total_work} мин')\n"
                "print(f'Отдых: {total_break} мин')\n"
                "print(f'Продуктивность: "
                "{total_work / (total_work + total_break) * 100:.1f}%')\n"
            ),
        },
        "graph_bfs": {
            "name": "🕸️ Графы (BFS/DFS)",
            "icon": "🕸️",
            "description": "Обход графа в ширину и глубину",
            "category": "Алгоритмы",
            "code": (
                "# BFS и DFS\n"
                "from collections import deque\n"
                "\n"
                "graph = {\n"
                "    'A': ['B', 'C'],\n"
                "    'B': ['A', 'D', 'E'],\n"
                "    'C': ['A', 'F'],\n"
                "    'D': ['B'],\n"
                "    'E': ['B', 'F'],\n"
                "    'F': ['C', 'E'],\n"
                "}\n"
                "\n"
                "def bfs(start):\n"
                "    visited = set()\n"
                "    queue = deque([start])\n"
                "    order = []\n"
                "    while queue:\n"
                "        node = queue.popleft()\n"
                "        if node in visited:\n"
                "            continue\n"
                "        visited.add(node)\n"
                "        order.append(node)\n"
                "        queue.extend(graph.get(node, []))\n"
                "    return order\n"
                "\n"
                "def dfs(start, visited=None):\n"
                "    if visited is None:\n"
                "        visited = set()\n"
                "    visited.add(start)\n"
                "    order = [start]\n"
                "    for n in graph.get(start, []):\n"
                "        if n not in visited:\n"
                "            order.extend(dfs(n, visited))\n"
                "    return order\n"
                "\n"
                "print('BFS от A:', bfs('A'))\n"
                "print('DFS от A:', dfs('A'))\n"
            ),
        },
    }

    @classmethod
    def get_all(cls) -> Dict[str, Dict[str, Any]]:
        return cls.BUILTIN

    @classmethod
    def get_categories(cls) -> List[str]:
        cats: Set[str] = set()
        for t in cls.BUILTIN.values():
            cats.add(t.get("category", "Прочее"))
        return sorted(cats)

    @classmethod
    def get_by_category(cls, category: str
                         ) -> List[Tuple[str, Dict[str, Any]]]:
        return [(k, v) for k, v in cls.BUILTIN.items()
                if v.get("category") == category]

    @classmethod
    def get(cls, template_id: str
             ) -> Optional[Dict[str, Any]]:
        return cls.BUILTIN.get(template_id)

    @classmethod
    def get_names(cls) -> List[str]:
        return [f"{v['icon']} {v['name']}"
                for v in cls.BUILTIN.values()]

    @classmethod
    def get_id_by_name(cls, name: str
                        ) -> Optional[str]:
        for k, v in cls.BUILTIN.items():
            if f"{v['icon']} {v['name']}" == name:
                return k
        return None

    @classmethod
    def get_user_templates(cls) -> List[Dict[str, Any]]:
        try:
            data = Store.load("code_templates/user", [])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @classmethod
    def save_user_template(cls, name: str, code: str,
                            user: str,
                            description: str = ""
                            ) -> Optional[Dict[str, Any]]:
        if not name or not code or not user:
            return None
        try:
            items = cls.get_user_templates()
            tpl = {
                "id": gen_id(),
                "name": name,
                "icon": "⭐",
                "description": description,
                "category": "Мои",
                "code": code,
                "author": user,
                "created_at": now_iso(),
            }
            items.append(tpl)
            if len(items) > 100:
                items = items[-100:]
            Store.save("code_templates/user", items)
            return tpl
        except Exception:
            return None

    @classmethod
    def delete_user_template(cls, template_id: str) -> bool:
        try:
            items = [t for t in cls.get_user_templates()
                     if t.get("id") != template_id]
            Store.save("code_templates/user", items)
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════
# CODE DRAFT MANAGER
# ═══════════════════════════════════════════════════════════
class CodeDraftManager:
    """
    Валидация черновика: если в файле мусор типа "222",
    или пустая строка, или что-то не похожее на код — None.
    """
    _lock = threading.RLock()
    _migrated: bool = False

    @classmethod
    def _is_valid_code(cls, code: str) -> bool:
        if not code:
            return False
        s = code.strip()
        if len(s) < 5:
            return False
        if s.replace(" ", "").isdigit():
            return False
        has_code_marker = any(
            marker in s for marker in (
                "print", "def ", "class ", "import ", "from ",
                "=", "(", ")", "[", "]", "{", "}", ":", "#",
                "return", "if ", "for ", "while ", "try",
            ))
        if not has_code_marker:
            return False
        try:
            compile(s, "<draft>", "exec")
            return True
        except SyntaxError:
            return len(s) > 20 and has_code_marker
        except Exception:
            return False

    @classmethod
    def save(cls, code: str,
             language: str = "python") -> bool:
        from nexus.core import CODE_DRAFT_PATH
        try:
            with cls._lock:
                payload = {
                    "code": code,
                    "language": language,
                    "saved_at": now_iso(),
                }
                tmp = CODE_DRAFT_PATH.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8")
                tmp.replace(CODE_DRAFT_PATH)
                return True
        except Exception:
            return False

    @classmethod
    def load(cls) -> Optional[Dict[str, Any]]:
        from nexus.core import (
            CODE_DRAFT_PATH,
            CODE_DRAFT_LEGACY_PATH,
        )
        cls._migrate_legacy()
        try:
            if not CODE_DRAFT_PATH.exists():
                return None
            raw = CODE_DRAFT_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                with suppress(Exception):
                    CODE_DRAFT_PATH.unlink()
                return None
            code = data.get("code", "")
            if not cls._is_valid_code(code):
                logger.info(
                    "Обнаружен битый черновик — удаляю: "
                    f"{code[:50]!r}")
                with suppress(Exception):
                    CODE_DRAFT_PATH.unlink()
                return None
            return data
        except Exception as e:
            logger.debug(f"draft load failed: {e}")
            with suppress(Exception):
                CODE_DRAFT_PATH.unlink()
            return None

    @classmethod
    def _migrate_legacy(cls) -> None:
        from nexus.core import (
            CODE_DRAFT_PATH,
            CODE_DRAFT_LEGACY_PATH,
        )
        if cls._migrated:
            return
        cls._migrated = True
        try:
            if (CODE_DRAFT_LEGACY_PATH.exists()
                    and not CODE_DRAFT_PATH.exists()):
                legacy = CODE_DRAFT_LEGACY_PATH.read_text(
                    encoding="utf-8", errors="ignore")
                if cls._is_valid_code(legacy):
                    cls.save(legacy, "python")
                    logger.info(
                        "Мигрирован старый code_draft.py "
                        "→ .json")
                with suppress(Exception):
                    CODE_DRAFT_LEGACY_PATH.unlink()
        except Exception as e:
            logger.debug(f"legacy draft migration: {e}")

    @classmethod
    def clear(cls) -> bool:
        from nexus.core import (
            CODE_DRAFT_PATH,
            CODE_DRAFT_LEGACY_PATH,
        )
        try:
            if CODE_DRAFT_PATH.exists():
                CODE_DRAFT_PATH.unlink()
            if CODE_DRAFT_LEGACY_PATH.exists():
                CODE_DRAFT_LEGACY_PATH.unlink()
            return True
        except Exception:
            return False


__all__ = [
    # Level / Achievements / Quests
    "LevelSystem", "level_system",
    "AchievementSystem", "achievement_system",
    "DailyQuests",
    # Domain managers
    "NoteManager", "SubtaskManager", "TaskManager",
    "ForumManager", "MarkdownRenderer",
    "AttachmentManager", "DraftManager",
    "CodeHistory", "SnippetManager", "snippet_manager",
    "TimerManager", "timer_manager",
    "PomodoroManager", "pomodoro",
    "AuditTrail", "audit_trail", "AuditManager",
    "BlockchainManager",
    # Analytics
    "Timeline", "PatternEngine", "pattern_engine",
    "ChatBot", "chat_bot",
    "ContextTracker", "context_tracker",
    # Utils
    "EmailSender",
    "CodeTemplates", "CodeDraftManager",
]