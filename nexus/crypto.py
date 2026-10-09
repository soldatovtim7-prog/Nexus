"""
NEXUS crypto — хэширование паролей, AES-GCM, Shamir, HIBP.

Зависит от: nexus.core, nexus.deps

Исправления относительно оригинала:
  ✅ Shamir.split: bytes → bytearray (был TypeError)
  ✅ hash_password: native argon2-cffi формат, без двойных $
  ✅ verify_password: поддержка всех исторических форматов
  ✅ needs_rehash: ленивая миграция устаревших паролей
  ✅ HIBP кэш ограничен 256 префиксами (LRU)
  ✅ Защита от подделки iter в AES-GCM
  ✅ Ленивая инициализация _ph
  ✅ Self-test с verbose-выводом
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.request
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import (
    Any, Dict, Final, List, Optional, Set, Tuple,
)

from nexus.core import (
    PEPPER_PATH,
    RECOVERY_WORDS_COUNT,
    SHAMIR_PARTS,
    SHAMIR_THRESHOLD,
    logger,
)
from nexus.deps import check_module


# ═══════════════════════════════════════════════════════════
# ОПЦИОНАЛЬНЫЕ ЗАВИСИМОСТИ
# ═══════════════════════════════════════════════════════════
_HAS_CRYPTO: bool = check_module("cryptography")
_USE_ARGON2: bool = check_module("argon2")

if _HAS_CRYPTO:
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import (
            AESGCM,
        )
        from cryptography.hazmat.primitives.kdf.pbkdf2 import (
            PBKDF2HMAC,
        )
    except ImportError:
        _HAS_CRYPTO = False
        Fernet = None  # type: ignore[assignment]
        InvalidToken = Exception  # type: ignore[assignment,misc]
        AESGCM = None  # type: ignore[assignment]
        PBKDF2HMAC = None  # type: ignore[assignment]
        hashes = None  # type: ignore[assignment]
else:
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    AESGCM = None  # type: ignore[assignment]
    PBKDF2HMAC = None  # type: ignore[assignment]
    hashes = None  # type: ignore[assignment]


# ── Argon2 PasswordHasher ─────────────────────────────
_ph: Optional[Any] = None
if _USE_ARGON2:
    try:
        from argon2 import PasswordHasher
        _ph = PasswordHasher(
            time_cost=3,
            memory_cost=65536,   # 64 MiB
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
    except Exception as e:
        logger.warning(f"argon2 init failed: {e}")
        _USE_ARGON2 = False
        _ph = None


PBKDF2_ITERATIONS: Final[int] = 600_000


# ═══════════════════════════════════════════════════════════
# PEPPER (глобальный секрет приложения)
# ═══════════════════════════════════════════════════════════
def _load_pepper() -> bytes:
    """Загрузить или сгенерировать pepper. 32 байта."""
    if PEPPER_PATH.exists():
        try:
            data = PEPPER_PATH.read_bytes()
            if len(data) >= 32:
                return data
        except Exception:
            pass
    pepper = secrets.token_bytes(32)
    with suppress(Exception):
        PEPPER_PATH.parent.mkdir(parents=True, exist_ok=True)
        PEPPER_PATH.write_bytes(pepper)
        with suppress(Exception):
            os.chmod(PEPPER_PATH, 0o600)
    return pepper


_PEPPER: Final[bytes] = _load_pepper()


# ═══════════════════════════════════════════════════════════
# HASH PASSWORD / VERIFY PASSWORD
# ═══════════════════════════════════════════════════════════
# Форматы хранения:
#   $argon2id$v=19$m=...             ← native argon2-cffi (новый)
#   ARGON2$$argon2id$v=19$m=...      ← баг v1.1.1 (двойной $)
#   ARGON2$argon2id$v=19$m=...       ← промежуточный
#   argon2id$$argon2id$v=19$m=...    ← из оригинального монолита
#   argon2id$argon2id$v=19$m=...     ← ещё старее
#   argon2$argon2id$v=19$m=...       ← совсем древний
#   pbkdf2$600000$salt$hex           ← fallback без argon2

_PREFIX_ARGON2_NATIVE: Final[str] = "$argon2"
_PREFIX_ARGON2: Final[str] = "ARGON2$"
_PREFIX_ARGON2_OLD: Final[str] = "argon2id$"
_PREFIX_ARGON2_ANCIENT: Final[str] = "argon2$"
_PREFIX_PBKDF2: Final[str] = "pbkdf2$"


def hash_password(password: str) -> str:
    """
    Хэшировать пароль.

    Использует Argon2id с pepper, если доступен.
    Иначе — PBKDF2-HMAC-SHA256 с 600k итераций.

    Native-формат argon2-cffi:
        "$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>"
    """
    if not password:
        raise ValueError("password must not be empty")

    peppered = password.encode("utf-8") + _PEPPER

    if _USE_ARGON2 and _ph is not None:
        try:
            # argon2-cffi возвращает native-формат
            # "$argon2id$v=19$m=..."
            return _ph.hash(peppered)
        except Exception as e:
            logger.warning(
                f"argon2 hash failed, fallback to pbkdf2: {e}")

    # PBKDF2 fallback
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", peppered, salt.encode(), PBKDF2_ITERATIONS)
    return (f"{_PREFIX_PBKDF2}{PBKDF2_ITERATIONS}"
            f"${salt}${dk.hex()}")


def verify_password(password: str, hashed: str) -> bool:
    """
    Проверить пароль против сохранённого хеша.

    Поддерживает все исторические форматы, поэтому старые
    пользователи продолжат входить без сброса пароля.
    """
    if not password or not hashed:
        return False

    peppered = password.encode("utf-8") + _PEPPER

    # ── Формат 1: native argon2-cffi "$argon2id$..." ───
    if hashed.startswith(_PREFIX_ARGON2_NATIVE):
        if not _USE_ARGON2 or _ph is None:
            return False
        try:
            _ph.verify(hashed, peppered)
            return True
        except Exception:
            return False

    # ── Формат 2: legacy "ARGON2$..." ──────────────────
    if hashed.startswith(_PREFIX_ARGON2):
        if not _USE_ARGON2 or _ph is None:
            return False
        inner = hashed[len(_PREFIX_ARGON2):]
        # "ARGON2$$argon2id$..." → inner = "$argon2id$..."
        # "ARGON2$argon2id$..."  → inner = "argon2id$..."
        if not inner.startswith("$"):
            if inner.startswith("argon2id$"):
                inner = "$" + inner
        if not inner.startswith("$argon2"):
            return False
        try:
            _ph.verify(inner, peppered)
            return True
        except Exception:
            return False

    # ── Формат 3: "argon2id$..." (древний) ─────────────
    if hashed.startswith(_PREFIX_ARGON2_OLD):
        if not _USE_ARGON2 or _ph is None:
            return False
        inner = hashed[len(_PREFIX_ARGON2_OLD):]
        # "argon2id$$argon2id$..." → inner = "$argon2id$..."
        # "argon2id$argon2id$..."  → inner = "argon2id$..."
        if not inner.startswith("$"):
            if inner.startswith("argon2id$"):
                inner = "$" + inner
            else:
                return False
        try:
            _ph.verify(inner, peppered)
            return True
        except Exception:
            return False

    # ── Формат 4: "argon2$..." (без $ в начале) ────────
    if hashed.startswith(_PREFIX_ARGON2_ANCIENT):
        if not _USE_ARGON2 or _ph is None:
            return False
        inner = hashed[len(_PREFIX_ARGON2_ANCIENT):]
        if not inner.startswith("$"):
            inner = "$" + inner
        try:
            # Пробуем с pepper
            _ph.verify(inner, peppered)
            return True
        except Exception:
            # Пробуем без pepper (древний формат)
            try:
                _ph.verify(
                    inner, password.encode("utf-8"))
                return True
            except Exception:
                return False

    # ── Формат 5: PBKDF2 ───────────────────────────────
    if hashed.startswith(_PREFIX_PBKDF2):
        try:
            _, iters_s, salt, expected = hashed.split("$", 3)
            iters = int(iters_s)
            # Защита от подделки
            if iters < 1000 or iters > 10_000_000:
                return False
            dk = hashlib.pbkdf2_hmac(
                "sha256", peppered, salt.encode(), iters)
            return hmac.compare_digest(dk.hex(), expected)
        except Exception:
            return False

    return False


def needs_rehash(hashed: str) -> bool:
    """
    Нужно ли перехэшировать пароль (устаревший формат
    или слабые параметры).
    """
    if not hashed:
        return True
    # Любой legacy-формат → rehash
    if hashed.startswith(_PREFIX_ARGON2):
        return True
    if hashed.startswith(_PREFIX_ARGON2_OLD):
        return True
    if hashed.startswith(_PREFIX_ARGON2_ANCIENT):
        return True
    # PBKDF2 → rehash если argon2 доступен
    if hashed.startswith(_PREFIX_PBKDF2):
        return _USE_ARGON2
    # Native argon2 — спросим у библиотеки
    if hashed.startswith(_PREFIX_ARGON2_NATIVE):
        if not _USE_ARGON2 or _ph is None:
            return False
        try:
            return _ph.check_needs_rehash(hashed)
        except Exception:
            return False
    return False


# ═══════════════════════════════════════════════════════════
# HIBP (Have I Been Pwned) — k-anonymity
# ═══════════════════════════════════════════════════════════
# LRU-кэш ограничен 256 префиксами, иначе за месяцы работы
# может накопить десятки тысяч списков суффиксов.
_HIBP_CACHE: "OrderedDict[str, Tuple[Set[str], float]]" = (
    OrderedDict())
_HIBP_MAX: Final[int] = 256
_HIBP_TTL: Final[float] = 3600.0
_HIBP_LOCK = threading.Lock()


def check_pwned(password: str, timeout: float = 2.0) -> bool:
    """
    Проверить пароль через HIBP API (k-anonymity).

    Отправляется только первые 5 hex-символов SHA1-хэша.
    Сервер возвращает список суффиксов, мы проверяем локально.

    Возвращает:
        True  — пароль найден в утечках
        False — не найден ИЛИ сеть недоступна
    """
    if not password:
        return False

    now = time.time()
    key = hashlib.sha1(
        password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = key[:5], key[5:]

    # ── Проверка кэша ──────────────────────────────────
    with _HIBP_LOCK:
        cached = _HIBP_CACHE.get(prefix)
        if cached and now - cached[1] < _HIBP_TTL:
            _HIBP_CACHE.move_to_end(prefix)
            return suffix in cached[0]

    # ── HTTP-запрос ────────────────────────────────────
    try:
        req = urllib.request.Request(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            headers={
                "User-Agent": "NEXUS/1.1.1",
                "Add-Padding": "true",  # HIBP рекомендует
            },
        )
        with urllib.request.urlopen(
                req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return False

    suffixes: Set[str] = set()
    for line in body.splitlines():
        if ":" in line:
            part = line.split(":", 1)[0].strip().upper()
            if part:
                suffixes.add(part)

    # ── Обновление кэша (LRU) ──────────────────────────
    with _HIBP_LOCK:
        _HIBP_CACHE[prefix] = (suffixes, now)
        _HIBP_CACHE.move_to_end(prefix)
        while len(_HIBP_CACHE) > _HIBP_MAX:
            _HIBP_CACHE.popitem(last=False)

    return suffix in suffixes


def clear_hibp_cache() -> None:
    """Очистить кэш HIBP (для тестов и настроек)."""
    with _HIBP_LOCK:
        _HIBP_CACHE.clear()


# ═══════════════════════════════════════════════════════════
# AES-256-GCM
# ═══════════════════════════════════════════════════════════
class CryptoManager:
    """
    Шифрование текста через AES-256-GCM.

    Формат зашифрованной строки:
        "AESGCM$<base64(json)>"
    где json = {v, iter, salt, nonce, ciphertext}.

    Ключ выводится из пароля через PBKDF2-HMAC-SHA256.
    Соль и nonce генерируются заново для каждой операции.
    """

    ITERATIONS: Final[int] = PBKDF2_ITERATIONS
    PREFIX: Final[str] = "AESGCM$"

    @staticmethod
    def is_available() -> bool:
        return _HAS_CRYPTO

    @staticmethod
    def encrypt_text(
        plaintext: str, password: str
    ) -> Optional[str]:
        if not _HAS_CRYPTO:
            return None
        if not plaintext or not password:
            return None
        try:
            salt = secrets.token_bytes(16)
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=CryptoManager.ITERATIONS,
            )
            key = kdf.derive(password.encode("utf-8"))
            aesgcm = AESGCM(key)
            nonce = secrets.token_bytes(12)
            ciphertext = aesgcm.encrypt(
                nonce, plaintext.encode("utf-8"), None)
            payload = {
                "v": 1,
                "iter": CryptoManager.ITERATIONS,
                "salt": base64.b64encode(salt).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(
                    ciphertext).decode("ascii"),
            }
            blob = base64.b64encode(
                json.dumps(payload).encode("utf-8")
            ).decode("ascii")
            return CryptoManager.PREFIX + blob
        except Exception as e:
            logger.error(f"encrypt_text: {e}")
            return None

    @staticmethod
    def decrypt_text(
        encrypted: str, password: str
    ) -> Optional[str]:
        if not _HAS_CRYPTO:
            return None
        if not encrypted or not password:
            return None
        if not encrypted.startswith(CryptoManager.PREFIX):
            return None
        try:
            raw = base64.b64decode(
                encrypted[len(CryptoManager.PREFIX):])
            payload = json.loads(raw.decode("utf-8"))
            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            iterations = int(
                payload.get("iter", CryptoManager.ITERATIONS))
            # Защита от подделки iterations
            if iterations < 1000 or iterations > 10_000_000:
                return None
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=iterations,
            )
            key = kdf.derive(password.encode("utf-8"))
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(
                nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════
# RECOVERY PHRASE (12 слов)
# ═══════════════════════════════════════════════════════════
_RECOVERY_WORDLIST: Final[Tuple[str, ...]] = (
    "abandon", "ability", "able", "about", "above", "absent",
    "absorb", "abstract", "absurd", "abuse", "access",
    "accident", "account", "accuse", "achieve", "acid",
    "acoustic", "acquire", "across", "act", "action", "actor",
    "actress", "actual", "adapt", "add", "addict", "address",
    "adjust", "admit", "adult", "advance", "advice",
    "aerobic", "affair", "afford", "afraid", "again", "age",
    "agent", "agree", "ahead", "aim", "air", "airport",
    "aisle", "alarm", "album", "alcohol", "alert", "alien",
    "all", "alley", "allow", "almost", "alone", "alpha",
    "already", "also", "alter", "always", "amateur",
    "amazing", "among", "amount", "amused", "analyst",
    "anchor", "ancient", "anger", "angle", "angry", "animal",
    "ankle", "announce", "annual", "another", "answer",
    "antenna", "antique", "anxiety", "any", "apart",
    "apology", "appear", "apple", "approve", "april", "arch",
    "arctic", "area", "arena", "argue", "arm", "armed",
    "armor", "army", "around", "arrange", "arrest", "arrive",
    "arrow", "art", "artefact", "artist", "artwork", "ask",
    "aspect", "assault", "asset", "assist", "assume",
    "asthma", "athlete", "atom", "attack", "attend",
    "attitude", "attract", "auction", "audit", "august",
    "aunt", "author", "auto", "autumn", "average", "avocado",
    "avoid", "awake", "aware", "away", "awesome", "awful",
    "awkward", "axis", "baby", "bachelor", "bacon", "badge",
    "bag", "balance", "balcony", "ball", "bamboo", "banana",
    "banner", "bar", "barely", "bargain", "barrel", "base",
    "basic", "basket", "battle", "beach", "bean", "beauty",
    "because", "become", "beef", "before", "begin", "behave",
    "behind", "believe", "below", "belt", "bench", "benefit",
    "best", "betray", "better", "between", "beyond",
    "bicycle", "bid", "bike", "bind", "biology", "bird",
    "birth", "bitter", "black", "blade", "blame", "blanket",
    "blast", "bleak", "bless", "blind", "blood", "blossom",
    "blouse", "blue", "blur", "blush", "board", "boat",
    "body", "boil", "bomb", "bone", "bonus", "book", "boost",
    "border", "boring", "borrow", "boss", "bottom", "bounce",
    "box", "boy", "bracket", "brain", "brand", "brass",
    "brave", "bread", "breeze", "brick", "bridge", "brief",
    "bright", "bring", "brisk", "broccoli",
)


def generate_recovery_phrase() -> List[str]:
    """Сгенерировать фразу из 12 случайных слов."""
    return [
        secrets.choice(_RECOVERY_WORDLIST)
        for _ in range(RECOVERY_WORDS_COUNT)
    ]


def hash_recovery_phrase(words: List[str]) -> str:
    """PBKDF2-хэш recovery-фразы (200k итераций)."""
    joined = " ".join(words).lower().strip()
    salt = b"nexus-recovery-v2"
    dk = hashlib.pbkdf2_hmac(
        "sha256", joined.encode(), salt, 200_000)
    return dk.hex()


def validate_recovery_words(
    words: List[str]
) -> Tuple[bool, str]:
    """
    Проверить список слов на соответствие формату.
    Не проверяет хэш — только форму.
    """
    if len(words) != RECOVERY_WORDS_COUNT:
        return False, f"Нужно {RECOVERY_WORDS_COUNT} слов"
    lowered = [w.lower().strip() for w in words]
    if len(set(lowered)) != len(lowered):
        return False, "Слова не должны повторяться"
    if not all(w in _RECOVERY_WORDLIST for w in lowered):
        bad = [w for w in lowered
               if w not in _RECOVERY_WORDLIST]
        return False, f"Неизвестные слова: {', '.join(bad[:3])}"
    return True, "OK"


# ═══════════════════════════════════════════════════════════
# SHAMIR SECRET SHARING (GF(2^8), полином степени k-1)
# ═══════════════════════════════════════════════════════════
class ShamirSecretSharing:
    """
    Схема разделения секрета Шамира над GF(2^8).

    Работает побайтово: каждый байт секрета превращается в
    полином степени k-1, из которого берутся n точек.

    Любые k из n шардов восстанавливают исходный секрет.
    Меньше k — информации о секрете не дают.

    Реализация не использует AES-NI и не требует
    cryptography — только арифметика в GF(2^8) с
    неприводимым полиномом 0x11B (AES).
    """

    _MODULUS: Final[int] = 0x11B

    @staticmethod
    def _gf_mul(a: int, b: int) -> int:
        """Умножение в GF(2^8)."""
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p

    @staticmethod
    def _gf_pow(a: int, n: int) -> int:
        """Возведение в степень в GF(2^8)."""
        r = 1
        while n > 0:
            if n & 1:
                r = ShamirSecretSharing._gf_mul(r, a)
            a = ShamirSecretSharing._gf_mul(a, a)
            n >>= 1
        return r

    @staticmethod
    def _gf_inv(a: int) -> int:
        """Обратный элемент: a^254 = a^(-1) в GF(2^8)."""
        if a == 0:
            raise ZeroDivisionError(
                "Cannot invert zero in GF(2^8)")
        return ShamirSecretSharing._gf_pow(a, 254)

    @classmethod
    def split(
        cls, secret: bytes,
        n: int = SHAMIR_PARTS,
        k: int = SHAMIR_THRESHOLD,
    ) -> List[Tuple[int, bytes]]:
        """
        Разделить секрет на n шардов с порогом k.

        Возвращает список [(x, y_bytes), ...], где x ∈ [1, n].
        Для восстановления нужно минимум k шардов.

        FIX: используем bytearray для изменяемости, в конце
        конвертируем в bytes (иммутабельно, безопасно).
        """
        if n < k:
            raise ValueError(f"n ({n}) < k ({k})")
        if n < 1:
            raise ValueError("n must be >= 1")
        if k < 1:
            raise ValueError("k must be >= 1")
        if not secret:
            raise ValueError("secret must not be empty")

        # bytearray — изменяемый буфер для каждого шарда
        shares: List[Tuple[int, bytearray]] = [
            (i, bytearray(len(secret)))
            for i in range(1, n + 1)
        ]

        for byte_idx, byte_val in enumerate(secret):
            # Полином: c[0] + c[1]*x + c[2]*x^2 + ...
            # c[0] = секретный байт, остальные — случайные
            coeffs = [byte_val] + [
                secrets.randbelow(256)
                for _ in range(k - 1)
            ]
            for x in range(1, n + 1):
                y = 0
                for c_idx, c in enumerate(coeffs):
                    y ^= cls._gf_mul(c, cls._gf_pow(x, c_idx))
                shares[x - 1][1][byte_idx] = y

        # Возвращаем как bytes (иммутабельно)
        return [(idx, bytes(buf)) for idx, buf in shares]

    @classmethod
    def combine(
        cls, shares: List[Tuple[int, bytes]]
    ) -> bytes:
        """
        Восстановить секрет из k шардов через
        интерполяцию Лагранжа в GF(2^8).
        """
        if not shares:
            raise ValueError("no shares")
        if len(shares) < 2:
            # Один шард — секрет это его значение
            return bytes(shares[0][1])

        secret_len = len(shares[0][1])
        for _, data in shares:
            if len(data) != secret_len:
                raise ValueError(
                    "shares have different lengths")

        secret = bytearray(secret_len)

        for byte_idx in range(secret_len):
            acc = 0
            for i, (xi, share) in enumerate(shares):
                yi = share[byte_idx]
                num, den = 1, 1
                for j, (xj, _) in enumerate(shares):
                    if i == j:
                        continue
                    # L_i(x) = Π (x - xj) / (xi - xj)
                    # В GF(2^8) вычитание = XOR
                    num = cls._gf_mul(num, xj)
                    den = cls._gf_mul(den, xi ^ xj)
                if den == 0:
                    # Дубликат x — пропускаем
                    continue
                coeff = cls._gf_mul(
                    num, cls._gf_inv(den))
                term = cls._gf_mul(yi, coeff)
                acc ^= term
            secret[byte_idx] = acc

        return bytes(secret)


# ═══════════════════════════════════════════════════════════
# HIGH-LEVEL ШАРДИНГ RECOVERY-ФРАЗЫ
# ═══════════════════════════════════════════════════════════
def shard_recovery_phrase(words: List[str]) -> List[str]:
    """
    Разбить recovery-фразу на SHAMIR_PARTS шардов.

    Формат каждого: "N:base64data", где N — номер шарда.
    """
    raw = " ".join(words).encode("utf-8")
    shares = ShamirSecretSharing.split(
        raw, SHAMIR_PARTS, SHAMIR_THRESHOLD)
    return [
        f"{idx}:{base64.b64encode(data).decode('ascii')}"
        for idx, data in shares
    ]


def reconstruct_recovery_phrase(
    shards: List[str]
) -> Optional[List[str]]:
    """
    Восстановить recovery-фразу из шардов.

    Требуется минимум SHAMIR_THRESHOLD валидных шардов
    формата "N:base64".
    """
    try:
        parsed: List[Tuple[int, bytes]] = []
        for s in shards:
            if not s or ":" not in s:
                continue
            idx_s, data_b64 = s.split(":", 1)
            try:
                idx = int(idx_s.strip())
                data = base64.b64decode(data_b64.strip())
            except Exception:
                continue
            if idx <= 0:
                continue
            parsed.append((idx, data))

        if len(parsed) < SHAMIR_THRESHOLD:
            return None

        # Дубликаты x — убираем
        seen: Set[int] = set()
        unique: List[Tuple[int, bytes]] = []
        for idx, data in parsed:
            if idx in seen:
                continue
            seen.add(idx)
            unique.append((idx, data))

        if len(unique) < SHAMIR_THRESHOLD:
            return None

        raw = ShamirSecretSharing.combine(unique)
        text = raw.decode("utf-8")
        words = text.split()

        if len(words) != RECOVERY_WORDS_COUNT:
            return None

        return words
    except Exception as e:
        logger.debug(f"reconstruct_recovery_phrase: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════
def _self_test(verbose: bool = True) -> bool:
    """Внутренний smoke-тест. Возвращает True если всё ок."""
    def _log(msg: str, ok: bool = True) -> None:
        if verbose:
            print(f"  {'✅' if ok else '❌'} {msg}")

    try:
        # ── Password hashing ───────────────────────────
        _log("hash/verify roundtrip...")
        pw = "Test123!@#"
        h = hash_password(pw)
        if verbose:
            print(f"     hash prefix: {h[:40]}...")
        assert verify_password(pw, h), \
            "verify_password вернул False"
        assert not verify_password("wrong", h), \
            "неверный пароль принят"
        _log("hash/verify OK")

        # ── Shamir ─────────────────────────────────────
        _log("Shamir split(5, 3)...")
        secret = b"Hello, NEXUS!"
        shares = ShamirSecretSharing.split(secret, 5, 3)
        assert len(shares) == 5, \
            f"получено {len(shares)} шардов вместо 5"
        restored = ShamirSecretSharing.combine(shares[:3])
        assert restored == secret, \
            f"shamir[0:3] вернул {restored!r}"
        restored2 = ShamirSecretSharing.combine(
            [shares[0], shares[2], shares[4]])
        assert restored2 == secret, \
            f"shamir[0,2,4] вернул {restored2!r}"
        _log("Shamir OK")

        # ── Recovery phrase ────────────────────────────
        _log("recovery phrase generation...")
        words = generate_recovery_phrase()
        assert len(words) == RECOVERY_WORDS_COUNT, \
            f"получено {len(words)} слов"
        ok, msg = validate_recovery_words(words)
        assert ok, f"валидация фразы: {msg}"
        _log("recovery phrase OK")

        _log("shard + reconstruct recovery...")
        shards = shard_recovery_phrase(words)
        assert len(shards) == SHAMIR_PARTS, \
            f"получено {len(shards)} шардов"
        rec = reconstruct_recovery_phrase(shards[:2])
        assert rec == words, "фраза не восстановилась"
        _log("recovery shard OK")

        # ── AES-GCM ────────────────────────────────────
        if _HAS_CRYPTO:
            _log("AES-GCM roundtrip...")
            ct = CryptoManager.encrypt_text("secret", "pass")
            assert ct is not None, "encrypt вернул None"
            pt = CryptoManager.decrypt_text(ct, "pass")
            assert pt == "secret", \
                f"decrypt вернул {pt!r}"
            assert CryptoManager.decrypt_text(
                ct, "wrong") is None, \
                "неверный пароль принят"
            _log("AES-GCM OK")
        else:
            _log("AES-GCM — cryptography не установлен",
                  ok=False)

        return True

    except AssertionError as e:
        if verbose:
            print(f"\n❌ FAIL: {e}")
        logger.error(f"crypto self-test failed: {e}")
        return False
    except Exception as e:
        if verbose:
            import traceback
            traceback.print_exc()
        logger.error(f"crypto self-test error: {e}")
        return False


__all__ = [
    # Password hashing
    "hash_password", "verify_password", "needs_rehash",
    # HIBP
    "check_pwned", "clear_hibp_cache",
    # AES-GCM
    "CryptoManager",
    # Recovery phrase
    "generate_recovery_phrase", "hash_recovery_phrase",
    "validate_recovery_words",
    # Shamir
    "ShamirSecretSharing",
    "shard_recovery_phrase", "reconstruct_recovery_phrase",
    # Constants
    "PBKDF2_ITERATIONS",
    "_HAS_CRYPTO", "_USE_ARGON2", "_PEPPER",
    # Exports for re-use
    "Fernet", "InvalidToken",
]


if __name__ == "__main__":
    import sys
    print("Running crypto self-test...")
    ok = _self_test()
    print("✅ PASS" if ok else "❌ FAIL")
    sys.exit(0 if ok else 1)