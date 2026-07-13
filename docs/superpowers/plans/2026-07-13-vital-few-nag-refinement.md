# Vital-Few Nag Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jolt's avoidance response diverge by importance (push important tasks toward starting, nudge low-value tasks toward the exit) and stop waved-off low-value tasks from going permanently silent.

**Architecture:** Three behavior changes on the existing modules, no schema change. A new pure `nag_stance(task)` in `selection.py` classifies a task as start-leaning (important) or drop-leaning (normal); `llm.py` branches its nag and focus prompts on that stance. A new pure `select_slow_resurface(...)` in `selection.py` picks one low-value, avoided task to poke on a slow cadence (gated by `last_nagged_at`), and `scheduler.send_nags` sends that poke alongside the usual frog nag.

**Tech Stack:** Python 3.12+, uv (deps), ruff (lint/format), pytest (tests). Anthropic client and Telegram send are mocked at the boundary in tests.

## Global Constraints

- **Python 3.12+**; manage deps with `uv`, run tests with `uv run pytest`, lint with `uv run ruff check .`.
- **No em dashes** (`—`) anywhere: code, comments, commit messages, and especially the LLM prompt strings. Use commas, colons, periods, or parentheses. Existing prompt strings already instruct Claude "no em dashes"; keep that.
- **All code, comments, and commit messages in English.** French appears only in runtime user-facing content, never here.
- **No database schema change.** Reuse the existing `status='dropped'` and `last_nagged_at` columns only.
- **Behavior tunables are single named constants in `src/jolt/config.py`.**
- **Commits:** conventional style (`feat(...)`, `test(...)`), concise. No `Co-Authored-By` trailer, no "Generated with" footer.
- **External APIs mocked at the boundary in tests:** never make a real Anthropic or Telegram call. The existing `FakeClient` / `collector()` helpers in the test files are the pattern to follow.
- **Reference spec:** `docs/superpowers/specs/2026-07-12-accountability-bot-design.md` (revised 2026-07-13).

---

### Task 1: Stance-aware nags (start-leaning vs drop-leaning)

Add a pure classifier for how a nag should lean, and branch the nag prompt on it: an important task gets a start-leaning nag (what is blocking it, break it down, escalate), a normal task gets a drop-leaning zero-based nudge (would you add it today, still want it).

**Files:**
- Modify: `src/jolt/selection.py` (add `nag_stance`)
- Modify: `src/jolt/llm.py` (branch `write_nag` on stance)
- Test: `tests/test_selection.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Task` (from `jolt.models`), `PRIORITY_IMPORTANT` (from `jolt.models`).
- Produces:
  - `selection.nag_stance(task: Task) -> str` returning `"start"` (important) or `"drop"` (normal).
  - `llm.write_nag(task: Task, now: datetime, client) -> str` (signature unchanged; internally calls `nag_stance`).

- [ ] **Step 1: Write the failing tests for `nag_stance`**

Append to `tests/test_selection.py`:

```python
def test_nag_stance_start_for_important():
    assert selection.nag_stance(make(1, priority=PRIORITY_IMPORTANT)) == "start"


def test_nag_stance_drop_for_normal():
    assert selection.nag_stance(make(1, priority=PRIORITY_NORMAL)) == "drop"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_selection.py::test_nag_stance_start_for_important tests/test_selection.py::test_nag_stance_drop_for_normal -v`
Expected: FAIL with `AttributeError: module 'jolt.selection' has no attribute 'nag_stance'`.

- [ ] **Step 3: Implement `nag_stance` in `selection.py`**

Add this function to `src/jolt/selection.py` (after `priority_sort_key`, or anywhere at module level):

