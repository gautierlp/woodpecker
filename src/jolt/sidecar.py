import sqlite3
from datetime import datetime

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


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_NAG_SCHEMA)
    conn.execute(_DISPLAY_SCHEMA)
    conn.commit()


def set_last_nagged(conn, task_id: int, when: datetime) -> None:
    conn.execute(
        "INSERT INTO nag_state (task_id, last_nagged_at) VALUES (?, ?) "
        "ON CONFLICT(task_id) DO UPDATE SET last_nagged_at = excluded.last_nagged_at",
        (task_id, when.isoformat()),
    )
    conn.commit()


def last_nagged_map(conn) -> dict[int, datetime]:
    rows = conn.execute("SELECT task_id, last_nagged_at FROM nag_state").fetchall()
    return {row["task_id"]: datetime.fromisoformat(row["last_nagged_at"]) for row in rows}


def save_display(conn, chat_id: int, task_ids: list[int]) -> None:
    conn.execute(
        "INSERT INTO display_snapshot (chat_id, task_ids) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET task_ids = excluded.task_ids",
        (chat_id, ",".join(str(i) for i in task_ids)),
    )
    conn.commit()


def load_display(conn, chat_id: int) -> list[int] | None:
    row = conn.execute(
        "SELECT task_ids FROM display_snapshot WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if row is None:
        return None
    raw = row["task_ids"]
    return [int(part) for part in raw.split(",")] if raw else []


def prune(conn, live_ids: set[int]) -> None:
    ids = {int(r["task_id"]) for r in conn.execute("SELECT task_id FROM nag_state")}
    stale = ids - live_ids
    if stale:
        conn.executemany("DELETE FROM nag_state WHERE task_id = ?", [(i,) for i in stale])
        conn.commit()
