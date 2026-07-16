# Vikunja Task Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Vikunja the single source of truth for tasks and slim Jolt down to a conversational quick-capture inbox plus the nagging brain, reading and writing Vikunja over its REST API.

**Architecture:** Jolt is a thin client. A new `vikunja.py` wraps the Vikunja REST API; a new `sidecar.py` keeps Jolt's own SQLite state (`last_nagged_at` and the numbered `display_snapshot`); a new `store.py` composes the two and returns the existing `Task` dataclass, so the rest of Jolt (selection, render, llm, orchestrator, scheduler, bot) keeps working against `Task`. `blocked_by` and the block/unblock/merge/edit intents are removed; Vikunja owns editing and dependencies. A one-time migration script moves current tasks into Vikunja.

**Tech Stack:** Python 3.12+, `httpx` (Vikunja client; already present transitively via python-telegram-bot/anthropic, now made explicit), `sqlite3` (stdlib, sidecar), `anthropic`, `python-telegram-bot`, `APScheduler`, `pytest`, `ruff`, `uv`. Vikunja self-hosted on jarvis (SQLite backend).

## Global Constraints

- Python `>=3.12`. Style enforced by `ruff` (`line-length = 100`). Run `uv run ruff check .` and `uv run ruff format .` before each commit.
- All code, comments, identifiers, commits, docs in **English**. French only in runtime bot content.
- **No em dash (`—`) anywhere** Jolt produces or in code/comments/docs. Use comma, colon, period, or parentheses.
- TDD: write the failing test, run it red, implement minimal code, run it green, commit. External APIs (Vikunja, Anthropic, Telegram) are mocked at the boundary; no real HTTP in tests.
- Tests run with `uv run pytest`. One test file per module. Conventional-commit messages (`feat(...)`, `refactor(...)`, `test(...)`).
- Vikunja is the single source of truth for tasks; Jolt holds no task copy. Jolt governs exactly one Vikunja project (the "Backlog" project id in `VIKUNJA_PROJECT_ID`).
- `important` <-> Vikunja priority `4`; `normal` <-> `0`. `deadline` (date) <-> Vikunja `due_date` at end-of-day Europe/Paris. `dropped` = delete the Vikunja task.

---

## Task 1: Stand up Vikunja on jarvis (ops prerequisite)

Not a TDD task. Produces a running Vikunja and an API token the later tasks integrate against. The user runs the deploy/secret steps (per repo convention, deploys and secrets are user-run). Code tasks 2 to 8 are unit-tested with mocks and do NOT need Vikunja live; only Task 9 (migration) and Task 10 (deploy) need it.

**Files:**
- Create: `~/docker/vikunja/docker-compose.yml` on jarvis (and mirror in the homelab repo `compose/vikunja/docker-compose.yml`)

**Interfaces:**
- Produces: a reachable Vikunja at `https://tasks.example.com` and `http://127.0.0.1:3456`, a "Backlog" project with a known integer id, and an API token string. These become `VIKUNJA_URL`, `VIKUNJA_PROJECT_ID`, `VIKUNJA_TOKEN`.

- [ ] **Step 1: Write the compose file** (SQLite backend, loopback-bound, on the NPM network for the public host)

```yaml
services:
  vikunja:
    image: vikunja/vikunja:0.24
    container_name: vikunja
    restart: unless-stopped
    environment:
      VIKUNJA_SERVICE_PUBLICURL: https://tasks.example.com
      VIKUNJA_DATABASE_TYPE: sqlite
      VIKUNJA_DATABASE_PATH: /db/vikunja.db
      VIKUNJA_SERVICE_TIMEZONE: Europe/Paris
      # Single-user instance: disable open registration once your account exists.
      VIKUNJA_SERVICE_ENABLEREGISTRATION: "true"
    ports:
      - "127.0.0.1:3456:3456"
    volumes:
      - ./db:/db
      - ./files:/app/vikunja/files
    networks:
      - default
      - npm
networks:
  npm:
    external: true
    name: nginx-proxy-manager_default
```

- [ ] **Step 2: User brings it up and creates the account/project/token**

Give the user these exact steps to run in their own terminal:
```bash
# on jarvis
mkdir -p ~/docker/vikunja/db ~/docker/vikunja/files
cd ~/docker/vikunja && docker compose up -d
# then, in the browser via the SSH tunnel or once NPM is set:
#   ssh -L 3456:127.0.0.1:3456 jarvis   (then open http://localhost:3456)
# 1. Register the single account, then set VIKUNJA_SERVICE_ENABLEREGISTRATION=false and `docker compose up -d` again.
# 2. Create a project named "Backlog". Note its id (visible in the URL /projects/<id>).
# 3. Settings -> API Tokens -> create a token with task read/write/delete + project read scope. Copy it.
```

- [ ] **Step 3: Add the NPM proxy host** (user, in the NPM admin UI): `tasks.example.com` -> `vikunja:3456`, force SSL, request a Let's Encrypt cert. Same pattern as the other public hosts.

- [ ] **Step 4: Verify the API answers with the token**

Run (user, over the tunnel or from jarvis):
```bash
curl -s -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:3456/api/v1/projects/<ID>/tasks | head -c 400
```
Expected: a JSON array (likely `[]` or `null` for an empty project), not a 401/403. This confirms URL, token, and project id before wiring Jolt.

- [ ] **Step 5: Record the facts** in the homelab repo's `CLAUDE.md` service list (new Vikunja entry: SQLite, `127.0.0.1:3456`, `tasks.example.com`, backed by restic via the bind mount) and commit the mirrored compose file. Do not commit the token.

