"""
database.py — SQLite база данных ИРУ v3.5

Таблицы:
  users          — пользователи (token, имя, plan, лимиты, согласие, terms)
  chats          — чаты пользователей (title, user_id, timestamps)
  messages       — сообщения в чатах (role, content, commands_json)
  training_data  — записи для обучения модели (input, команды, контекст ОС)
  refresh_tokens — JWT refresh-токены (для logout + ротации)
  device_memory  — память устройства (команды + закреплённые факты по machine_guid)

Файл БД создаётся рядом с main.py при первом запуске.
При старте автоматически создаётся администратор (admin).
"""

import sqlite3
import uuid
import json
import time
from datetime import datetime, timezone
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

            CREATE TABLE IF NOT EXISTS device_profiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT    UNIQUE NOT NULL,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                hostname    TEXT,
                os          TEXT,
                os_version  TEXT,
                username    TEXT,
                desktop_path TEXT,
                cpu         TEXT,
                gpu         TEXT,
                ram_gb      REAL,
                disks       TEXT,
                machine_guid TEXT,
                agent_version TEXT,
                updated_at  REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_device_profiles_device ON device_profiles(device_id);
            CREATE INDEX IF NOT EXISTS idx_device_profiles_user   ON device_profiles(user_id);

            -- Конвейер-агентность: задачи и их шаги
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                chat_id     INTEGER REFERENCES chats(id) ON DELETE CASCADE,
                device_id   TEXT,
                goal        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'running',
                -- 'running' | 'completed' | 'failed' | 'cancelled'
                created_at  REAL    NOT NULL,
                updated_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                idx         INTEGER NOT NULL,
                description TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',
                -- 'pending' | 'running' | 'done' | 'failed' | 'skipped'
                summary     TEXT,
                started_at  REAL,
                finished_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_chat   ON tasks(chat_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_user   ON tasks(user_id);
            CREATE INDEX IF NOT EXISTS idx_task_steps   ON task_steps(task_id, idx);

            -- Память устройства (команды + закреплённые факты)
            CREATE TABLE IF NOT EXISTS device_memory (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_guid    TEXT    NOT NULL,
                device_id       TEXT,
                type            TEXT    NOT NULL,
                command         TEXT,
                intent          TEXT,
                exit_code       INTEGER,
                success         INTEGER,
                stdout_preview  TEXT,
                stderr_preview  TEXT,
                fact_text       TEXT,
                category        TEXT,
                pinned          INTEGER DEFAULT 0,
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_device_memory_guid_created
                ON device_memory(machine_guid, created_at DESC);

            -- миграция: добавить agent_version если не существует
        """)
        try:
            conn.execute("ALTER TABLE device_profiles ADD COLUMN agent_version TEXT")
        except Exception:
            pass  # уже существует
        conn.executescript("""
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


# ── Device Profiles ─────────────────────────────────────────────────────────

def upsert_device_profile(device_id: str, user_id: int, profile: dict) -> None:
    """Insert or update device profile. profile — dict с полями hostname, os, etc."""
    now = time.time()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM device_profiles WHERE device_id = ?", (device_id,)
        ).fetchone()
        disks_json = json.dumps(profile.get("disks"), ensure_ascii=False) if profile.get("disks") else None
        vals = (
            user_id,
            profile.get("hostname"),
            profile.get("os"),
            profile.get("os_version"),
            profile.get("username"),
            profile.get("desktop_path"),
            profile.get("cpu"),
            profile.get("gpu"),
            profile.get("ram_gb"),
            disks_json,
            profile.get("machine_guid"),
            profile.get("agent_version"),
            now,
        )
        if existing:
            conn.execute(
                """UPDATE device_profiles SET
                    user_id = ?, hostname = ?, os = ?, os_version = ?,
                    username = ?, desktop_path = ?, cpu = ?, gpu = ?,
                    ram_gb = ?, disks = ?, machine_guid = ?, agent_version = ?, updated_at = ?
                   WHERE device_id = ?""",
                vals + (device_id,)
            )
        else:
            conn.execute(
                """INSERT INTO device_profiles
                   (device_id, user_id, hostname, os, os_version,
                    username, desktop_path, cpu, gpu, ram_gb, disks, machine_guid, agent_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (device_id,) + vals
            )


def get_device_profile(device_id: str) -> dict | None:
    """Get device profile by device_id. Returns dict or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM device_profiles WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("disks"):
            try:
                d["disks"] = json.loads(d["disks"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d


def get_user_device_profiles(user_id: int) -> list[dict]:
    """Get all device profiles for a user."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM device_profiles WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("disks"):
                try:
                    d["disks"] = json.loads(d["disks"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result


def delete_device_profile(device_id: str) -> bool:
    """Delete device profile."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM device_profiles WHERE device_id = ?", (device_id,)
        )
        return cursor.rowcount > 0


# ── Tasks (конвейер-агентность) ────────────────────────────────────────────────

def create_task(user_id: int, chat_id: int | None, device_id: str | None,
                goal: str, steps: list[str]) -> int:
    """Создать задачу с планом. Возвращает task_id."""
    now = time.time()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(user_id, chat_id, device_id, goal, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (user_id, chat_id, device_id, goal, now, now),
        )
        task_id = cur.lastrowid
        for i, desc in enumerate(steps):
            conn.execute(
                "INSERT INTO task_steps(task_id, idx, description, status) "
                "VALUES (?, ?, ?, 'pending')",
                (task_id, i, str(desc).strip()),
            )
        conn.commit()
        return task_id


def get_task(task_id: int) -> dict | None:
    """Получить задачу со списком шагов."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        task = dict(row)
        steps = conn.execute(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY idx ASC",
            (task_id,),
        ).fetchall()
        task["steps"] = [dict(s) for s in steps]
        return task


def update_step(task_id: int, idx: int, status: str,
                summary: str | None = None) -> bool:
    """Обновить статус шага. status: 'running' | 'done' | 'failed' | 'skipped'."""
    now = time.time()
    with get_db() as conn:
        fields = ["status = ?"]
        values = [status]
        if status == "running":
            fields.append("started_at = ?")
            values.append(now)
        elif status in ("done", "failed", "skipped"):
            fields.append("finished_at = ?")
            values.append(now)
        if summary is not None:
            fields.append("summary = ?")
            values.append(summary)
        values.extend([task_id, idx])
        cur = conn.execute(
            f"UPDATE task_steps SET {', '.join(fields)} WHERE task_id = ? AND idx = ?",
            values,
        )
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        conn.commit()
        return cur.rowcount > 0


def finish_task(task_id: int, status: str) -> bool:
    """Завершить задачу. status: 'completed' | 'failed' | 'cancelled'."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), task_id),
        )
        conn.commit()
        return cur.rowcount > 0


def list_chat_tasks(chat_id: int) -> list[dict]:
    """Список задач в чате со статусами шагов (для UI)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            steps = conn.execute(
                "SELECT idx, description, status, summary FROM task_steps "
                "WHERE task_id = ? ORDER BY idx ASC",
                (d["id"],),
            ).fetchall()
            d["steps"] = [dict(s) for s in steps]
            result.append(d)
        return result


# ── Device Memory (команды + факты по machine_guid) ──────────────────────

def _utc_iso() -> str:
    """ISO8601 timestamp в UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_command_memory(machine_guid: str, device_id: str | None,
                       command: str, intent: str | None,
                       exit_code: int, stdout: str | None,
                       stderr: str | None) -> int:
    """Записать выполненную команду в память устройства. Возвращает id."""
    success = 1 if exit_code == 0 else 0
    stdout_preview = (stdout or "")[:500] or None
    stderr_preview = (stderr or "")[:500] or None
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO device_memory
               (machine_guid, device_id, type, command, intent, exit_code,
                success, stdout_preview, stderr_preview, pinned, created_at)
               VALUES (?, ?, 'command', ?, ?, ?, ?, ?, ?, 0, ?)""",
            (machine_guid, device_id, command, intent, exit_code,
             success, stdout_preview, stderr_preview, _utc_iso()),
        )
        return cur.lastrowid


def add_fact(machine_guid: str, device_id: str | None,
             text: str, category: str | None) -> int:
    """Добавить закреплённый факт. Возвращает id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO device_memory
               (machine_guid, device_id, type, fact_text, category, pinned, created_at)
               VALUES (?, ?, 'fact', ?, ?, 1, ?)""",
            (machine_guid, device_id, text, category, _utc_iso()),
        )
        return cur.lastrowid


def delete_fact(machine_guid: str, fact_id: int) -> bool:
    """Удалить факт по id (только если принадлежит этому устройству)."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM device_memory WHERE id = ? AND machine_guid = ? AND type = 'fact'",
            (fact_id, machine_guid),
        )
        return cur.rowcount > 0


def get_recent_commands(machine_guid: str, limit: int = 20) -> list[dict]:
    """Последние команды для устройства (новые первыми)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, command, intent, exit_code, success,
                      stdout_preview, stderr_preview, created_at
               FROM device_memory
               WHERE machine_guid = ? AND type = 'command'
               ORDER BY created_at DESC LIMIT ?""",
            (machine_guid, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pinned_facts(machine_guid: str) -> list[dict]:
    """Все закреплённые факты для устройства (старые первыми)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, fact_text, category, created_at
               FROM device_memory
               WHERE machine_guid = ? AND type = 'fact' AND pinned = 1
               ORDER BY created_at ASC""",
            (machine_guid,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_memory_stats(machine_guid: str) -> dict:
    """Количество фактов и команд в памяти устройства."""
    with get_db() as conn:
        facts_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM device_memory WHERE machine_guid = ? AND type = 'fact' AND pinned = 1",
            (machine_guid,),
        ).fetchone()
        cmds_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM device_memory WHERE machine_guid = ? AND type = 'command'",
            (machine_guid,),
        ).fetchone()
        return {
            "facts": facts_row["cnt"] if facts_row else 0,
            "commands": cmds_row["cnt"] if cmds_row else 0,
        }
