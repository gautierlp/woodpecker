# Bump tracking and frog-first morning message Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start counting how many times each task's due date is pushed forward, and replace the 06:00 full-backlog dump with a frog-first morning message driven by that count and priority.

**Architecture:** Jolt's sidecar SQLite gains a `bump_state` table (last-seen due date + a forward-move counter per task). `Store.list_pending` updates it on every read and injects a `bump_count` onto each `Task`. A new `selection.select_frog` picks the single most-avoided important task; a new `render.render_matters` renders only the priority-worthy due-today/overdue tasks plus a summary line; `llm.write_focus` and `scheduler.send_daily_focus` are reworked to lead on the frog. The nag path (`send_nags`) and the escalation engine are OUT OF SCOPE here (a later plan).

**Tech Stack:** Python 3.12, sqlite3 (stdlib), httpx, Anthropic SDK, APScheduler, pytest. Package `src/jolt`, tests in `tests/`.

**Scope note:** This is Phases 1 and 2 of the spec `docs/superpowers/specs/2026-07-17-bump-aware-escalation-design.md`. Phase 3 (the escalation engine: scheduler-tick rewrite, per-bump frequency and bluntness) is a separate plan, deliberately deferred so bump data accrues before escalation goes live.

## Global Constraints

- Python `>= 3.12`. Run tests with `uv run pytest` (config in `pyproject.toml`, `pythonpath = ["src", "."]`). The `dev` extra provides pytest: if pytest is missing, run `uv sync --extra dev` once.
- TDD: write the failing test, run it red, implement minimally, run it green, commit. One commit per task.
- Conventional-commit messages. No `Co-Authored-By` / "Generated with" trailer.
- Never emit an em dash (`—`) in any code, comment, or user-facing/prompt copy. Use a comma, colon, period, or parentheses.
- `Task.priority` is the raw Vikunja integer (0-5). Bands: High `>= 3`, Mid `1-2`, Low/unset `0`. The morning/frog "matters" cutoff is `MATTERS_MIN_PRIORITY = 2` (Medium and up), separate from the nag-stance bands.
- Due-date comparison is on the calendar day. `Task.deadline` is a `date`; ISO strings (`YYYY-MM-DD`) compare correctly with `<`, `>`, `==`.
- The sidecar holds only Jolt's own state; Vikunja stays the source of truth for tasks. Never punish pulling a due date in or completing a task.

---

### Task 1: Sidecar `bump_state` table and accessors

Add the table, a reader, the compare-and-increment writer, and extend `prune` to cover it. Pure sidecar functions; no behavior change yet, so the suite stays green.

**Files:**
- Modify: `src/jolt/sidecar.py` (add `from datetime import date`, a schema constant, `init_db`, three functions)
- Test: `tests/test_sidecar.py`

**Interfaces:**
- Produces:
  - `sidecar.bump_counts(conn) -> dict[int, int]`
  - `sidecar.apply_due_snapshot(conn, due_by_task: dict[int, date | None]) -> None`
  - `sidecar.prune(conn, live_ids: set[int]) -> None` (extended to also prune `bump_state`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sidecar.py`:

```python
from datetime import date


def test_apply_due_snapshot_new_task_starts_at_zero(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    assert sidecar.bump_counts(tmp_conn) == {1: 0}


def test_apply_due_snapshot_forward_move_increments(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 19)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})
    assert sidecar.bump_counts(tmp_conn) == {1: 2}


def test_apply_due_snapshot_pull_in_and_equal_do_not_increment(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})  # equal
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})  # pulled in
    assert sidecar.bump_counts(tmp_conn) == {1: 0}


def test_apply_due_snapshot_skips_tasks_without_a_due_date(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: None})
    assert sidecar.bump_counts(tmp_conn) == {}


def test_apply_due_snapshot_starts_tracking_when_a_date_appears(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: None})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 19)})
    assert sidecar.bump_counts(tmp_conn) == {1: 1}


