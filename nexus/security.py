"""
NEXUS security — пользователи, сессии, vault паролей,
API-токены. Зависит от: core, storage, crypto, domain.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple

from nexus.core import (
    LOGIN_RATE_LIMIT,
    LOGIN_RATE_WINDOW,
    PASSWORDS_PATH,
    RECOVERY_DIR,
    SESSION_KEY_PATH,
    SESSION_ROLLING_DRIFT,
    SESSION_ROLLING_WINDOW,
    SESSION_SLIDING_THRESHOLD,
    SESSION_TOKEN_PATH,
    SESSION_TOKEN_TTL_DAYS,
    SHAMIR_PARTS,
    SHAMIR_THRESHOLD,
    gen_id,
    gen_short_id,
    get_host_info,
    logger,
    now_iso,
    parse_iso,
    sanitize_filename,
    validate_email,
    validate_password,
    validate_username,
)
# Может не быть hash_recovery_phrase в core — импортируем из crypto
from nexus.crypto import (
    CryptoManager,
    generate_recovery_phrase,
    hash_password,
    hash_recovery_phrase,
    needs_rehash,
    reconstruct_recovery_phrase,
    shard_recovery_phrase,
    validate_recovery_words,
    verify_password,
    _HAS_CRYPTO,
    Fernet,
    InvalidToken,
)
from nexus.storage import db
from nexus.domain import (
    AchievementSystem,
    LevelSystem,
    achievement_system,
    level_system,
)


# ═══════════════════════════════════════════════════════════
# USER MANAGER
# ═══════════════════════════════════════════════════════════
class UserManager:
    @staticmethod
    def create(username: str, email: str,
               password: str
               ) -> Tuple[bool, str]:
        ok, msg = validate_username(username)
        if not ok:
            return False, msg
        if not validate_email(email):
            return False, "Неверный email"
        ok, msg = validate_password(password)
        if not ok:
            return False, msg
        if db.execute(
                "SELECT username FROM users "
                "WHERE username = ? OR email = ?",
                (username, email)):
            return False, "Пользователь уже существует"
        hashed = hash_password(password)
        avatar = __import__("random").choice(
            ["🧠", "🦊", "🐱", "🐉", "🦄", "🌟", "🔥", "💎",
             "👾", "🎯"])
        recovery_words = generate_recovery_phrase()
        recovery_hash = hash_recovery_phrase(recovery_words)
        result = db.execute(
            "INSERT INTO users (username, email, password, avatar, "
            "bio, created_at, last_active, preferences, "
            "recovery_phrase_hash, recovery_phrase_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (username, email, hashed, avatar,
             "Моя память — в NEXUS! 🧠",
             now_iso(), now_iso(), "{}",
             recovery_hash, now_iso()))
        if result is None:
            return False, "Ошибка БД"
        UserManager._save_recovery_for_user(
            username, recovery_words)
        db.audit(username, "register")
        return True, "Регистрация успешна!"

    @staticmethod
    def _save_recovery_for_user(username: str,
                                  words: List[str]) -> None:
        try:
            RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
            path = (RECOVERY_DIR
                    / f"{sanitize_filename(username)}.txt")
            path.write_text(
                "NEXUS Recovery Phrase v2\n"
                + "=" * 40 + "\n"
                + "Пользователь: " + username + "\n"
                + "Дата: " + now_iso() + "\n\n"
                + "Фраза (12 слов, разделены пробелом):\n\n"
                + " ".join(words) + "\n\n"
                + "SHAMIR-части (любые 2 из 3 восстанавливают "
                + "фразу):\n"
                + "\n".join(shard_recovery_phrase(words))
                + "\n\n"
                + "⚠️ СОХРАНИТЕ ЭТУ ФРАЗУ В НАДЁЖНОМ МЕСТЕ!\n",
                encoding="utf-8")
            with suppress(Exception):
                os.chmod(path, 0o600)
        except Exception as e:
            logger.error(f"save_recovery: {e}")

    @staticmethod
    def get_recovery_phrase(username: str
                             ) -> Optional[List[str]]:
        try:
            path = (RECOVERY_DIR
                    / f"{sanitize_filename(username)}.txt")
            if not path.exists():
                return None
            content = path.read_text(encoding="utf-8")
            match = re.search(
                r"Фраза \(12 слов.*?\):\n\n([a-z ]+)",
                content, re.DOTALL)
            if match:
                return match.group(1).strip().split()
        except Exception:
            pass
        return None

    @staticmethod
    def get_recovery_shards(username: str) -> List[str]:
        try:
            path = (RECOVERY_DIR
                    / f"{sanitize_filename(username)}.txt")
            if not path.exists():
                return []
            content = path.read_text(encoding="utf-8")
            match = re.search(
                r"SHAMIR-части[^\n]*:\n"
                r"([^\n]+(?:\n[^\n]+)*)", content)
            if match:
                return [s.strip() for s in
                        match.group(1).split("\n")
                        if ":" in s and s.strip()]
        except Exception:
            pass
        return []

    @staticmethod
    def verify(login: str, password: str
               ) -> Tuple[bool, str, Optional[str]]:
        allowed, wait = db.check_rate_limit(login)
        if not allowed:
            return (False,
                    f"Слишком много попыток. Подождите "
                    f"{wait // 60} мин.", None)
        rows = db.execute(
            "SELECT username, password FROM users "
            "WHERE username = ? OR email = ?", (login, login))
        if not rows:
            db.record_login_attempt(login, False)
            return False, "Пользователь не найден", None
        u = rows[0]
        if verify_password(password, u["password"]):
            db.record_login_attempt(login, True)
            db.execute(
                "UPDATE users SET last_active = ? "
                "WHERE username = ?",
                (now_iso(), u["username"]))
            # Ленивая миграция устаревшего хэша
            if needs_rehash(u["password"]):
                try:
                    new_hash = hash_password(password)
                    db.execute(
                        "UPDATE users SET password = ? "
                        "WHERE username = ?",
                        (new_hash, u["username"]))
                    logger.info(
                        f"Rehashed password for "
                        f"{u['username']}")
                except Exception as e:
                    logger.debug(f"rehash: {e}")
            db.audit(u["username"], "login")
            return True, "Вход выполнен", u["username"]
        db.record_login_attempt(login, False)
        return False, "Неверный пароль", None

    @staticmethod
    def reset_password(username: str, email: str,
                        new_pass: str
                        ) -> Tuple[bool, str]:
        rows = db.execute(
            "SELECT email FROM users WHERE username = ?",
            (username,))
        if not rows:
            return False, "Пользователь не найден"
        if rows[0]["email"] != email:
            return False, "Email не совпадает"
        ok, msg = validate_password(new_pass)
        if not ok:
            return False, msg
        db.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (hash_password(new_pass), username))
        db.audit(username, "password.reset")
        return True, "Пароль изменён"

    @staticmethod
    def reset_with_recovery(username: str,
                              recovery_words: List[str],
                              new_pass: str
                              ) -> Tuple[bool, str]:
        rows = db.execute(
            "SELECT recovery_phrase_hash FROM users "
            "WHERE username = ?", (username,))
        if not rows:
            return False, "Пользователь не найден"
        stored = rows[0]["recovery_phrase_hash"] or ""
        if not stored:
            return False, "Recovery не настроена"
        ok, _ = validate_recovery_words(recovery_words)
        if not ok:
            return False, "Неверный формат фразы"
        given = hash_recovery_phrase(recovery_words)
        if not hmac.compare_digest(stored, given):
            db.audit(username, "recovery.failed")
            return False, "Неверная recovery-фраза"
        ok, msg = validate_password(new_pass)
        if not ok:
            return False, msg
        db.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (hash_password(new_pass), username))
        db.audit(username, "recovery.success")
        return True, "Пароль восстановлен"

    @staticmethod
    def change_password(username: str, old_pass: str,
                         new_pass: str) -> Tuple[bool, str]:
        rows = db.execute(
            "SELECT password FROM users WHERE username = ?",
            (username,))
        if not rows:
            return False, "Пользователь не найден"
        if not verify_password(old_pass, rows[0]["password"]):
            return False, "Неверный текущий пароль"
        ok, msg = validate_password(new_pass)
        if not ok:
            return False, msg
        db.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (hash_password(new_pass), username))
        db.audit(username, "password.change")
        return True, "Пароль изменён"

    @staticmethod
    def exists(username: str) -> bool:
        if not username:
            return False
        return bool(db.execute(
            "SELECT 1 FROM users WHERE username = ?",
            (username,)))

    @staticmethod
    def get(username: str) -> Optional[Dict[str, Any]]:
        rows = db.execute(
            "SELECT username, email, avatar, bio, created_at, "
            "last_active, is_admin, recovery_phrase_created "
            "FROM users WHERE username = ?", (username,))
        return dict(rows[0]) if rows else None


# ═══════════════════════════════════════════════════════════
# ROLLING TOKEN
# ═══════════════════════════════════════════════════════════
class RollingToken:
    WINDOW: Final[int] = SESSION_ROLLING_WINDOW
    DRIFT: Final[int] = SESSION_ROLLING_DRIFT
    _key_cache: Dict[Tuple[str, str], bytes] = {}
    _cache_lock = threading.RLock()

    @staticmethod
    def _master_key() -> bytes:
        if SESSION_KEY_PATH.exists():
            try:
                return SESSION_KEY_PATH.read_bytes()
            except Exception:
                pass
        key = os.urandom(32)
        with suppress(Exception):
            SESSION_KEY_PATH.parent.mkdir(
                parents=True, exist_ok=True)
            SESSION_KEY_PATH.write_bytes(key)
            os.chmod(SESSION_KEY_PATH, 0o600)
        return key

    @classmethod
    def derive_key(cls, username: str,
                    machine_id: str) -> bytes:
        ck = (username, machine_id)
        with cls._cache_lock:
            if ck in cls._key_cache:
                return cls._key_cache[ck]
            master = cls._master_key()
            key = hmac.new(
                master,
                f"rolling:{username}:{machine_id}".encode(),
                hashlib.sha256).digest()
            cls._key_cache[ck] = key
            return key

    @classmethod
    def invalidate_cache(cls) -> None:
        with cls._cache_lock:
            cls._key_cache.clear()

    @classmethod
    def current(cls, key: bytes,
                t: Optional[int] = None) -> str:
        import base64
        t = (t if t is not None
             else int(time.time() // cls.WINDOW))
        mac = hmac.new(key, str(t).encode(),
                        hashlib.sha256).digest()
        return base64.b64encode(mac[:12]).decode()

    @classmethod
    def verify(cls, key: bytes, token: str,
                drift: Optional[int] = None) -> bool:
        drift = drift if drift is not None else cls.DRIFT
        now_t = int(time.time() // cls.WINDOW)
        for offset in range(-drift, drift + 1):
            if hmac.compare_digest(
                    cls.current(key, now_t + offset), token):
                return True
        return False


# ═══════════════════════════════════════════════════════════
# SESSION MANAGER
# ═══════════════════════════════════════════════════════════
class SessionManager:
    _lock = threading.RLock()
    _TTL_DAYS = SESSION_TOKEN_TTL_DAYS
    _last_save: Dict[str, float] = {}

    @staticmethod
    def _get_fernet() -> Optional[Any]:
        if not _HAS_CRYPTO or Fernet is None:
            return None
        with SessionManager._lock:
            if SESSION_KEY_PATH.exists():
                key = SESSION_KEY_PATH.read_bytes()
            else:
                key = Fernet.generate_key()
                SESSION_KEY_PATH.parent.mkdir(
                    parents=True, exist_ok=True)
                SESSION_KEY_PATH.write_bytes(key)
                with suppress(Exception):
                    os.chmod(SESSION_KEY_PATH, 0o600)
            try:
                return Fernet(key)
            except Exception:
                return None

    @staticmethod
    def _primary_mac() -> str:
        try:
            n = uuid.getnode()
            if n >> 40 & 1:
                return ""
            return f"{n:012x}"
        except Exception:
            return ""

    @staticmethod
    def _fingerprint() -> str:
        import base64
        parts = [
            platform.node() or "",
            platform.machine() or "",
            platform.system() or "",
            str(Path.home()),
            sys_platform := __import__("sys").platform,
            SessionManager._primary_mac(),
        ]
        fp = hashlib.sha256(
            "|".join(parts).encode("utf-8")).digest()
        return base64.b64encode(fp).decode("ascii")

    @staticmethod
    def save(username: str, force: bool = False,
             device_label: str = "") -> Optional[str]:
        if not username:
            return None
        now = time.time()
        last = SessionManager._last_save.get(username, 0)
        if not force and now - last < SESSION_SLIDING_THRESHOLD:
            return None
        fernet = SessionManager._get_fernet()
        if fernet is None:
            return None
        with SessionManager._lock:
            try:
                nonce = os.urandom(16).hex()
                machine_id = SessionManager._fingerprint()
                payload = {
                    "username": username,
                    "expires": (
                        datetime.now(timezone.utc)
                        + timedelta(
                            days=SessionManager._TTL_DAYS)
                    ).isoformat(),
                    "created": now_iso(),
                    "host": get_host_info(),
                    "machine_id": machine_id,
                    "device_label": (device_label
                                      or platform.node()),
                    "version": "1.1.1",
                    "nonce": nonce,
                }
                raw = json.dumps(
                    payload,
                    ensure_ascii=False).encode("utf-8")
                token = fernet.encrypt(raw)
                with suppress(Exception):
                    db.execute(
                        "INSERT OR REPLACE INTO sessions "
                        "(token, username, created_at, "
                        " expires_at, ip_address, user_agent, "
                        " machine_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (nonce, username, payload["created"],
                         payload["expires"], machine_id,
                         get_host_info()[:200], machine_id))
                tmp = SESSION_TOKEN_PATH.with_suffix(
                    ".token.tmp")
                tmp.write_bytes(token)
                tmp.replace(SESSION_TOKEN_PATH)
                with suppress(Exception):
                    os.chmod(SESSION_TOKEN_PATH, 0o600)
                SessionManager._last_save[username] = now
                key = RollingToken.derive_key(
                    username, machine_id)
                key_hash = hashlib.sha256(key).hexdigest()
                with suppress(Exception):
                    db.execute(
                        "INSERT OR REPLACE INTO rolling_sessions "
                        "(username, key_hash, created_at, "
                        " last_used) VALUES (?, ?, ?, ?)",
                        (username, key_hash, now_iso(),
                         now_iso()))
                return nonce
            except Exception as e:
                logger.error(f"Session save: {e}")
                return None

    @staticmethod
    def _decode_file_token(
    ) -> Optional[Dict[str, Any]]:
        if not SESSION_TOKEN_PATH.exists():
            return None
        fernet = SessionManager._get_fernet()
        if fernet is None:
            return None
        try:
            token = SESSION_TOKEN_PATH.read_bytes()
            raw = fernet.decrypt(token)
            return json.loads(raw.decode("utf-8"))
        except InvalidToken:
            return None
        except Exception:
            return None

    @staticmethod
    def _validate(payload: Optional[Dict[str, Any]]
                   ) -> Optional[str]:
        if not payload:
            return None
        try:
            expires = datetime.fromisoformat(
                payload["expires"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires:
                return None
        except Exception:
            return None
        username = payload.get("username")
        if not username or not UserManager.exists(username):
            return None
        stored_machine = payload.get("machine_id", "")
        if not stored_machine:
            return None
        current = SessionManager._fingerprint()
        if not hmac.compare_digest(
                stored_machine, current):
            logger.warning(
                f"Session: machine_id mismatch for {username}")
            return None
        return username

    @staticmethod
    def load() -> Optional[str]:
        with SessionManager._lock:
            user = SessionManager._validate(
                SessionManager._decode_file_token())
            if user:
                return user
            try:
                fp = SessionManager._fingerprint()
                rows = db.execute(
                    "SELECT username FROM sessions "
                    "WHERE ip_address = ? AND expires_at > ? "
                    "  AND machine_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (fp, now_iso(), fp))
                if rows:
                    u = rows[0]["username"]
                    if UserManager.exists(u):
                        SessionManager.save(u, force=True)
                        return u
            except Exception:
                pass
            return None

    @staticmethod
    def refresh(username: str) -> bool:
        if not username:
            return False
        return SessionManager.save(
            username, force=True) is not None

    @staticmethod
    def clear(specific_user: Optional[str] = None) -> None:
        with SessionManager._lock:
            with suppress(Exception):
                if SESSION_TOKEN_PATH.exists():
                    SESSION_TOKEN_PATH.unlink()
            try:
                fp = SessionManager._fingerprint()
                if specific_user:
                    db.execute(
                        "DELETE FROM sessions "
                        "WHERE ip_address = ? AND username = ?",
                        (fp, specific_user))
                    db.execute(
                        "DELETE FROM rolling_sessions "
                        "WHERE username = ?", (specific_user,))
                    SessionManager._last_save.pop(
                        specific_user, None)
                else:
                    db.execute(
                        "DELETE FROM sessions "
                        "WHERE ip_address = ?", (fp,))
            except Exception:
                pass
            RollingToken.invalidate_cache()

    @staticmethod
    def list_devices(username: str
                      ) -> List[Dict[str, Any]]:
        try:
            rows = db.execute(
                "SELECT token, created_at, expires_at, "
                "ip_address, user_agent, machine_id "
                "FROM sessions "
                "WHERE username = ? AND expires_at > ? "
                "ORDER BY created_at DESC",
                (username, now_iso()))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    @staticmethod
    def revoke_all(username: str) -> int:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM sessions "
                "WHERE username = ?", (username,))
            n = rows[0]["c"] if rows else 0
            db.execute(
                "DELETE FROM sessions WHERE username = ?",
                (username,))
            db.execute(
                "DELETE FROM rolling_sessions "
                "WHERE username = ?", (username,))
            with SessionManager._lock:
                with suppress(Exception):
                    if SESSION_TOKEN_PATH.exists():
                        SESSION_TOKEN_PATH.unlink()
                SessionManager._last_save.pop(username, None)
            RollingToken.invalidate_cache()
            return int(n or 0)
        except Exception:
            return 0

    @staticmethod
    def get_info() -> Optional[Dict[str, Any]]:
        payload = SessionManager._decode_file_token()
        if not payload:
            return None
        return {
            "username": payload.get("username"),
            "expires": payload.get("expires"),
            "created": payload.get("created"),
            "host": payload.get("host", "unknown"),
            "device_label": payload.get("device_label", ""),
            "machine_id": payload.get("machine_id", ""),
        }


# ═══════════════════════════════════════════════════════════
# PASSWORD VAULT
# ═══════════════════════════════════════════════════════════
class PasswordVault:
    _lock = threading.RLock()

    @staticmethod
    def vault_exists() -> bool:
        return PASSWORDS_PATH.exists()

    @staticmethod
    def load(mp: str
              ) -> Optional[List[Dict[str, Any]]]:
        if not mp:
            return None
        with PasswordVault._lock:
            if not PASSWORDS_PATH.exists():
                return None
            try:
                encrypted = PASSWORDS_PATH.read_text(
                    encoding="utf-8").strip()
            except Exception:
                return None
            if not encrypted:
                return None
            decrypted = CryptoManager.decrypt_text(
                encrypted, mp)
            if decrypted is None:
                return None
            try:
                items = json.loads(decrypted)
            except Exception:
                return None
            if not isinstance(items, list):
                return None
            clean: List[Dict[str, Any]] = []
            for it in items:
                if isinstance(it, dict):
                    it.setdefault("id", gen_id())
                    it.setdefault("created", now_iso())
                    clean.append(it)
            return clean

    @staticmethod
    def create_new(mp: str) -> bool:
        if not mp:
            return False
        return PasswordVault.save(mp, [])

    @staticmethod
    def save(mp: str,
              items: List[Dict[str, Any]]) -> bool:
        if not mp or not isinstance(items, list):
            return False
        with PasswordVault._lock:
            try:
                encrypted = CryptoManager.encrypt_text(
                    json.dumps(items, ensure_ascii=False), mp)
                if encrypted is None:
                    return False
                PASSWORDS_PATH.parent.mkdir(
                    parents=True, exist_ok=True)
                tmp = PASSWORDS_PATH.with_suffix(".enc.tmp")
                tmp.write_text(encrypted, encoding="utf-8")
                tmp.replace(PASSWORDS_PATH)
                with suppress(Exception):
                    os.chmod(PASSWORDS_PATH, 0o600)
                return True
            except Exception as e:
                logger.error(f"PasswordVault.save: {e}")
                return False

    @staticmethod
    def add_entry(
        mp: str, items: List[Dict[str, Any]],
        service: str, login: str, password: str,
        notes: str = "", user: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not service or not password:
            return None
        entry = {
            "id": gen_id(), "service": service,
            "login": login, "password": password,
            "notes": notes, "created": now_iso(),
        }
        items.append(entry)
        if not PasswordVault.save(mp, items):
            items.pop()
            return None
        if user:
            level_system.add_xp(user, "password_saved")
            with suppress(Exception):
                if len(items) >= 10:
                    achievement_system.check(
                        user, "password_keeper")
        return entry

    @staticmethod
    def update_entry(
        mp: str, items: List[Dict[str, Any]],
        entry_id: str, **fields: Any,
    ) -> bool:
        for it in items:
            if it.get("id") == entry_id:
                for k, v in fields.items():
                    if k in ("service", "login",
                              "password", "notes"):
                        it[k] = v
                return PasswordVault.save(mp, items)
        return False

    @staticmethod
    def delete_entry(
        mp: str, items: List[Dict[str, Any]],
        entry_id: str,
    ) -> bool:
        before = len(items)
        items[:] = [i for i in items
                    if i.get("id") != entry_id]
        if len(items) == before:
            return False
        return PasswordVault.save(mp, items)

    @staticmethod
    def search(items: List[Dict[str, Any]],
                query: str) -> List[Dict[str, Any]]:
        if not query:
            return list(items)
        q = query.lower()
        result: List[Dict[str, Any]] = []
        for it in items:
            haystack = (f"{it.get('service', '')} "
                        f"{it.get('login', '')} "
                        f"{it.get('notes', '')}").lower()
            if q in haystack:
                result.append(it)
        return result

    @staticmethod
    def generate_password(length: int = 16,
                          use_symbols: bool = True) -> str:
        import string as _string
        length = max(8, min(64, int(length)))
        alphabet = _string.ascii_letters + _string.digits
        if use_symbols:
            alphabet += "!@#$%^&*()-_=+[]{};:,.?"
        pw = [
            secrets.choice(_string.ascii_lowercase),
            secrets.choice(_string.ascii_uppercase),
            secrets.choice(_string.digits),
        ]
        if use_symbols:
            pw.append(
                secrets.choice("!@#$%^&*()-_=+[]{};:,.?"))
        pw.extend(
            secrets.choice(alphabet)
            for _ in range(length - len(pw)))
        secrets.SystemRandom().shuffle(pw)
        return "".join(pw)


# ═══════════════════════════════════════════════════════════
# VAULT SESSION
# ═══════════════════════════════════════════════════════════
class VaultSession:
    def __init__(self) -> None:
        from nexus.storage import SecureString
        self._SecureString = SecureString
        self._mp: Optional[Any] = None
        self._items: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_activity: float = 0.0
        self._auto_lock_minutes: int = 15

    @property
    def is_unlocked(self) -> bool:
        with self._lock:
            return self._mp is not None and bool(self._mp)

    @property
    def items(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)

    def unlock(self, master_password: str,
                allow_create: bool = False
                ) -> Tuple[bool, str]:
        with self._lock:
            if not master_password:
                return False, "Пустой пароль"
            data = PasswordVault.load(master_password)
            if data is None:
                if (not PasswordVault.vault_exists()
                        and allow_create):
                    if PasswordVault.create_new(
                            master_password):
                        self._mp = self._SecureString(
                            master_password)
                        self._items = []
                        self._last_activity = time.time()
                        return True, "Хранилище создано"
                return False, "Неверный пароль"
            if self._mp is not None:
                self._mp.destroy()
            self._mp = self._SecureString(master_password)
            self._items = list(data)
            self._last_activity = time.time()
            return True, f"Открыто ({len(self._items)} записей)"

    def lock(self) -> None:
        with self._lock:
            if self._mp is not None:
                self._mp.destroy()
            self._mp = None
            self._items = []

    def touch(self) -> None:
        with self._lock:
            self._last_activity = time.time()

    def needs_auto_lock(self) -> bool:
        with self._lock:
            if self._mp is None:
                return False
            return ((time.time() - self._last_activity)
                    > self._auto_lock_minutes * 60)

    def add(self, service: str, login: str, password: str,
            notes: str = "", user: Optional[str] = None
            ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._mp:
                return None
            self.touch()
            with self._mp.open() as mp:
                entry = PasswordVault.add_entry(
                    mp, self._items, service, login,
                    password, notes, user)
            return entry

    def update(self, entry_id: str, **fields: Any) -> bool:
        with self._lock:
            if not self._mp:
                return False
            self.touch()
            with self._mp.open() as mp:
                return PasswordVault.update_entry(
                    mp, self._items, entry_id, **fields)

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            if not self._mp:
                return False
            self.touch()
            with self._mp.open() as mp:
                return PasswordVault.delete_entry(
                    mp, self._items, entry_id)

    def search(self, query: str
                ) -> List[Dict[str, Any]]:
        with self._lock:
            self.touch()
            return PasswordVault.search(self._items, query)


vault_session = VaultSession()


# ═══════════════════════════════════════════════════════════
# API TOKEN MANAGER
# ═══════════════════════════════════════════════════════════
class APITokenManager:
    @staticmethod
    def create(user: str, name: str = "",
                scopes: Optional[List[str]] = None,
                ttl_days: int = 365
                ) -> Tuple[bool, str, Optional[str]]:
        if not user:
            return False, "no user", None
        try:
            raw = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(
                raw.encode()).hexdigest()
            expires = (
                datetime.now(timezone.utc)
                + timedelta(days=max(1, ttl_days))
            ).isoformat()
            db.execute(
                "INSERT INTO api_tokens "
                "(id, user, token_hash, name, scopes, "
                " created_at, expires_at, last_used, revoked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '', 0)",
                (gen_id(), user, token_hash,
                 name or f"token-{gen_short_id(6)}",
                 json.dumps(scopes or ["read"]),
                 now_iso(), expires))
            with suppress(Exception):
                achievement_system.check(
                    user, "api_explorer")
            return True, "OK", raw
        except Exception as e:
            return False, str(e), None

    @staticmethod
    def list_for_user(user: str
                       ) -> List[Dict[str, Any]]:
        try:
            rows = db.execute(
                "SELECT id, name, scopes, created_at, "
                "expires_at, last_used, revoked "
                "FROM api_tokens "
                "WHERE user = ? ORDER BY created_at DESC",
                (user,))
            return [dict(r) for r in (rows or [])]
        except Exception:
            return []

    @staticmethod
    def revoke(token_id: str, user: str) -> bool:
        try:
            db.execute(
                "UPDATE api_tokens SET revoked = 1 "
                "WHERE id = ? AND user = ?", (token_id, user))
            return True
        except Exception:
            return False

    @staticmethod
    def revoke_all(user: str) -> int:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM api_tokens "
                "WHERE user = ? AND revoked = 0", (user,))
            n = rows[0]["c"] if rows else 0
            db.execute(
                "UPDATE api_tokens SET revoked = 1 "
                "WHERE user = ?", (user,))
            return int(n or 0)
        except Exception:
            return 0


# Чтобы избежать случайной ошибки — импорт sys локально
import sys  # noqa: E402


__all__ = [
    "UserManager",
    "RollingToken", "SessionManager",
    "PasswordVault", "VaultSession", "vault_session",
    "APITokenManager",
]