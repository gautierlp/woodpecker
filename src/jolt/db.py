import sqlite3
from datetime import date, datetime

from .models import STATUS_DONE, STATUS_DROPPED, STATUS_PENDING, Task

_UNSET = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    priority TEXT NOT NULL,
    deadline TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    last_nagged_at TEXT,
    completed_at TEXT,
    blocked_by INTEGER
);
"""

# One row per chat: the task ids, in order, of the last list shown to it. A number the
# user types is a position in this list, so it must survive a restart. Kept here rather
# than in-process memory precisely so a redeploy does not drop resolution back to the
# live order (the "28 done hit the wrong task" bug).
_DISPLAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS display_snapshot (
    chat_id INTEGER PRIMARY KEY,
    task_ids TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.execute(_DISPLAY_SCHEMA)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "blocked_by" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN blocked_by INTEGER")
    conn.commit()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        text=row["text"],
        priority=row["priority"],
        deadline=_d(row["deadline"]),
        created_at=_dt(row["created_at"]),
        status=row["status"],
        last_nagged_at=_dt(row["last_nagged_at"]),
        completed_at=_dt(row["completed_at"]),
        blocked_by=row["blocked_by"],
    )


def add_task(conn, text: str, priority: str, deadline: date | None, created_at: datetime) -> Task:
    cur = conn.execute(
        "INSERT INTO tasks (text, priority, deadline, created_at, status) VALUES (?, ?, ?, ?, ?)",
        (
            text,
            priority,
            deadline.isoformat() if deadline else None,
            created_at.isoformat(),
            STATUS_PENDING,
        ),
    )
    conn.commit()
    return get_task(conn, cur.lastrowid)


def get_task(conn, task_id: int) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_pending(conn) -> list[Task]:
    rows = conn.execute("SELECT * FROM tasks WHERE status = ?", (STATUS_PENDING,)).fetchall()
    return [_row_to_task(r) for r in rows]


def list_all(conn) -> list[Task]:
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    return [_row_to_task(r) for r in rows]


def complete_task(conn, task_id: int, completed_at: datetime) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ? AND status = ?",
        (STATUS_DONE, completed_at.isoformat(), task_id, STATUS_PENDING),
    )
    if cur.rowcount:
        conn.execute("UPDATE tasks SET blocked_by = NULL WHERE blocked_by = ?", (task_id,))
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def drop_task(conn, task_id: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ? AND status = ?",
        (STATUS_DROPPED, task_id, STATUS_PENDING),
    )
    if cur.rowcount:
        conn.execute("UPDATE tasks SET blocked_by = NULL WHERE blocked_by = ?", (task_id,))
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def block_task(conn, task_id: int, blocked_by: int) -> Task | None:
    """Make task_id wait on blocked_by. The orchestrator validates cycles and both statuses
    before calling; these guards make the function safe on its own against a self-block or a
    blocker that does not exist."""
    if task_id == blocked_by:
        return None
    blocker = get_task(conn, blocked_by)
    if blocker is None or blocker.status != STATUS_PENDING:
        return None
    cur = conn.execute(
        "UPDATE tasks SET blocked_by = ? WHERE id = ? AND status = ?",
        (blocked_by, task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def update_task(conn, task_id, *, text=None, priority=None, deadline=_UNSET) -> Task | None:
    assignments = []
    values = []
    if text is not None:
        assignments.append("text = ?")
        values.append(text)
    if priority is not None:
        assignments.append("priority = ?")
        values.append(priority)
    if deadline is not _UNSET:
        assignments.append("deadline = ?")
        values.append(deadline.isoformat() if deadline else None)
    if not assignments:
        task = get_task(conn, task_id)
        return task if task and task.status == STATUS_PENDING else None
    values.extend([task_id, STATUS_PENDING])
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ? AND status = ?",
        values,
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def unblock_task(conn, task_id: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET blocked_by = NULL WHERE id = ? AND status = ?",
        (task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def mark_nagged(conn, task_id: int, when: datetime) -> None:
    conn.execute("UPDATE tasks SET last_nagged_at = ? WHERE id = ?", (when.isoformat(), task_id))
    conn.commit()


def save_display(conn, chat_id: int, task_ids: list[int]) -> None:
    """Record the order of the last list shown to a chat, replacing any previous one.
    Only a fresh list overwrites it, so numbers keep resolving against what the user saw."""
    conn.execute(
        "INSERT INTO display_snapshot (chat_id, task_ids) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET task_ids = excluded.task_ids",
        (chat_id, ",".join(str(i) for i in task_ids)),
    )
    conn.commit()


def load_display(conn, chat_id: int) -> list[int] | None:
    """The ordered ids of the last list shown to a chat, or None if none has been shown
    (callers then fall back to the live order)."""
    row = conn.execute(
        "SELECT task_ids FROM display_snapshot WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if row is None:
        return None
    raw = row["task_ids"]
    return [int(part) for part in raw.split(",")] if raw else []