def test_prune_removes_bump_state_for_dead_tasks(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18), 2: date(2026, 7, 18)})
    sidecar.prune(tmp_conn, {1})
    assert sidecar.bump_counts(tmp_conn) == {1: 0}
```

If `tests/test_sidecar.py` has no `tmp_conn` fixture, add this near the top (an in-memory DB with the schema applied):

```python
import pytest
from jolt import sidecar


@pytest.fixture
def tmp_conn():
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    yield conn
    conn.close()
```

(If a `tmp_conn`/equivalent fixture already exists, reuse it and only add the six tests.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sidecar.py -k "apply_due_snapshot or prune_removes_bump" -v`
Expected: FAIL with `AttributeError: module 'jolt.sidecar' has no attribute 'apply_due_snapshot'`.

- [ ] **Step 3: Implement the table and accessors**

In `src/jolt/sidecar.py`, add `date` to the datetime import (line 2 becomes `from datetime import date, datetime`), then add the schema constant after `_DISPLAY_SCHEMA` (line 16):

```python
_BUMP_SCHEMA = """
CREATE TABLE IF NOT EXISTS bump_state (
    task_id INTEGER PRIMARY KEY,
    last_due_date TEXT,
    bump_count INTEGER NOT NULL DEFAULT 0
);
"""
```

In `init_db`, add the new table (after the `_DISPLAY_SCHEMA` execute):

```python
    conn.execute(_BUMP_SCHEMA)
```

Add the two accessors (anywhere at module level, e.g. after `last_nagged_map`):

```python
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
```

Replace `prune` (lines 64-69) so it cleans both tables:

```python
def prune(conn: sqlite3.Connection, live_ids: set[int]) -> None:
    for table in ("nag_state", "bump_state"):
        ids = {int(r["task_id"]) for r in conn.execute(f"SELECT task_id FROM {table}")}
        stale = ids - live_ids
        if stale:
            conn.executemany(f"DELETE FROM {table} WHERE task_id = ?", [(i,) for i in stale])
    conn.commit()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sidecar.py -v`
Expected: PASS (existing sidecar tests plus the six new ones).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/sidecar.py tests/test_sidecar.py
git commit -m "feat(sidecar): track due-date bump count per task"
```

---

### Task 2: `Task.bump_count` field and Store wiring

`Task` gains a `bump_count` field; `Store.list_pending` runs the snapshot and injects the count (like it already injects `last_nagged_at`); `get_task` injects it too.

**Files:**
- Modify: `src/jolt/models.py` (the `Task` dataclass, add one field)
- Modify: `src/jolt/store.py` (`list_pending`, `get_task`)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `sidecar.apply_due_snapshot`, `sidecar.bump_counts` (Task 1)
- Produces: `models.Task.bump_count: int = 0`; `Store.list_pending` and `Store.get_task` return tasks with `bump_count` populated from the sidecar.

- [ ] **Step 1: Add the field**

In `src/jolt/models.py`, add to the `Task` dataclass (after `project_name`, keeping it last so all fields stay keyword-defaulted):

```python
    bump_count: int = 0  # forward due-date moves counted in the sidecar; the avoidance signal
```

- [ ] **Step 2: Write the failing test**

In `tests/test_store.py`, add (the file already has a `FakeVikunja` and a store fixture; reuse them). This test relies on the store's real sidecar connection updating and reflecting the count across two reads with a moved due date:

```python
from datetime import date


def test_list_pending_injects_bump_count_after_a_forward_move(store, fake_vikunja):
    # fake_vikunja returns one task; move its deadline forward between two reads.
    fake_vikunja.set_open([_task(id=1, deadline=date(2026, 7, 18))])
    first = store.list_pending()
    assert first[0].bump_count == 0
    fake_vikunja.set_open([_task(id=1, deadline=date(2026, 7, 19))])
    second = store.list_pending()
    assert second[0].bump_count == 1