> Note: pin the image tag to the actual current Vikunja version at build time and confirm the REST paths against that version's Swagger at `/api/v1/docs` (Vikunja's task-filter query syntax has changed across versions). Tasks 2 and 9 assume: create = `PUT /api/v1/projects/{pid}/tasks`, list = `GET /api/v1/projects/{pid}/tasks`, get = `GET /api/v1/tasks/{id}`, update = `POST /api/v1/tasks/{id}`, delete = `DELETE /api/v1/tasks/{id}`.

---

## Task 2: Vikunja mapping helpers (pure functions)

Pure translation between Jolt's `Task` and Vikunja's task JSON. No HTTP, so fully unit-testable in isolation. Written before the HTTP client so the client can reuse them.

**Files:**
- Create: `src/jolt/vikunja.py` (mapping helpers only in this task; the client class is added in Task 3)
- Test: `tests/test_vikunja.py`

**Interfaces:**
- Produces:
  - `PRIORITY_IMPORTANT_VALUE = 4`
  - `vikunja_to_task(raw: dict) -> Task` (sets `last_nagged_at=None`; `store.py` fills it later)
  - `task_create_payload(text: str, priority: str, deadline: date | None) -> dict`
  - `_parse_due(value: str | None) -> date | None` and `_due_for(deadline: date | None) -> str | None` (end-of-day Europe/Paris, RFC3339, or `None` to omit)
- Consumes: `models.Task`, `models.PRIORITY_IMPORTANT`, `models.PRIORITY_NORMAL`, `models.STATUS_PENDING`, `models.STATUS_DONE`. NOTE: this task depends on Task 4 having added `position` to `Task` and removed `blocked_by`. Do Task 4 first if `Task` still has `blocked_by`. (Sequencing: 4 then 2/3, or add `position` and drop `blocked_by` as the first commit of this task. The plan orders 4 before 2 in execution; keep that order.)

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import vikunja
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING


def _raw(**over):
    base = {
        "id": 7,
        "title": "Call the vet",
        "priority": 0,
        "due_date": "0001-01-01T00:00:00Z",  # Vikunja's "unset" sentinel
        "created": "2026-07-10T08:00:00Z",
        "done": False,
        "done_at": "0001-01-01T00:00:00Z",
        "position": 12,
    }
    base.update(over)
    return base


def test_maps_basic_open_task():
    t = vikunja.vikunja_to_task(_raw())
    assert t.id == 7
    assert t.text == "Call the vet"
    assert t.priority == PRIORITY_NORMAL
    assert t.status == STATUS_PENDING
    assert t.deadline is None
    assert t.position == 12
    assert t.last_nagged_at is None
    assert t.completed_at is None


def test_high_priority_maps_to_important():
    assert vikunja.vikunja_to_task(_raw(priority=4)).priority == PRIORITY_IMPORTANT
    assert vikunja.vikunja_to_task(_raw(priority=5)).priority == PRIORITY_IMPORTANT
    assert vikunja.vikunja_to_task(_raw(priority=3)).priority == PRIORITY_NORMAL


def test_due_date_round_trips_to_local_date():
    t = vikunja.vikunja_to_task(_raw(due_date="2026-07-20T21:59:00Z"))  # 23:59 Paris
    assert t.deadline == date(2026, 7, 20)


def test_done_task_maps_status_and_completed_at():
    t = vikunja.vikunja_to_task(_raw(done=True, done_at="2026-07-11T09:30:00Z"))
    assert t.status == STATUS_DONE
    assert t.completed_at == datetime(2026, 7, 11, 9, 30, tzinfo=ZoneInfo("UTC"))


def test_create_payload_important_with_deadline():
    p = vikunja.task_create_payload("Pay taxes", PRIORITY_IMPORTANT, date(2026, 7, 31))
    assert p["title"] == "Pay taxes"
    assert p["priority"] == 4
    assert p["due_date"].startswith("2026-07-31T")  # end of day Paris


def test_create_payload_no_deadline_omits_field():
    p = vikunja.task_create_payload("tidy desk", PRIORITY_NORMAL, None)
    assert p == {"title": "tidy desk", "priority": 0}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_vikunja.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.vikunja'` (or attribute errors).

- [ ] **Step 3: Implement the mapping helpers**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_vikunja.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/vikunja.py tests/test_vikunja.py
git commit -m "feat(vikunja): add Task <-> Vikunja JSON mapping helpers"
```

---

## Task 3: Vikunja HTTP client

The thin REST client. HTTP is exercised in tests via a fake transport (`httpx.MockTransport`), so no network and no real Vikunja needed.

**Files:**
- Modify: `src/jolt/vikunja.py` (add the client class)
- Modify: `tests/test_vikunja.py` (add client tests)
- Modify: `pyproject.toml` (add `httpx>=0.27` to `dependencies`)

**Interfaces:**
- Consumes: the mapping helpers from Task 2.
- Produces: `class VikunjaClient` with:
  - `__init__(self, base_url: str, token: str, project_id: int, timeout: float = 10.0)`
  - `create_task(self, text: str, priority: str, deadline) -> Task`
  - `list_open(self) -> list[Task]`
  - `get_task(self, task_id: int) -> Task | None` (None on 404)
  - `mark_done(self, task_id: int) -> Task | None` (None on 404)
  - `delete_task(self, task_id: int) -> bool` (False on 404)
  - raises `VikunjaError` on non-404 HTTP failures

- [ ] **Step 1: Write the failing tests** (fake transport records requests and returns canned JSON)

```python
import httpx
import pytest

from jolt import vikunja
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL


def _client(handler):
    transport = httpx.MockTransport(handler)
    c = vikunja.VikunjaClient("http://vk", "tok", project_id=3)
    c._http = httpx.Client(transport=transport, base_url="http://vk")
    return c


