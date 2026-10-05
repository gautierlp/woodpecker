import sqlite3
from datetime import date, datetime

_NAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS nag_state (
    task_id INTEGER PRIMARY KEY,
    last_nagged_at TEXT NOT NULL
);
"""

_DISPLAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS display_snapshot (
    chat_id INTEGER PRIMARY KEY,
    task_ids TEXT NOT NULL
);
"""

_BUMP_SCHEMA = """
CREATE TABLE IF NOT EXISTS bump_state (
    task_id INTEGER PRIMARY KEY,
    last_due_date TEXT,
    bump_count INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_NAG_SCHEMA)
    conn.execute(_DISPLAY_SCHEMA)
    conn.execute(_BUMP_SCHEMA)
    conn.commit()


def set_last_nagged(conn: sqlite3.Connection, task_id: int, when: datetime) -> None:
    conn.execute(
        "INSERT INTO nag_state (task_id, last_nagged_at) VALUES (?, ?) "
        "ON CONFLICT(task_id) DO UPDATE SET last_nagged_at = excluded.last_nagged_at",
        (task_id, when.isoformat()),
    )
    conn.commit()


def last_nagged_map(conn: sqlite3.Connection) -> dict[int, datetime]:
    rows = conn.execute("SELECT task_id, last_nagged_at FROM nag_state").fetchall()
    return {row["task_id"]: datetime.fromisoformat(row["last_nagged_at"]) for row in rows}


def save_display(conn: sqlite3.Connection, chat_id: int, task_ids: list[int]) -> None:
    conn.execute(
        "INSERT INTO display_snapshot (chat_id, task_ids) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET task_ids = excluded.task_ids",
        (chat_id, ",".join(str(i) for i in task_ids)),
    )
    conn.commit()


def load_display(conn: sqlite3.Connection, chat_id: int) -> list[int] | None:
    row = conn.execute(
        "SELECT task_ids FROM display_snapshot WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if row is None:
        return None
    raw = row["task_ids"]
    return [int(part) for part in raw.split(",")] if raw else []


def bump_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT task_id, bump_count FROM bump_state").fetchall()
    return {row["task_id"]: row["bump_count"] for row in rows}


def apply_due_snapshot(conn: sqlite3.Connection, due_by_task: dict[int, date | None]) -> None:
    """Record each task's current due date and count forward moves. A still-tracked task
    whose due date moved forward since last seen gets bump_count incremented. Pulling the
    date in or an unchanged date updates the stored date without a bump. Tasks with no due
    date are not tracked, so a date appearing later starts a fresh count at 0."""
    stored = {
        row["task_id"]: row["last_due_date"]
        for row in conn.execute("SELECT task_id, last_due_date FROM bump_state")
    }
    for task_id, due in due_by_task.items():
        if due is None:
            continue
        due_str = due.isoformat()
        prev = stored.get(task_id)
        if task_id not in stored:
            conn.execute(
                "INSERT INTO bump_state (task_id, last_due_date, bump_count) VALUES (?, ?, 0)",
                (task_id, due_str),
            )
        elif prev is not None and due_str > prev:
            conn.execute(
                "UPDATE bump_state SET last_due_date = ?, bump_count = bump_count + 1 "
                "WHERE task_id = ?",
                (due_str, task_id),
            )
        elif prev != due_str:
            conn.execute(
                "UPDATE bump_state SET last_due_date = ? WHERE task_id = ?",
                (due_str, task_id),
            )
    conn.commit()


def prune(conn: sqlite3.Connection, live_ids: set[int]) -> None:
    for table in ("nag_state", "bump_state"):
        ids = {int(r["task_id"]) for r in conn.execute(f"SELECT task_id FROM {table}")}
        stale = ids - live_ids
        if stale:
            conn.executemany(f"DELETE FROM {table} WHERE task_id = ?", [(i,) for i in stale])
    conn.commit()