```

Adapt to the file's existing helpers: use whatever the file already uses to build a `Task` (a local `_task(...)` builder) and to seed the fake's open list (e.g. `fake_vikunja.set_open([...])` or setting an attribute the fake reads in `list_open`). If the existing `FakeVikunja` returns a fixed list, add a mutable `open_tasks` attribute it returns from `list_open`, and set it in the test. Keep the `deadline` a `date`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_store.py -k "bump_count" -v`
Expected: FAIL (either `bump_count` is always 0 because the store does not inject it, or an `AttributeError` if the fake lacks a mutable open list).

- [ ] **Step 4: Wire the store**

In `src/jolt/store.py`, replace `list_pending` (lines 20-28) with:

```python
    def list_pending(self) -> list[Task]:
        tasks = self._vk.list_open()
        sidecar.apply_due_snapshot(self._conn, {t.id: t.deadline for t in tasks})
        nagged = sidecar.last_nagged_map(self._conn)
        counts = sidecar.bump_counts(self._conn)
        tasks = [
            replace(t, last_nagged_at=nagged.get(t.id), bump_count=counts.get(t.id, 0))
            for t in tasks
        ]
        # An empty result is ambiguous between "truly no open tasks" and a broken
        # filter/transient glitch. Only prune when we have at least one live task to
        # prune against, so a flaky response can't wipe everyone's sidecar state.
        if tasks:
            sidecar.prune(self._conn, {t.id for t in tasks})
        return tasks
```

Replace `get_task` (lines 30-35) with:

```python
    def get_task(self, task_id: int) -> Task | None:
        task = self._vk.get_task(task_id)
        if task is None:
            return None
        nagged = sidecar.last_nagged_map(self._conn)
        counts = sidecar.bump_counts(self._conn)
        return replace(task, last_nagged_at=nagged.get(task.id), bump_count=counts.get(task.id, 0))
```

- [ ] **Step 5: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS. (`Task` gained a defaulted field, so existing `Task(...)` builders are unaffected.)

- [ ] **Step 6: Commit**

```bash
git add src/jolt/models.py src/jolt/store.py tests/test_store.py
git commit -m "feat(store): expose per-task bump_count from the sidecar"
```

---

### Task 3: `MATTERS_MIN_PRIORITY` and `select_frog`

The frog is the single most-avoided important task, with a day-one fallback so the morning message always has a lead.

**Files:**
- Modify: `src/jolt/config.py` (add `MATTERS_MIN_PRIORITY`)
- Modify: `src/jolt/selection.py` (add `select_frog`)
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Task.bump_count` (Task 2), `Task.priority` (int), `config.MATTERS_MIN_PRIORITY`
- Produces: `config.MATTERS_MIN_PRIORITY = 2`; `selection.select_frog(tasks: list[Task], now: datetime) -> Task | None`

- [ ] **Step 1: Add the constant**

In `src/jolt/config.py`, after the priority-band constants (`PRIORITY_MID_MIN` line), add:

```python
# The morning message and the frog only consider tasks at or above this raw priority
# (2 = Medium and up: Medium, High, Urgent, Critical). Low (1) and unset (0) are excluded.
MATTERS_MIN_PRIORITY = 2
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_selection.py`, add. Use the file's existing `make(...)` helper (default `priority=0`); pass `bump_count` and `created`/`priority` explicitly. `NOW` is the module's reference time:

```python
def test_select_frog_picks_highest_bump_count():
    a = replace(make(1, priority=3, created=NOW - timedelta(days=2)), bump_count=1)
    b = replace(make(2, priority=3, created=NOW - timedelta(days=2)), bump_count=4)
    a = replace(a, deadline=NOW.date())
    b = replace(b, deadline=NOW.date())
    assert selection.select_frog([a, b], NOW).id == 2


def test_select_frog_tie_on_bumps_breaks_by_priority_then_age():
    older_low = replace(make(1, priority=2, created=NOW - timedelta(days=9)), bump_count=3)
    newer_high = replace(make(2, priority=4, created=NOW - timedelta(days=1)), bump_count=3)
    older_low = replace(older_low, deadline=NOW.date())
    newer_high = replace(newer_high, deadline=NOW.date())
    assert selection.select_frog([older_low, newer_high], NOW).id == 2


