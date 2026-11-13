"""SQLite persistence for saved event plans."""
from contextlib import contextmanager
import json
import hashlib
import math
import os
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

configured_db_path = os.environ.get("PLANBOT_DATABASE_PATH")
DB_PATH = (
    Path(configured_db_path).expanduser()
    if configured_db_path
    else Path(__file__).with_name("planbot.db")
)
if not DB_PATH.is_absolute():
    DB_PATH = Path(__file__).resolve().parent / DB_PATH
DB_PATH = DB_PATH.resolve()


@contextmanager
def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, event_type TEXT NOT NULL, event_date TEXT NOT NULL,
            location TEXT NOT NULL, guests INTEGER NOT NULL, budget REAL NOT NULL,
            theme TEXT NOT NULL, food TEXT NOT NULL, decor TEXT NOT NULL,
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(plans)")}
        if "fingerprint" not in columns:
            db.execute("ALTER TABLE plans ADD COLUMN fingerprint TEXT")
        if "owner_id" not in columns:
            db.execute("ALTER TABLE plans ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        db.execute("DROP INDEX IF EXISTS idx_plans_fingerprint")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_owner_fingerprint "
            "ON plans(owner_id, fingerprint)"
        )
        # Give existing plans a fingerprint too, so a repeat submission after an upgrade reuses them.
        rows = db.execute(
            "SELECT * FROM plans WHERE fingerprint IS NULL AND owner_id IS NOT NULL ORDER BY id DESC"
        ).fetchall()
        seen = set()
        for row in rows:
            fingerprint = plan_fingerprint(dict(row))
            key = (row["owner_id"], fingerprint)
            if key not in seen:
                db.execute("UPDATE plans SET fingerprint=? WHERE id=?", (fingerprint, row["id"]))
                seen.add(key)
        db.execute("""CREATE TABLE IF NOT EXISTS conversations (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL,
            title TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            conversation_json TEXT NOT NULL,
            PRIMARY KEY (user_id, conversation_id)
        )""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_user_updated "
            "ON conversations(user_id, updated_at DESC)"
        )


def create_user(email, password_hash):
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        return cursor.lastrowid


def get_user_by_email(email):
    with connect() as db:
        row = db.execute(
            "SELECT id, email, password_hash FROM users WHERE email=? COLLATE NOCASE",
            (email,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT id, email FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def _serialize(row):
    item = dict(row)
    item.update(json.loads(item.pop("plan_json")))
    allocation = item.get("allocation")
    missing_miscellaneous = not isinstance(allocation, dict) or "Miscellaneous" not in allocation
    required_fields = ("overview", "schedule", "photography_suggestions", "entertainment_suggestions", "checklist_items")
    if missing_miscellaneous or any(field not in item for field in required_fields):
        # Older saved plans gain newly added generated sections when they are viewed.
        from planner import build_plan
        source = {key: item[key] for key in (
            "event_type", "event_date", "location", "guests", "budget", "theme", "food", "decor"
        )}
        generated = build_plan(source)
        for key, value in generated.items():
            if missing_miscellaneous and key in ("allocation", "allocated_total", "remaining_budget"):
                item[key] = value
                continue
            if key == "checklist_items" and key not in item:
                tasks = item.get("checklist")
                if not isinstance(tasks, list):
                    tasks = [entry["task"] for entry in value]
                item[key] = [
                    {"task": task, "completed": False}
                    for task in tasks
                ]
                continue
            item.setdefault(key, value)
    return item


def get_plans(user_id):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM plans WHERE owner_id=? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [_serialize(row) for row in rows]


def get_plan(plan_id, user_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM plans WHERE id=? AND owner_id=?",
            (plan_id, user_id),
        ).fetchone()
    return _serialize(row) if row else None


def create_plan(plan, user_id):
    fingerprint = plan_fingerprint(plan)
    fields = ("title", "event_type", "event_date", "location", "guests", "budget", "theme", "food", "decor")
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM plans WHERE owner_id=? AND fingerprint=?",
            (user_id, fingerprint),
        ).fetchone()
        if existing:
            return existing["id"], False
        try:
            cursor = db.execute(
                "INSERT INTO plans (title,event_type,event_date,location,guests,budget,theme,food,decor,plan_json,fingerprint,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(plan[key] for key in fields) + (json.dumps(plan), fingerprint, user_id),
            )
            return cursor.lastrowid, True
        except sqlite3.IntegrityError:
            existing = db.execute(
                "SELECT id FROM plans WHERE owner_id=? AND fingerprint=?",
                (user_id, fingerprint),
            ).fetchone()
            if existing:
                return existing["id"], False
            raise


def update_plan(plan_id, user_id, plan):
    fields = ("title", "event_type", "event_date", "location", "guests", "budget", "theme", "food", "decor")
    with connect() as db:
        db.execute(
            "UPDATE plans SET title=?,event_type=?,event_date=?,location=?,guests=?,budget=?,theme=?,food=?,decor=?,plan_json=?,fingerprint=? WHERE id=? AND owner_id=?",
            tuple(plan[key] for key in fields) + (json.dumps(plan), plan_fingerprint(plan), plan_id, user_id),
        )


def update_plan_allocation(plan_id, user_id, allocation):
    from planner import BUDGET_CATEGORIES, build_plan, validate_allocation

    with connect() as db:
        row = db.execute("SELECT * FROM plans WHERE id=? AND owner_id=?", (plan_id, user_id)).fetchone()
        if not row:
            return None, None

        plan = _serialize(row)
        errors = validate_allocation(allocation, plan["budget"])
        if errors:
            return None, errors

        amounts = {
            category: Decimal(str(allocation[category])).quantize(Decimal("0.01"))
            for category in BUDGET_CATEGORIES
        }
        source = {
            key: plan[key]
            for key in ("event_type", "event_date", "location", "guests", "budget", "theme", "food", "decor")
        }
        updated = build_plan(source, allocation=amounts)
        for key in ("schedule", "checklist", "checklist_items"):
            updated[key] = plan[key]
        _write_plan_json(db, plan_id, updated)

    return get_plan(plan_id, user_id), {}


def update_plan_checklist(plan_id, user_id, items):
    from planner import validate_checklist

    with connect() as db:
        row = db.execute("SELECT * FROM plans WHERE id=? AND owner_id=?", (plan_id, user_id)).fetchone()
        if not row:
            return None, None
        errors = validate_checklist(items)
        if errors:
            return None, errors

        plan = _serialize(row)
        normalized = [
            {"task": item["task"].strip(), "completed": item["completed"]}
            for item in items
        ]
        plan["checklist_items"] = normalized
        plan["checklist"] = [item["task"] for item in normalized]
        _write_plan_json(db, plan_id, plan)

    return get_plan(plan_id, user_id), {}


def update_plan_schedule(plan_id, user_id, schedule):
    from planner import validate_schedule

    with connect() as db:
        row = db.execute("SELECT * FROM plans WHERE id=? AND owner_id=?", (plan_id, user_id)).fetchone()
        if not row:
            return None, None
        errors = validate_schedule(schedule)
        if errors:
            return None, errors

        plan = _serialize(row)
        plan["schedule"] = [
            {
                field: activity[field].strip()
                for field in ("time", "title", "detail")
            }
            for activity in schedule
        ]
        _write_plan_json(db, plan_id, plan)

    return get_plan(plan_id, user_id), {}


def _write_plan_json(db, plan_id, plan):
    stored = dict(plan)
    for field in ("id", "created_at", "fingerprint"):
        stored.pop(field, None)
    db.execute(
        "UPDATE plans SET plan_json=? WHERE id=?",
        (json.dumps(stored), plan_id),
    )


def delete_plan(plan_id, user_id):
    with connect() as db:
        cursor = db.execute("DELETE FROM plans WHERE id=? AND owner_id=?", (plan_id, user_id))
        return cursor.rowcount > 0


def validate_conversation(conversation, expected_id):
    if not isinstance(conversation, dict):
        return {"conversation": "Provide the conversation as an object."}

    errors = {}
    if conversation.get("id") != expected_id or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", expected_id):
        errors["id"] = "Conversation ID is invalid."
    title = conversation.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        errors["title"] = "Conversation title must be between 1 and 120 characters."
    messages = conversation.get("messages")
    if not isinstance(messages, list):
        errors["messages"] = "Conversation messages must be provided as a list."
    else:
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                errors[f"messages.{index}"] = "Each message must have a valid role."
                continue
            text = message.get("text", "")
            if not isinstance(text, str) or len(text) > 8000:
                errors[f"messages.{index}.text"] = "Message text cannot exceed 8,000 characters."
    answers = conversation.get("answers", {})
    if not isinstance(answers, dict):
        errors["answers"] = "Conversation answers must be an object."
    step = conversation.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step <= 7:
        errors["step"] = "Conversation step is invalid."
    updated_at = conversation.get("updatedAt")
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(updated_at)
    ):
        errors["updatedAt"] = "Conversation update time is invalid."
    try:
        if len(json.dumps(conversation, ensure_ascii=False)) > 1_000_000:
            errors["conversation"] = "Conversation data cannot exceed 1 MB."
    except (TypeError, ValueError):
        errors["conversation"] = "Conversation data must contain valid JSON values."
    return errors


def get_conversations(user_id):
    with connect() as db:
        rows = db.execute(
            "SELECT conversation_json FROM conversations WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [json.loads(row["conversation_json"]) for row in rows]


def save_conversation(user_id, conversation):
    conversation_id = conversation["id"]
    serialized = json.dumps(conversation, ensure_ascii=False)
    with connect() as db:
        db.execute(
            """INSERT INTO conversations (user_id, conversation_id, title, updated_at, conversation_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, conversation_id) DO UPDATE SET
               title=excluded.title,
               updated_at=excluded.updated_at,
               conversation_json=excluded.conversation_json""",
            (
                user_id,
                conversation_id,
                conversation["title"].strip(),
                int(conversation["updatedAt"]),
                serialized,
            ),
        )
        db.execute(
            """DELETE FROM conversations
               WHERE user_id=? AND conversation_id NOT IN (
                   SELECT conversation_id FROM conversations
                   WHERE user_id=? ORDER BY updated_at DESC LIMIT 40
               )""",
            (user_id, user_id),
        )


def delete_conversation(user_id, conversation_id):
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM conversations WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )
    return cursor.rowcount > 0


def sync_plan_conversations(user_id, plan):
    """Refresh saved plan snapshots in this user's chat history after plan edits."""
    plan_id = plan["id"]
    with connect() as db:
        rows = db.execute(
            "SELECT conversation_id, title, conversation_json FROM conversations WHERE user_id=?",
            (user_id,),
        ).fetchall()
        for row in rows:
            conversation = json.loads(row["conversation_json"])
            linked = False
            saved_plan = conversation.get("savedPlan")
            if isinstance(saved_plan, dict) and saved_plan.get("id") == plan_id:
                conversation["savedPlan"] = {**saved_plan, **plan}
                linked = True
            for message in conversation.get("messages", []):
                message_plan = message.get("plan") if isinstance(message, dict) else None
                if isinstance(message_plan, dict) and message_plan.get("id") == plan_id:
                    message["plan"] = {**message_plan, **plan}
                    linked = True
            if linked:
                answers = conversation.get("answers")
                if isinstance(answers, dict):
                    for field in (
                        "event_type", "event_date", "location", "guests",
                        "budget", "theme", "food", "decor",
                    ):
                        answers[field] = plan[field]
                conversation["title"] = f"{plan['event_type']} plan"
                db.execute(
                    """UPDATE conversations SET title=?, conversation_json=?
                       WHERE user_id=? AND conversation_id=?""",
                    (
                        conversation["title"],
                        json.dumps(conversation, ensure_ascii=False),
                        user_id,
                        row["conversation_id"],
                    ),
                )


def plan_fingerprint(plan):
    """Stable key makes automatic saves idempotent across refreshes and retries."""
    normalized = [
        str(plan.get("event_type", "")).strip().casefold(),
        str(plan.get("event_date", "")).strip(),
        str(plan.get("location", "")).strip().casefold(),
        int(plan.get("guests", 0)),
        round(float(plan.get("budget", 0)) * 100),
        str(plan.get("theme", "")).strip().casefold(),
        str(plan.get("food", "")).strip().casefold(),
        str(plan.get("decor", "")).strip().casefold(),
    ]
    payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
