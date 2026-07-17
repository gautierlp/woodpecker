import logging
import re
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

_log = logging.getLogger(__name__)

PRIORITY_IMPORTANT_VALUE = 4
_LIST_OPEN_PAGE_SIZE = 250
_LIST_OPEN_MAX_PAGES = 100
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
    end_of_day = datetime.combine(deadline, time(23, 59, 59), tzinfo=_PARIS)
    return end_of_day.astimezone(_UTC).isoformat().replace("+00:00", "Z")


# mdone (the task app) has no native duration field, so it stores an estimate as an HTML
# comment inside the Vikunja description: "<!-- mdone:estimate=SECONDS -->". We parse the
# seconds out and strip the comment so the visible description is clean text.
_ESTIMATE_RE = re.compile(r"<!--\s*mdone:estimate=(\d+)\s*-->")


def parse_estimate_seconds(description: str | None) -> int | None:
    if not description:
        return None
    match = _ESTIMATE_RE.search(description)
    return int(match.group(1)) if match else None


def clean_description(description: str | None) -> str:
    if not description:
        return ""
    return _ESTIMATE_RE.sub("", description).strip()


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
        self._cached_list_view_id: int | None = None

    def close(self) -> None:
        """Close the underlying HTTP connection pool. Jolt runs as a long-lived process so
        this is rarely needed there, but it lets callers (and tests) release sockets cleanly."""
        self._http.close()

    def __enter__(self) -> "VikunjaClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response | None:
        # Auth is attached per-request (rather than baked into the httpx.Client at
        # construction) so it still applies even if the client instance is swapped out,
        # e.g. tests replace `_http` with a MockTransport-backed client.
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = self._http.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise VikunjaError(f"{method} {path} failed: {exc}") from exc
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

    def _list_view_id(self) -> int:
        # Vikunja 2.x makes "position" a per-VIEW concept (not per-project), and
        # /projects/{id}/tasks?sort_by=position 400s with "You must provide a project
        # view ID when sorting by position". So the list endpoint must be addressed
        # through a specific view. We discover the project's list view once and cache
        # it on the client instance, since it does not change during a run.
        if self._cached_list_view_id is not None:
            return self._cached_list_view_id
        resp = self._request("GET", f"/api/v1/projects/{self._project_id}/views")
        if resp is None:
            raise VikunjaError(f"list views got 404 for project {self._project_id}")
        views = resp.json() or []
        if not views:
            raise VikunjaError(f"project {self._project_id} has no views")
        view = next((v for v in views if v.get("view_kind") == "list"), views[0])
        self._cached_list_view_id = view["id"]
        return self._cached_list_view_id

    def list_open(self) -> list[Task]:
        # Vikunja returns only undone tasks by default on a project list; the explicit
        # filter guards against that default changing. Verify the filter syntax against the
        # deployed version's /api/v1/docs (it has changed across releases).
        #
        # Vikunja paginates project-task listings, so a single page can silently drop tasks
        # once the backlog grows past per_page. The server also enforces its own
        # max_items_per_page (50 by default) regardless of what we request, so a page
        # shorter than our requested per_page does NOT mean we're done: it just means the
        # server capped it. The only reliable end-of-list signal is an EMPTY page. We loop
        # until that happens, capping at _LIST_OPEN_MAX_PAGES to avoid ever looping forever
        # against a misbehaving server.
        view_id = self._list_view_id()
        tasks: list[Task] = []
        for page in range(1, _LIST_OPEN_MAX_PAGES + 1):
            resp = self._request(
                "GET",
                f"/api/v1/projects/{self._project_id}/views/{view_id}/tasks",
                params={
                    "filter": "done = false",
                    "sort_by": "position",
                    "order_by": "asc",
                    "per_page": _LIST_OPEN_PAGE_SIZE,
                    "page": page,
                },
            )
            if resp is None:
                raise VikunjaError(f"list_open got 404 for project {self._project_id}")
            raw = resp.json() or []
            if not raw:
                break
            tasks.extend(vikunja_to_task(item) for item in raw)
        else:
            _log.warning(
                "list_open hit the %d-page cap for project %d; results may be incomplete",
                _LIST_OPEN_MAX_PAGES,
                self._project_id,
            )
        return tasks

    def get_task(self, task_id: int) -> Task | None:
        resp = self._request("GET", f"/api/v1/tasks/{task_id}")
        return vikunja_to_task(resp.json()) if resp is not None else None

    def mark_done(self, task_id: int) -> Task | None:
        resp = self._request("GET", f"/api/v1/tasks/{task_id}")
        if resp is None:
            return None
        raw = resp.json()
        raw["done"] = True
        resp = self._request("POST", f"/api/v1/tasks/{task_id}", json=raw)
        return vikunja_to_task(resp.json()) if resp is not None else None

    def delete_task(self, task_id: int) -> bool:
        return self._request("DELETE", f"/api/v1/tasks/{task_id}") is not None