def test_select_frog_day_one_fallback_to_top_priority_due_now():
    # No bumps yet: fall back to the highest-priority eligible task due today/overdue.
    high_due = replace(make(1, priority=4), deadline=NOW.date())
    mid_due = replace(make(2, priority=2), deadline=NOW.date())
    assert selection.select_frog([high_due, mid_due], NOW).id == 1


def test_select_frog_excludes_low_priority_and_undated():
    low = replace(make(1, priority=1), deadline=NOW.date())  # below the cutoff
    undated_high = make(2, priority=4)  # eligible priority but no due date
    assert selection.select_frog([low, undated_high], NOW) is None


def test_select_frog_none_when_no_eligible_tasks():
    assert selection.select_frog([make(1, priority=0)], NOW) is None
```

Ensure `from dataclasses import replace` is imported at the top of `tests/test_selection.py` (Task 3 of the prior feature added it; if absent, add it).

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_selection.py -k "select_frog" -v`
Expected: FAIL with `AttributeError: module 'jolt.selection' has no attribute 'select_frog'`.

- [ ] **Step 4: Implement `select_frog`**

In `src/jolt/selection.py`, add (near `select_daily_focus`; `config`, `STATUS_PENDING`, `Task`, `datetime`, `date` are already imported):

```python
def select_frog(tasks: list[Task], now: datetime) -> Task | None:
    """The single most-avoided important task: the lead of the day. Eligible = pending,
    priority >= MATTERS_MIN_PRIORITY, has a due date. Picks the highest bump_count, ties
    broken by higher priority then oldest. If nothing has been bumped yet (day one), falls
    back to the highest-priority eligible task due today or overdue, then oldest."""
    today = now.date()
    eligible = [
        t
        for t in tasks
        if t.status == STATUS_PENDING
        and t.priority >= config.MATTERS_MIN_PRIORITY
        and t.deadline is not None
    ]
    if not eligible:
        return None
    bumped = [t for t in eligible if t.bump_count > 0]
    if bumped:
        return max(bumped, key=lambda t: (t.bump_count, t.priority, -t.created_at.timestamp()))
    due_now = [t for t in eligible if t.deadline <= today]
    pool = due_now or eligible
    return max(pool, key=lambda t: (t.priority, -t.created_at.timestamp()))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_selection.py -k "select_frog" -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/jolt/config.py src/jolt/selection.py tests/test_selection.py
git commit -m "feat(selection): select_frog, the most-avoided important task"
```

---

### Task 4: `render_matters` and the shown-order helper

The deterministic half of the morning message: the priority-worthy due-today/overdue list plus a summary line for the rest.

**Files:**
- Modify: `src/jolt/render.py` (add `from . import config`, `morning_shown`, `render_matters`, a priority-flag helper)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `config.MATTERS_MIN_PRIORITY` (Task 3), `Task` fields
- Produces:
  - `render.morning_shown(tasks: list[Task], now: datetime) -> list[Task]` (ordered tasks the morning message lists)
  - `render.render_matters(tasks: list[Task], now: datetime) -> str`

- [ ] **Step 1: Write the failing tests**

