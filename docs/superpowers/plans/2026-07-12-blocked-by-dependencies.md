# Blocked-by Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user say (in plain language) that one task must finish before another, and have Jolt redirect all its nagging onto the actionable blocker while going quiet on tasks that can't be started yet.

**Architecture:** One new nullable column `blocked_by` on the `tasks` table points at the prerequisite task. A task is *effectively blocked* when its blocker is still pending; such tasks are excluded from focus selection, nags, and staleness, and render below their blocker. Two new LLM intents (`block` / `unblock`) let the user set and clear the relationship. Completing or dropping a blocker auto-clears its dependents.

**Tech Stack:** Python 3.12, stdlib `sqlite3`, `pytest`, `ruff`. Anthropic and Telegram are mocked at the boundary in tests.

## Global Constraints

- Python 3.12+; dependency management via `uv` (run tests with `uv run pytest`).
- Style enforced by `ruff` (`uv run ruff format .` and `uv run ruff check .`). No manual style debates.
- All code, comments, and commits in English. No em dashes anywhere.
- Conventional-commit messages (`feat(...)`, `test(...)`).
- No over-engineering: single-user project. A task has **at most one** blocker (single nullable column, no join table).
- External APIs (Anthropic, Telegram) mocked at the boundary. Never make real HTTP calls in tests.
- Do NOT `git push` during implementation. Commit locally only. Pushing `main` auto-deploys the bot.

## File structure

- `src/jolt/models.py` — add `blocked_by: int | None = None` to `Task`.
- `src/jolt/db.py` — schema column + idempotent migration; `block_task`, `unblock_task`; auto-clear dependents in `complete_task` / `drop_task`; read column in `_row_to_task`.
- `src/jolt/selection.py` — `is_blocked(task, tasks)`; `order_backlog` places blocked tasks below blocker; `select_daily_focus` excludes blocked tasks.
- `src/jolt/render.py` — blocked tasks get a neutral dot and a `(blocked by N)` tag.
- `src/jolt/llm.py` — `Intent.blocked_by`; `parse_intent` reads it; `record_intent` tool gains `block`/`unblock` actions and a `blocked_by` property; system prompt teaches dependency language.
- `src/jolt/orchestrator.py` — `block` / `unblock` branches with self/dangling/cycle rejection.
- `src/jolt/scheduler.py` — **no change** (it calls `select_daily_focus`, so nag suppression is inherited).
- Tests: `tests/test_db.py`, `tests/test_selection.py`, `tests/test_render.py`, `tests/test_llm.py`, `tests/test_orchestrator.py`.

---

### Task 1: Data model + DB layer

