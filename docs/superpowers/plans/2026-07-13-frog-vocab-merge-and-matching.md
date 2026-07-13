# Frog Vocabulary, Task Merging, and Steadier Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the bot the Eat That Frog vocabulary, make it steadier at matching tasks by text, and add a `merge` action that folds two tasks into one.

**Architecture:** Three prompt sentences in `llm.py` (frog vocabulary, matching guidance, merge usage) plus a new `merge` intent that flows through the existing pipeline: tool schema and `Intent` -> `parse_intent` -> `_resolve_positions` -> `db.merge_tasks` -> `orchestrator.apply_intent`. Claude writes the merged text and picks the survivor (judgment); deterministic code takes the more urgent deadline and priority and drops the other task (mechanical).

**Tech Stack:** Python 3.12, `anthropic` SDK, `pytest`, `ruff`, `uv`.

## Global Constraints

- Python 3.12+; run tests with `uv run pytest`, lint with `uv run ruff check .`.
- All code, comments, and docs in English. No em dashes anywhere (use commas, colons, periods, or parentheses).
- Conventional commit messages (`feat(...)`, `fix(...)`, `test(...)`). No `Co-Authored-By` trailer, no "Generated with Claude Code" footer.
- Division of labour: Claude does judgment/tone (understanding "frog", matching by text, writing the merged text, picking the survivor); deterministic code does the mechanical merge (more urgent deadline and priority, drop the other). The full backlog dump is never sent through Claude.
- Do NOT unit-test LLM output quality. Prompt changes (Task 5) are verified by inspection and by the full suite still passing, not by asserting model behavior.
- Do NOT change happy-path behavior of existing intents (add/complete/drop/edit/block/unblock/list/answer), the 06:00 focus, nags, quiet hours, staleness, or rendering.
- Merge rule: the survivor keeps its own id, created_at (age), and last_nagged_at; it takes the earlier deadline of the two (a real date beats None) and the higher priority (important beats normal).

---

### Task 1: Merge intent plumbing (schema, dataclass, parse)

Add the `merge` action and its `merge_from` field to the tool schema, the `Intent` dataclass, and `parse_intent`.

**Files:**
- Modify: `src/jolt/llm.py` (`Intent` dataclass lines 39-47, `parse_intent` lines 50-61, `_TOOL` lines 64-108)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `Intent` gains `merge_from: int | None = None`. `_TOOL` action enum includes `"merge"` and the schema has a `merge_from` integer property. `parse_intent` reads `merge_from` from the tool input.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py`:

```python
def test_parse_merge_with_merge_from():
    intent = llm.parse_intent(
        {"action": "merge", "task_id": 2, "merge_from": 5, "text": "combined task text"}
    )
    assert intent.action == "merge"
    assert intent.task_id == 2
    assert intent.merge_from == 5
    assert intent.text == "combined task text"


def test_tool_schema_supports_merge():
    assert "merge" in llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "merge_from" in llm._TOOL["input_schema"]["properties"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::test_parse_merge_with_merge_from tests/test_llm.py::test_tool_schema_supports_merge -v`
Expected: FAIL (`Intent` has no `merge_from`; `"merge"` not in enum).

- [ ] **Step 3: Add `merge_from` to the `Intent` dataclass**

In the `Intent` dataclass (llm.py:39-47), add the field after `blocked_by`:

```python
    blocked_by: int | None = None
    merge_from: int | None = None
    reply: str | None = None
    clear_deadline: bool = False
```

- [ ] **Step 4: Read `merge_from` in `parse_intent`**

In `parse_intent` (llm.py:50-61), add the line after `blocked_by`:

```python
        blocked_by=tool_input.get("blocked_by"),
        merge_from=tool_input.get("merge_from"),
        reply=tool_input.get("reply"),
```

- [ ] **Step 5: Add `merge` to the enum and `merge_from` to the schema**

In `_TOOL` (llm.py:64-108): add `"merge"` to the action `enum`, extend the action `description` to mention merge, and add a `merge_from` property after `blocked_by`.

Change the action enum and description to:

```python
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "edit", "block", "unblock", "merge", "list", "answer"],
                "description": "add a task, complete/drop an existing one, edit an existing task's deadline/priority/text, block a task on another / unblock it, merge two tasks into one, list the backlog, or answer a question / reply to the user.",
            },