In `tests/test_render.py`, add (reuse the file's existing `Task(...)`/`_task(...)` builder and its `NOW`; build tasks with explicit `priority`, `deadline`, `status`):

```python
def test_morning_shown_only_priority_due_today_or_overdue():
    today = NOW.date()
    shown = render.morning_shown(
        [
            _task(id=1, priority=4, deadline=today),  # in: urgent, due today
            _task(id=2, priority=2, deadline=today - timedelta(days=2)),  # in: overdue
            _task(id=3, priority=1, deadline=today),  # out: below cutoff
            _task(id=4, priority=3, deadline=today + timedelta(days=3)),  # out: future
            _task(id=5, priority=5, deadline=None),  # out: no due date
        ],
        NOW,
    )
    assert [t.id for t in shown] == [2, 1]  # overdue first, then today


def test_render_matters_lists_shown_and_summarizes_the_rest():
    today = NOW.date()
    text = render.render_matters(
        [
            _task(id=1, priority=4, deadline=today),
            _task(id=3, priority=1, deadline=today),  # hidden
            _task(id=5, priority=0, deadline=None),  # hidden
        ],
        NOW,
    )
    assert "1." in text
    assert "+ 2 more" in text  # two hidden tasks summarized, not listed


def test_render_matters_no_priority_tasks_is_all_summary():
    today = NOW.date()
    text = render.render_matters([_task(id=1, priority=0, deadline=today)], NOW)
    assert "+ 1 more" in text
```

Adapt `_task(...)` to the file's actual builder. `timedelta` is imported in `render.py`'s tests already if the file uses dates; if not, add `from datetime import timedelta`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_render.py -k "morning_shown or render_matters" -v`
Expected: FAIL with `AttributeError: module 'jolt.render' has no attribute 'morning_shown'`.

- [ ] **Step 3: Implement**

In `src/jolt/render.py`, add `from . import config` to the imports, then add:

```python
# Priority flag for the morning list, by raw Vikunja priority.
_PRIO_FLAG = {5: "[critical]", 4: "[urgent]", 3: "[high]", 2: "[med]"}


def _matters_key(task: Task, today: date) -> tuple:
    # Overdue before due-today, then higher priority, then oldest position/creation.
    overdue_rank = 0 if task.deadline < today else 1
    return (overdue_rank, -task.priority, task.position, task.created_at)


def morning_shown(tasks: list[Task], now: datetime) -> list[Task]:
    today = now.date()
    matters = [
        t
        for t in tasks
        if t.status == STATUS_PENDING
        and t.priority >= config.MATTERS_MIN_PRIORITY
        and t.deadline is not None
        and t.deadline <= today
    ]
    return sorted(matters, key=lambda t: _matters_key(t, today))


def render_matters(tasks: list[Task], now: datetime) -> str:
    today = now.date()
    shown = morning_shown(tasks, now)
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    hidden = len(pending) - len(shown)
    lines: list[str] = ["🎯 Today's priorities"]
    for i, task in enumerate(shown, 1):
        flag = _PRIO_FLAG.get(task.priority, "")
        suffix = f" ({(today - task.deadline).days}d overdue)" if task.deadline < today else ""
        lines.append(f"{i}. {flag} {task.text}{suffix}")
    if not shown:
        lines.append("Nothing high-priority is due today. Good.")
    if hidden > 0:
        lines.append("")
        lines.append(f"+ {hidden} more (say 'list' to see everything)")
    return "\n".join(lines)
```

`STATUS_PENDING` must be importable in `render.py`. If it is not already imported, change the models import to `from .models import STATUS_PENDING, Task`.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS.
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/render.py tests/test_render.py
git commit -m "feat(render): frog-first morning matters list with a summary line"
```

---

### Task 5: Rework `write_focus` and `send_daily_focus` to lead on the frog

The morning message becomes the frog lead (LLM prose, bump count called out) followed by `render_matters`, and the display snapshot saves the shown subset so completion-by-number matches.

**Files:**
- Modify: `src/jolt/llm.py` (`write_focus` signature and body; it now takes the frog `Task | None`)
- Modify: `src/jolt/scheduler.py` (`send_daily_focus` assembly and imports)
- Test: `tests/test_llm.py`, `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `selection.select_frog` (Task 3), `render.render_matters` / `render.morning_shown` (Task 4), `llm._importance`, `llm._format_duration`
- Produces: `llm.write_focus(frog: Task | None, now: datetime, client) -> str`; `scheduler.send_daily_focus` sends `write_focus(frog) + render_matters`.

- [ ] **Step 1: Write the failing llm tests**

In `tests/test_llm.py`, replace the existing `write_focus`-based tests (the ones that build a `DailyFocus` and call `llm.write_focus(focus, ...)`, currently `test_write_focus_*`) with frog-based ones. Reuse the file's `_high_task(...)` builder and `FakeClient`/`TZ`:

```python
def test_write_focus_leads_on_the_frog_and_names_the_bump_count():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
    frog = replace(_high_task(estimate_seconds=7200, project_name="Backlog"), bump_count=4)
    llm.write_focus(frog, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    payload = str(client.messages.calls[0]).lower()
    assert "4" in payload  # the bump count is surfaced
    assert "backlog" in payload


def test_write_focus_handles_no_frog():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
    out = llm.write_focus(None, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    assert isinstance(out, str) and out
    assert client.messages.calls == []  # no Claude call when there is nothing to lead on
```

Add `from dataclasses import replace` to `tests/test_llm.py` if not present. Remove any now-obsolete `DailyFocus`-based `write_focus` tests and the `DailyFocus` import if it becomes unused in this file.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_llm.py -k "write_focus" -v`
Expected: FAIL (`write_focus` still expects a `DailyFocus` and references `focus.focus`).

- [ ] **Step 3: Rework `write_focus`**

In `src/jolt/llm.py`, replace `write_focus` (currently lines ~329-357) with:

```python
def write_focus(frog: Task | None, now: datetime, client) -> str:
    if frog is None:
        return "Nothing high-priority is on the hook today. Use the breathing room."
    age = (now - frog.created_at).days
    duration = _format_duration(frog.estimate_seconds)
    system = (
        "You are Jolt. Write a short morning message (2 to 4 lines) about the ONE task below, "
        "the single thing to attack today. Push to start it: name the blocker and a concrete "
        "small first step. If it has been pushed forward before, call that out plainly and get "
        "more insistent the more it has slipped. Be direct, never guilt-tripping. "
        "Never use em dashes; use a comma, a colon, or a period instead. This rule has no "
        "exceptions."
    )
    bits = [f"Focus task: {frog.text}", f"priority {_importance(frog)}", f"{age}d old"]
    if frog.bump_count:
        bits.append(f"due date pushed forward {frog.bump_count} time(s)")
    if duration:
        bits.append(f"est {duration}")
    if frog.project_name:
        bits.append(f"in {frog.project_name}")
    details_line = f" Notes: {frog.details}." if frog.details else ""
    user = ", ".join(bits) + "." + details_line
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_focus", response)
    return _text_of(response)
```

If `DailyFocus` is now unused in `llm.py`, drop it from the `from .models import ...` line.

- [ ] **Step 4: Write the failing scheduler test**

In `tests/test_scheduler.py`, update the `send_daily_focus` test(s). The job should: read pending, pick the frog, send `write_focus(frog) + render_matters`, and save the morning-shown order. Reuse the file's fakes/spies:

```python
def test_send_daily_focus_leads_on_frog_and_sends_matters(monkeypatch):
    sent = []
    saved = {}
    store = _FakeStore(pending=[_task(id=1, priority=4, deadline=NOW.date())])
    store.save_display = lambda chat_id, ids: saved.__setitem__(chat_id, ids)
    monkeypatch.setattr(scheduler.llm, "write_focus", lambda frog, now, client: "LEAD")
    scheduler.send_daily_focus(store, sent.append, client=object(), now=NOW, chat_id=7)
    assert sent, "a message was sent"
    assert sent[0].startswith("LEAD")
    assert "Today's priorities" in sent[0]
    assert saved[7] == [1]  # display saved to the shown subset
```

Adapt `_FakeStore`/`_task`/`NOW` to the file's existing helpers. If the file mocks `llm` differently (e.g. a fake client returning canned text), keep that style and instead assert the message contains the matters header and the saved display equals the shown ids.

- [ ] **Step 5: Run to verify failure**

Run: `uv run pytest tests/test_scheduler.py -k "daily_focus" -v`
Expected: FAIL (`send_daily_focus` still calls `select_daily_focus` + `render_backlog` and `write_focus(focus_obj, ...)`).

- [ ] **Step 6: Rework `send_daily_focus`**

In `src/jolt/scheduler.py`, change the import line (line 5-6 area) from:

```python
from .render import display_order, render_backlog
from .selection import is_quiet_hours, select_daily_focus, select_slow_resurface
```

to:

```python
from .render import morning_shown, render_matters
from .selection import (
    is_quiet_hours,
    select_daily_focus,
    select_frog,
    select_slow_resurface,
)
```

(`select_daily_focus`, `select_slow_resurface`, `is_quiet_hours` stay: `send_nags` still uses them until the later escalation plan. `render_backlog`/`display_order` are no longer used by the scheduler; drop them from the import as shown.)

Replace `send_daily_focus` (lines 11-29) with:

```python
def send_daily_focus(store, send, client, now: datetime, chat_id: int) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Daily focus job aborted: could not load pending tasks")
        return
    frog = select_frog(tasks, now)
    logger.info("Selected frog task_id=%s", frog.id if frog else None)
    matters = render_matters(tasks, now)
    # Record the exact order shown, so a number the user types resolves against this
    # morning list, not a live order that later completions may have renumbered.
    store.save_display(chat_id, [t.id for t in morning_shown(tasks, now)])
    try:
        prose = llm.write_focus(frog, now, client)
    except Exception:
        logger.exception("Daily focus prose failed; sending the priorities with a plain lead")
        prose = "Morning. Here's what matters today."
    send(f"{prose}\n\n{matters}")
```

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. If any test still constructs a `DailyFocus` for `write_focus`, or references `render_backlog` via the scheduler, update it per the changes above. `render_backlog` itself is unchanged and still used by `orchestrator.py`'s `list` intent, so its own tests stay green.

- [ ] **Step 8: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

```bash
git add src/jolt/llm.py src/jolt/scheduler.py tests/test_llm.py tests/test_scheduler.py
git commit -m "feat: frog-first 06:00 message (frog lead + priority matters list)"
```

---

## Self-Review

**Spec coverage (Phases 1-2 of the spec):**
- Detection via sidecar snapshot + forward-move counter -> Task 1; injected onto `Task` -> Task 2.
- Bump count supersedes creation-age staleness for the frog -> Task 3 (`select_frog` uses `bump_count`, not `is_stale`).
- Frog selection with day-one fallback -> Task 3.
- Morning message: frog lead + priority>=2 due-today/overdue, uncapped, no-date excluded, "+N more" summary -> Tasks 4-5.
- `MATTERS_MIN_PRIORITY = 2` cutoff -> Task 3.
- Never punish pull-in/complete; no-date not tracked -> Task 1.
- Full backlog still on demand (`render_backlog` unchanged, used by `list` intent) -> untouched.
- Phase 3 (escalation engine, scheduler-tick rewrite, per-bump frequency and bluntness) -> deliberately a separate plan; `send_nags`, `build_scheduler` cron, `select_daily_focus`, `select_slow_resurface` are untouched here.

**Placeholder scan:** No TBD/TODO. Every code step shows full code; test steps show full test bodies. Where a test must bind to an existing helper whose exact name varies (`_task`, `FakeStore`, fixtures), the step names the adaptation explicitly and gives the assertion that must hold.

**Type consistency:** `Task.bump_count: int` populated in `store.py`, read in `selection.select_frog` and `render.morning_shown`/`render_matters` and `llm.write_focus`. `apply_due_snapshot(conn, dict[int, date | None])` matches the call in `store.list_pending` (`{t.id: t.deadline for t in tasks}`, and `Task.deadline` is `date | None`). `select_frog(tasks, now) -> Task | None` matches `send_daily_focus`. `write_focus(Task | None, now, client) -> str` matches its new call site. `render_matters`/`morning_shown` signatures match their call sites in `send_daily_focus`.