```python
def nag_stance(task: Task) -> str:
    """How the nag should lean. 'start' pushes an important task toward action (what is
    blocking it, break it down). 'drop' nudges a low-value task toward the exit with a
    zero-based question. Importance is the whole signal here: age only gates whether the
    task is nagged at all, not how the nag leans."""
    return "start" if task.priority == PRIORITY_IMPORTANT else "drop"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_selection.py::test_nag_stance_start_for_important tests/test_selection.py::test_nag_stance_drop_for_normal -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing tests for the branched `write_nag` prompt**

Append to `tests/test_llm.py` (the file already defines `_task()` = normal, `_important_task()` = important, `FakeClient`, and `TZ`):

```python
def test_write_nag_important_is_start_leaning():
    # An important task's nag pushes toward starting: ask what is blocking it / break it down.
    text_block = SimpleNamespace(type="text", text="go")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    llm.write_nag(_important_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "blocking" in system or "break it down" in system


def test_write_nag_normal_is_drop_leaning():
    # A low-value task's nag leans toward dropping it with a zero-based question, not scheduling it.
    text_block = SimpleNamespace(type="text", text="go")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    llm.write_nag(_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "drop" in system or "still want" in system or "add it today" in system
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::test_write_nag_normal_is_drop_leaning -v`
Expected: FAIL on the assertion (current `write_nag` prompt contains neither "drop" nor "still want" nor "add it today").

- [ ] **Step 7: Branch `write_nag` on stance in `llm.py`**

At the top of `src/jolt/llm.py`, add the import next to the existing ones:

```python
from .selection import nag_stance
```

(There is no import cycle: `selection.py` imports only `config` and `models`, not `llm`.)

Replace the entire `write_nag` function with:

```python
def write_nag(task: Task, now: datetime, client) -> str:
    age = (now - task.created_at).days
    stance = nag_stance(task)
    if stance == "start":
        guidance = (
            "This task matters. Push toward starting it: first ask what is actually blocking "
            "it and offer to break it down into a small first step. The older it is and the "
            "later in the day, the blunter and more insistent you get, up to a flat 'do it or "
            "delete it' by evening."
        )
    else:
        guidance = (
            "This task is low-stakes and has been sitting untouched. Do not chase it to get "
            "done. Instead nudge toward dropping it with a zero-based question: if it were not "
            "already on the list, would they add it today? Still want it, or drop it? Stay "
            "light and easy to wave off."
        )
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines, no em dashes) about the task below. "
        + guidance
        + " Never guilt-trip."
    )
    user = (
        f"Task: {task.text}. Importance: {_importance(task)}. Age: {age} days. "
        f"Current hour: {now.hour}."
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_nag", response)
    return _text_of(response)
```

- [ ] **Step 8: Run the affected tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -k "write_nag" -v`
Expected: PASS. This covers the two new tests plus the existing `test_write_nag_passes_importance_to_prompt` (still passes: an important task's `user` string contains "important") and `test_write_nag_returns_text` (still passes: normal task returns the canned text).

- [ ] **Step 9: Commit**

```bash
git add src/jolt/selection.py src/jolt/llm.py tests/test_selection.py tests/test_llm.py
git commit -m "feat(llm): lean nags start-ward for frogs, drop-ward for tadpoles"
```

---

### Task 2: Drop-leaning framing for low-value daily-focus rescues

The 06:00 focus message lists up to two stale "rescue" tasks and currently asks "what is blocking them" for all of them. For a low-value (normal) rescue, that should instead lean toward "is this still worth keeping?", matching the nag change in Task 1. The rescue importance is already passed into the prompt, so this is a prompt-guidance change only.

**Files:**
- Modify: `src/jolt/llm.py` (`write_focus` system prompt)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `DailyFocus` (from `jolt.models`), `_importance` (already in `llm.py`).
- Produces: `llm.write_focus(focus: DailyFocus, now: datetime, client) -> str` (signature unchanged).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm.py`:

```python
def test_write_focus_leans_drop_for_low_value_rescue():
    # A normal (low-value) rescue should be framed as "still worth keeping?", not
    # "what is blocking it?", so the morning digest matches the drop-leaning nag stance.
    text_block = SimpleNamespace(type="text", text="ok")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    focus = DailyFocus(focus=_important_task(1), rescues=[_task(2)])  # _task is normal
    llm.write_focus(focus, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "drop" in system or "worth keeping" in system or "still want" in system
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_llm.py::test_write_focus_leans_drop_for_low_value_rescue -v`
Expected: FAIL on the assertion (current `write_focus` system prompt contains none of "drop", "worth keeping", "still want").

- [ ] **Step 3: Update the `write_focus` system prompt in `llm.py`**

In `src/jolt/llm.py` (the `write_focus` function), replace the `system` assignment with:

```python
    system = (
        "You are Jolt. Write a short morning message (2 to 4 lines, no em dashes). Lead with the "
        "one focus task as the single thing to hit today, and push harder on important tasks than "
        "low-stakes ones. If there are rescue tasks that have gone stale, mention them: for an "
        "important rescue ask what is blocking it, but for a low-stakes (normal) rescue lean the "
        "other way and ask whether it is still worth keeping or should just be dropped. Be plain "
        "and direct, never guilt-tripping."
    )
```

(The `user` line already carries each rescue's importance as `(Nd old, normal|important)`, so Claude can tell which framing to use.)

- [ ] **Step 4: Run the affected tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -k "write_focus" -v`
Expected: PASS. Covers the new test plus existing `test_write_focus_passes_importance_to_prompt` and `test_write_focus_returns_text`.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat(llm): frame low-value daily rescues as drop candidates"
```

---

### Task 3: `select_slow_resurface` pure selection rule

Add the pure function that picks the single low-value, avoided task to poke on the slow cadence, plus its tunable constant. No I/O, fully unit-tested.

**Files:**
- Modify: `src/jolt/config.py` (add `SLOW_RESURFACE_DAYS`)
- Modify: `src/jolt/selection.py` (add `select_slow_resurface`)
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Task`, `PRIORITY_NORMAL`, `STATUS_PENDING` (from `jolt.models`); `is_stale`, `is_blocked` (already in `selection.py`); `config.SLOW_RESURFACE_DAYS`.
- Produces: `selection.select_slow_resurface(tasks: list[Task], now: datetime, exclude_id: int | None = None, cadence_days: int = config.SLOW_RESURFACE_DAYS) -> Task | None`.

- [ ] **Step 1: Add the tunable constant**

In `src/jolt/config.py`, add after `STALE_THRESHOLD_DAYS` (keep the group of behavior tunables together):

```python
SLOW_RESURFACE_DAYS = 7  # a waved-off normal, stale task is re-poked at most once this often
```

- [ ] **Step 2: Update the `make` test helper to set `last_nagged_at`**

In `tests/test_selection.py`, replace the `make` helper so tests can set a nag time (the current helper hardcodes `last_nagged_at=None`):

```python
def make(
    id,
    *,
    priority=PRIORITY_NORMAL,
    deadline=None,
    created=NOW,
    status=STATUS_PENDING,
    blocked_by=None,
    nagged=None,
):
    return Task(
        id=id,
        text=f"t{id}",
        priority=priority,
        deadline=deadline,
        created_at=created,
        status=status,
        last_nagged_at=nagged,
        completed_at=None,
        blocked_by=blocked_by,
    )
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_selection.py`:

```python
def test_slow_resurface_picks_normal_stale_task():
    t = make(1, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([t], NOW).id == 1


def test_slow_resurface_none_when_nothing_eligible():
    fresh = make(1, created=NOW)  # not stale
    important = make(2, priority=PRIORITY_IMPORTANT, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([fresh, important], NOW) is None


def test_slow_resurface_excludes_focus_id():
    focus = make(1, created=NOW - timedelta(days=5))
    other = make(2, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([focus, other], NOW, exclude_id=1).id == 2


def test_slow_resurface_gated_within_cadence():
    recent = make(1, created=NOW - timedelta(days=10), nagged=NOW - timedelta(days=2))
    assert selection.select_slow_resurface([recent], NOW) is None


def test_slow_resurface_eligible_after_cadence():
    old = make(1, created=NOW - timedelta(days=30), nagged=NOW - timedelta(days=8))
    assert selection.select_slow_resurface([old], NOW).id == 1


def test_slow_resurface_prefers_never_nagged():
    never = make(1, created=NOW - timedelta(days=5))
    nagged_long_ago = make(2, created=NOW - timedelta(days=20), nagged=NOW - timedelta(days=10))
    assert selection.select_slow_resurface([never, nagged_long_ago], NOW).id == 1


def test_slow_resurface_ignores_blocked_task():
    blocker = make(1, created=NOW - timedelta(days=5))
    dep = make(2, created=NOW - timedelta(days=5), blocked_by=1)
    # exclude the blocker as the focus; the only other candidate (dep) is blocked -> None
    assert selection.select_slow_resurface([blocker, dep], NOW, exclude_id=1) is None
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_selection.py -k slow_resurface -v`
Expected: FAIL with `AttributeError: module 'jolt.selection' has no attribute 'select_slow_resurface'`.

- [ ] **Step 5: Implement `select_slow_resurface` in `selection.py`**

Add to `src/jolt/selection.py`. The `timedelta` import already exists at the top (`from datetime import date, datetime, timedelta`); `PRIORITY_NORMAL` and `STATUS_PENDING` must be importable, so update the models import line at the top of the file from:

```python
from .models import DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING, Task
```

to:

```python
from .models import DailyFocus, PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task
```

Then add the function (place it after `select_daily_focus`):

```python
def select_slow_resurface(
    tasks: list[Task],
    now: datetime,
    exclude_id: int | None = None,
    cadence_days: int = config.SLOW_RESURFACE_DAYS,
) -> Task | None:
    """The one low-value, avoided task to poke on the slow cadence, or None.

    A waved-off normal task should not vanish, but it must not be chased like a frog.
    Eligible: pending, normal priority, stale, not blocked, not the excluded focus, and
    either never nagged or last nagged at least cadence_days ago. Among the eligible,
    prefer the one that has waited longest for a poke: never-nagged first, then the
    oldest last_nagged_at, then the oldest task."""
    eligible = [
        t
        for t in tasks
        if t.status == STATUS_PENDING
        and t.priority == PRIORITY_NORMAL
        and t.id != exclude_id
        and is_stale(t, now)
        and not is_blocked(t, tasks)
        and (t.last_nagged_at is None or now - t.last_nagged_at >= timedelta(days=cadence_days))
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda t: (
            t.last_nagged_at is not None,  # False (never nagged) sorts first
            t.last_nagged_at or t.created_at,  # then oldest last poke
            t.created_at,  # then oldest task
        ),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_selection.py -k slow_resurface -v`
Expected: PASS (7 passed).

- [ ] **Step 7: Commit**

```bash
git add src/jolt/config.py src/jolt/selection.py tests/test_selection.py
git commit -m "feat(selection): add slow-resurface rule for waved-off low-value tasks"
```

---

### Task 4: Wire the slow re-surface into the nag job

`send_nags` currently pings only the daily-focus task. Extend it so that, alongside the frog nag, it also pokes one eligible slow-resurface tadpole (excluding the focus) and marks it nagged. Each tadpole is gated to at most once per `SLOW_RESURFACE_DAYS`, so it stays much lazier than the frog's 3x/day rhythm.

**Files:**
- Modify: `src/jolt/scheduler.py` (`send_nags`)
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `db.list_all`, `db.mark_nagged`, `llm.write_nag`, `selection.select_daily_focus`, `selection.select_slow_resurface`, `selection.is_quiet_hours`.
- Produces: `scheduler.send_nags(conn, send, client, now: datetime) -> None` (signature unchanged; now can emit two messages).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py`:

```python
def test_slow_resurface_nags_a_normal_stale_nonfocus_task():
    # An important stale task is the focus (frog). A separate normal stale task should
    # also get a slow-resurface poke, so two messages go out and the tadpole is marked.
    conn = fresh()
    created = datetime(2026, 7, 5, tzinfo=TZ)  # 7 days before `now`
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    db.add_task(conn, "file taxes", "important", None, created)  # id 1, becomes focus
    tad = db.add_task(conn, "sort old photos", "normal", None, created)  # id 2, tadpole
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert len(sent) == 2
    assert db.get_task(conn, tad.id).last_nagged_at == now


def test_slow_resurface_gated_by_cadence():
    # The tadpole was poked yesterday, well within SLOW_RESURFACE_DAYS, so only the
    # frog nag goes out this run.
    conn = fresh()
    created = datetime(2026, 7, 1, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    db.add_task(conn, "file taxes", "important", None, created)  # focus
    tad = db.add_task(conn, "sort old photos", "normal", None, created)
    db.mark_nagged(conn, tad.id, datetime(2026, 7, 11, 13, tzinfo=TZ))  # poked yesterday
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert len(sent) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scheduler.py -k slow_resurface -v`
Expected: FAIL. `test_slow_resurface_nags_a_normal_stale_nonfocus_task` fails with `assert 1 == 2` (current `send_nags` sends only the focus nag).

- [ ] **Step 3: Update `send_nags` in `scheduler.py`**

Update the import line at the top of `src/jolt/scheduler.py` from:

```python
from .selection import is_quiet_hours, select_daily_focus
```

to:

```python
from .selection import is_quiet_hours, select_daily_focus, select_slow_resurface
```

Replace the entire `send_nags` function with:

```python
def send_nags(conn, send, client, now: datetime) -> None:
    logger.info("Nag job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping nag: quiet hours")
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    focus_id = focus.focus.id if focus.focus else None
    if focus.focus is not None:
        logger.info("Nagging about focus task_id=%s", focus.focus.id)
        send(llm.write_nag(focus.focus, now, client))
        db.mark_nagged(conn, focus.focus.id, now)
    tadpole = select_slow_resurface(tasks, now, exclude_id=focus_id)
    if tadpole is not None:
        logger.info("Slow re-surface of task_id=%s", tadpole.id)
        send(llm.write_nag(tadpole, now, client))
        db.mark_nagged(conn, tadpole.id, now)
    if focus.focus is None and tadpole is None:
        logger.info("Skipping nag: nothing to nag about")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scheduler.py -k "slow_resurface or nag" -v`
Expected: PASS. Covers the two new tests plus the existing `test_nag_sends_and_marks_nagged` (single normal task is the focus, so the tadpole selector excludes it and returns None: still exactly one message) and `test_nags_skipped_when_no_pending_task` / `test_nags_skipped_during_quiet_hours` (still no messages).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): poke waved-off low-value tasks on a slow cadence"
```

---

### Task 5: Full suite, lint, and format

Confirm the whole refinement is green and clean before handoff.

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, all tests green (the prior 40+ plus the new ones from Tasks 1, 3, and 4). If anything fails, fix the offending task before continuing.

- [ ] **Step 2: Lint and format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: `ruff check` reports no errors; `ruff format` reports the files already formatted (or reformats them). If `ruff format` changes files, re-run `uv run pytest -q` to confirm still green, then `git add -A && git commit -m "style: ruff format"`.

- [ ] **Step 3: Confirm no em dash slipped into new code**

Run: `grep -rn "—" src/jolt/ tests/`
Expected: no matches inside functions/strings you added. (Pre-existing matches elsewhere, if any, are out of scope.) If a match is in your new code, replace it with a comma, colon, period, or parentheses and re-commit.