```

Add the `merge_from` property immediately after the `blocked_by` property block:

```python
            "blocked_by": {
                "type": "integer",
                "description": "For action=block: the backlog number of the prerequisite task "
                "that must be finished first.",
            },
            "merge_from": {
                "type": "integer",
                "description": "For action=merge: the backlog number of the task to fold into "
                "task_id. task_id is the task that remains; put the combined text in the text field.",
            },
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat(llm): add the merge intent to the tool schema and Intent"
```

---

### Task 2: Resolve `merge_from` positions

`_resolve_positions` translates display numbers to ids and clarifies out-of-range numbers. Extend it to cover `merge_from` alongside `task_id` and `blocked_by`.

**Files:**
- Modify: `src/jolt/llm.py` (`_resolve_positions` lines 143-182)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Intent.merge_from` (from Task 1).
- Produces: `_resolve_positions` resolves an in-range `merge_from` to its id, and converts an intent with an out-of-range `merge_from` into the existing answer clarification (with `merge_from` set to None).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py` (reuse the existing `_task(id=...)` helper and `TZ`/`datetime`):

```python
def test_resolve_positions_resolves_merge_from():
    tasks = [_task(id=10), _task(id=20)]
    display_ids = [10, 20]
    intent = llm.Intent(action="merge", task_id=1, merge_from=2, text="combined")
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "merge"
    assert resolved[0].task_id == 10
    assert resolved[0].merge_from == 20


def test_resolve_positions_out_of_range_merge_from_clarifies():
    tasks = [_task(id=10)]
    display_ids = [10]
    intent = llm.Intent(action="merge", task_id=1, merge_from=9, text="combined")
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "answer"
    assert "9" in resolved[0].reply
    assert resolved[0].merge_from is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::test_resolve_positions_resolves_merge_from tests/test_llm.py::test_resolve_positions_out_of_range_merge_from_clarifies -v`
Expected: FAIL (`merge_from` is not resolved; stays 2/9).

- [ ] **Step 3: Add `merge_from` to the missing-check, the clarification reset, and the resolution**

In `_resolve_positions` (llm.py:157-181), change the `missing` list to include `merge_from`:

```python
        missing = [
            n
            for n in (intent.task_id, intent.blocked_by, intent.merge_from)
            if n is not None and n not in pos_to_id
        ]
```

In the clarification `replace(...)` call (llm.py:165-173), add `merge_from=None`:

```python
            resolved.append(
                replace(
                    intent,
                    action="answer",
                    reply=f"I don't have a task numbered {numbers} on the current list. "
                    "Send 'list' to see the current numbers.",
                    task_id=None,
                    blocked_by=None,
                    merge_from=None,
                )
            )
