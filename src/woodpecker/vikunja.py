import logging
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from .models import (
    PRIORITY_IMPORTANT,
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


def vikunja_to_task(raw: dict, project_name: str = "") -> Task:
    done = bool(raw.get("done"))
    description = raw.get("description") or ""
    return Task(
        id=raw["id"],
        text=raw.get("title", ""),
        priority=int(raw.get("priority") or 0),
        deadline=_parse_due(raw.get("due_date")),
        created_at=_parse_dt(raw.get("created")) or datetime.now(_UTC),
        status=STATUS_DONE if done else STATUS_PENDING,
        last_nagged_at=None,
        completed_at=_parse_dt(raw.get("done_at")) if done else None,
        position=raw.get("position") or 0,
        estimate_seconds=parse_estimate_seconds(description),
        details=clean_description(description),
        project_id=int(raw.get("project_id") or 0),
        project_name=project_name,
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
        self._list_view_ids: dict[int, int] = {}

    def close(self) -> None:
        """Close the underlying HTTP connection pool. Woodpecker runs as a long-lived process so
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

    def _list_view_id(self, project_id: int) -> int:
        # Vikunja 2.x makes "position" a per-VIEW concept, so the list endpoint must be
        # addressed through a specific view. Discover each project's list view once and
        # cache it per project, since it does not change during a run.
        if project_id in self._list_view_ids:
            return self._list_view_ids[project_id]
        resp = self._request("GET", f"/api/v1/projects/{project_id}/views")
        if resp is None:
            raise VikunjaError(f"list views got 404 for project {project_id}")
        views = resp.json() or []
        if not views:
            raise VikunjaError(f"project {project_id} has no views")
        view = next((v for v in views if v.get("view_kind") == "list"), views[0])
        self._list_view_ids[project_id] = view["id"]
        return self._list_view_ids[project_id]

    def list_projects(self) -> list[tuple[int, str]]:
        # Enumerate every real project live, so newly created projects are picked up with
        # no config change. Skip Vikunja's pseudo-projects (negative ids, e.g. Favorites)
        # and archived projects, whose tasks should not be nagged about.
        #
        # This endpoint paginates too (see the pagination note in _list_open_project): a
        # page shorter than requested does not mean we are done, only an empty page does.
        raw_projects: list[dict] = []
        for page in range(1, _LIST_OPEN_MAX_PAGES + 1):
            resp = self._request(
                "GET",
                "/api/v1/projects",
                params={"per_page": _LIST_OPEN_PAGE_SIZE, "page": page},
            )
            if resp is None:
                raise VikunjaError("list projects got 404")
            raw = resp.json() or []
            if not raw:
                break
            raw_projects.extend(raw)
        else:
            _log.warning(
                "list_projects hit the %d-page cap; project enumeration may be incomplete",
                _LIST_OPEN_MAX_PAGES,
            )
        return [
            (p["id"], p.get("title", ""))
            for p in raw_projects
            if p["id"] > 0 and not p.get("is_archived", False)
        ]

    def list_open(self) -> list[Task]:
        # Read open tasks from every project and merge them. The write path
        # (create_task) still targets the single configured project; only reading spans
        # all of them. A failure to enumerate projects at all is a total failure (fail
        # loud), but a single project's read failing must not take down the others: log
        # and skip it so the rest of the backlog still surfaces.
        projects = self.list_projects()
        if self._project_id not in {pid for pid, _ in projects}:
            # The configured write-target project may be missing from the enumerated
            # set (e.g. it was archived, or pagination somehow missed it). Tasks Woodpecker
            # itself creates there must never silently vanish from the backlog, so read
            # it directly regardless.
            _log.warning(
                "list_open: write-target project %s not in the enumerated set; reading it directly",
                self._project_id,
            )
            projects = [*projects, (self._project_id, "")]
        tasks: list[Task] = []
        for project_id, project_name in projects:
            try:
                tasks.extend(self._list_open_project(project_id, project_name))
            except VikunjaError:
                _log.warning(
                    "list_open: skipping project %s (%s), read failed",
                    project_id,
                    project_name,
                )
        return tasks

    def _list_open_project(self, project_id: int, project_name: str) -> list[Task]:
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
        view_id = self._list_view_id(project_id)
        tasks: list[Task] = []
        for page in range(1, _LIST_OPEN_MAX_PAGES + 1):
            resp = self._request(
                "GET",
                f"/api/v1/projects/{project_id}/views/{view_id}/tasks",
                params={
                    "filter": "done = false",
                    "sort_by": "position",
                    "order_by": "asc",
                    "per_page": _LIST_OPEN_PAGE_SIZE,
                    "page": page,
                },
            )
            if resp is None:
                raise VikunjaError(f"list_open got 404 for project {project_id}")
            raw = resp.json() or []
            if not raw:
                break
            tasks.extend(vikunja_to_task(item, project_name) for item in raw)
        else:
            _log.warning(
                "list_open hit the %d-page cap for project %d; results may be incomplete",
                _LIST_OPEN_MAX_PAGES,
                project_id,
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

    def update_task(
        self,
        task_id: int,
        deadline: date | None = None,
        priority: str | None = None,
        text: str | None = None,
    ) -> Task | None:
        """Change an existing task's due date, priority and/or title in place. An argument left at
        None means "leave that field as it is", so this never clears a date the user did not
        ask to clear. Read-modify-write like mark_done: Vikunja's POST replaces the object,
        so the stored task is round-tripped with only the named fields overwritten."""
        resp = self._request("GET", f"/api/v1/tasks/{task_id}")
        if resp is None:
            return None
        raw = resp.json()
        if deadline is not None:
            raw["due_date"] = _due_for(deadline)
        if priority is not None:
            raw["priority"] = PRIORITY_IMPORTANT_VALUE if priority == PRIORITY_IMPORTANT else 0
        if text is not None:
            raw["title"] = text
        resp = self._request("POST", f"/api/v1/tasks/{task_id}", json=raw)
        return vikunja_to_task(resp.json()) if resp is not None else None

    def delete_task(self, task_id: int) -> bool:
        return self._request("DELETE", f"/api/v1/tasks/{task_id}") is not None
