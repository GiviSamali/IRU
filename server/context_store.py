"""Owned compaction checkpoints in existing SQLite; no background service.

Chat history already lives in messages. Only displaced Worker message groups
need an archive. Summaries are derivatives, never permission or run evidence.
"""
import hashlib
import json
import time
import uuid

try:
    from . import database as db
except ImportError:
    import database as db


def init_context_storage():
    with db.get_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS context_checkpoints (
            checkpoint_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            scope TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            summary TEXT NOT NULL, source_json TEXT NOT NULL,
            covered_message_id INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            UNIQUE(owner_user_id,chat_id,scope,source_sha256))""")
        c.execute("CREATE INDEX IF NOT EXISTS context_checkpoint_scope ON context_checkpoints(owner_user_id,chat_id,scope,created_at)")


def latest(owner, chat, scope):
    init_context_storage()
    with db.get_db() as c:
        row = c.execute("""SELECT x.* FROM context_checkpoints x JOIN chats c ON c.id=x.chat_id
            WHERE x.owner_user_id=? AND x.chat_id=? AND x.scope=? AND c.user_id=?
            ORDER BY x.covered_message_id DESC,x.created_at DESC,x.rowid DESC LIMIT 1""", (owner, chat, scope, owner)).fetchone()
        return dict(row) if row else None


def conversation_rows(owner, chat, covered=0):
    """Read dialogue without loading serialized Worker commands or full reports."""
    with db.get_db() as c:
        if not c.execute("SELECT 1 FROM chats WHERE id=? AND user_id=?", (chat, owner)).fetchone():
            raise ValueError("context_chat_not_owned")
        rows = c.execute("""SELECT id,role,content,task_metadata FROM messages
            WHERE chat_id=? AND id>? ORDER BY id DESC LIMIT 1000""", (chat, covered)).fetchall()
    result = []
    for row in reversed(rows):
        try:
            meta = json.loads(row["task_metadata"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("resultFirst") and meta.get("taskKind") == "worker":
            continue
        content = (meta.get("executionDetails") or row["content"]) if meta.get("taskKind") == "worker" else row["content"]
        result.append({"id":row["id"], "role":row["role"], "content":content})
    return result, len(rows) == 1000


def save(owner, chat, scope, source, summary, covered=0):
    init_context_storage()
    raw = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    key = str(uuid.uuid4())
    with db.get_db() as c:
        if not c.execute("SELECT 1 FROM chats WHERE id=? AND user_id=?", (chat, owner)).fetchone():
            raise ValueError("context_chat_not_owned")
        c.execute("""INSERT OR IGNORE INTO context_checkpoints
            (checkpoint_id,owner_user_id,chat_id,scope,source_sha256,summary,source_json,covered_message_id,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
                  (key, owner, chat, scope, digest, summary, raw, covered, time.time()))
        row = c.execute("""SELECT checkpoint_id FROM context_checkpoints
            WHERE owner_user_id=? AND chat_id=? AND scope=? AND source_sha256=?""",
                        (owner, chat, scope, digest)).fetchone()
    return row[0]


