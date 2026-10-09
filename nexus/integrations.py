"""
NEXUS integrations — HTTP API, Webhooks, Plugins, Backup,
Export, HTML reports, Sync, Vaults, WebhookWorker.

Зависит от: core, storage, crypto, domain, security, qr.
Не импортирует UI (customtkinter).

Исправления:
  ✅ _auth: проверки в Python (revoked, expires),
     подробное логирование для диагностики 401
  ✅ APIServer.start: WAL checkpoint перед запуском
"""
from __future__ import annotations

import csv
import hashlib
import html
import importlib.util
import inspect
import io
import ipaddress
import json
import os
import shutil
import socket
import sqlite3
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import (
    Any, Callable, Dict, Final, List, Optional, Set, Tuple,
)

from nexus.core import (
    API_MAX_BODY,
    API_RATE_LIMIT_RPM,
    BACKUP_DIR,
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    EXPORT_DIR,
    HTML_REPORTS_DIR,
    INCREMENTAL_BACKUP_DIR,
    PLUGINS_DIR,
    QR_EXPORT_DIR,
    VAULTS_DIR,
    WEBHOOK_QUEUE_MAX,
    gen_id,
    gen_short_id,
    get_host_info,
    logger,
    now_iso,
    parse_iso,
    sanitize_filename,
    sha256_file,
    truncate,
)
from nexus.storage import (
    Store,
    cache,
    db,
    http_with_retry,
    circuit_breaker,
)
from nexus.domain import (
    BlockchainManager,
    CodeHistory,
    NoteManager,
    TaskManager,
    achievement_system,
    level_system,
)
from nexus.security import (
    APITokenManager,
    SessionManager,
    UserManager,
)
from nexus.qr import (
    qr_gen,
    qr_history,
    DynamicQRManager,
)


