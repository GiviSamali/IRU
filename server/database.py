"""
database.py — SQLite база данных ИРУ v3.5

Таблицы:
  users          — пользователи (token, имя, plan, лимиты, согласие, terms)
  chats          — чаты пользователей (title, user_id, timestamps)
  messages       — сообщения в чатах (role, content, commands_json)
  training_data  — записи для обучения модели (input, команды, контекст ОС)
  refresh_tokens — JWT refresh-токены (для logout + ротации)

Файл БД создаётся рядом с main.py при первом запуске.
При старте автоматически создаётся администратор (admin).
"""

import sqlite3
import uuid
import json
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "iru.db"

# ── Подключение ───────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """Get DB connection (context manager)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Инициализация ───────────────────────────────────────────────────────────────

def init_db():
    """Create tables if not exist. Create admin user."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token       TEXT    UNIQUE NOT NULL,
                name        TEXT    NOT NULL,
                created_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                title       TEXT    NOT NULL DEFAULT 'Новый чат',
                created_at  REAL    NOT NULL,
                updated_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                commands    TEXT,
                created_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                chat_id     INTEGER NOT NULL REFERENCES chats(id),
                input       TEXT    NOT NULL,
                os          TEXT,
                hostname    TEXT,
                method      TEXT DEFAULT 'powershell',
                running_processes TEXT,
                commands    TEXT,
                success     INTEGER DEFAULT 1,
                created_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token       TEXT    UNIQUE NOT NULL,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at  REAL    NOT NULL,
                created_at  REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chats_user        ON chats(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat     ON messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_training_user     ON training_data(user_id);
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_name   TEXT,
                action      TEXT    NOT NULL,
                detail      TEXT,
                ip          TEXT,
                created_at  REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_refresh_token     ON refresh_tokens(token);
            CREATE INDEX IF NOT EXISTS idx_refresh_user      ON refresh_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_user        ON audit_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_created     ON audit_log(created_at);
        """)

        # Миграции: добавить новые колонки если их нет
        migrations = [
            "ALTER TABLE users ADD COLUMN data_consent INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
            "ALTER TABLE users ADD COLUMN daily_commands_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN daily_commands_date TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN accepted_terms_at REAL DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # Колонка уже существует

        # Создать admin-пользователя, если его нет
        admin = conn.execute("SELECT id FROM users WHERE name = 'admin'").fetchone()
        if not admin:
            admin_token = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (token, name, created_at) VALUES (?, ?, ?)",
                (admin_token, "admin", time.time())
            )
            print(f"[db] Создан admin-пользователь. Токен: {admin_token}")


# ── Users ───────────────────────────────────────────────────────────────────────

def get_user_by_token(token: str) -> dict | None:
    """Find user by static token. Returns dict or None."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Find user by ID. Returns dict or None."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(name: str) -> dict:
    """Create new user. Returns dict with token."""
    token = str(uuid.uuid4())
    now = time.time()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (token, name, created_at) VALUES (?, ?, ?)",
            (token, name, now)
        )
        row = conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
        return dict(row)


def list_users() -> list[dict]:
    """List all users."""
    with get_db() as conn:
        rows = conn.execute("SELECT id, token, name, created_at, plan FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    """Delete user and all related data."""
    with get_db() as conn:
        conn.execute("DELETE FROM training_data WHERE user_id = ?", (user_id,))
        chat_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM chats WHERE user_id = ?", (user_id,)
        ).fetchall()]
        for cid in chat_ids:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (cid,))
        conn.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0


# ── Refresh Tokens ───────────────────────────────────────────────────────────

def store_refresh_token(user_id: int, token: str, ttl: int) -> None:
    """Save refresh token to DB."""
    now = time.time()
    with get_db() as conn:
        # Ограничим количество активных refresh-токенов на пользователя (max 5)
        conn.execute(
            """DELETE FROM refresh_tokens WHERE user_id = ? AND id NOT IN (
               SELECT id FROM refresh_tokens WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 4)""",
            (user_id, user_id)
        )
        conn.execute(
            "INSERT INTO refresh_tokens (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now + ttl, now)
        )


def get_refresh_token(token: str) -> dict | None:
    """Get refresh token record if valid (not expired). Returns dict or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM refresh_tokens WHERE token = ? AND expires_at > ?",
            (token, time.time())
        ).fetchone()
        return dict(row) if row else None


