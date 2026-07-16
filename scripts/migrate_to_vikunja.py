"""One-time migration of the old Jolt SQLite tasks into Vikunja.

Usage:
    VIKUNJA_URL=... VIKUNJA_TOKEN=... VIKUNJA_PROJECT_ID=... \
    uv run python -m scripts.migrate_to_vikunja /path/to/old/jolt.db /path/to/new/sidecar.db
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone


class MigrationAborted(RuntimeError):
    pass


def _parse_dt(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    # Older / hand-edited rows can have a naive timestamp. Treat it as UTC so it can
    # always be compared against the (aware) cutoff without raising.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(value):
    return date.fromisoformat(value) if value else None


def migrate(old_db_path, vikunja, sidecar_conn, now, history_days=90) -> dict:
    if vikunja.list_open():
        raise MigrationAborted(
            "Vikunja Backlog is not empty; refusing to migrate to avoid duplicates."
        )
    from jolt import sidecar as sc

    conn = sqlite3.connect(old_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()

    counts = {"pending": 0, "done": 0, "skipped_dropped": 0}
    cutoff = now - timedelta(days=history_days)
    for row in rows:
        status = row["status"]
        if status == "dropped":
            counts["skipped_dropped"] += 1
            continue
        if status == "done":
            completed = _parse_dt(row["completed_at"])
            if completed is None or completed < cutoff:
                continue
            created = vikunja.create_task(
                row["text"], row["priority"], _parse_date(row["deadline"])
            )
            vikunja.mark_done(created.id)
            counts["done"] += 1
            continue
        # pending
        created = vikunja.create_task(row["text"], row["priority"], _parse_date(row["deadline"]))
        last_nagged = _parse_dt(row["last_nagged_at"])
        if last_nagged is not None:
            sc.set_last_nagged(sidecar_conn, created.id, last_nagged)
        counts["pending"] += 1
    return counts


def main(argv):
    import os

    from jolt import sidecar as sc
    from jolt.vikunja import VikunjaClient

    old_db_path, sidecar_path = argv[1], argv[2]
    vk = VikunjaClient(
        os.environ["VIKUNJA_URL"],
        os.environ["VIKUNJA_TOKEN"],
        int(os.environ["VIKUNJA_PROJECT_ID"]),
    )
    conn = sc.connect(sidecar_path)
    sc.init_db(conn)
    counts = migrate(old_db_path, vk, conn, datetime.now(timezone.utc))
    print(f"Migration complete: {counts}")


if __name__ == "__main__":
    main(sys.argv)