# ═══════════════════════════════════════════════════════════
# HTML EXPORTER
# ═══════════════════════════════════════════════════════════
class HTMLExporter:
    @staticmethod
    def _base_style() -> str:
        return """
        body{font-family:'Segoe UI',sans-serif;
        background:linear-gradient(135deg,#0a0a1a,#12122a);
        color:#e8e8ff;padding:40px;margin:0;}
        h1{color:#00e5ff;font-size:32px;margin-bottom:8px;}
        h2{color:#b026ff;font-size:22px;margin-top:30px;
        border-bottom:2px solid #3a3a6a;padding-bottom:6px;}
        .card{background:#1a1a2e;border:2px solid #3a3a6a;
        border-radius:12px;padding:18px;margin:12px 0;}
        .value{font-size:28px;font-weight:bold;color:#00e5ff;}
        .grid{display:grid;
        grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
        gap:12px;}
        .footer{margin-top:40px;text-align:center;
        color:#8888bb;font-size:12px;}
        .md-code{background:#0a0a15;color:#e8e8e8;
        padding:8px;border-radius:6px;font-family:monospace;}
        .md-inline{background:#0a0a15;color:#e8e8e8;
        padding:2px 4px;border-radius:3px;font-family:monospace;}
        """

    @staticmethod
    def export_user_report(user: str, out_path: Path) -> bool:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            s = level_system.get_stats(user)
            tasks = TaskManager.get_active()
            ach_total, ach_unlocked, ach_points = (
                achievement_system.get_progress(user))
            all_ach = achievement_system.get_all(
                include_hidden=False)
            user_ach = set(
                achievement_system.get_user_achievements(user))

            safe_user = html.escape(user)
            parts: List[str] = []
            parts.append('<!DOCTYPE html><html lang="ru">')
            parts.append('<head><meta charset="UTF-8">')
            parts.append(f'<title>NEXUS · {safe_user}</title>')
            parts.append(
                f'<style>{HTMLExporter._base_style()}</style>')
            parts.append('</head><body>')
            parts.append('<h1>🌀 NEXUS Report</h1>')
            parts.append(
                f'<p>Пользователь: <b>{safe_user}</b> · '
                f'{datetime.now():%d.%m.%Y %H:%M} · '
                f'v1.1.1 CORE++ FIX</p>')

            parts.append('<h2>📊 Статистика</h2><div class="grid">')
            for label, value in [
                ("🏆 Уровень", s.get("level", 1)),
                ("⭐ Опыт",
                 f"{s.get('xp', 0)}/{s.get('xp_next', 100)}"),
                ("🔥 Стрик", f"{s.get('streak', 0)}д"),
                ("✅ Активных задач", len(tasks)),
                ("📱 QR создано", s.get("qr_generated", 0)),
                ("💻 Кода запущено", s.get("code_executed", 0)),
                ("🏅 Достижений", f"{ach_unlocked}/{ach_total}"),
                ("⭐ Очков", ach_points),
                ("🍅 Помидоров", s.get("pomodoro_sessions", 0)),
                ("🎯 Квестов", s.get("quests_completed", 0)),
            ]:
                parts.append(
                    f'<div class="card"><div>{html.escape(label)}'
                    f'</div><div class="value">'
                    f'{html.escape(str(value))}</div></div>')
            parts.append('</div>')

            parts.append(
                '<h2>🏅 Достижения</h2><div class="grid">')
            for key, a in all_ach.items():
                unlocked = key in user_ach
                style = "" if unlocked else 'style="opacity:0.35"'
                parts.append(
                    f'<div class="card" {style}>'
                    f'<div><b>'
                    f'{html.escape(a.get("name", ""))}'
                    f'</b></div>'
                    f'<div style="font-size:12px;opacity:0.7;">'
                    f'{html.escape(a.get("desc", ""))}'
                    f'</div></div>')
            parts.append('</div>')

            parts.append(
                f'<div class="footer">Сгенерировано NEXUS '
                f'1.1.1 · {now_iso()}</div>')
            parts.append('</body></html>')

            out_path.write_text("".join(parts), encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"HTML export: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# BACKUP MANAGER
# ═══════════════════════════════════════════════════════════
class BackupManager:
    @staticmethod
    def list_backups() -> List[Dict[str, Any]]:
        try:
            if not BACKUP_DIR.exists():
                return []
            backups = sorted(
                BACKUP_DIR.glob("nexus_*.db"),
                key=lambda p: p.stat().st_mtime,
                reverse=True)
            result: List[Dict[str, Any]] = []
            for b in backups:
                try:
                    st = b.stat()
                    result.append({
                        "path": b, "name": b.name,
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(
                            st.st_mtime)})
                except Exception:
                    pass
            return result
        except Exception:
            return []

    @staticmethod
    def list_incremental() -> List[Dict[str, Any]]:
        try:
            if not INCREMENTAL_BACKUP_DIR.exists():
                return []
            files = sorted(
                INCREMENTAL_BACKUP_DIR.glob("nexus_*.sql"),
                key=lambda p: p.stat().st_mtime,
                reverse=True)
            return [{"path": f, "name": f.name,
                     "size": f.stat().st_size,
                     "mtime": datetime.fromtimestamp(
                         f.stat().st_mtime)}
                    for f in files]
        except Exception:
            return []

    @staticmethod
    def restore_from(backup_path: str) -> Tuple[bool, str]:
        try:
            src = Path(backup_path).resolve()
            if not src.exists():
                return False, "Файл не найден"
            try:
                src.relative_to(BACKUP_DIR.resolve())
            except ValueError:
                return False, "Файл вне директории бэкапов"
            with suppress(Exception):
                if DB_PATH.exists():
                    emergency = (BACKUP_DIR /
                                 f"emergency_"
                                 f"{datetime.now():%Y%m%d_%H%M%S}.db")
                    shutil.copy2(DB_PATH, emergency)
            with suppress(Exception):
                db.close()
            shutil.copy2(src, DB_PATH)
            for ext in ("-wal", "-shm"):
                p = DB_PATH.with_suffix(DB_PATH.suffix + ext)
                if p.exists():
                    with suppress(Exception):
                        p.unlink()
            return True, "Восстановлено"
        except Exception as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════
# VaultManager — управление локальными хранилищами
# ═══════════════════════════════════════════════════════════
class VaultManager:
    """
    Хранилища — это изолированные папки внутри nexus_data/vaults/.
    Soft-delete: удалённые уходят в nexus_data/_trash/ с таймстампом.
    """

    # ── Создание ──────────────────────────────────────
    @staticmethod
    def create_vault(name: str) -> Tuple[bool, str]:
        try:
            from nexus.core import VAULTS_DIR
            VAULTS_DIR.mkdir(parents=True, exist_ok=True)
            path = VAULTS_DIR / name
            if path.exists():
                return False, f"Хранилище «{name}» уже существует"
            path.mkdir()
            # README, чтобы папка не была пустой
            try:
                (path / "README.txt").write_text(
                    f"Vault: {name}\n"
                    f"Created: {datetime.now():%Y-%m-%d %H:%M:%S}\n",
                    encoding="utf-8")
            except Exception:
                pass
            logger.info(f"Vault created: {name}")
            return True, f"Создано: {name}"
        except Exception as e:
            logger.error(f"create_vault: {e}")
            return False, str(e)

    # ── Список ────────────────────────────────────────
    @staticmethod
    def list_vaults() -> List[Dict[str, Any]]:
        try:
            from nexus.core import VAULTS_DIR
            if not VAULTS_DIR.exists():
                return []
            items: List[Dict[str, Any]] = []
            for p in sorted(VAULTS_DIR.iterdir()):
                if not p.is_dir():
                    continue
                size = 0
                try:
                    size = sum(
                        f.stat().st_size for f in p.rglob("*")
                        if f.is_file())
                except Exception:
                    pass
                items.append({
                    "name": p.name,
                    "path": str(p),
                    "size": size,
                    "mtime": datetime.fromtimestamp(
                        p.stat().st_mtime),
                })
            return items
        except Exception as e:
            logger.error(f"list_vaults: {e}")
            return []

    # ── Переименование ────────────────────────────────
    @staticmethod
    def rename_vault(old_name: str,
                     new_name: str) -> Tuple[bool, str]:
        try:
            from nexus.core import VAULTS_DIR
            src = VAULTS_DIR / old_name
            dst = VAULTS_DIR / new_name
            if not src.exists():
                return False, "Исходное хранилище не найдено"
            if dst.exists():
                return False, "Хранилище с таким именем уже есть"
            src.rename(dst)
            logger.info(f"Vault renamed: {old_name} → {new_name}")
            return True, new_name
        except Exception as e:
            logger.error(f"rename_vault: {e}")
            return False, str(e)

    # ── Удаление (soft) ───────────────────────────────
    @staticmethod
    def delete_vault(name: str,
                     soft: bool = True) -> Tuple[bool, str]:
        try:
            import shutil
            from nexus.core import VAULTS_DIR, TRASH_DIR
            src = VAULTS_DIR / name
            if not src.exists():
                return False, "Не найдено"
            if soft:
                TRASH_DIR.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dst = TRASH_DIR / f"{name}_{ts}"
                shutil.move(str(src), str(dst))
            else:
                shutil.rmtree(src)
            logger.info(f"Vault deleted: {name} (soft={soft})")
            return True, "OK"
        except Exception as e:
            logger.error(f"delete_vault: {e}")
            return False, str(e)

    # ── Восстановление из _trash ──────────────────────
    @staticmethod
    def restore_vault(name: str) -> Tuple[bool, str]:
        try:
            import shutil
            from nexus.core import VAULTS_DIR, TRASH_DIR
            if not TRASH_DIR.exists():
                return False, "Корзина пуста"
            candidates = sorted(
                [p for p in TRASH_DIR.iterdir()
                 if p.is_dir() and p.name.startswith(f"{name}_")],
                key=lambda p: p.stat().st_mtime,
                reverse=True)
            if not candidates:
                return False, "Не найдено в корзине"
            src = candidates[0]
            dst = VAULTS_DIR / name
            if dst.exists():
                return False, "Хранилище с таким именем уже есть"
            shutil.move(str(src), str(dst))
            return True, "OK"
        except Exception as e:
            logger.error(f"restore_vault: {e}")
            return False, str(e)

    # ── Экспорт в ZIP ─────────────────────────────────
    @staticmethod
    def export_vault(name: str,
                     out_path: str) -> Tuple[bool, str]:
        try:
            import zipfile
            from nexus.core import VAULTS_DIR
            src = VAULTS_DIR / name
            if not src.exists():
                return False, "Не найдено"
            with zipfile.ZipFile(out_path, "w",
                                  zipfile.ZIP_DEFLATED) as z:
                for f in src.rglob("*"):
                    if f.is_file():
                        z.write(f, f.relative_to(src.parent))
            logger.info(f"Vault exported: {name} → {out_path}")
            return True, "OK"
        except Exception as e:
            logger.error(f"export_vault: {e}")
            return False, str(e)


    # ── Загрузка файла ────────────────────────────────
    @staticmethod
    def upload_file(vault_name: str,
                     src_path: str) -> Tuple[bool, str]:
        """
        Скопировать файл в хранилище.

        Args:
            vault_name: имя хранилища (папки внутри VAULTS_DIR)
            src_path:   путь к файлу-источнику

        Returns:
            (True, "имя файла → vault") при успехе
            (False, "причина") при ошибке
        """
        try:
            import shutil
            from nexus.core import VAULTS_DIR

            src = Path(src_path)
            if not src.exists():
                return False, "Файл не найден"
            if not src.is_file():
                return False, "Это не файл (папки пока не поддержаны)"

            dst_dir = VAULTS_DIR / vault_name
            if not dst_dir.exists():
                return False, f"Хранилище «{vault_name}» не найдено"

            dst = dst_dir / src.name
            # Если файл с таким именем уже есть —
            # добавляем таймстамп к имени
            if dst.exists():
                stem = src.stem
                suffix = src.suffix
                ts = datetime.now().strftime("%H%M%S")
                dst = dst_dir / f"{stem}_{ts}{suffix}"

            # copy2 сохраняет метаданные (mtime, права)
            shutil.copy2(str(src), str(dst))

            logger.info(
                f"Vault upload: {src.name} → {vault_name}")
            return True, f"{src.name} → {vault_name}"

        except PermissionError:
            return False, "Нет прав на запись"
        except Exception as e:
            logger.error(f"upload_file: {e}")
            return False, str(e)

    # ── Извлечение ZIP ────────────────────────────────
    @staticmethod
    def extract_zip(vault_name: str,
                     zip_path: str) -> Tuple[bool, str]:
        """
        Распаковать ZIP-архив в хранилище.

        Безопасно: защита от Zip Slip (path traversal).
        Структура папок внутри архива сохраняется.
        """
        try:
            import zipfile
            from nexus.core import VAULTS_DIR

            zp = Path(zip_path)
            if not zp.exists():
                return False, "ZIP-файл не найден"
            if not zp.is_file():
                return False, "Это не файл"

            if not zipfile.is_zipfile(str(zp)):
                return False, "Не похоже на ZIP-архив"

            dst_dir = VAULTS_DIR / vault_name
            if not dst_dir.exists():
                return False, f"Хранилище «{vault_name}» не найдено"

            extracted = 0
            skipped = 0

            with zipfile.ZipFile(str(zp), "r") as z:
                for member in z.namelist():
                    # ── Защита от Zip Slip ──────────
                    # Злоумышленник может положить в архив
                    # файл с именем "../../etc/passwd".
                    # Проверяем, что итоговый путь
                    # остаётся внутри dst_dir.
                    target = (dst_dir / member).resolve()
                    dst_resolved = dst_dir.resolve()

                    if not str(target).startswith(
                            str(dst_resolved)):
                        logger.warning(
                            f"ZIP slip заблокирован: {member}")
                        skipped += 1
                        continue

                    z.extract(member, str(dst_dir))
                    extracted += 1

            msg = f"Извлечено: {extracted}"
            if skipped:
                msg += f" (пропущено: {skipped})"
            logger.info(
                f"Vault extract_zip: {zp.name} → "
                f"{vault_name} ({extracted} файлов)")
            return True, msg

        except zipfile.BadZipFile:
            return False, "Повреждённый ZIP"
        except Exception as e:
            logger.error(f"extract_zip: {e}")
            return False, str(e)

    # ── Список файлов ─────────────────────────────────
    @staticmethod
    def list_files(vault_name: str) -> List[Dict[str, Any]]:
        """
        Вернуть список файлов внутри хранилища
        (рекурсивно, включая подпапки).

        Returns:
            [{"name": "rel/path.png",
              "size": 12345,
              "mtime": datetime}, ...]
        """
        try:
            from nexus.core import VAULTS_DIR

            root = VAULTS_DIR / vault_name
            if not root.exists():
                return []

            files: List[Dict[str, Any]] = []
            for f in sorted(root.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(root)
                st = f.stat()
                files.append({
                    "name": str(rel),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime),
                })
            return files
        except Exception as e:
            logger.error(f"list_files: {e}")
            return []

    # ── Удаление файла (soft — в _trash) ──────────────
    @staticmethod
    def delete_file(vault_name: str,
                     file_name: str) -> Tuple[bool, str]:
        """
        Удалить файл из хранилища.
        Soft-delete: файл уходит в nexus_data/_trash/
        с префиксом vault__<vault>__<имя>_<ts>.
        """
        try:
            import shutil
            from nexus.core import VAULTS_DIR, TRASH_DIR

            src = VAULTS_DIR / vault_name / file_name
            if not src.exists():
                return False, "Файл не найден"
            if not src.is_file():
                return False, "Это не файл"

            TRASH_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = Path(file_name).name  # без ../..
            dst = (TRASH_DIR /
                   f"{vault_name}__{safe_name}_{ts}")

            shutil.move(str(src), str(dst))
            logger.info(
                f"Vault delete_file: {vault_name}/{file_name}")
            return True, "OK"

        except Exception as e:
            logger.error(f"delete_file: {e}")
            return False, str(e)


# ═══════════════════════════════════════════════════════════
# SYNC MANAGER (LWW)
# ═══════════════════════════════════════════════════════════
class SyncManager:
    _SYNC_FILES = ("blockchain.json", "webhooks.json",
                    "code_snippets/snippets.json")

    @staticmethod
    def sync_to_local_folder(target_dir: str
                              ) -> Tuple[bool, str]:
        if not target_dir:
            return False, "Пустой путь"
        try:
            target = Path(target_dir).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = target / f"nexus_sync_{ts}"
            dst.mkdir(exist_ok=True)
            if DB_PATH.exists():
                shutil.copy2(DB_PATH, dst / "nexus.db")
            else:
                return False, "БД не найдена"
            copied = 1
            for name in SyncManager._SYNC_FILES:
                src = DATA_DIR / name
                if src.exists():
                    shutil.copy2(src, dst / Path(name).name)
                    copied += 1
            meta = {
                "version": "1.1.1",
                "synced_at": now_iso(),
                "host": get_host_info(),
                "files": copied,
                "db_sha256": sha256_file(DB_PATH),
                "lww_policy": "last-write-wins",
            }
            (dst / "sync_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8")
            return True, f"Выгружено ({copied} файлов)"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def sync_from_local_folder(src_dir: str
                                 ) -> Tuple[bool, str]:
        if not src_dir:
            return False, "Пустой путь"
        try:
            src = Path(src_dir).expanduser().resolve()
            if not src.exists() or not src.is_dir():
                return False, "Папка не найдена"
            db_file = src / "nexus.db"
            if not db_file.exists():
                return False, "nexus.db не найден"
            local_mtime = (DB_PATH.stat().st_mtime
                           if DB_PATH.exists() else 0)
            remote_mtime = db_file.stat().st_mtime
            conflicts: List[str] = []
            if remote_mtime < local_mtime:
                conflicts.append(
                    f"remote старше на "
                    f"{int(local_mtime - remote_mtime)}с")
            with suppress(Exception):
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                if DB_PATH.exists():
                    emergency = (BACKUP_DIR /
                                 f"emergency_sync_"
                                 f"{datetime.now():%Y%m%d_%H%M%S}.db")
                    shutil.copy2(DB_PATH, emergency)
            with suppress(Exception):
                db.close()
            shutil.copy2(db_file, DB_PATH)
            for ext in ("-wal", "-shm"):
                p = DB_PATH.with_suffix(DB_PATH.suffix + ext)
                if p.exists():
                    with suppress(Exception):
                        p.unlink()
            if conflicts:
                return True, ("Загружено с конфликтами: "
                              + "; ".join(conflicts))
            return True, "Загружено. Перезапустите."
        except Exception as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════
# WEBHOOK MANAGER
# ═══════════════════════════════════════════════════════════
class WebhookManager:
    _ALLOWED_PORTS: Final[Set[int]] = {
        80, 443, 8000, 8080, 8443, 3000, 5000}
    _RETRY_BACKOFF: Final[Tuple[int, ...]] = (
        5, 30, 120, 600, 3600)

    @staticmethod
    def _resolve_and_check(url: str
                            ) -> Tuple[bool, Optional[str]]:
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False, None
            host = (parsed.hostname or "").lower()
            if not host or host == "localhost":
                return False, None
            port = parsed.port
            if (port is not None
                    and port not in WebhookManager._ALLOWED_PORTS):
                return False, None
            try:
                infos = socket.getaddrinfo(host, port or 443)
            except Exception:
                return False, None
            resolved: Optional[str] = None
            for entry in infos:
                try:
                    ip = ipaddress.ip_address(entry[4][0])
                except ValueError:
                    return False, None
                if (ip.is_private or ip.is_loopback
                        or ip.is_link_local or ip.is_reserved
                        or ip.is_multicast
                        or ip.is_unspecified):
                    return False, None
                if resolved is None:
                    resolved = str(ip)
            return (resolved is not None), resolved
        except Exception:
            return False, None

    @staticmethod
    def get_webhooks() -> List[Dict[str, Any]]:
        try:
            data = Store.load("webhooks", [])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @staticmethod
    def add_webhook(url: str) -> Tuple[bool, str]:
        try:
            if not url.startswith(("http://", "https://")):
                return False, "URL должен начинаться с http(s)://"
            safe, resolved = WebhookManager._resolve_and_check(url)
            if not safe:
                return False, "URL ведёт во внутреннюю сеть (SSRF)"
            webhooks = WebhookManager.get_webhooks()
            if len(webhooks) >= 20:
                return False, "Максимум 20 webhooks"
            webhooks.append({
                "id": gen_id(), "url": url,
                "resolved_ip": resolved,
                "events": ["all"],
                "created": now_iso(),
                "enabled": True,
                "deliveries": 0,
                "failures": 0,
            })
            Store.save("webhooks", webhooks)
            return True, "OK"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def remove_webhook(wh_id: str) -> bool:
        try:
            webhooks = [w for w in WebhookManager.get_webhooks()
                        if w.get("id") != wh_id]
            Store.save("webhooks", webhooks)
            return True
        except Exception:
            return False

    @staticmethod
    def _enqueue(wh_id: str, url: str, event: str,
                  payload: Dict[str, Any],
                  last_error: str = "") -> None:
        try:
            rows = db.execute(
                "SELECT COUNT(*) AS c FROM webhook_queue "
                "WHERE webhook_id = ? AND delivered_at = ''",
                (wh_id,))
            if rows and rows[0]["c"] \
                    and rows[0]["c"] > WEBHOOK_QUEUE_MAX:
                return
            next_try = (
                datetime.now(timezone.utc)
                + timedelta(
                    seconds=WebhookManager._RETRY_BACKOFF[0])
            ).isoformat()
            db.execute(
                "INSERT INTO webhook_queue "
                "(id, webhook_id, url, event, payload, "
                " attempts, next_try_at, last_error, "
                " created_at, delivered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                (gen_id(), wh_id, url, event,
                 json.dumps(payload, ensure_ascii=False),
                 0, next_try, last_error[:200], now_iso()))
        except Exception as e:
            logger.debug(f"webhook enqueue: {e}")

    @staticmethod
    def _process_queue() -> None:
        try:
            now = now_iso()
            rows = db.execute(
                "SELECT * FROM webhook_queue "
                "WHERE delivered_at = '' AND next_try_at <= ? "
                "ORDER BY next_try_at ASC LIMIT 20", (now,))
            for row in (rows or []):
                rid = row["id"]
                url = row["url"]
                try:
                    payload = json.loads(row["payload"] or "{}")
                except Exception:
                    payload = {}
                event = row["event"]
                attempts = int(row["attempts"] or 0)
                safe, _ = WebhookManager._resolve_and_check(url)
                if not safe:
                    db.execute(
                        "UPDATE webhook_queue SET "
                        "delivered_at = ?, last_error = ? "
                        "WHERE id = ?",
                        (now_iso(), "SSRF blocked", rid))
                    continue
                try:
                    data = json.dumps({
                        "event": event,
                        "timestamp": now_iso(),
                        "payload": payload,
                        "nexus_version": "1.1.1",
                        "attempt": attempts + 1,
                    }, ensure_ascii=False).encode("utf-8")
                    ok, status, err = http_with_retry(
                        url=url, data=data,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "NEXUS/1.1.1"},
                        method="POST", timeout=5)
                    if ok:
                        db.execute(
                            "UPDATE webhook_queue SET "
                            "delivered_at = ?, "
                            "attempts = attempts + 1 "
                            "WHERE id = ?", (now_iso(), rid))
                    else:
                        attempts += 1
                        if attempts >= len(
                                WebhookManager._RETRY_BACKOFF):
                            db.execute(
                                "UPDATE webhook_queue SET "
                                "delivered_at = ?, "
                                "attempts = ?, last_error = ? "
                                "WHERE id = ?",
                                (now_iso(), attempts,
                                 "FAILED:" + (err or str(
                                     status))[:190],
                                 rid))
                        else:
                            delay = WebhookManager._RETRY_BACKOFF[
                                attempts]
                            next_try = (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=delay)
                            ).isoformat()
                            db.execute(
                                "UPDATE webhook_queue SET "
                                "attempts = ?, next_try_at = ?, "
                                "last_error = ? WHERE id = ?",
                                (attempts, next_try,
                                 (err or str(status))[:200], rid))
                except Exception as e:
                    attempts += 1
                    db.execute(
                        "UPDATE webhook_queue SET attempts = ?, "
                        "last_error = ? WHERE id = ?",
                        (attempts, str(e)[:200], rid))
        except Exception as e:
            logger.debug(f"webhook queue: {e}")

    @staticmethod
    def queue_stats() -> Dict[str, int]:
        try:
            total = db.execute(
                "SELECT COUNT(*) AS c FROM webhook_queue "
                "WHERE delivered_at = ''")
            delivered = db.execute(
                "SELECT COUNT(*) AS c FROM webhook_queue "
                "WHERE delivered_at != '' "
                "  AND last_error NOT LIKE 'FAILED:%'")
            failed = db.execute(
                "SELECT COUNT(*) AS c FROM webhook_queue "
                "WHERE last_error LIKE 'FAILED:%'")
            return {
                "pending": total[0]["c"] if total else 0,
                "delivered": (delivered[0]["c"]
                              if delivered else 0),
                "failed": failed[0]["c"] if failed else 0,
            }
        except Exception:
            return {"pending": 0, "delivered": 0, "failed": 0}

    @staticmethod
    def fire(event: str, payload: Dict[str, Any]) -> None:
        def work() -> None:
            try:
                for wh in WebhookManager.get_webhooks():
                    if not wh.get("enabled", True):
                        continue
                    safe, _ = WebhookManager._resolve_and_check(
                        wh["url"])
                    if not safe:
                        continue
                    cb_key = f"webhook:{wh['id']}"
                    if circuit_breaker.is_open(cb_key):
                        WebhookManager._enqueue(
                            wh["id"], wh["url"], event, payload,
                            "circuit open")
                        continue
                    try:
                        data = json.dumps({
                            "event": event,
                            "timestamp": now_iso(),
                            "payload": payload,
                            "nexus_version": "1.1.1",
                        }, ensure_ascii=False).encode("utf-8")
                        ok, _, err = http_with_retry(
                            url=wh["url"], data=data,
                            headers={
                                "Content-Type":
                                    "application/json",
                                "User-Agent": "NEXUS/1.1.1"},
                            method="POST", timeout=5)
                        if ok:
                            circuit_breaker.record_success(cb_key)
                        else:
                            circuit_breaker.record_failure(cb_key)
                            WebhookManager._enqueue(
                                wh["id"], wh["url"], event,
                                payload, err or "http fail")
                    except Exception as e:
                        circuit_breaker.record_failure(cb_key)
                        WebhookManager._enqueue(
                            wh["id"], wh["url"], event,
                            payload, str(e))
            except Exception as e:
                logger.error(f"Webhook.fire: {e}")
        from nexus.storage import executor
        executor.submit_safe(work)


