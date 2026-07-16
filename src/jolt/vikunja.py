from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

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


class VikunjaError(RuntimeError):
    """A Vikunja API call failed for a reason other than a 404 (which callers treat as absent)."""


class VikunjaClient:
    def __init__(self, base_url: str, token: str, project_id: int, timeout: float = 10.0):
        self._project_id = project_id
        self._token = token
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    def _request(self, method: str, path: str, *, json=None, params=None) -> httpx.Response | None:
        # Auth is attached per-request (rather than baked into the httpx.Client at
        # construction) so it still applies even if the client instance is swapped out,
        # e.g. tests replace `_http` with a MockTransport-backed client.
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = self._http.request(method, path, json=json, params=params, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise VikunjaError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp

    def create_task(self, text: str, priority: str, deadline: date | None) -> Task:
        payload = task_create_payload(text, priority, deadline)
        resp = self._request("PUT", f"/api/v1/projects/{self._project_id}/tasks", json=payload)
        if resp is None:
            raise VikunjaError(f"create_task got 404 for project {self._project_id}")
        return vikunja_to_task(resp.json())

    def list_open(self) -> list[Task]:
        # Vikunja returns only undone tasks by default on a project list; the explicit
        # filter guards against that default changing. Verify the filter syntax against the
        # deployed version's /api/v1/docs (it has changed across releases).
        resp = self._request(
            "GET",
            f"/api/v1/projects/{self._project_id}/tasks",
            params={
                "filter": "done = false",
                "sort_by": "position",
                "order_by": "asc",
                "per_page": 250,
            },
        )
        raw = resp.json() or []
        return [vikunja_to_task(item) for item in raw]

    def get_task(self, task_id: int) -> Task | None:
        resp = self._request("GET", f"/api/v1/tasks/{task_id}")
        return vikunja_to_task(resp.json()) if resp is not None else None

    def mark_done(self, task_id: int) -> Task | None:
        resp = self._request("POST", f"/api/v1/tasks/{task_id}", json={"done": True})
        return vikunja_to_task(resp.json()) if resp is not None else None

    def delete_task(self, task_id: int) -> bool:
        return self._request("DELETE", f"/api/v1/tasks/{task_id}") is not None
