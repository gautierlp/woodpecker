# Task Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `edit` intent so Jolt can change an existing pending task's deadline, priority, or text in place (including bulk edits like "change all due dates to today"), instead of forcing drop-and-re-add.

**Architecture:** A new `db.update_task` does a partial SQL update of only the fields supplied. A new `edit` action on the `record_intent` tool carries `task_id` plus the changed fields, with a `clear_deadline` boolean to distinguish "remove the due date" from "leave it alone". `apply_intent` gains an `edit` branch that translates the intent into the db call and confirms the change. Editing never touches `created_at`, so a task's age and nag pressure survive an edit.

**Tech Stack:** Python 3.12+, sqlite3 (stdlib), pytest, Anthropic tool-use (mocked in tests).

## Global Constraints

- All code, comments, names, commits, docs in **English**.
- No em dashes anywhere (use comma, colon, period, or parentheses).
- Style enforced by **ruff**; run `uv run ruff format .` and `uv run ruff check .` before each commit.
- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.
- External APIs (Anthropic, Telegram) mocked at the boundary; no live HTTP in tests.
- No over-engineering: single-user personal project, only what the spec asks for.
- Spec: `docs/superpowers/specs/2026-07-12-task-editing-design.md`.

## File Structure

- `src/jolt/db.py` — add `update_task` and the `_UNSET` sentinel. Partial update, pending-only.
- `src/jolt/llm.py` — add `edit` to the tool enum, add the `clear_deadline` field to `Intent`, parse it in `parse_intent`, teach the system prompt when to use `edit`.
- `src/jolt/orchestrator.py` — add the `edit` branch to `apply_intent`.
- `tests/test_db.py`, `tests/test_llm.py`, `tests/test_orchestrator.py` — one concern per test.

Run the whole suite with `uv run pytest`.

---

### Task 1: Data layer — `db.update_task`

**Files:**
- Modify: `src/jolt/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `get_task`, `add_task`, `STATUS_PENDING`, `Task`.
- Produces: `_UNSET` (module-level sentinel) and
  `update_task(conn, task_id: int, *, text: str | None = None, priority: str | None = None, deadline=_UNSET) -> Task | None`.
  `text` / `priority` default `None` meaning "leave unchanged". `deadline` defaults to the `_UNSET` sentinel meaning "leave unchanged"; pass a `date` to set it, pass `None` to clear it to SQL NULL. Returns the refreshed `Task`, or `None` if the task is missing or not pending. A call with no fields supplied is a harmless no-op that returns the current task (or `None`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
def test_update_task_changes_only_given_fields():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "call vet", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, priority=PRIORITY_IMPORTANT)
    assert updated.priority == PRIORITY_IMPORTANT
    assert updated.text == "call vet"          # untouched
    assert updated.deadline == date(2026, 7, 15)  # untouched


def test_update_task_sets_deadline():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    updated = db.update_task(conn, t.id, deadline=date(2026, 7, 12))
    assert updated.deadline == date(2026, 7, 12)


def test_update_task_clears_deadline_with_explicit_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, deadline=None)
    assert updated.deadline is None


def test_update_task_default_leaves_deadline_untouched():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, text="renamed")
    assert updated.deadline == date(2026, 7, 15)  # not cleared by the default


def test_update_task_never_touches_created_at():
    conn = fresh()
    created = datetime(2026, 7, 1, 8, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, created)
    db.update_task(conn, t.id, deadline=date(2026, 7, 20))
    assert db.get_task(conn, t.id).created_at == created


def test_update_task_no_fields_is_noop():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    updated = db.update_task(conn, t.id)
    assert updated.text == "x"


def test_update_task_on_done_returns_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, t.id, now)
    assert db.update_task(conn, t.id, priority=PRIORITY_IMPORTANT) is None


def test_update_task_missing_returns_none():
    conn = fresh()
    assert db.update_task(conn, 999, priority=PRIORITY_IMPORTANT) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k update_task -v`
Expected: FAIL with `AttributeError: module 'jolt.db' has no attribute 'update_task'`.

- [ ] **Step 3: Implement `update_task`**

In `src/jolt/db.py`, add the sentinel near the top (after the imports) and the function next to `block_task`:

```python
_UNSET = object()
```