**Files:**
- Modify: `src/jolt/models.py` (Task dataclass)
- Modify: `src/jolt/db.py` (schema, `init_db`, `_row_to_task`, `complete_task`, `drop_task`; add `block_task`, `unblock_task`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `db.connect`, `db.init_db`, `db.add_task`, `db.get_task`, `db.complete_task`, `db.drop_task`; `models.STATUS_PENDING`.
- Produces:
  - `Task.blocked_by: int | None` (dataclass field, default `None`).
  - `db.block_task(conn, task_id: int, blocked_by: int) -> Task | None` — sets `blocked_by` on a pending task, returns the updated task (or `None` if no pending row matched).
  - `db.unblock_task(conn, task_id: int) -> Task | None` — clears `blocked_by`, returns the updated task (or `None` if the id doesn't exist).
  - `db.complete_task` / `db.drop_task` additionally clear `blocked_by` on every task pointing at the completed/dropped task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
def test_add_task_defaults_blocked_by_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    assert t.blocked_by is None


def test_block_task_sets_blocked_by():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    updated = db.block_task(conn, a.id, b.id)
    assert updated.blocked_by == b.id
    assert db.get_task(conn, a.id).blocked_by == b.id


def test_unblock_task_clears_blocked_by():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    db.block_task(conn, a.id, b.id)
    updated = db.unblock_task(conn, a.id)
    assert updated.blocked_by is None


def test_completing_blocker_clears_dependents():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    blocker = db.add_task(conn, "blocker", PRIORITY_NORMAL, None, now)
    dep1 = db.add_task(conn, "dep1", PRIORITY_NORMAL, None, now)
    dep2 = db.add_task(conn, "dep2", PRIORITY_NORMAL, None, now)
    db.block_task(conn, dep1.id, blocker.id)
    db.block_task(conn, dep2.id, blocker.id)
    db.complete_task(conn, blocker.id, now)
    assert db.get_task(conn, dep1.id).blocked_by is None
    assert db.get_task(conn, dep2.id).blocked_by is None


def test_dropping_blocker_clears_dependents():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    blocker = db.add_task(conn, "blocker", PRIORITY_NORMAL, None, now)
    dep = db.add_task(conn, "dep", PRIORITY_NORMAL, None, now)
    db.block_task(conn, dep.id, blocker.id)
    db.drop_task(conn, blocker.id)
    assert db.get_task(conn, dep.id).blocked_by is None


def test_init_db_migrates_existing_table_without_blocked_by():
    conn = db.connect(":memory:")
    # Simulate the live table created before the blocked_by column existed.
    conn.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
        "priority TEXT NOT NULL, deadline TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL, "
        "last_nagged_at TEXT, completed_at TEXT)"
    )
    conn.commit()
    db.init_db(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "blocked_by" in cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "blocked or migrates" -v`
Expected: FAIL (e.g. `AttributeError: 'Task' object has no attribute 'blocked_by'` and `AttributeError: module 'jolt.db' has no attribute 'block_task'`).

- [ ] **Step 3: Add the `blocked_by` field to the Task model**

In `src/jolt/models.py`, add the field as the last entry of `Task` (default keeps every existing constructor call working):

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
    blocked_by: int | None = None
```

- [ ] **Step 4: Update the schema, migration, and db functions**

In `src/jolt/db.py`:

Add the column to the create statement:

```python
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
```

Make `init_db` also migrate an existing table (idempotent, covers the live DB where the table already exists without the column):

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "blocked_by" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN blocked_by INTEGER")
    conn.commit()
```

Read the column in `_row_to_task` (add as the last argument):

```python
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
```

Add the two new functions (place them after `drop_task`):

```python
def block_task(conn, task_id: int, blocked_by: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET blocked_by = ? WHERE id = ? AND status = ?",
        (blocked_by, task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def unblock_task(conn, task_id: int) -> Task | None:
    conn.execute("UPDATE tasks SET blocked_by = NULL WHERE id = ?", (task_id,))
    conn.commit()
    return get_task(conn, task_id)
```

Clear dependents inside `complete_task` and `drop_task` (add the extra UPDATE before returning). New `complete_task`:

```python
def complete_task(conn, task_id: int, completed_at: datetime) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ? AND status = ?",
        (STATUS_DONE, completed_at.isoformat(), task_id, STATUS_PENDING),
    )
    conn.execute("UPDATE tasks SET blocked_by = NULL WHERE blocked_by = ?", (task_id,))
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None
```

New `drop_task`:

```python
def drop_task(conn, task_id: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ? AND status = ?",
        (STATUS_DROPPED, task_id, STATUS_PENDING),
    )
    conn.execute("UPDATE tasks SET blocked_by = NULL WHERE blocked_by = ?", (task_id,))
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (new tests plus all existing `test_db.py` tests).

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/models.py src/jolt/db.py tests/test_db.py
git commit -m "feat(db): add blocked_by column, block/unblock, auto-clear on complete/drop"
```

---

### Task 2: Selection — blocked tasks go quiet and sink below their blocker

**Files:**
- Modify: `src/jolt/selection.py` (`order_backlog`, `select_daily_focus`; add `is_blocked`)
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Task.blocked_by` (Task 1); `models.STATUS_PENDING`, `PRIORITY_IMPORTANT`; existing `priority_sort_key`, `is_stale`.
- Produces:
  - `selection.is_blocked(task: Task, tasks: list[Task]) -> bool` — True when `task.blocked_by` points at a task that is still `pending`.
  - `order_backlog` output places each effectively-blocked task directly below its blocker (recursively for chains).
  - `select_daily_focus` never returns an effectively-blocked task as focus or rescue.

- [ ] **Step 1: Write the failing tests**

First extend the `make` helper in `tests/test_selection.py` to accept a blocker:

```python
def make(
    id, *, priority=PRIORITY_NORMAL, deadline=None, created=NOW, status=STATUS_PENDING, blocked_by=None
):
    return Task(
        id=id,
        text=f"t{id}",
        priority=priority,
        deadline=deadline,
        created_at=created,
        status=status,
        last_nagged_at=None,
        completed_at=None,
        blocked_by=blocked_by,
    )
```

Then add these tests to `tests/test_selection.py`:

```python
def test_is_blocked_true_when_blocker_pending():
    blocker = make(1)
    dep = make(2, blocked_by=1)
    assert selection.is_blocked(dep, [blocker, dep]) is True


def test_is_blocked_false_when_blocker_done():
    blocker = make(1, status=STATUS_DONE)
    dep = make(2, blocked_by=1)
    assert selection.is_blocked(dep, [blocker, dep]) is False


def test_is_blocked_false_when_not_blocked():
    assert selection.is_blocked(make(1), [make(1)]) is False


def test_blocked_task_sorts_directly_below_its_blocker():
    # An important, near-deadline blocked task would normally sort to the top, but it
    # must appear right after its blocker instead.
    blocker = make(1, priority=PRIORITY_NORMAL, created=NOW)
    dep = make(2, priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 13), blocked_by=1)
    other = make(3, priority=PRIORITY_NORMAL, created=NOW)
    ordered = [t.id for t in selection.order_backlog([dep, blocker, other])]
    assert ordered.index(2) == ordered.index(1) + 1  # dep is immediately after blocker


def test_blocked_task_is_not_selected_as_focus():
    # The blocked task is important + stale (would normally lead); the blocker is fresh
    # and normal. Focus must still land on the actionable blocker.
    blocker = make(1, priority=PRIORITY_NORMAL, created=NOW)
    dep = make(2, priority=PRIORITY_IMPORTANT, created=NOW - timedelta(days=5), blocked_by=1)
    result = selection.select_daily_focus([blocker, dep], NOW)
    assert result.focus.id == 1


def test_blocked_task_is_not_a_rescue():
    blocker = make(1, priority=PRIORITY_IMPORTANT, created=NOW)
    stale_dep = make(2, created=NOW - timedelta(days=5), blocked_by=1)
    result = selection.select_daily_focus([blocker, stale_dep], NOW)
    assert 2 not in [t.id for t in result.rescues]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_selection.py -k "blocked" -v`
Expected: FAIL (`module 'jolt.selection' has no attribute 'is_blocked'`, ordering/focus assertions fail).

- [ ] **Step 3: Implement `is_blocked`, the new ordering, and focus exclusion**

In `src/jolt/selection.py`, add `is_blocked` and rewrite `order_backlog` and `select_daily_focus`:

```python
def is_blocked(task: Task, tasks: list[Task]) -> bool:
    if task.blocked_by is None:
        return False
    blocker = next((t for t in tasks if t.id == task.blocked_by), None)
    return blocker is not None and blocker.status == STATUS_PENDING


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    dependents: dict[int, list[Task]] = {}
    for t in pending:
        if is_blocked(t, pending):
            dependents.setdefault(t.blocked_by, []).append(t)
    roots = sorted([t for t in pending if not is_blocked(t, pending)], key=priority_sort_key)
    ordered: list[Task] = []

    def emit(task: Task) -> None:
        ordered.append(task)
        for dep in sorted(dependents.get(task.id, []), key=priority_sort_key):
            emit(dep)

    for root in roots:
        emit(root)
    return ordered


def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    ordered = order_backlog(tasks)
    actionable = [t for t in ordered if not is_blocked(t, tasks)]
    if not actionable:
        return DailyFocus(focus=None, rescues=[])
    # Avoidance wins the lead spot: an important task that has gone stale is the
    # deferral signal, so it leads even over a fresher, nearer-deadline one. Only when
    # nothing important is being dodged does the lead fall back to the top of the order.
    stale_important = [
        t for t in actionable if t.priority == PRIORITY_IMPORTANT and is_stale(t, now)
    ]
    focus = stale_important[0] if stale_important else actionable[0]
    rescues = [t for t in actionable if t.id != focus.id and is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)
```

Note: `is_blocked` inside `order_backlog` is called with `pending` (blockers are always pending when they block); `select_daily_focus` passes the full `tasks` list, which is equivalent for pending blockers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_selection.py -v`
Expected: PASS (new tests plus all existing selection tests).

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/selection.py tests/test_selection.py
git commit -m "feat(selection): exclude blocked tasks from focus and sink them below their blocker"
```

---

### Task 3: Render — neutral dot and `(blocked by N)` tag

**Files:**
- Modify: `src/jolt/render.py` (`render_backlog`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `selection.order_backlog`, `selection.is_blocked` (Task 2); existing `_dot`.
- Produces: a blocked task renders as `<n>. ⚪ <text>[ (due …)] (blocked by <k>)` where `<k>` is the 1-based position of its blocker in the rendered list.

- [ ] **Step 1: Write the failing test**

First extend the `make` helper in `tests/test_render.py`:

```python
def make(id, *, text, priority=PRIORITY_NORMAL, deadline=None, created_at=FRESH, blocked_by=None):
    return Task(
        id=id,
        text=text,
        priority=priority,
        deadline=deadline,
        created_at=created_at,
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        blocked_by=blocked_by,
    )
```

Then add:

```python
def test_blocked_task_shows_neutral_dot_and_tag():
    blocker = make(1, text="do accounts")
    dep = make(2, text="submit expenses", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 1), blocked_by=1)
    out = render.render_backlog([blocker, dep], NOW)
    lines = out.splitlines()
    assert lines[1] == "1. ⚪ do accounts"
    assert lines[2] == "2. ⚪ submit expenses (due 2026-07-01) (blocked by 1)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render.py::test_blocked_task_shows_neutral_dot_and_tag -v`
Expected: FAIL (line 2 has an urgency dot and no `(blocked by 1)` tag).

- [ ] **Step 3: Implement blocked rendering**

In `src/jolt/render.py`, rewrite `render_backlog` (keep `_dot` unchanged):

```python
def render_backlog(tasks: list[Task], now: datetime) -> str:
    ordered = order_backlog(tasks)
    if not ordered:
        return "Backlog empty. Nice."
    position = {task.id: i for i, task in enumerate(ordered, 1)}
    lines = ["Backlog:"]
    for i, task in enumerate(ordered, 1):
        due = f" (due {task.deadline.isoformat()})" if task.deadline else ""
        if is_blocked(task, tasks):
            dot = "⚪"
            tag = f" (blocked by {position[task.blocked_by]})"
        else:
            dot = _dot(task, now)
            tag = ""
        lines.append(f"{i}. {dot} {task.text}{due}{tag}")
    return "\n".join(lines)
```

Update the import line at the top of `render.py`:

```python
from .selection import is_blocked, is_stale, is_urgent, order_backlog
```

(`is_stale` and `is_urgent` stay; they are used by `_dot`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (new test plus all existing render tests).

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/render.py tests/test_render.py
git commit -m "feat(render): neutral dot and (blocked by N) tag for blocked tasks"
```

---

### Task 4: LLM — `block` / `unblock` intents and dependency prompt

**Files:**
- Modify: `src/jolt/llm.py` (`Intent`, `parse_intent`, `_TOOL`, `interpret_message` system prompt)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: existing `Intent`, `parse_intent`, `_TOOL`, `interpret_message`, `FakeClient` test helper.
- Produces:
  - `Intent.blocked_by: int | None` (default `None`).
  - `record_intent` tool `action` enum includes `"block"` and `"unblock"`, plus an integer `blocked_by` property.
  - A dependency-language instruction in the system prompt.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py`:

```python
def test_parse_block_intent():
    intent = llm.parse_intent({"action": "block", "task_id": 1, "blocked_by": 3})
    assert intent.action == "block"
    assert intent.task_id == 1
    assert intent.blocked_by == 3


def test_parse_unblock_intent():
    intent = llm.parse_intent({"action": "unblock", "task_id": 2})
    assert intent.action == "unblock"
    assert intent.task_id == 2
    assert intent.blocked_by is None


def test_tool_enum_includes_block_and_unblock():
    actions = llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "block" in actions
    assert "unblock" in actions


def test_prompt_mentions_dependencies():
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "block" in system


def test_interpret_message_maps_dependency_to_two_block_intents():
    # "1 and 2 need 3 first" must become one block intent per blocked task.
    blocks = [
        SimpleNamespace(
            type="tool_use", name="record_intent", input={"action": "block", "task_id": 1, "blocked_by": 3}
        ),
        SimpleNamespace(
            type="tool_use", name="record_intent", input={"action": "block", "task_id": 2, "blocked_by": 3}
        ),
    ]
    client = FakeClient(SimpleNamespace(content=blocks))
    intents = llm.interpret_message("1 and 2 need 3 first", [_task()], NOW, client)
    assert [(i.action, i.task_id, i.blocked_by) for i in intents] == [
        ("block", 1, 3),
        ("block", 2, 3),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -k "block or dependencies" -v`
Expected: FAIL (`Intent` has no `blocked_by`; `"block"` not in enum; prompt lacks "block").

- [ ] **Step 3: Add the field, parse it, extend the tool, update the prompt**

In `src/jolt/llm.py`:

Add `blocked_by` to the `Intent` dataclass (after `task_id`):

```python
@dataclass(frozen=True)
class Intent:
    action: str
    text: str | None = None
    priority: str | None = None
    deadline: date | None = None
    task_id: int | None = None
    blocked_by: int | None = None
    reply: str | None = None
```

Read it in `parse_intent`:

```python
def parse_intent(tool_input: dict) -> Intent:
    deadline = tool_input.get("deadline")
    return Intent(
        action=tool_input["action"],
        text=tool_input.get("text"),
        priority=tool_input.get("priority"),
        deadline=date.fromisoformat(deadline) if deadline else None,
        task_id=tool_input.get("task_id"),
        blocked_by=tool_input.get("blocked_by"),
        reply=tool_input.get("reply"),
    )
```

Extend the `action` enum and add the `blocked_by` property in `_TOOL`:

```python
"action": {
    "type": "string",
    "enum": ["add", "complete", "drop", "block", "unblock", "list", "answer"],
    "description": "add a task, complete/drop an existing one, block a task on another / unblock it, list the backlog, or answer a question / reply to the user.",
},
```

Add, alongside the other properties (e.g. after `task_id`):

```python
"blocked_by": {
    "type": "integer",
    "description": "For action=block: the id of the prerequisite task that must be finished first.",
},
```

Add a dependency instruction to the `system` string in `interpret_message` (append after the complete/drop sentence, before the question/blocker-conversation sentence):

```python
"When the user says one task must happen before another (for example 'X needs Y "
"first', 'can't do X until Y', 'Y blocks X'), call record_intent with action=block, "
"task_id = the task that is blocked and blocked_by = the prerequisite task's id; emit "
"one block call per blocked task. To lift a dependency, use action=unblock with the "
"task_id. "
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (new tests plus all existing llm tests).

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat(llm): add block/unblock intents and dependency prompt"
```

---

### Task 5: Orchestrator — apply block / unblock with validation

**Files:**
- Modify: `src/jolt/orchestrator.py` (`apply_intent`; add `_would_cycle`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `db.block_task`, `db.unblock_task`, `db.get_task` (Task 1); `Intent.action`, `Intent.task_id`, `Intent.blocked_by` (Task 4); `models.STATUS_PENDING`.
- Produces: `apply_intent` handles `action="block"` and `action="unblock"`, returning a plain-language reply and rejecting self-block, dangling ids, and cycles with no DB write.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py` (uses the existing `fresh()` helper and `Intent` import):

```python
def test_block_intent_sets_dependency_and_confirms():
    conn = fresh()
    a = db.add_task(conn, "submit expenses", "important", None, NOW)
    b = db.add_task(conn, "do accounts", "important", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=b.id), NOW
    )
    assert db.get_task(conn, a.id).blocked_by == b.id
    assert "do accounts" in reply


def test_unblock_intent_clears_dependency():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    b = db.add_task(conn, "b", "normal", None, NOW)
    db.block_task(conn, a.id, b.id)
    reply = orchestrator.apply_intent(conn, Intent(action="unblock", task_id=a.id), NOW)
    assert db.get_task(conn, a.id).blocked_by is None
    assert reply == "Unblocked."


def test_block_self_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=a.id), NOW
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "itself" in reply.lower()


def test_block_dangling_id_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=999), NOW
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "find" in reply.lower()


def test_block_cycle_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    b = db.add_task(conn, "b", "normal", None, NOW)
    db.block_task(conn, b.id, a.id)  # b already waits on a
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=b.id), NOW  # a waits on b -> loop
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "loop" in reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k "block" -v`
Expected: FAIL (`apply_intent` returns the fallback reply and sets nothing).

- [ ] **Step 3: Implement the block / unblock branches**

In `src/jolt/orchestrator.py`, add the import for the pending status and a cycle helper, then the two branches in `apply_intent` (before the final `return intent.reply ...`).

Update the import line:

```python
from .models import PRIORITY_NORMAL, STATUS_PENDING
```

Add the helper above `apply_intent`:

```python
def _would_cycle(conn, task_id: int, blocked_by: int) -> bool:
    """True if making task_id wait on blocked_by would form a loop. Walks the
    blocked_by chain up from the proposed blocker; a loop exists if it leads back
    to task_id."""
    seen: set[int] = set()
    cursor = blocked_by
    while cursor is not None and cursor not in seen:
        if cursor == task_id:
            return True
        seen.add(cursor)
        task = db.get_task(conn, cursor)
        cursor = task.blocked_by if task else None
    return False
```

Add the branches inside `apply_intent`, immediately after the `drop` branch:

```python
    if intent.action == "block":
        if intent.task_id == intent.blocked_by:
            return "A task can't block itself."
        blocked = db.get_task(conn, intent.task_id)
        blocker = db.get_task(conn, intent.blocked_by)
        if (
            blocked is None
            or blocked.status != STATUS_PENDING
            or blocker is None
            or blocker.status != STATUS_PENDING
        ):
            logger.info("Block rejected: could not find both pending tasks")
            return "Couldn't find those tasks."
        if _would_cycle(conn, intent.task_id, intent.blocked_by):
            logger.info("Block rejected: would create a cycle")
            return "That would create a loop, so I left it alone."
        db.block_task(conn, intent.task_id, intent.blocked_by)
        logger.info("Blocked task_id=%s on %s", intent.task_id, intent.blocked_by)
        return f'Noted: "{blocked.text}" waits on "{blocker.text}" first.'
    if intent.action == "unblock":
        task = db.unblock_task(conn, intent.task_id)
        logger.info("Unblock task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        return "Unblocked." if task else "Couldn't find that one."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (new tests plus all existing orchestrator tests).

- [ ] **Step 5: Run the whole suite, format, lint, commit**

```bash
uv run pytest
uv run ruff format . && uv run ruff check .
git add src/jolt/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): apply block/unblock with self, dangling, and cycle guards"
```
Expected: full suite green (the original 40 tests plus the new ones).

---

## Self-review

**Spec coverage:**
- Data model (`blocked_by` column, single blocker, CREATE + ALTER migration) → Task 1. ✓
- Effectively-blocked definition + exclusion from focus/nag/stale → Task 2 (`is_blocked`, `select_daily_focus`); nags inherit via `scheduler` calling `select_daily_focus` (no code change, noted in file structure). ✓
- No urgency inheritance → nothing boosts the blocker; it rises only via dependents stepping aside (Task 2). ✓
- Chains → recursive `emit` in `order_backlog` + per-task `is_blocked` evaluation (Task 2). ✓
- Auto-unblock on complete/drop → Task 1 (`complete_task`, `drop_task` clear dependents). ✓
- `block` / `unblock` intents + dependency prompt → Task 4. ✓
- Orchestrator edge cases (self, dangling, cycle, unblock-not-blocked) → Task 5; unblock-not-blocked is harmless (`unblock_task` returns the task, reply "Unblocked."). ✓
- Rendering (neutral dot, `(blocked by N)`, sort below blocker) → Task 3 (dot/tag) + Task 2 (ordering). ✓
- Testing matrix → covered across Tasks 1-5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `blocked_by: int | None` used consistently in `Task`, `Intent`, db functions, `is_blocked`. `db.block_task(conn, task_id, blocked_by) -> Task | None` and `db.unblock_task(conn, task_id) -> Task | None` match their call sites in Task 5. `selection.is_blocked(task, tasks)` matches its calls in `order_backlog`, `select_daily_focus`, and `render_backlog`. ✓
