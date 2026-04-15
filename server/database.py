"""
database.py — SQLite база данных ИРУ v3.2

Таблицы:
  users    — пользователи (token, имя, дата создания)
  chats    — чаты пользователей (title, user_id, timestamps)
  messages — сообщения в чатах (role, content, commands_json)

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

# ── Подключение ──────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """Получить соединение с БД (context manager)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Инициализация ────────────────────────────────────────────────────────

def init_db():
    """Создать таблицы, если не существуют. Создать admin-пользователя."""
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

            CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
        """)

        # Создать admin-пользователя, если его нет
        admin = conn.execute("SELECT id FROM users WHERE name = 'admin'").fetchone()
        if not admin:
            admin_token = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (token, name, created_at) VALUES (?, ?, ?)",
                (admin_token, "admin", time.time())
            )
            print(f"[db] Создан admin-пользователь. Токен: {admin_token}")


# ── Users ────────────────────────────────────────────────────────────────

def get_user_by_token(token: str) -> dict | None:
    """Найти пользователя по токену. Возвращает dict или None."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def create_user(name: str) -> dict:
    """Создать нового пользователя. Возвращает dict с токеном."""
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
    """Список всех пользователей."""
    with get_db() as conn:
        rows = conn.execute("SELECT id, token, name, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    """Удалить пользователя и все его данные."""
    with get_db() as conn:
        # Удалить сообщения из чатов пользователя
        chat_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM chats WHERE user_id = ?", (user_id,)
        ).fetchall()]
        for cid in chat_ids:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (cid,))
        conn.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0


# ── Chats ────────────────────────────────────────────────────────────────

def create_chat(user_id: int, title: str = "Новый чат") -> dict:
    """Создать новый чат для пользователя."""
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
    """Список чатов пользователя (новые сверху)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chats WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_chat(chat_id: int, user_id: int) -> dict | None:
    """Получить чат, только если принадлежит пользователю."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def update_chat_title(chat_id: int, user_id: int, title: str) -> bool:
    """Обновить название чата."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, time.time(), chat_id, user_id)
        )
        return cursor.rowcount > 0


def delete_chat(chat_id: int, user_id: int) -> bool:
    """Удалить чат и все его сообщения."""
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        cursor = conn.execute(
            "DELETE FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        return cursor.rowcount > 0


def touch_chat(chat_id: int):
    """Обновить updated_at чата (вызывать при новом сообщении)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?",
            (time.time(), chat_id)
        )


# ── Messages ─────────────────────────────────────────────────────────────

def add_message(chat_id: int, role: str, content: str, commands: list | None = None) -> dict:
    """Добавить сообщение в чат."""
    now = time.time()
    commands_json = json.dumps(commands, ensure_ascii=False) if commands else None
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO messages (chat_id, role, content, commands, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, commands_json, now)
        )
        # Обновить время чата
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        msg_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        result = dict(row)
        result["commands"] = commands
        return result


def get_messages(chat_id: int, limit: int = 50) -> list[dict]:
    """
    Получить последние N сообщений чата (скользящее окно).
    Возвращает в хронологическом порядке (старые → новые).
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM messages WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (chat_id, limit)
        ).fetchall()

        messages = []
        for row in reversed(rows):  # Хронологический порядок
            msg = dict(row)
            if msg["commands"]:
                try:
                    msg["commands"] = json.loads(msg["commands"])
                except json.JSONDecodeError:
                    msg["commands"] = None
            return_msg = {
                "id": msg["id"],
                "role": msg["role"],
                "content": msg["content"],
                "commands": msg["commands"],
                "created_at": msg["created_at"],
            }
            messages.append(return_msg)
        return messages


def get_message_count(chat_id: int) -> int:
    """Количество сообщений в чате."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        return row["cnt"]
