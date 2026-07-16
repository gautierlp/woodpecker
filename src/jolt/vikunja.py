from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .models import (
    PRIORITY_IMPORTANT,
    PRIORITY_NORMAL,
    STATUS_DONE,
    STATUS_PENDING,
    Task,
)

PRIORITY_IMPORTANT_VALUE = 4
_PARIS = ZoneInfo("Europe/Paris")
_UTC = ZoneInfo("UTC")
# Vikunja represents an unset date as year 0001.
_UNSET_PREFIX = "0001-01-01"


def _parse_dt(value: str | None) -> datetime | None:
    if not value or value.startswith(_UNSET_PREFIX):
        return None
    # Vikunja returns RFC3339 with a trailing Z; make it fromisoformat-friendly.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_due(value: str | None) -> date | None:
    dt = _parse_dt(value)
    return dt.astimezone(_PARIS).date() if dt else None


def _due_for(deadline: date | None) -> str | None:
    if deadline is None:
        return None
    end_of_day = datetime.combine(deadline, time(23, 59), tzinfo=_PARIS)
    return end_of_day.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def vikunja_to_task(raw: dict) -> Task:
    done = bool(raw.get("done"))
    return Task(
        id=raw["id"],
        text=raw.get("title", ""),
        priority=PRIORITY_IMPORTANT
        if (raw.get("priority") or 0) >= PRIORITY_IMPORTANT_VALUE
        else PRIORITY_NORMAL,
        deadline=_parse_due(raw.get("due_date")),
        created_at=_parse_dt(raw.get("created")) or datetime.now(_UTC),
        status=STATUS_DONE if done else STATUS_PENDING,
        last_nagged_at=None,
        completed_at=_parse_dt(raw.get("done_at")) if done else None,
        position=raw.get("position") or 0,
    )


def task_create_payload(text: str, priority: str, deadline: date | None) -> dict:
    payload = {
        "title": text,
        "priority": PRIORITY_IMPORTANT_VALUE if priority == PRIORITY_IMPORTANT else 0,
    }
    due = _due_for(deadline)
    if due is not None:
        payload["due_date"] = due
    return payload