```python
def update_task(conn, task_id, *, text=None, priority=None, deadline=_UNSET) -> Task | None:
    assignments = []
    values = []
    if text is not None:
        assignments.append("text = ?")
        values.append(text)
    if priority is not None:
        assignments.append("priority = ?")
        values.append(priority)
    if deadline is not _UNSET:
        assignments.append("deadline = ?")
        values.append(deadline.isoformat() if deadline else None)
    if not assignments:
        return get_task(conn, task_id)
    values.extend([task_id, STATUS_PENDING])
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ? AND status = ?",
        values,
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k update_task -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/db.py tests/test_db.py
git commit -m "feat(db): add update_task for partial edits of pending tasks"
```

---

### Task 2: Intent parsing, tool schema, and prompt (`llm.py`)

**Files:**
- Modify: `src/jolt/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: the existing `Intent` dataclass, `parse_intent`, `_TOOL`, `interpret_message`.
- Produces: `Intent.clear_deadline: bool = False`; `parse_intent` reads `clear_deadline` from `tool_input`; `_TOOL` enum includes `"edit"` and its `input_schema` exposes a `clear_deadline` boolean property; the `interpret_message` system prompt mentions `edit`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py`:

```python
def test_parse_edit_intent_with_deadline():
    intent = llm.parse_intent({"action": "edit", "task_id": 4, "deadline": "2026-07-12"})
    assert intent.action == "edit"
    assert intent.task_id == 4
    assert intent.deadline == date(2026, 7, 12)
    assert intent.clear_deadline is False


def test_parse_edit_intent_with_clear_deadline():
    intent = llm.parse_intent({"action": "edit", "task_id": 4, "clear_deadline": True})
    assert intent.action == "edit"
    assert intent.clear_deadline is True
    assert intent.deadline is None


def test_tool_enum_includes_edit():
    actions = llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "edit" in actions


def test_tool_schema_exposes_clear_deadline():
    assert "clear_deadline" in llm._TOOL["input_schema"]["properties"]


def test_prompt_mentions_edit():
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "edit" in system


def test_interpret_message_maps_bulk_reschedule_to_edit_per_task():
    # "change all due dates to today" -> one edit intent per pending task, each with today.
    blocks = [
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "edit", "task_id": 1, "deadline": "2026-07-12"},
        ),
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "edit", "task_id": 2, "deadline": "2026-07-12"},
        ),
    ]
    client = FakeClient(SimpleNamespace(content=blocks))
    intents = llm.interpret_message("change all due dates to today", [_task(1), _task(2)], NOW, client)
    assert [(i.action, i.task_id, i.deadline) for i in intents] == [
        ("edit", 1, date(2026, 7, 12)),
        ("edit", 2, date(2026, 7, 12)),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -k "edit or clear_deadline" -v`
Expected: FAIL (`edit` not in enum, `clear_deadline` not a field, prompt has no "edit").

- [ ] **Step 3: Add `clear_deadline` to `Intent` and `parse_intent`**

In `src/jolt/llm.py`, extend the dataclass:

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
    clear_deadline: bool = False
```

And in `parse_intent`, add the field to the returned `Intent(...)`:

```python
        reply=tool_input.get("reply"),
        clear_deadline=tool_input.get("clear_deadline", False),
    )
```

- [ ] **Step 4: Add `edit` to the tool enum and the `clear_deadline` property**

In `_TOOL["input_schema"]["properties"]`, update the `action` enum and description, and add the new property:

```python
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "edit", "block", "unblock", "list", "answer"],
                "description": "add a task, complete/drop an existing one, edit an existing task's deadline/priority/text, block a task on another / unblock it, list the backlog, or answer a question / reply to the user.",
            },
```

Add after the `blocked_by` property:

```python
            "clear_deadline": {
                "type": "boolean",
                "description": "For action=edit only: set true to remove a task's due date entirely. Leave unset to keep the current due date.",
            },
```

Also update the `task_id` property description so it covers edit:

```python
            "task_id": {
                "type": "integer",
                "description": "The id of the existing task, for complete/drop/edit.",
            },