def test_create_task_posts_payload_and_returns_task():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 5, "title": "Pay taxes", "priority": 4})

    c = _client(handler)
    task = c.create_task("Pay taxes", PRIORITY_IMPORTANT, None)
    assert seen["method"] == "PUT"
    assert "/api/v1/projects/3/tasks" in seen["url"]
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["priority"] == 4
    assert task.id == 5


def test_list_open_maps_all_returned_tasks():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "title": "a", "priority": 0, "position": 2},
            {"id": 2, "title": "b", "priority": 4, "position": 1},
        ])

    c = _client(handler)
    tasks = c.list_open()
    assert [t.id for t in tasks] == [1, 2]
    assert tasks[1].priority == PRIORITY_IMPORTANT


def test_get_task_returns_none_on_404():
    c = _client(lambda r: httpx.Response(404, json={"message": "not found"}))
    assert c.get_task(99) is None


def test_mark_done_posts_done_true():
    seen = {}

    def handler(request):
        import json
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 5, "title": "x", "done": True,
                                         "done_at": "2026-07-16T09:00:00Z"})

    c = _client(handler)
    task = c.mark_done(5)
    assert seen["method"] == "POST"
    assert "/api/v1/tasks/5" in seen["url"]
    assert seen["body"]["done"] is True
    assert task.completed_at is not None


def test_delete_task_true_on_success_false_on_404():
    assert _client(lambda r: httpx.Response(200, json={})).delete_task(5) is True
    assert _client(lambda r: httpx.Response(404, json={})).delete_task(5) is False