def revoke_refresh_token(token: str) -> bool:
    """Revoke (delete) a refresh token."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))
        return cursor.rowcount > 0


def revoke_all_refresh_tokens(user_id: int) -> None:
    """Revoke all refresh tokens for a user (logout from all devices)."""
    with get_db() as conn:
        conn.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))


def cleanup_expired_refresh_tokens() -> None:
    """Delete all expired refresh tokens. Call periodically."""
    with get_db() as conn:
        conn.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (time.time(),))


# ── Chats ───────────────────────────────────────────────────────────────────────

def create_chat(user_id: int, title: str = "Новый чат") -> dict:
    now = time.time()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO chats (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now)
        )
        chat_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return dict(row)


def list_chats(user_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chats WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_chat(chat_id: int, user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def update_chat_title(chat_id: int, user_id: int, title: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, time.time(), chat_id, user_id)
        )
        return cursor.rowcount > 0


def delete_chat(chat_id: int, user_id: int) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM training_data WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        cursor = conn.execute(
            "DELETE FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        return cursor.rowcount > 0


def touch_chat(chat_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?",
            (time.time(), chat_id)
        )


# ── Messages ───────────────────────────────────────────────────────────────────

def add_message(chat_id: int, role: str, content: str, commands: list | None = None) -> dict:
    now = time.time()
    commands_json = json.dumps(commands, ensure_ascii=False) if commands else None
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (chat_id, role, content, commands, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, commands_json, now)
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        msg_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        result = dict(row)
        result["commands"] = commands
        return result


def get_messages(chat_id: int, limit: int = 50) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM messages WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (chat_id, limit)
        ).fetchall()
        messages = []
        for row in reversed(rows):
            msg = dict(row)
            if msg["commands"]:
                try:
                    msg["commands"] = json.loads(msg["commands"])
                except json.JSONDecodeError:
                    msg["commands"] = None
            messages.append({
                "id":         msg["id"],
                "role":       msg["role"],
                "content":    msg["content"],
                "commands":   msg["commands"],
                "created_at": msg["created_at"],
            })
        return messages


def get_message_count(chat_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        return row["cnt"]


# ── Training Data ──────────────────────────────────────────────────────────────

def add_training_record(user_id: int, chat_id: int, input_text: str,
                        os_info: str, hostname: str, method: str,
                        running_processes: list, commands: list,
                        success: bool) -> dict:
    now = time.time()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO training_data
               (user_id, chat_id, input, os, hostname, method, running_processes, commands, success, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, input_text, os_info, hostname, method,
             json.dumps(running_processes, ensure_ascii=False) if running_processes else None,
             json.dumps(commands, ensure_ascii=False) if commands else None,
             1 if success else 0, now)
        )
        return {"id": cursor.lastrowid}


def get_training_data(limit: int = 100, offset: int = 0) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM training_data ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for field in ("running_processes", "commands"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        pass
            result.append(d)
        return result


def get_training_count() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM training_data").fetchone()
        return row["cnt"]


# ── Согласие пользователя ────────────────────────────────────────────────────────

def set_user_consent(user_id: int, consent: bool) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET data_consent = ? WHERE id = ?",
            (1 if consent else 0, user_id)
        )
        return cursor.rowcount > 0


# ── Plans & Limits ──────────────────────────────────────────────────────────────

PLAN_LIMITS = {
    "free":     {"max_devices": 1,    "max_commands_per_day": 30,   "dev_mode": False},
    "pro":      {"max_devices": 9999, "max_commands_per_day": 9999, "dev_mode": True},
    "business": {"max_devices": 9999, "max_commands_per_day": 9999, "dev_mode": True},
}


def get_user_plan(user_id: int) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT plan FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["plan"] if row and row["plan"] else "free"


def set_user_plan(user_id: int, plan: str) -> bool:
    if plan not in PLAN_LIMITS:
        return False
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET plan = ? WHERE id = ?",
            (plan, user_id)
        )
        return cursor.rowcount > 0


def check_daily_command_limit(user_id: int) -> dict:
    import datetime
    today = datetime.date.today().isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT plan, daily_commands_count, daily_commands_date FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return {"allowed": False, "used": 0, "limit": 0}
        plan   = row["plan"] or "free"
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        count  = row["daily_commands_count"] or 0
        if row["daily_commands_date"] != today:
            count = 0
            conn.execute(
                "UPDATE users SET daily_commands_count = 0, daily_commands_date = ? WHERE id = ?",
                (today, user_id)
            )
        return {
            "allowed": count < limits["max_commands_per_day"],
            "used":    count,
            "limit":   limits["max_commands_per_day"],
        }


def increment_daily_commands(user_id: int):
    import datetime
    today = datetime.date.today().isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET daily_commands_count = daily_commands_count + 1, daily_commands_date = ? WHERE id = ?",
            (today, user_id)
        )


def check_device_limit(user_id: int, current_device_count: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT plan FROM users WHERE id = ?", (user_id,)).fetchone()
        plan   = (row["plan"] if row and row["plan"] else "free")
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        return {
            "allowed": current_device_count < limits["max_devices"],
            "current": current_device_count,
            "limit":   limits["max_devices"],
        }


def accept_terms(user_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE users SET accepted_terms_at = ? WHERE id = ?",
            (time.time(), user_id)
        )
        return cursor.rowcount > 0


def has_accepted_terms(user_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT accepted_terms_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return bool(row and row["accepted_terms_at"])


# ── Audit Log ───────────────────────────────────────────────────────────────────────

def add_audit_log(user_id: int | None, user_name: str | None,
                  action: str, detail: str | None = None,
                  ip: str | None = None) -> None:
    """Write an audit log entry."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO audit_log (user_id, user_name, action, detail, ip, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, user_name, action, detail, ip, time.time())
        )


def get_audit_log(limit: int = 100, offset: int = 0,
                  user_id: int | None = None) -> list[dict]:
    """Return audit log entries, newest first."""
    with get_db() as conn:
        if user_id is not None:
            rows = conn.execute(
                """SELECT * FROM audit_log WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (user_id, limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]


def get_audit_log_count(user_id: int | None = None) -> int:
    """Return total audit log entries count."""
    with get_db() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM audit_log WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as cnt FROM audit_log").fetchone()
        return row["cnt"]