```

- [ ] **Step 5: Teach the system prompt when to use `edit`**

In `interpret_message`, add this sentence to the `system` string, right after the block/unblock instruction (before the "For a question or a blocker conversation" sentence):

```python
        "To change an existing task rather than add a new one, use action=edit with its task_id "
        "and only the fields that change: a new deadline, a new priority (normal/important), or "
        "reworded text. Prefer edit over dropping and re-adding, so the task keeps its age. To "
        "remove a due date entirely, use action=edit with clear_deadline=true. A message like "
        "'change all due dates to today' becomes one edit call per pending task. "
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -k "edit or clear_deadline" -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat(llm): add edit intent, clear_deadline field, and prompt guidance"
```

---

### Task 3: Orchestrator `edit` branch

**Files:**
- Modify: `src/jolt/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `db.update_task` (Task 1), `Intent.clear_deadline` (Task 2), existing `db.add_task`, `db.get_task`.
- Produces: an `edit` branch in `apply_intent` returning a plain confirmation string.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py`:

```python
def test_edit_intent_changes_deadline_and_confirms():
    conn = fresh()
    t = db.add_task(conn, "accounts", "important", date(2026, 7, 20), NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, deadline=date(2026, 7, 12)), NOW
    )
    assert db.get_task(conn, t.id).deadline == date(2026, 7, 12)
    assert "2026-07-12" in reply


def test_edit_intent_clears_deadline():
    conn = fresh()
    t = db.add_task(conn, "accounts", "important", date(2026, 7, 20), NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, clear_deadline=True), NOW
    )
    assert db.get_task(conn, t.id).deadline is None
    assert "removed" in reply.lower()


def test_edit_intent_changes_priority():
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, priority="important"), NOW
    )
    assert db.get_task(conn, t.id).priority == "important"
    assert reply == "Updated."


def test_edit_intent_empty_change_is_rejected():
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="edit", task_id=t.id), NOW)
    assert reply == "Nothing to change."


def test_edit_intent_unknown_id_is_graceful():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=999, deadline=date(2026, 7, 12)), NOW
    )
    assert reply == "Couldn't find that one."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k edit -v`
Expected: FAIL (the `edit` action falls through to the default `intent.reply` line, so replies do not match).

- [ ] **Step 3: Implement the `edit` branch**

In `src/jolt/orchestrator.py`, add this branch in `apply_intent`, right after the `drop` branch and before the `block` branch:

```python
    if intent.action == "edit":
        if (
            intent.text is None
            and intent.priority is None
            and intent.deadline is None
            and not intent.clear_deadline
        ):
            return "Nothing to change."
        if intent.deadline is not None:
            task = db.update_task(
                conn, intent.task_id, text=intent.text, priority=intent.priority,
                deadline=intent.deadline,
            )
        elif intent.clear_deadline:
            task = db.update_task(
                conn, intent.task_id, text=intent.text, priority=intent.priority, deadline=None
            )
        else:
            task = db.update_task(
                conn, intent.task_id, text=intent.text, priority=intent.priority
            )
        logger.info("Edit task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        if task is None:
            return "Couldn't find that one."
        ack = "Updated."
        if intent.deadline is not None:
            ack += f" Due {task.deadline.isoformat()}."
        elif intent.clear_deadline:
            ack += " Due date removed."
        return ack
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -k edit -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS (all existing tests plus the 18 new ones).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/jolt/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): apply edit intent with empty-change and not-found guards"
```

---

## Self-Review

**Spec coverage:**
- Intent `edit` + only-changed-fields → Task 2 (tool enum, prompt), Task 3 (branch). ✓
- Bulk "all due dates to today" → Task 2 `test_interpret_message_maps_bulk_reschedule_to_edit_per_task` + prompt sentence. ✓
- Clear deadline via `clear_deadline` → Task 1 (db None-clears), Task 2 (field + schema), Task 3 (branch + "Due date removed."). ✓
- `db.update_task` partial update, pending-only, `_UNSET` sentinel → Task 1. ✓
- Orchestrator not-found / empty-edit / valid confirmations → Task 3. ✓
- Deliberate non-effect: `created_at` untouched → Task 1 `test_update_task_never_touches_created_at`. ✓
- Rendering: no change required (spec says none). ✓
- Tests per spec's testing section → covered across Tasks 1-3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `update_task(conn, task_id, *, text=None, priority=None, deadline=_UNSET)` is defined in Task 1 and called with exactly those keyword names in Task 3. `Intent.clear_deadline` defined in Task 2, read in Task 3. ✓