def read_page(owner, chat, checkpoint_id, offset=0, task_id=None, step_id=None):
    if not isinstance(checkpoint_id, str) or len(checkpoint_id) > 64:
        return {"status": "blocked", "error": "invalid_context_checkpoint"}
    init_context_storage()
    with db.get_db() as c:
        row = c.execute("""SELECT x.* FROM context_checkpoints x JOIN chats c ON c.id=x.chat_id
            WHERE x.checkpoint_id=? AND x.owner_user_id=? AND x.chat_id=? AND c.user_id=?""",
                        (checkpoint_id, owner, chat, owner)).fetchone()
        if not row or (row["scope"].startswith("worker:") and row["scope"] != "worker:" + str(task_id)):
            return {"status": "blocked", "error": "context_checkpoint_not_in_scope"}
        raw = row["source_json"]
        if hashlib.sha256(raw.encode()).hexdigest() != row["source_sha256"]:
            return {"status": "blocked", "error": "context_checkpoint_corrupt"}
        if step_id is not None:
            if not isinstance(step_id, str) or row["scope"] != "worker:" + str(task_id):
                return {"status":"blocked", "error":"context_step_not_in_scope"}
            entries = json.loads(raw).get("journal") or []
            entry = next((e for e in entries if e.get("step_id") == step_id), None)
            if entry is None:
                return {"status":"missing", "error":"context_step_not_found"}
            raw = json.dumps(entry, ensure_ascii=False)
        previous = None
        if row["scope"] == "orchestrator":
            refs = json.loads(raw)
            previous = refs.get("previous_checkpoint_id")
            ids = refs.get("message_ids", [])
            # Rehydrate original messages, not a second copy of their contents.
            records = []
            for mid in ids:
                item = c.execute("SELECT id,role,content FROM messages WHERE id=? AND chat_id=?", (mid, chat)).fetchone()
                if item: records.append(dict(item))
            raw = json.dumps(records, ensure_ascii=False)
        if type(offset) is not int or not 0 <= offset < len(raw):
            return {"status": "blocked", "error": "invalid_context_offset"}
        end = min(len(raw), offset + 6000)
        return {"status": "success", "checkpoint_id": checkpoint_id, "source_sha256": row["source_sha256"],
                "historical_material_only": True, "current_execution_evidence": False,
                "previous_checkpoint_id": previous,
                "material_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "content": raw[offset:end], "offset": offset,
                "next_offset": end if end < len(raw) else None, "total_chars": len(raw)}


def history_page(owner, chat, before_id=None, message_id=None, offset=0):
    """Addressable old dialogue without loading whole journals or another chat."""
    if (before_id is not None and (type(before_id) is not int or before_id <= 0)
            or message_id is not None and (type(message_id) is not int or message_id <= 0)):
        return {"status":"blocked", "error":"invalid_context_message_id"}
    with db.get_db() as c:
        if not c.execute("SELECT 1 FROM chats WHERE id=? AND user_id=?", (chat, owner)).fetchone():
            return {"status":"blocked", "error":"context_chat_not_owned"}
        if message_id is not None:
            row = c.execute("SELECT id,role,content,task_metadata FROM messages WHERE chat_id=? AND id=?", (chat, message_id)).fetchone()
            if not row or type(offset) is not int or not 0 <= offset < len(row["content"]):
                return {"status":"blocked", "error":"context_message_or_offset_unavailable"}
            meta = json.loads(row["task_metadata"] or "{}")
            if isinstance(meta, dict) and meta.get("taskKind") == "worker":
                return {"status":"blocked", "error":"use_scoped_source_task_report"}
            text = row["content"]
            end = min(len(text), offset + 6000)
            return {"status":"success", "historical_material_only":True, "current_execution_evidence":False,
                    "message_id":message_id, "role":row["role"], "content":text[offset:end],
                    "offset":offset, "next_offset":end if end < len(text) else None,
                    "sha256":hashlib.sha256(text.encode()).hexdigest()}
        rows = c.execute("""SELECT id,role,substr(content,1,400) AS preview,length(content) AS chars,task_metadata
            FROM messages WHERE chat_id=? AND id<? AND content!='' ORDER BY id DESC LIMIT 8""",
                         (chat, before_id or 9223372036854775807)).fetchall()
        records = []
        for row in rows:
            meta = json.loads(row["task_metadata"] or "{}")
            if isinstance(meta, dict) and meta.get("taskKind") == "worker":
                continue
            records.append({k:row[k] for k in ("id","role","preview","chars")})
        return {"status":"success", "historical_material_only":True, "current_execution_evidence":False,
                "messages":records, "next_before_message_id":rows[-1]["id"] if len(rows)==8 else None}