# ═══════════════════════════════════════════════════════════
# WEBHOOK WORKER
# ═══════════════════════════════════════════════════════════
class WebhookWorker:
    _instance: Optional["WebhookWorker"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "WebhookWorker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._thread: Optional[
                    threading.Thread] = None
                cls._instance._stop = threading.Event()
            return cls._instance

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name="webhook-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                WebhookManager._process_queue()
            except Exception as e:
                logger.debug(f"webhook worker: {e}")
            self._stop.wait(15)


webhook_worker = WebhookWorker()


# ═══════════════════════════════════════════════════════════
# HTTP API SERVER
# ═══════════════════════════════════════════════════════════
class HTTPAPIHandler(BaseHTTPRequestHandler):
    server_version = "NEXUS/1.1.1"
    _rate_buckets: Dict[str, deque] = {}
    _rate_lock = threading.Lock()
    _last_used_cache: Dict[str, float] = {}
    _last_used_lock = threading.Lock()
    _last_gc: float = 0.0

    def log_message(self, fmt: str, *args: Any) -> None:
        with suppress(Exception):
            logger.debug(f"API: {fmt % args}")

    def _send_json(self, code: int, payload: Any) -> None:
        try:
            body = json.dumps(
                payload, ensure_ascii=False,
                default=str).encode("utf-8")
            self.send_response(code)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_text(self, code: int, text: str) -> None:
        try:
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header(
                "Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _check_rate(self, key: str) -> bool:
        now = time.time()
        with HTTPAPIHandler._rate_lock:
            if now - HTTPAPIHandler._last_gc > 60:
                HTTPAPIHandler._last_gc = now
                stale = [
                    k for k, b in
                    HTTPAPIHandler._rate_buckets.items()
                    if not b or now - b[-1] > 300
                ]
                for k in stale:
                    HTTPAPIHandler._rate_buckets.pop(k, None)
            bucket = HTTPAPIHandler._rate_buckets.setdefault(
                key, deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= API_RATE_LIMIT_RPM:
                return False
            bucket.append(now)
            return True

    def _get_token(self) -> Optional[str]:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return (self.headers.get("X-API-Token", "").strip()
                or None)

    def _auth(self) -> Optional[str]:
        """
        FIX: проверки в Python (revoked, expires_at),
        подробное логирование для диагностики 401.
        """
        token = self._get_token()
        if not token:
            logger.warning("API auth: no token in headers")
            return None
        try:
            token_hash = hashlib.sha256(
                token.encode()).hexdigest()

            # Упрощённый SELECT — только по хэшу
            rows = db.execute(
                "SELECT user, last_used, revoked, expires_at "
                "FROM api_tokens WHERE token_hash = ?",
                (token_hash,))

            if not rows:
                cnt = db.execute(
                    "SELECT COUNT(*) AS c FROM api_tokens")
                total = cnt[0]['c'] if cnt else 'ERR'
                logger.warning(
                    f"API auth: NO MATCH for "
                    f"{token_hash[:16]}... "
                    f"(total tokens in this thread's view = "
                    f"{total})")
                return None

            r = rows[0]
            if r["revoked"]:
                logger.warning(
                    f"API auth: token revoked "
                    f"({token_hash[:16]}...)")
                return None
            expires = r["expires_at"] or ""
            if expires and expires <= now_iso():
                logger.warning(
                    f"API auth: token expired "
                    f"({token_hash[:16]}...)")
                return None

            user = r["user"]
            logger.info(
                f"API auth: OK user={user} "
                f"({token_hash[:16]}...)")

            now = time.time()
            with HTTPAPIHandler._last_used_lock:
                last = HTTPAPIHandler._last_used_cache.get(
                    token_hash, 0.0)
                if now - last > 60:
                    HTTPAPIHandler._last_used_cache[
                        token_hash] = now
                    db.execute(
                        "UPDATE api_tokens SET last_used = ? "
                        "WHERE token_hash = ?",
                        (now_iso(), token_hash))
            return user
        except Exception as e:
            logger.error(f"API auth error: {e}")
            return None

    def _read_body(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(
                self.headers.get("Content-Length", "0"))
            if length <= 0 or length > API_MAX_BODY:
                return None
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def do_GET(self) -> None:
        ip = self.client_address[0]
        if not self._check_rate(ip):
            self._send_json(429, {"error": "rate limited"})
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/v1/health":
            self._send_json(200, {
                "ok": True,
                "version": "1.1.1",
                "name": "CORE++ FIX",
                "ts": now_iso(),
            })
            return
        if path.startswith("/r/"):
            qid = path[3:]
            target = DynamicQRManager.resolve(qid)
            if not target:
                self._send_text(404, "not found")
                return
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        if path == "" or path == "/":
            self._send_text(
                200,
                "NEXUS API v1.1.1 — OK\n"
                "Endpoints:\n"
                "  GET  /api/v1/health\n"
                "  GET  /api/v1/stats\n"
                "  GET  /api/v1/tasks\n"
                "  GET  /api/v1/notes?limit=N\n"
                "  GET  /api/v1/qr?limit=N\n"
                "  GET  /api/v1/blockchain\n"
                "  POST /api/v1/tasks\n"
                "  POST /api/v1/notes\n"
                "  POST /api/v1/qr\n"
                "  POST /api/v1/dynamic\n"
                "  GET  /r/{id}\n")
            return

        user = self._auth()
        if not user:
            self._send_json(401, {"error": "unauthorized"})
            return

        if path == "/api/v1/stats":
            stats = level_system.get_stats(user)
            tasks = TaskManager.stats_for(user)
            ach_total, ach_unlocked, ach_points = \
                achievement_system.get_progress(user)
            self._send_json(200, {
                "user": user,
                "level": stats.get("level", 1),
                "xp": stats.get("xp", 0),
                "xp_next": stats.get("xp_next", 100),
                "streak": stats.get("streak", 0),
                "tasks": tasks,
                "achievements": {
                    "total": ach_total,
                    "unlocked": ach_unlocked,
                    "points": ach_points,
                },
            })
            return

        if path == "/api/v1/tasks":
            active = TaskManager.get_active()
            self._send_json(200, {"tasks": [
                {"id": t["id"], "title": t["title"],
                 "priority": t.get("priority"),
                 "status": t.get("status"),
                 "deadline": t.get("deadline", ""),
                 "created_at": t.get("created_at")}
                for t in active[:100]
            ]})
            return

        if path == "/api/v1/notes":
            limit = int(query.get("limit", ["20"])[0])
            limit = max(1, min(200, limit))
            notes = NoteManager.get_user_notes(user)[:limit]
            self._send_json(200, {"notes": [
                {"id": n["id"], "title": n["title"],
                 "tags": n.get("tags", []),
                 "created_at": n.get("created_at"),
                 "updated_at": n.get("updated_at")}
                for n in notes
            ]})
            return

        if path == "/api/v1/qr":
            limit = int(query.get("limit", ["20"])[0])
            history = qr_history.get_user_history(user, limit)
            self._send_json(200, {"qr": [
                {"id": h["id"], "data": h.get("data"),
                 "style": h.get("style"),
                 "created_at": h.get("created_at")}
                for h in history
            ]})
            return

        if path == "/api/v1/blockchain":
            chain = BlockchainManager.get_all()
            stats = BlockchainManager.stats()
            self._send_json(200, {
                "stats": stats,
                "blocks": [
                    {"id": b["id"], "sender": b["sender"],
                     "hash": b.get("hash", ""),
                     "timestamp": b.get("timestamp")}
                    for b in chain[-50:]
                ],
            })
            return

        self._send_json(404, {"error": "not found",
                                "path": path})

    def do_POST(self) -> None:
        ip = self.client_address[0]
        if not self._check_rate(ip):
            self._send_json(429, {"error": "rate limited"})
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = self._auth()
        if not user:
            self._send_json(401, {"error": "unauthorized"})
            return
        body = self._read_body() or {}

        if path == "/api/v1/tasks":
            title = (body.get("title") or "").strip()
            if not title:
                self._send_json(400,
                                {"error": "title required"})
                return
            t = TaskManager.create(
                title=title, author=user,
                priority=body.get("priority", "normal"),
                description=body.get("description", ""),
                tags=body.get("tags") or [],
                deadline=body.get("deadline"))
            if t:
                self._send_json(201, {"id": t["id"],
                                       "title": t["title"]})
            else:
                self._send_json(500,
                                {"error": "create failed"})
            return

        if path == "/api/v1/notes":
            title = (body.get("title") or "").strip()
            content = (body.get("content") or "").strip()
            if not title:
                self._send_json(400,
                                {"error": "title required"})
                return
            n = NoteManager.create(
                title=title, content=content or title,
                author=user, tags=body.get("tags") or [])
            if n:
                self._send_json(201, {"id": n["id"],
                                       "title": n["title"]})
            else:
                self._send_json(500,
                                {"error": "create failed"})
            return

        if path == "/api/v1/qr":
            data = (body.get("data") or "").strip()
            if not data:
                self._send_json(400,
                                {"error": "data required"})
                return
            style = body.get("style", "neon_cyan")
            size = int(body.get("size", 400))
            img = qr_gen.generate(data, size=size, style=style)
            if img is None:
                self._send_json(500,
                                {"error": "generate failed"})
                return
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length",
                              str(len(buf.getvalue())))
            self.end_headers()
            self.wfile.write(buf.getvalue())
            with suppress(Exception):
                level_system.add_xp(user, "generate_qr")
                level_system.increment(user, "qr_generated")
            return

        if path == "/api/v1/dynamic":
            target = (body.get("target") or "").strip()
            if not target.startswith(("http://", "https://")):
                self._send_json(
                    400,
                    {"error": "target must be http(s) URL"})
                return
            qid = DynamicQRManager.create(user, target)
            if qid:
                port = getattr(self.server, "server_port", 8765)
                self._send_json(201, {
                    "id": qid,
                    "short_url":
                        f"http://127.0.0.1:{port}/r/{qid}",
                })
            else:
                self._send_json(500,
                                {"error": "create failed"})
            return

        self._send_json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                          "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                          "Authorization, Content-Type")
        self.end_headers()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class APIServer:
    _instance: Optional["APIServer"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "APIServer":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._server: Optional[Any] = None
                cls._instance._thread: Optional[
                    threading.Thread] = None
                cls._instance._port: int = 0
            return cls._instance

    def is_running(self) -> bool:
        return (self._thread is not None
                and self._thread.is_alive()
                and self._server is not None)

    def start(self, port: int = 8765) -> Tuple[bool, str]:
        if self.is_running():
            return False, "Уже запущен"
        try:
            # FIX: WAL checkpoint перед стартом —
            # гарантия, что HTTP-поток увидит свежие данные
            with suppress(Exception):
                db.conn.execute("PRAGMA wal_checkpoint(FULL)")

            server = ThreadingHTTPServer(
                ("127.0.0.1", int(port)), HTTPAPIHandler)
            self._server = server
            self._port = int(port)

            def run() -> None:
                try:
                    server.serve_forever(poll_interval=0.5)
                except Exception as e:
                    logger.debug(
                        f"API server stopped: {e}")

            self._thread = threading.Thread(
                target=run, daemon=True, name="http-api")
            self._thread.start()
            logger.info(
                f"API started on http://127.0.0.1:{port}")
            return True, f"Запущен на порту {port}"
        except OSError as e:
            return False, f"Порт занят: {e}"
        except Exception as e:
            return False, str(e)

    def stop(self) -> None:
        if self._server is not None:
            with suppress(Exception):
                self._server.shutdown()
            with suppress(Exception):
                self._server.server_close()
        self._server = None
        self._thread = None

    def port(self) -> int:
        return self._port


api_server = APIServer()


# ═══════════════════════════════════════════════════════════
# PLUGIN MANAGER
# ═══════════════════════════════════════════════════════════
class PluginBase:
    NAME = "Unnamed Plugin"
    VERSION = "1.0.0"
    AUTHOR = "Unknown"
    DESCRIPTION = "No description"

    def on_load(self, app: Any) -> None:
        pass

    def on_unload(self, app: Any) -> None:
        pass

    def on_note_created(self, note_id: str,
                         author: str) -> None:
        pass

    def on_page_shown(self, page: str) -> None:
        pass

    def on_event(self, event: str, **kwargs: Any) -> None:
        pass


class PluginManager:
    _instance: Optional["PluginManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "PluginManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._plugins: Dict[
                    str, Dict[str, Any]] = {}
                cls._instance._enabled: Set[str] = set()
                cls._instance._app: Optional[Any] = None
                cls._instance._lock = threading.RLock()
                cls._instance.discover()
                try:
                    from nexus.storage import event_bus
                    event_bus.subscribe(
                        "note.created",
                        cls._instance._on_note_created)
                except Exception:
                    pass
            return cls._instance

    def _load_plugin_file(self, py_file: Path
                           ) -> Optional[Dict[str, Any]]:
        try:
            spec = importlib.util.spec_from_file_location(
                f"nexus_plugin_{py_file.stem}", py_file)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plugin_cls: Optional[type] = None
            for _, obj in inspect.getmembers(
                    module, inspect.isclass):
                if (obj is not PluginBase
                        and issubclass(obj, PluginBase)):
                    plugin_cls = obj
                    break
            if plugin_cls is None:
                return None
            instance = plugin_cls()
            return {
                "class": plugin_cls, "instance": instance,
                "file": py_file,
                "meta": {
                    "name": getattr(plugin_cls, "NAME",
                                     py_file.stem),
                    "version": getattr(
                        plugin_cls, "VERSION", "1.0.0"),
                    "author": getattr(
                        plugin_cls, "AUTHOR", "Unknown"),
                    "desc": getattr(
                        plugin_cls, "DESCRIPTION", ""),
                },
            }
        except Exception as e:
            logger.warning(
                f"plugin load: {py_file.name}: {e}")
            return None

    def discover(self) -> int:
        with self._lock:
            self._plugins.clear()
            if not PLUGINS_DIR.exists():
                return 0
            count = 0
            for py_file in sorted(PLUGINS_DIR.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                plugin = self._load_plugin_file(py_file)
                if plugin is None:
                    continue
                name = plugin["meta"]["name"]
                self._plugins[name] = plugin
                self._enabled.add(name)
                count += 1
            return count

    def get_plugins(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._plugins)

    def set_app(self, app: Any) -> None:
        with self._lock:
            self._app = app
            plugins = list(self._plugins.items())
            enabled = set(self._enabled)
        for name, p in plugins:
            if name not in enabled:
                continue
            try:
                p["instance"].on_load(app)
            except Exception as e:
                logger.error(f"Plugin {name} on_load: {e}")

    def reload_all(self) -> int:
        with self._lock:
            app = self._app
            plugins = list(self._plugins.items())
            enabled = set(self._enabled)
        if app is not None:
            for name, p in plugins:
                if name not in enabled:
                    continue
                try:
                    p["instance"].on_unload(app)
                except Exception:
                    pass
        count = self.discover()
        if app is not None:
            self.set_app(app)
        return count

    def _on_note_created(self, note_id: str,
                          author: str,
                          **kw: Any) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p["instance"].on_note_created(
                    note_id, author)
            except Exception:
                pass

    def emit_page_shown(self, page: str) -> None:
        with self._lock:
            plugins = list(self._plugins.values())
        for p in plugins:
            try:
                p["instance"].on_page_shown(page)
            except Exception:
                pass


plugin_manager = PluginManager()


class PluginMarketplace:
    _MAX_ZIP_SIZE = 10 * 1024 * 1024

    @staticmethod
    def list_available() -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for name, p in plugin_manager.get_plugins().items():
            try:
                size = p["file"].stat().st_size
            except Exception:
                size = 0
            result.append({
                "name": name, **p["meta"],
                "path": str(p["file"]), "size": size})
        return result

    @staticmethod
    def install_from_file(zip_path: str
                           ) -> Tuple[bool, str]:
        try:
            zip_p = Path(zip_path)
            if not zip_p.is_file():
                return False, "Файл не найден"
            if zip_p.stat().st_size > \
                    PluginMarketplace._MAX_ZIP_SIZE:
                return False, "ZIP слишком большой (>10 МБ)"
            installed = 0
            with zipfile.ZipFile(zip_p, "r") as zf:
                for member in zf.namelist():
                    if (".." in member
                            or member.startswith("/")
                            or member.startswith("\\")):
                        continue
                    if not member.endswith(".py"):
                        continue
                    name = Path(member).name
                    if name.startswith("_"):
                        continue
                    target = PLUGINS_DIR / name
                    try:
                        with zf.open(member) as src, \
                                open(target, "wb") as dst:
                            dst.write(src.read())
                        installed += 1
                    except Exception:
                        pass
            if installed == 0:
                return False, "В архиве нет плагинов"
            plugin_manager.discover()
            return True, f"Установлено: {installed}"
        except zipfile.BadZipFile:
            return False, "Повреждённый ZIP"
        except Exception as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════
# EXPORT / IMPORT WRAPPER
# ═══════════════════════════════════════════════════════════
class DataExporter:
    @staticmethod
    def export_all(user: str) -> Tuple[bool, str]:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_dir = EXPORT_DIR / f"export_{ts}"
            tmp_dir.mkdir(exist_ok=True)

            data = {
                "version": "1.1.1",
                "export_date": now_iso(),
                "user": user,
                "tasks": TaskManager.get_all(),
                "blockchain": BlockchainManager.get_all(
                    include_deleted=True),
                "code_history": CodeHistory.get_user_history(
                    user, limit=100),
                "qr_history": qr_history.get_user_history(
                    user, limit=100),
                "achievements": [
                    dict(r) for r in (
                        db.execute(
                            "SELECT * FROM achievements "
                            "WHERE user = ?", (user,)) or [])
                ],
                "merkle_root": BlockchainManager.merkle_root(),
            }
            try:
                from nexus.domain import ForumManager
                data["forum"] = ForumManager.get_all()
            except Exception:
                pass

            with open(tmp_dir / "nexus_export.json",
                      "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            zip_path = EXPORT_DIR / f"nexus_backup_{ts}.zip"
            with zipfile.ZipFile(
                    zip_path, "w",
                    zipfile.ZIP_DEFLATED) as zf:
                for fp in tmp_dir.rglob("*"):
                    if fp.is_file():
                        zf.write(fp,
                                  fp.relative_to(tmp_dir))
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return True, str(zip_path)
        except Exception as e:
            logger.exception("DataExporter.export_all")
            return False, str(e)

    @staticmethod
    def import_from_zip(zip_path: str,
                         user: str) -> Tuple[bool, str]:
        try:
            from nexus.core import TEMP_DIR
            tmp = TEMP_DIR / f"import_{gen_short_id()}"
            tmp.mkdir(exist_ok=True)
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for member in zf.namelist():
                        if (".." in member
                                or member.startswith("/")
                                or member.startswith("\\")):
                            continue
                        zf.extract(member, tmp)
                json_file = next(
                    tmp.rglob("nexus_export.json"), None)
                if not json_file:
                    return False, "nexus_export.json не найден"
                data = json.loads(json_file.read_text(
                    encoding="utf-8"))
                if "blockchain" in data:
                    Store.save("blockchain", data["blockchain"])
                if "snippets" in data:
                    Store.save("code_snippets/snippets",
                                data["snippets"])
                cache.clear()
                BlockchainManager._invalidate_caches()
                return True, "Импорт завершён"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:
            logger.exception("DataExporter.import_from_zip")
            return False, str(e)


__all__ = [
    "HTMLExporter", "BackupManager", "VaultManager",
    "SyncManager", "WebhookManager", "WebhookWorker",
    "webhook_worker", "HTTPAPIHandler", "APIServer",
    "api_server", "ThreadingHTTPServer",
    "PluginBase", "PluginManager", "plugin_manager",
    "PluginMarketplace", "DataExporter",
]