```

In the resolution block (llm.py:176-181), add the `merge_from` translation:

```python
        changes = {}
        if intent.task_id is not None:
            changes["task_id"] = pos_to_id[intent.task_id]
        if intent.blocked_by is not None:
            changes["blocked_by"] = pos_to_id[intent.blocked_by]
        if intent.merge_from is not None:
            changes["merge_from"] = pos_to_id[intent.merge_from]
        resolved.append(replace(intent, **changes) if changes else intent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (existing position-resolution tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat(llm): resolve merge_from positions like task_id and blocked_by"
```

---

### Task 3: `db.merge_tasks`

Add the mechanical merge to `db.py`: fold `from_id` into `survivor_id`, taking the more urgent deadline and priority, then drop the other task.

**Files:**
- Modify: `src/jolt/db.py` (add `merge_tasks` and a deadline helper; extend the models import)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `merge_tasks(conn, survivor_id: int, from_id: int, text: str) -> Task | None`. Returns the updated survivor, or `None` if `survivor_id == from_id` or either task is missing or not pending. The survivor keeps its id/created_at/last_nagged_at; its text becomes `text`; its deadline becomes the earlier of the two (a real date beats None); its priority becomes important if either was important. The other task is dropped.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py` (reuse the file's `datetime`, `TZ`, `date`, and `PRIORITY_*`/`STATUS_*` imports; `date` is already imported):

```python
def test_merge_takes_earlier_deadline_and_higher_priority_and_drops_other():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    survivor = db.add_task(conn, "buy molds", "normal", None, now)
    other = db.add_task(conn, "buy shower drain", "important", date(2026, 7, 17), now)
    merged = db.merge_tasks(conn, survivor.id, other.id, "buy molds and shower drain")
    assert merged is not None
    assert merged.id == survivor.id
    assert merged.text == "buy molds and shower drain"
    assert merged.deadline == date(2026, 7, 17)  # the only / earlier deadline wins
    assert merged.priority == PRIORITY_IMPORTANT  # important beats normal
    assert merged.created_at == survivor.created_at  # survivor keeps its age
    assert db.get_task(conn, other.id).status == STATUS_DROPPED


def test_merge_keeps_the_sooner_of_two_deadlines():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", "normal", date(2026, 7, 20), now)
    b = db.add_task(conn, "b", "normal", date(2026, 7, 15), now)
    merged = db.merge_tasks(conn, a.id, b.id, "a and b")
    assert merged.deadline == date(2026, 7, 15)


def test_merge_rejects_self_merge():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "a", "normal", None, now)
    assert db.merge_tasks(conn, t.id, t.id, "a") is None


def test_merge_rejects_missing_or_done_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", "normal", None, now)
    b = db.add_task(conn, "b", "normal", None, now)
    db.complete_task(conn, b.id, now)  # b no longer pending
    assert db.merge_tasks(conn, a.id, b.id, "a and b") is None
    assert db.merge_tasks(conn, a.id, 9999, "a and gone") is None
    assert db.get_task(conn, a.id).text == "a"  # survivor untouched on rejection
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k merge -v`
Expected: FAIL with `AttributeError: module 'jolt.db' has no attribute 'merge_tasks'`.

- [ ] **Step 3: Extend the models import**

At the top of `db.py`, the import currently reads:

```python
from .models import STATUS_DONE, STATUS_DROPPED, STATUS_PENDING, Task
```

Change it to add the priority constants:

```python
from .models import (
    PRIORITY_IMPORTANT,
    PRIORITY_NORMAL,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_PENDING,
    Task,
)
```

- [ ] **Step 4: Add `merge_tasks` (place it right after `unblock_task`, near line 171)**

```python
def _merge_deadline(a: date | None, b: date | None) -> date | None:
    # The more urgent of two deadlines: a real date always beats "no deadline".
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def merge_tasks(conn, survivor_id: int, from_id: int, text: str) -> Task | None:
    """Fold from_id into survivor_id, then drop from_id. The survivor keeps its id, age,
    and last_nagged_at; it takes the given text, the earlier deadline of the two, and the
    higher priority (important beats normal). Returns the updated survivor, or None if the
    two ids are equal or either task is missing or not pending."""
    if survivor_id == from_id:
        return None
    survivor = get_task(conn, survivor_id)
    other = get_task(conn, from_id)
    if survivor is None or survivor.status != STATUS_PENDING:
        return None
    if other is None or other.status != STATUS_PENDING:
        return None
    deadline = _merge_deadline(survivor.deadline, other.deadline)
    priority = (
        PRIORITY_IMPORTANT
        if PRIORITY_IMPORTANT in (survivor.priority, other.priority)
        else PRIORITY_NORMAL
    )
    conn.execute(
        "UPDATE tasks SET text = ?, deadline = ?, priority = ? WHERE id = ?",
        (text, deadline.isoformat() if deadline else None, priority, survivor_id),
    )
    conn.commit()
    drop_task(conn, from_id)  # clears any dangling blocked_by references to the dropped task
    return get_task(conn, survivor_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/jolt/db.py tests/test_db.py
git commit -m "feat(db): add merge_tasks to fold two tasks into one"
```

---

### Task 4: Orchestrator `merge` branch

Wire the `merge` intent into `apply_intent`: validate both tasks, build the text (Claude's or a fallback), call `db.merge_tasks`, and return a confirmation.

**Files:**
- Modify: `src/jolt/orchestrator.py` (`apply_intent`, add a branch before the final `list`/`answer` handling)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Intent` with `action="merge"`, `task_id` (survivor), `merge_from` (other), optional `text`; `db.merge_tasks`, `db.get_task`.
- Produces: a `merge` branch in `apply_intent` returning `Merged into "<text>".` on success, or `Couldn't find those tasks.` / `Can't merge a task with itself.` on a guard rejection.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py` (match the file's existing setup: it uses an in-memory `db` connection and `datetime`; reuse whatever `fresh()`/`now` helpers the file already defines. If it builds Intents directly, do the same):

```python
def test_merge_folds_two_tasks_and_confirms():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    b = db.add_task(conn, "buy shower drain", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=b.id, text="buy molds and shower drain")
    reply = orchestrator.apply_intent(conn, intent, now)
    assert "buy molds and shower drain" in reply
    assert db.get_task(conn, b.id).status == STATUS_DROPPED
    assert db.get_task(conn, a.id).text == "buy molds and shower drain"


def test_merge_falls_back_to_joined_text_when_claude_gives_none():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    b = db.add_task(conn, "buy shower drain", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=b.id, text=None)
    orchestrator.apply_intent(conn, intent, now)
    assert db.get_task(conn, a.id).text == "buy molds and buy shower drain"


def test_merge_missing_task_reports_not_found():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=9999, text="x")
    reply = orchestrator.apply_intent(conn, intent, now)
    assert reply == "Couldn't find those tasks."
    assert db.get_task(conn, a.id).text == "buy molds"  # unchanged
```

Ensure the test file imports `Intent` from `jolt.llm`, `db`, `orchestrator`, `datetime`, `TZ`, and `STATUS_DROPPED` (add any that are missing, following the file's existing import style).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -k merge -v`
Expected: FAIL (no `merge` branch, so `apply_intent` falls through to `intent.reply or "Not sure what you mean. Try rephrasing?"`).

- [ ] **Step 3: Add the `merge` branch**

In `orchestrator.py`, `apply_intent`, add this branch immediately after the `unblock` branch (after the line that returns for `action == "unblock"`, before the `if intent.action == "list":` branch):

```python
    if intent.action == "merge":
        if intent.task_id == intent.merge_from:
            return "Can't merge a task with itself."
        survivor = db.get_task(conn, intent.task_id)
        other = db.get_task(conn, intent.merge_from)
        if (
            survivor is None
            or survivor.status != STATUS_PENDING
            or other is None
            or other.status != STATUS_PENDING
        ):
            logger.info("Merge rejected: could not find both pending tasks")
            return "Couldn't find those tasks."
        text = intent.text or f"{survivor.text} and {other.text}"
        merged = db.merge_tasks(conn, intent.task_id, intent.merge_from, text)
        logger.info("Merged task_id=%s from %s", intent.task_id, intent.merge_from)
        return f'Merged into "{merged.text}".'
```

`STATUS_PENDING` is already imported in `orchestrator.py` (`from .models import PRIORITY_NORMAL, STATUS_PENDING`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): apply the merge intent to fold two tasks"
```

---

### Task 5: Prompt updates (frog vocabulary, matching, merge usage)

Update the `interpret_message` system prompt: teach the frog/tadpole vocabulary, strengthen text-based matching, add merge usage guidance, and reconcile the existing "never fold several tasks into one" instruction (which is about the add path) with the deliberate merge action. Prompt-only; no unit test.

**Files:**
- Modify: `src/jolt/llm.py` (the `system` string in `interpret_message`, lines 194-226)

**Interfaces:** none (prompt text only).

- [ ] **Step 1: Reconcile the "never fold" instruction**

In the system string (llm.py:196-198), the sentence currently reads:
`"...call record_intent once per distinct task or action, never fold several tasks into one."`
Change the clause so it scopes to adding and points at merge for the deliberate case:

```python
        "at once (for example a pasted list of tasks); call record_intent once per distinct "
        "task or action. When adding tasks, never fold several into one add; but when the user "
        "explicitly asks to combine or group existing tasks, use action=merge (see below). "
```

- [ ] **Step 2: Strengthen text matching**

The system string currently has (llm.py:204-205):
`"When the user names a task by its text instead of a number, find the matching line and use its number."`
Replace that sentence with stronger guidance:

```python
        "When the user describes a task in words rather than typing a number, match it to the "
        "backlog line by its text and use that line's number; only treat a bare number as task_id "
        "when the user actually typed that number. "
```

- [ ] **Step 3: Add merge usage guidance**

Immediately after the `unblock` sentence (llm.py:208-209, the sentence ending "...use action=unblock with the task_id. "), insert:

```python
        "When the user asks to combine, group, or merge two tasks, or says two entries are really "
        "the same task, call record_intent with action=merge: task_id = the task to keep, "
        "merge_from = the other task's number, and text = the combined text for the kept task. "
        "Only merge when they ask to combine existing tasks, not when they describe a dependency. "
```

- [ ] **Step 4: Add the frog / tadpole vocabulary**

Immediately before the `f"Today is {now:%A, %Y-%m-%d}. ..."` line (llm.py:224), insert a vocabulary sentence used for answering questions:

```python
        "Vocabulary: the user's 'frog' is the single most important thing to do today, the one "
        "lead task to hit first; a 'tadpole' is a low-value task that has been sitting and is a "
        "candidate to drop rather than chase. When the user asks what their frog is or what they "
        "should do today, answer with action=answer, naming the task from the backlog that best "
        "fits (lead with the most important, and prefer one that has gone stale). "
```

- [ ] **Step 5: Verify no regression and no em dashes**

Run: `uv run pytest -q && uv run ruff check .`
Expected: full suite passes, ruff clean (the prompt change breaks no existing test).

Run: `git diff src/jolt/llm.py | grep -n "^+" | grep "—"`
Expected: no output (no added line contains an em dash). If any appears, replace it.

- [ ] **Step 6: Commit**

```bash
git add src/jolt/llm.py
git commit -m "feat(llm): teach frog/tadpole vocabulary, steadier matching, and merge usage"
```

---

## Final verification

- [ ] Run the whole suite and lint:

```bash
uv run pytest -q && uv run ruff check .
```

Expected: all tests pass, ruff clean.

- [ ] Confirm no happy-path behavior changed: the pre-existing tests across `tests/` still pass unmodified (only additions were made to them), and the merge branch is the only new `apply_intent` path.