def test_server_error_raises():
    c = _client(lambda r: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(vikunja.VikunjaError):
        c.list_open()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_vikunja.py -v`
Expected: FAIL (`AttributeError: module 'jolt.vikunja' has no attribute 'VikunjaClient'`).

- [ ] **Step 3: Add `httpx` to dependencies**

In `pyproject.toml`, under `[project].dependencies`, add:
```toml
    "httpx>=0.27",
```
Then run `uv sync`.

- [ ] **Step 4: Implement the client** (append to `src/jolt/vikunja.py`)

```python
import httpx


class VikunjaError(RuntimeError):
    """A Vikunja API call failed for a reason other than a 404 (which callers treat as absent)."""


class VikunjaClient:
    def __init__(self, base_url: str, token: str, project_id: int, timeout: float = 10.0):
        self._project_id = project_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def _request(self, method: str, path: str, *, json=None, params=None) -> httpx.Response | None:
        resp = self._http.request(method, path, json=json, params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise VikunjaError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp

    def create_task(self, text, priority, deadline) -> Task:
        payload = task_create_payload(text, priority, deadline)
        resp = self._request("PUT", f"/api/v1/projects/{self._project_id}/tasks", json=payload)
        return vikunja_to_task(resp.json())

    def list_open(self) -> list[Task]:
        # Vikunja returns only undone tasks by default on a project list; the explicit
        # filter guards against that default changing. Verify the filter syntax against the
        # deployed version's /api/v1/docs (it has changed across releases).
        resp = self._request(
            "GET",
            f"/api/v1/projects/{self._project_id}/tasks",
            params={"filter": "done = false", "sort_by": "position", "order_by": "asc",
                    "per_page": 250},
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
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_vikunja.py -v`
Expected: PASS (all mapping + client tests).

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/vikunja.py tests/test_vikunja.py pyproject.toml uv.lock
git commit -m "feat(vikunja): add REST client for tasks (create/list/get/done/delete)"
```

---

## Task 4: Slim `models` and `selection` (remove `blocked_by`, add `position` tiebreaker)

Do this BEFORE tasks 2/3 land if `Task` still carries `blocked_by`; the plan's execution order runs 4 first. Removes the dependency feature from the domain model and the selection logic, and adds Vikunja manual order as the selection tiebreaker.

**Files:**
- Modify: `src/jolt/models.py`
- Modify: `src/jolt/selection.py`
- Modify: `tests/test_selection.py`

**Interfaces:**
- Produces:
  - `Task` with fields: `id, text, priority, deadline, created_at, status, last_nagged_at, completed_at, position` (int, default 0). `blocked_by` removed.
  - `selection.priority_sort_key(task) -> tuple` = `(prio_rank, deadline_rank, position, created_at)`
  - `selection.order_backlog(tasks) -> list[Task]` = pending tasks sorted by `priority_sort_key`
  - `select_daily_focus`, `select_slow_resurface`, `is_stale`, `is_quiet_hours`, `nag_stance` unchanged in signature; internals no longer reference blocking.
  - `is_blocked` REMOVED.
- Consumes: nothing new.

- [ ] **Step 1: Update `models.py`**

Remove `blocked_by` from `Task`; add `position`:
```python
@dataclass(frozen=True)
class Task:
    id: int
    text: str
    priority: str
    deadline: date | None
    created_at: datetime
    status: str
    last_nagged_at: datetime | None
    completed_at: datetime | None
    position: int = 0
```

- [ ] **Step 2: Write/adjust the failing selection tests**

Add the tiebreaker test and remove blocked-dependency tests. New test:
```python
from datetime import datetime
from zoneinfo import ZoneInfo

from jolt.models import PRIORITY_NORMAL, STATUS_PENDING, Task
from jolt import selection


def _t(id, position, created="2026-07-10T08:00:00+00:00"):
    return Task(id=id, text=f"t{id}", priority=PRIORITY_NORMAL, deadline=None,
                created_at=datetime.fromisoformat(created), status=STATUS_PENDING,
                last_nagged_at=None, completed_at=None, position=position)


def test_position_breaks_ties_when_priority_and_deadline_equal():
    tasks = [_t(1, position=5), _t(2, position=1), _t(3, position=3)]
    ordered = selection.order_backlog(tasks)
    assert [t.id for t in ordered] == [2, 3, 1]
```
Delete from `tests/test_selection.py` every test that constructs a `Task` with `blocked_by=` or asserts blocked ordering/nesting/`is_blocked`. Update all remaining `Task(...)` constructions in this file to drop `blocked_by=` and (where relevant) pass `position=`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_selection.py -v`
Expected: FAIL (new tiebreaker test fails; `is_blocked`/`blocked_by` references error).

- [ ] **Step 4: Implement the slimmed `selection.py`**

Replace `priority_sort_key`, remove `is_blocked`, and simplify `order_backlog`:
```python
def priority_sort_key(task: Task) -> tuple:
    prio_rank = 0 if task.priority == PRIORITY_IMPORTANT else 1
    deadline_rank = task.deadline or date.max
    return (prio_rank, deadline_rank, task.position, task.created_at)


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    return sorted(pending, key=priority_sort_key)
```
In `select_daily_focus`, drop the `is_blocked` filter:
```python
def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    ordered = order_backlog(tasks)
    if not ordered:
        return DailyFocus(focus=None, rescues=[])
    stale_important = [
        t for t in ordered if t.priority == PRIORITY_IMPORTANT and is_stale(t, now)
    ]
    focus = stale_important[0] if stale_important else ordered[0]
    rescues = [t for t in ordered if t.id != focus.id and is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)
```
In `select_slow_resurface`, remove the `and not is_blocked(tasks...)` clause from the eligibility comprehension.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_selection.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/models.py src/jolt/selection.py tests/test_selection.py
git commit -m "refactor(model): drop blocked_by, add Vikunja position tiebreaker"
```

---

## Task 5: Slim `render` (flat list, no dependency chains)

Removes the blocked-chain nesting. Keeps a flat, grouped, numbered read-only backlog for the daily focus message and the `list` command (kept: it preserves the numbered "N done" completion flow from Telegram, which is core to the nag loop; only task *editing* moves to Vikunja).

**Files:**
- Modify: `src/jolt/render.py`
- Modify: `tests/test_render.py`

**Interfaces:**
- Produces: `render.display_order(tasks, now) -> list[Task]` and `render.render_backlog(tasks, now) -> str`, both without any blocked/nesting behavior. `_chain` REMOVED.
- Consumes: `selection.order_backlog`.

- [ ] **Step 1: Adjust the failing tests**

In `tests/test_render.py`, delete every test asserting nesting/`↳`/blocked chains, and drop `blocked_by=` from all `Task(...)` constructions. Keep/adjust tests for deadline grouping, numbering, and the empty case. Add:
```python
def test_flat_numbering_follows_group_order():
    # two no-deadline pending tasks render as "1." and "2." with no indentation
    ...
    assert "↳" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL (references to removed `_chain`/nesting).

- [ ] **Step 3: Implement the flat render**

Rewrite `_grouped`, `display_order`, `render_backlog` to group by each task's own `deadline` with no chain logic, and remove `_chain`:
```python
def _grouped(tasks: list[Task], today: date) -> dict[str, list[Task]]:
    ordered = order_backlog(tasks)
    grouped: dict[str, list[Task]] = {key: [] for key, _ in _GROUPS}
    for task in ordered:
        grouped[_bucket(task.deadline, today)].append(task)
    return grouped


def display_order(tasks: list[Task], now: datetime) -> list[Task]:
    grouped = _grouped(tasks, now.date())
    return [task for key, _ in _GROUPS for task in grouped[key]]


def render_backlog(tasks: list[Task], now: datetime) -> str:
    today = now.date()
    ordered = display_order(tasks, now)
    if not ordered:
        return "Backlog empty. Nice."
    position = {task.id: i for i, task in enumerate(ordered, 1)}
    grouped = _grouped(tasks, today)
    count = len(ordered)
    lines = [f"📋 {count} task{'s' if count != 1 else ''}"]
    for key, header in _GROUPS:
        group = grouped[key]
        if not group:
            continue
        lines.append("")
        lines.append(header)
        for task in group:
            suffix = _date_suffix(task.deadline, today)
            lines.append(f"{position[task.id]}. {task.text}{suffix}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/render.py tests/test_render.py
git commit -m "refactor(render): flat backlog, remove dependency nesting"
```

---

## Task 6: `sidecar.py` (Jolt's own SQLite state)

Holds only `last_nagged_at` (keyed by Vikunja task id) and the `display_snapshot` numbered-list table. The `display_snapshot` functions move here verbatim from the old `db.py`.

**Files:**
- Create: `src/jolt/sidecar.py`
- Test: `tests/test_sidecar.py`

**Interfaces:**
- Produces:
  - `connect(path: str) -> sqlite3.Connection`
  - `init_db(conn) -> None`
  - `set_last_nagged(conn, task_id: int, when: datetime) -> None`
  - `last_nagged_map(conn) -> dict[int, datetime]`
  - `save_display(conn, chat_id: int, task_ids: list[int]) -> None`
  - `load_display(conn, chat_id: int) -> list[int] | None`
  - `prune(conn, live_ids: set[int]) -> None` (delete nag rows for ids no longer live)

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone

from jolt import sidecar


def _conn():
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    return c


def test_last_nagged_round_trip():
    c = _conn()
    when = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 42, when)
    assert sidecar.last_nagged_map(c) == {42: when}


def test_set_last_nagged_is_upsert():
    c = _conn()
    sidecar.set_last_nagged(c, 42, datetime(2026, 7, 15, tzinfo=timezone.utc))
    later = datetime(2026, 7, 16, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 42, later)
    assert sidecar.last_nagged_map(c)[42] == later


def test_display_snapshot_round_trip():
    c = _conn()
    sidecar.save_display(c, 100, [3, 1, 2])
    assert sidecar.load_display(c, 100) == [3, 1, 2]
    assert sidecar.load_display(c, 999) is None


def test_prune_drops_stale_nag_rows():
    c = _conn()
    sidecar.set_last_nagged(c, 1, datetime(2026, 7, 16, tzinfo=timezone.utc))
    sidecar.set_last_nagged(c, 2, datetime(2026, 7, 16, tzinfo=timezone.utc))
    sidecar.prune(c, live_ids={1})
    assert set(sidecar.last_nagged_map(c)) == {1}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sidecar.py -v`
Expected: FAIL (`No module named 'jolt.sidecar'`).

- [ ] **Step 3: Implement `sidecar.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sidecar.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/sidecar.py tests/test_sidecar.py
git commit -m "feat(sidecar): Jolt-owned SQLite for nag state and display snapshot"
```

---

## Task 7: `store.py` (compose Vikunja + sidecar into Task operations)

The seam the rest of Jolt calls. Reads from Vikunja, merges `last_nagged_at` from the sidecar onto each `Task`, and writes changes back to Vikunja / sidecar. Replaces the old `db.py` call surface used by orchestrator/scheduler/bot.

**Files:**
- Create: `src/jolt/store.py`
- Test: `tests/test_store.py`
- Delete: `src/jolt/db.py` and `tests/test_db.py` (superseded; done in this task's final step)

**Interfaces:**
- Consumes: `VikunjaClient` (Task 3), `sidecar` (Task 6), `Task`.
- Produces: `class Store`:
  - `__init__(self, vikunja, sidecar_conn)`
  - `add_task(self, text, priority, deadline, now) -> Task`
  - `list_pending(self) -> list[Task]` (Vikunja open tasks, each with `last_nagged_at` merged in)
  - `get_task(self, task_id) -> Task | None`
  - `complete_task(self, task_id, now) -> Task | None`
  - `drop_task(self, task_id) -> bool`
  - `mark_nagged(self, task_id, when) -> None`
  - `save_display(self, chat_id, task_ids) -> None`
  - `load_display(self, chat_id) -> list[int] | None`

- [ ] **Step 1: Write the failing tests** (fake Vikunja client, in-memory sidecar)

```python
from datetime import date, datetime, timezone

from jolt import sidecar, store
from jolt.models import PRIORITY_NORMAL, STATUS_PENDING, Task


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self.deleted = []
        self.completed = []

    def list_open(self):
        return list(self._open.values())

    def create_task(self, text, priority, deadline):
        t = Task(id=99, text=text, priority=priority, deadline=deadline,
                 created_at=datetime.now(timezone.utc), status=STATUS_PENDING,
                 last_nagged_at=None, completed_at=None, position=0)
        self._open[t.id] = t
        return t

    def get_task(self, task_id):
        return self._open.get(task_id)

    def mark_done(self, task_id):
        self.completed.append(task_id)
        return self._open.get(task_id)

    def delete_task(self, task_id):
        if task_id in self._open:
            self.deleted.append(task_id)
            return True
        return False


def _task(id, **over):
    base = dict(text=f"t{id}", priority=PRIORITY_NORMAL, deadline=None,
               created_at=datetime(2026, 7, 10, tzinfo=timezone.utc), status=STATUS_PENDING,
               last_nagged_at=None, completed_at=None, position=0)
    base.update(over)
    return Task(id=id, **base)


def _store(vk):
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    return store.Store(vk, c), c


def test_list_pending_merges_last_nagged_from_sidecar():
    vk = FakeVikunja([_task(1), _task(2)])
    s, c = _store(vk)
    when = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 2, when)
    by_id = {t.id: t for t in s.list_pending()}
    assert by_id[1].last_nagged_at is None
    assert by_id[2].last_nagged_at == when


def test_mark_nagged_persists_and_shows_next_read():
    vk = FakeVikunja([_task(1)])
    s, c = _store(vk)
    when = datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc)
    s.mark_nagged(1, when)
    assert s.list_pending()[0].last_nagged_at == when


def test_complete_calls_vikunja_mark_done():
    vk = FakeVikunja([_task(1)])
    s, _ = _store(vk)
    s.complete_task(1, datetime.now(timezone.utc))
    assert vk.completed == [1]


def test_complete_missing_returns_none():
    vk = FakeVikunja([])
    s, _ = _store(vk)
    assert s.complete_task(123, datetime.now(timezone.utc)) is None


def test_drop_deletes_in_vikunja():
    vk = FakeVikunja([_task(1)])
    s, _ = _store(vk)
    assert s.drop_task(1) is True
    assert vk.deleted == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL (`No module named 'jolt.store'`).

- [ ] **Step 3: Implement `store.py`**

```python
import logging
from dataclasses import replace
from datetime import date, datetime

from . import sidecar
from .models import Task

logger = logging.getLogger(__name__)


class Store:
    """The task-storage seam. Vikunja is the source of truth for tasks; the sidecar holds
    Jolt's own nag state and the display snapshot. Returns and accepts the Task dataclass so
    the rest of Jolt is unaware of Vikunja."""

    def __init__(self, vikunja, sidecar_conn):
        self._vk = vikunja
        self._conn = sidecar_conn

    def add_task(self, text: str, priority: str, deadline: date | None, now: datetime) -> Task:
        return self._vk.create_task(text, priority, deadline)

    def list_pending(self) -> list[Task]:
        nagged = sidecar.last_nagged_map(self._conn)
        tasks = [
            replace(t, last_nagged_at=nagged.get(t.id)) for t in self._vk.list_open()
        ]
        sidecar.prune(self._conn, {t.id for t in tasks})
        return tasks

    def get_task(self, task_id: int) -> Task | None:
        task = self._vk.get_task(task_id)
        if task is None:
            return None
        nagged = sidecar.last_nagged_map(self._conn)
        return replace(task, last_nagged_at=nagged.get(task.id))

    def complete_task(self, task_id: int, now: datetime) -> Task | None:
        return self._vk.mark_done(task_id)

    def drop_task(self, task_id: int) -> bool:
        return self._vk.delete_task(task_id)

    def mark_nagged(self, task_id: int, when: datetime) -> None:
        sidecar.set_last_nagged(self._conn, task_id, when)

    def save_display(self, chat_id: int, task_ids: list[int]) -> None:
        sidecar.save_display(self._conn, chat_id, task_ids)

    def load_display(self, chat_id: int) -> list[int] | None:
        return sidecar.load_display(self._conn, chat_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Delete the old storage module**

```bash
git rm src/jolt/db.py tests/test_db.py
```
(The `list`/orchestrator/scheduler/bot rewiring in Task 8 is what removes the last imports of `db`.)

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add -A
git commit -m "feat(store): compose Vikunja + sidecar behind the Task storage seam"
```

---

## Task 8: Rewire orchestrator, llm, scheduler, bot, main onto `Store`

Threads `Store` where `conn`+`db` were, and shrinks the intent set to `add / complete / drop / list / answer` (removing `edit / block / unblock / merge`).

**Files:**
- Modify: `src/jolt/orchestrator.py`, `src/jolt/llm.py`, `src/jolt/scheduler.py`, `src/jolt/bot.py`, `src/jolt/main.py`, `src/jolt/config.py`
- Modify: `tests/test_orchestrator.py`, `tests/test_llm.py`, `tests/test_scheduler.py`, `tests/test_bot.py`, `tests/test_main.py`

**Interfaces:**
- Produces: `orchestrator.apply_intent(store, intent, now) -> str`; `Intent(action, text, priority, deadline, task_id, reply)`; `config.vikunja_url()/vikunja_token()/vikunja_project_id()/sidecar_path()`.
- Consumes: `Store` (Task 7).

- [ ] **Step 1: Shrink `Intent` and the tool schema in `llm.py`**

- `Intent` dataclass: keep `action, text, priority, deadline, task_id, reply`; remove `blocked_by, merge_from, clear_deadline`.
- `parse_intent`: drop the removed fields.
- `_TOOL` `action` enum -> `["add", "complete", "drop", "list", "answer"]`; remove the `blocked_by`, `merge_from`, `clear_deadline` properties; update the `action`/`task_id` descriptions to name only add/complete/drop/list.
- `_ID_FIELDS = {"complete": ("task_id",), "drop": ("task_id",)}`; `_ALL_ID_FIELDS = ("task_id",)`.
- System prompt: delete the block/unblock/merge/edit paragraphs; keep add/complete/drop/list/answer, number-resolution, frog/tadpole, em-dash rule, "store text in English", relative-date resolution.

- [ ] **Step 2: Update `llm.py` tests**

In `tests/test_llm.py`, delete tests asserting `edit`/`block`/`unblock`/`merge`/`clear_deadline` behavior and any `blocked_by=`/`merge_from=` usage; keep add/complete/drop/list/answer, multi-intent, position-resolution, truncation, and malformed-block tests. Run: `uv run pytest tests/test_llm.py -v` and expect FAIL before the `llm.py` edits, PASS after.

- [ ] **Step 3: Shrink `orchestrator.py`**

Replace the module so `apply_intent(store, intent, now)` handles only add/complete/drop/list/answer; remove `_would_cycle` and the edit/block/unblock/merge branches:
```python
import logging
from datetime import datetime

from .llm import Intent
from .models import PRIORITY_NORMAL
from .render import render_backlog

logger = logging.getLogger(__name__)


def apply_intent(store, intent: Intent, now: datetime) -> str:
    logger.info("Applying intent action=%s task_id=%s", intent.action, intent.task_id)
    if intent.action == "add":
        task = store.add_task(intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now)
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack
    if intent.action == "complete":
        task = store.complete_task(intent.task_id, now)
        return "Done, nice." if task else "Couldn't find that one."
    if intent.action == "drop":
        ok = store.drop_task(intent.task_id)
        return "Dropped." if ok else "Couldn't find that one."
    if intent.action == "list":
        return render_backlog(store.list_pending(), now)
    return intent.reply or "Not sure what you mean. Try rephrasing?"
```

- [ ] **Step 4: Update `orchestrator` tests**

Rewrite `tests/test_orchestrator.py` to pass a fake `Store` (same shape as Task 7's `FakeVikunja`-backed store, or a minimal stub exposing add/complete/drop/list_pending) and assert the add/complete/drop/list/answer replies. Delete edit/block/merge tests. Run red then green.

- [ ] **Step 5: Rewire `scheduler.py`**

Replace `conn`/`db` with `store`: `db.list_all(conn)` -> `store.list_pending()`; `db.save_display(conn, ...)` -> `store.save_display(...)`; `db.mark_nagged(conn, ...)` -> `store.mark_nagged(...)`. Signatures become `send_daily_focus(store, send, client, now, chat_id)` and `send_nags(store, send, client, now)`. Update `tests/test_scheduler.py` to inject a fake store. Run red then green.

- [ ] **Step 6: Rewire `bot.py`**

In `handle_message`: `conn = ...` becomes `store = context.bot_data["store"]`; `db.list_all(conn)` -> `store.list_pending()`; `db.load_display(conn, chat_id)` -> `store.load_display(chat_id)`; `orchestrator.apply_intent(conn, ...)` -> `orchestrator.apply_intent(store, ...)`; the post-`list` snapshot save uses `store.save_display(chat_id, [t.id for t in render.display_order(store.list_pending(), now)])`. Update `tests/test_bot.py` fakes (`bot_data["store"]`). Run red then green.

- [ ] **Step 7: Update `config.py` and `main.py`**

Add to `config.py`:
```python
def vikunja_url() -> str:
    return os.environ["VIKUNJA_URL"]


def vikunja_token() -> str:
    return os.environ["VIKUNJA_TOKEN"]


def vikunja_project_id() -> int:
    return int(os.environ["VIKUNJA_PROJECT_ID"])


def sidecar_path() -> str:
    return os.environ.get("JOLT_DB_PATH", "data/jolt.db")
```
In `main.py`, replace the db wiring:
```python
from . import bot, config, llm, scheduler, sidecar
from .store import Store
from .vikunja import VikunjaClient
...
    conn = sidecar.connect(config.sidecar_path())
    sidecar.init_db(conn)
    vk = VikunjaClient(config.vikunja_url(), config.vikunja_token(), config.vikunja_project_id())
    store = Store(vk, conn)
    client = llm.build_client(config.anthropic_api_key())
    ...
    application.bot_data.update({"store": store, "client": client, "chat_id": chat_id, "memory": memory})
```
`build_scheduler(store, send, client, chat_id)` and the two coroutine jobs pass `store`. Update `tests/test_main.py` accordingly. Run `uv run pytest tests/test_main.py -v` red then green.

- [ ] **Step 8: Full suite green**

Run: `uv run pytest`
Expected: PASS across all files. Fix any lingering `db`/`blocked_by`/`conn` references surfaced here.

- [ ] **Step 9: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add -A
git commit -m "refactor: run Jolt on the Vikunja-backed store; drop edit/block/merge intents"
```

---

## Task 9: One-time migration script

Moves current `jolt.db` tasks into Vikunja and seeds the sidecar nag state. Run once, manually, at cutover.

**Files:**
- Create: `scripts/migrate_to_vikunja.py`
- Test: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `VikunjaClient` (Task 3), `sidecar` (Task 6), the OLD `jolt.db` schema (raw `sqlite3`, since `db.py` is deleted).
- Produces: `migrate(old_db_path, vikunja, sidecar_conn, now, history_days=90) -> dict` returning counts `{"pending": n, "done": m, "skipped_dropped": k}`; raises `MigrationAborted` if the Vikunja Backlog is not empty.

- [ ] **Step 1: Write the failing tests**

```python
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from jolt import sidecar
from scripts import migrate_to_vikunja as m


def _old_db(path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text TEXT, priority TEXT, "
              "deadline TEXT, created_at TEXT, status TEXT, last_nagged_at TEXT, "
              "completed_at TEXT, blocked_by INTEGER)")
    c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()


class FakeVikunja:
    def __init__(self, empty=True):
        self._empty = empty
        self.created = []
        self.completed = []
        self._next = 1000

    def list_open(self):
        return [] if self._empty else [object()]

    def create_task(self, text, priority, deadline):
        from jolt.models import STATUS_PENDING, Task
        self._next += 1
        t = Task(id=self._next, text=text, priority=priority, deadline=deadline,
                 created_at=datetime.now(timezone.utc), status=STATUS_PENDING,
                 last_nagged_at=None, completed_at=None, position=0)
        self.created.append(t)
        return t

    def mark_done(self, task_id):
        self.completed.append(task_id)
        return None


def test_aborts_if_backlog_not_empty(tmp_path):
    p = str(tmp_path / "old.db"); _old_db(p, [])
    c = sidecar.connect(":memory:"); sidecar.init_db(c)
    with pytest.raises(m.MigrationAborted):
        m.migrate(p, FakeVikunja(empty=False), c, datetime.now(timezone.utc))


def test_imports_pending_and_seeds_last_nagged(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    _old_db(p, [
        (1, "Pay taxes", "important", "2026-07-31", "2026-07-10T08:00:00+00:00",
         "pending", "2026-07-15T09:00:00+00:00", None, None),
    ])
    c = sidecar.connect(":memory:"); sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["pending"] == 1
    assert vk.created[0].text == "Pay taxes"
    # last_nagged seeded under the NEW vikunja id
    assert sidecar.last_nagged_map(c)[vk.created[0].id].isoformat().startswith("2026-07-15")


def test_recent_done_imported_old_done_skipped(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    recent = (now - timedelta(days=10)).date().isoformat()
    old = (now - timedelta(days=200)).date().isoformat()
    _old_db(p, [
        (1, "recent done", "normal", None, "2026-01-01T00:00:00+00:00", "done", None,
         f"{recent}T00:00:00+00:00", None),
        (2, "ancient done", "normal", None, "2025-01-01T00:00:00+00:00", "done", None,
         f"{old}T00:00:00+00:00", None),
    ])
    c = sidecar.connect(":memory:"); sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["done"] == 1
    assert [t.text for t in vk.created] == ["recent done"]
    assert len(vk.completed) == 1


def test_dropped_skipped(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    _old_db(p, [(1, "x", "normal", None, "2026-07-10T00:00:00+00:00", "dropped", None, None, None)])
    c = sidecar.connect(":memory:"); sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["skipped_dropped"] == 1
    assert vk.created == []
```

Add an empty `scripts/__init__.py` if needed so `from scripts import ...` imports under the `pythonpath` (or add `"."` to `pythonpath` in `pyproject.toml [tool.pytest.ini_options]`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_migrate.py -v`
Expected: FAIL (`No module named 'scripts.migrate_to_vikunja'`).

- [ ] **Step 3: Implement the migration**

```python
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
    return datetime.fromisoformat(value) if value else None


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
            created = vikunja.create_task(row["text"], row["priority"], _parse_date(row["deadline"]))
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
    vk = VikunjaClient(os.environ["VIKUNJA_URL"], os.environ["VIKUNJA_TOKEN"],
                       int(os.environ["VIKUNJA_PROJECT_ID"]))
    conn = sc.connect(sidecar_path)
    sc.init_db(conn)
    counts = migrate(old_db_path, vk, conn, datetime.now(timezone.utc))
    print(f"Migration complete: {counts}")


if __name__ == "__main__":
    main(sys.argv)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_migrate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/migrate_to_vikunja.py tests/test_migrate.py pyproject.toml
git commit -m "feat(migrate): one-time import of old Jolt tasks into Vikunja"
```

---

## Task 10: Deploy config and docs

Wires the new env vars into the container and updates docs. Deploy itself (push -> runner) and the migration run are user-executed at cutover.

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `CLAUDE.md`, `README.md`
- Modify (homelab repo): `CLAUDE.md` service list (Vikunja entry, done in Task 1 Step 5)

- [ ] **Step 1: Update `.env.example`**

Replace the SQLite-task hint with Vikunja vars (keep `JOLT_DB_PATH` for the sidecar):
```
# Vikunja API (task source of truth)
VIKUNJA_URL=https://tasks.example.com
VIKUNJA_TOKEN=
VIKUNJA_PROJECT_ID=
# Sidecar SQLite (Jolt's own nag state + display snapshot), defaults to data/jolt.db
JOLT_DB_PATH=data/jolt.db
```

- [ ] **Step 2: Update `docker-compose.yml`** so `env_file: .env` carries the Vikunja vars (no compose change needed beyond confirming `.env` has them; the `./data` mount now stores only the sidecar). Add a one-line comment noting `./data` is the sidecar, not the task store.

- [ ] **Step 3: Update `CLAUDE.md` and `README.md`** in the jolt repo: architecture now "Vikunja is the task store; Jolt is the nagging brain over its API"; storage section describes `vikunja.py` + `sidecar.py` + `store.py`; module list updated; note `blocked_by`/edit/block/merge removed. Do not add em dashes.

- [ ] **Step 4: Commit**

```bash
git add .env.example docker-compose.yml CLAUDE.md README.md
git commit -m "docs: document Vikunja-backed architecture and env vars"
```

- [ ] **Step 5: Cutover runbook** (user-executed, in order; document these in the commit body or a `docs/` note):
  1. Vikunja is up (Task 1) with the Backlog project and token.
  2. Stop Jolt on jarvis: `cd ~/docker/jolt && docker compose stop`.
  3. Run the migration against the live old DB and the (same) sidecar path:
     `VIKUNJA_URL=... VIKUNJA_TOKEN=... VIKUNJA_PROJECT_ID=... uv run python -m scripts.migrate_to_vikunja ~/docker/jolt/data/jolt.db ~/docker/jolt/data/sidecar.db`
     (Use a NEW sidecar filename so the old tasks table file is kept untouched as an archive; set `JOLT_DB_PATH` to that new file.)
  4. Put the Vikunja vars and the new `JOLT_DB_PATH` into `~/docker/jolt/.env`.
  5. `git push` Jolt (runner auto-deploys) or `docker compose up -d` from the runner dir.
  6. Verify: send a task in Telegram, confirm it appears in the Vikunja app; complete it from Telegram, confirm it closes in Vikunja; wait for / trigger a nag.

---

## Self-Review

**Spec coverage:**
- Vikunja standup (SQLite, loopback+tailnet, NPM, restic via bind mount): Task 1. ✓
- Thin client, Vikunja is truth: Tasks 3 + 7 (no task copy; live reads). ✓
- One dedicated Backlog project: `VIKUNJA_PROJECT_ID`, Task 1 + client scoping. ✓
- Sidecar for `last_nagged_at` + `display_snapshot`: Task 6. ✓
- Approach A storage swap, `Task` kept: Tasks 4, 7. ✓
- Field mapping (priority, date<->datetime, done, delete, position): Task 2. ✓
- `dropped` = delete: Task 7 (`drop_task`), Task 8 (orchestrator drop). ✓
- `blocked_by` removed; edit/block/merge shed: Tasks 4, 5, 8. ✓
- Position tiebreaker: Task 4. ✓
- Migration (pending + 90-day done, seed nag state, abort if non-empty): Task 9. ✓
- Error handling (404 -> "couldn't find"/"gone"; scheduler never crashes): Task 3 (404->None), Task 8 scheduler keeps its existing try/except per-nag structure. NOTE for implementer: preserve scheduler's existing try/except so a Vikunja outage during a job logs + sends the fallback line rather than crashing; add a guard around `store.list_pending()` in both jobs. ✓
- Testing philosophy (mock at boundary): every code task. ✓
- Deploy/docs: Task 10. ✓

**Placeholder scan:** No "TBD"/"implement later". The one soft spot is exact Vikunja REST paths/filter syntax (Task 1 note + Task 3 comment): concrete code is given, with a required verify-against-Swagger step because the API differs by version. That is a verification step, not a placeholder.

**Type consistency:** `Task` fields (with `position`, without `blocked_by`) are consistent across Tasks 2, 4, 5, 7, 9. `Store` method names match between Task 7 and their callers in Task 8. `VikunjaClient` methods match between Task 3 and Task 7's `FakeVikunja` shape and Task 9's `FakeVikunja`.

**Deviation from spec to confirm with the user:** the spec's "trim the full backlog list" is implemented as *keeping* a flat read-only `list` command + numbered completion (Task 5/8), rather than removing it, because that flow is core to completing a nagged task from Telegram. Editing still moves to Vikunja. Flag at execution start.
