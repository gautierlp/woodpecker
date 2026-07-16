# Multi-project reading and metadata-aware nags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jolt read tasks from every Vikunja project and enrich its nags with full priority (three bands), the mdone duration estimate, and the task description.

**Architecture:** Jolt stays a thin client over Vikunja. The read model (`Task`) gains raw priority, a parsed duration, a clean description, and project identity. `selection.py` derives three priority bands and uses duration as an avoidance-buster in focus/rescue ranking. `llm.py` feeds band, duration, description, and project into the nag/focus prompts. `vikunja.py` enumerates all projects live and merges their open tasks.

**Tech Stack:** Python 3.12, httpx (Vikunja REST), Anthropic SDK, pytest. Package layout `src/jolt`, tests in `tests/`.

## Global Constraints

- Python `>= 3.12`. Run tests with `uv run pytest` (config in `pyproject.toml`, `pythonpath = ["src", "."]`).
- TDD: write the failing test, run it red, implement minimally, run it green, commit. One commit per task.
- Conventional-commit messages. No `Co-Authored-By` / "Generated with" trailer.
- Never emit an em dash (`—`) in any user-facing copy or prompt text. Use a comma, colon, period, or parentheses. (Jolt's existing prompts already carry this rule.)
- mdone stores the duration estimate as an HTML comment inside `description`, exact form `<!-- mdone:estimate=SECONDS -->` (seconds, integer).
- The `PRIORITY_IMPORTANT = "important"` / `PRIORITY_NORMAL = "normal"` constants in `models.py` stay: they are the task-*creation* vocabulary (add flow, `task_create_payload`, LLM add-tool enum, orchestrator default). Only read-model `task.priority == PRIORITY_*` comparisons are removed.
- `Task.priority` on the read model becomes the raw Vikunja integer (0-5). Bands are derived, never stored.
- Priority bands (constants, `config.py`): High = `priority >= 3`, Mid = `1 <= priority <= 2`, Low/unset = `priority == 0`.
- Long-duration cutoff for nag wording: `>= 3600` seconds (1h).

---

### Task 1: Parse the mdone duration estimate and clean description

Pure helper functions in `vikunja.py`. Additive, no model change yet, so the whole suite stays green.

**Files:**
- Modify: `src/jolt/vikunja.py` (add `import re` and two functions near the top-level helpers, after line 42)
- Test: `tests/test_vikunja.py` (add a test block)

**Interfaces:**
- Produces:
  - `parse_estimate_seconds(description: str | None) -> int | None`
  - `clean_description(description: str | None) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vikunja.py` (after the imports, anywhere at module level):

```python
def test_parse_estimate_reads_the_mdone_sentinel():
    assert vikunja.parse_estimate_seconds("<!-- mdone:estimate=3600 -->") == 3600
    assert vikunja.parse_estimate_seconds("do X <!-- mdone:estimate=900 --> notes") == 900


def test_parse_estimate_absent_or_malformed_is_none():
    assert vikunja.parse_estimate_seconds("") is None
    assert vikunja.parse_estimate_seconds(None) is None
    assert vikunja.parse_estimate_seconds("plain notes, no estimate") is None
    assert vikunja.parse_estimate_seconds("<!-- mdone:estimate=abc -->") is None


def test_parse_estimate_tolerates_whitespace_in_sentinel():
    assert vikunja.parse_estimate_seconds("<!--  mdone:estimate=120  -->") == 120


def test_clean_description_strips_the_sentinel_and_trims():
    assert vikunja.clean_description("<!-- mdone:estimate=3600 -->") == ""
    assert vikunja.clean_description("call the accountant <!-- mdone:estimate=1800 -->") == "call the accountant"
    assert vikunja.clean_description(None) == ""


def test_clean_description_leaves_a_malformed_sentinel_in_place():
    # A non-numeric estimate is not a valid sentinel, so it is left as visible text.
    assert vikunja.clean_description("<!-- mdone:estimate=abc -->") == "<!-- mdone:estimate=abc -->"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vikunja.py -k "estimate or clean_description" -v`
Expected: FAIL with `AttributeError: module 'jolt.vikunja' has no attribute 'parse_estimate_seconds'`

- [ ] **Step 3: Implement the helpers**

In `src/jolt/vikunja.py`, add `import re` to the imports at the top (alongside `import logging`), then add after the `_due_for` function (currently ends at line 42):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_vikunja.py -k "estimate or clean_description" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/jolt/vikunja.py tests/test_vikunja.py
git commit -m "feat(vikunja): parse mdone duration estimate from description"
```

---

### Task 2: Read model carries raw priority, estimate, description, project; selection derives bands

Atomic migration: `Task.priority` flips from the binary string to the raw Vikunja integer, and every read-model consumer (`vikunja_to_task`, `selection.py`, and the two priority reads in `llm.py`) moves to a derived band. These are coupled through the shared `Task.priority` field, so they change together to keep the suite green. New `Task` fields (`estimate_seconds`, `details`, `project_id`, `project_name`) are populated here using Task 1's helpers.

**Files:**
- Modify: `src/jolt/models.py:12-22` (the `Task` dataclass)
- Modify: `src/jolt/config.py` (add band-threshold constants)
- Modify: `src/jolt/vikunja.py:45-59` (`vikunja_to_task`) and its imports (lines 7-13)
- Modify: `src/jolt/selection.py` (band helper + rewrite `priority_sort_key`, `nag_stance`, `select_daily_focus`, `select_slow_resurface`)
- Modify: `src/jolt/llm.py:41-42` (`_importance`) and `src/jolt/llm.py:146` (the `[important]` tag in `_task_lines`)
- Test: `tests/test_vikunja.py`, `tests/test_selection.py`, `tests/test_llm.py`, and the `Task(...)` builders in `tests/test_render.py`, `tests/test_orchestrator.py`, `tests/test_scheduler.py`, `tests/test_store.py`, `tests/test_bot.py`, `tests/test_migrate.py`

**Interfaces:**
- Consumes: `parse_estimate_seconds`, `clean_description` (Task 1)
- Produces:
  - `models.Task` with fields `priority: int` and new `estimate_seconds: int | None = None`, `details: str = ""`, `project_id: int = 0`, `project_name: str = ""`
  - `vikunja.vikunja_to_task(raw: dict, project_name: str = "") -> Task`
  - `config.PRIORITY_HIGH_MIN = 3`, `config.PRIORITY_MID_MIN = 1`
  - `selection.BAND_HIGH = "high"`, `selection.BAND_MID = "mid"`, `selection.BAND_LOW = "low"`
  - `selection.priority_band(task: Task) -> str`

- [ ] **Step 1: Update the Task dataclass**

Replace `src/jolt/models.py:12-22` with:

```python
@dataclass(frozen=True)
class Task:
    id: int
    text: str
    priority: int  # raw Vikunja priority 0-5; bands are derived in selection.py
    deadline: date | None
    created_at: datetime
    status: str
    last_nagged_at: datetime | None
    completed_at: datetime | None
    position: int = 0
    estimate_seconds: int | None = None  # from the mdone sentinel in the description
    details: str = ""  # the visible description, sentinel stripped
    project_id: int = 0
    project_name: str = ""
```

Leave `PRIORITY_NORMAL` / `PRIORITY_IMPORTANT` (lines 4-5) unchanged: they remain the creation vocabulary. Add a short comment above them:

```python
# Task-creation vocabulary only (add flow, task_create_payload, LLM add-tool enum). The
# read model's Task.priority is the raw Vikunja integer; see selection.priority_band.
PRIORITY_NORMAL = "normal"
PRIORITY_IMPORTANT = "important"
```

- [ ] **Step 2: Add band-threshold constants to config**

In `src/jolt/config.py`, after line 9 (`NAG_HOURS = (9, 13, 19)`), add:

```python
# Priority bands derived from the raw Vikunja priority (0-5). High pushes hard, Mid gets a
# gentle poke, Low/unset gets nudged toward dropping. See selection.priority_band.
PRIORITY_HIGH_MIN = 3  # priority >= 3 -> high band
PRIORITY_MID_MIN = 1  # priority 1-2 -> mid band; 0 -> low band
```

- [ ] **Step 3: Write the failing test for the raw-priority mapping**

In `tests/test_vikunja.py`, replace `test_high_priority_maps_to_important` (lines 39-42) with:

```python
def test_priority_is_stored_raw():
    assert vikunja.vikunja_to_task(_raw(priority=0)).priority == 0
    assert vikunja.vikunja_to_task(_raw(priority=3)).priority == 3
    assert vikunja.vikunja_to_task(_raw(priority=5)).priority == 5


def test_maps_estimate_and_clean_description_and_project():
    raw = _raw(description="file it <!-- mdone:estimate=1800 -->", project_id=2)
    t = vikunja.vikunja_to_task(raw, project_name="Backlog")
    assert t.estimate_seconds == 1800
    assert t.details == "file it"
    assert t.project_id == 2
    assert t.project_name == "Backlog"


def test_maps_missing_description_to_no_estimate_empty_details():
    t = vikunja.vikunja_to_task(_raw())
    assert t.estimate_seconds is None
    assert t.details == ""
```

Also fix `test_maps_basic_open_task` (line 31): change `assert t.priority == PRIORITY_NORMAL` to `assert t.priority == 0`. And remove the now-unused `PRIORITY_IMPORTANT, PRIORITY_NORMAL` from the `test_maps_*` assertions (the create-payload tests at lines 56-66 still use them, so keep the import).

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_vikunja.py -k "priority or estimate or clean or basic_open" -v`
Expected: FAIL (`vikunja_to_task` still maps priority to a string and has no `project_name` param)

- [ ] **Step 5: Rewrite vikunja_to_task**

In `src/jolt/vikunja.py`, change the imports block (lines 7-13) to drop the read-side constants (keep `PRIORITY_IMPORTANT` for `task_create_payload`):

```python
from .models import (
    PRIORITY_IMPORTANT,
    STATUS_DONE,
    STATUS_PENDING,
    Task,
)
```

Replace `vikunja_to_task` (lines 45-59) with:

```python
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
```

`task_create_payload` (lines 62-70) is unchanged: it still compares its string `priority` argument against `PRIORITY_IMPORTANT`.

- [ ] **Step 6: Run to verify the vikunja tests pass**

Run: `uv run pytest tests/test_vikunja.py -k "priority or estimate or clean or basic_open or create_payload" -v`
Expected: PASS

- [ ] **Step 7: Write the failing selection band tests**

In `tests/test_selection.py`, update the two helpers `make` (lines 17-37) and `_t` (lines 40-51) so their default priority is the integer `0`, and add band tests. Change both helpers' signature default from `priority=PRIORITY_NORMAL` to `priority=0`. Then replace `test_important_sorts_before_normal` (60-63), `test_nag_stance_start_for_important` (139-140), `test_nag_stance_drop_for_normal` (143-144) and add band coverage:

```python
def test_priority_band_maps_raw_priority():
    assert selection.priority_band(make(1, priority=5)) == selection.BAND_HIGH
    assert selection.priority_band(make(2, priority=3)) == selection.BAND_HIGH
    assert selection.priority_band(make(3, priority=2)) == selection.BAND_MID
    assert selection.priority_band(make(4, priority=1)) == selection.BAND_MID
    assert selection.priority_band(make(5, priority=0)) == selection.BAND_LOW


def test_high_band_sorts_before_mid_before_low():
    low = make(1, priority=0)
    mid = make(2, priority=2)
    high = make(3, priority=4)
    assert [t.id for t in selection.order_backlog([low, mid, high])] == [3, 2, 1]


def test_nag_stance_start_for_high_band():
    assert selection.nag_stance(make(1, priority=4)) == "start"


def test_nag_stance_drop_for_low_band():
    assert selection.nag_stance(make(1, priority=0)) == "drop"
```

Then, everywhere in `tests/test_selection.py` that passes `priority=PRIORITY_IMPORTANT`, change it to `priority=4`; everywhere that passes `priority=PRIORITY_NORMAL`, change it to `priority=0`. This affects `test_stale_important_leads_and_is_not_also_a_rescue`, `test_lead_falls_back_to_top_priority_when_no_important_is_stale`, `test_select_daily_focus_picks_top_and_two_rescues`, `test_slow_resurface_none_when_nothing_eligible`, and the inline `Task(...)` in `test_slow_resurface_eligible_at_exactly_the_cadence_boundary` (line 188, `priority=PRIORITY_NORMAL` -> `priority=0`). Remove the now-unused `PRIORITY_IMPORTANT, PRIORITY_NORMAL` names from the import at the top of the file.

- [ ] **Step 8: Run to verify failure**

Run: `uv run pytest tests/test_selection.py -v`
Expected: FAIL (`selection.priority_band`, `BAND_HIGH` etc. do not exist; `nag_stance` still compares strings)

- [ ] **Step 9: Rewrite selection.py band logic**

In `src/jolt/selection.py`, change the import (line 4) to drop the priority string constants:

```python
from datetime import date, datetime, timedelta

from . import config
from .models import DailyFocus, STATUS_PENDING, Task

BAND_HIGH = "high"
BAND_MID = "mid"
BAND_LOW = "low"


def priority_band(task: Task) -> str:
    if task.priority >= config.PRIORITY_HIGH_MIN:
        return BAND_HIGH
    if task.priority >= config.PRIORITY_MID_MIN:
        return BAND_MID
    return BAND_LOW


_BAND_RANK = {BAND_HIGH: 0, BAND_MID: 1, BAND_LOW: 2}
```

Replace `priority_sort_key` (lines 7-10):

```python
def priority_sort_key(task: Task) -> tuple:
    deadline_rank = task.deadline or date.max
    return (_BAND_RANK[priority_band(task)], deadline_rank, task.position, task.created_at)
```

Replace `nag_stance` (lines 13-18). Keep it two-way for now (High pushes, everything else leans drop); the Mid "poke" stance arrives in Task 4:

```python
def nag_stance(task: Task) -> str:
    """How the nag should lean. 'start' pushes a high-priority task toward action; 'drop'
    nudges a low-value task toward the exit. Extended to a third 'poke' stance in Task 4."""
    return "start" if priority_band(task) == BAND_HIGH else "drop"
```

In `select_daily_focus`, replace the `stale_important` line (line 39):

```python
    stale_important = [t for t in ordered if priority_band(t) == BAND_HIGH and is_stale(t, now)]
```

In `select_slow_resurface`, replace the priority condition (line 70) so the drop-nudge targets only the Low band:

```python
        and priority_band(t) == BAND_LOW
```

- [ ] **Step 10: Run to verify the selection tests pass**

Run: `uv run pytest tests/test_selection.py -v`
Expected: PASS

- [ ] **Step 11: Write the failing llm test for the band-based importance label**

In `tests/test_llm.py`, update the `_task` (59-69), `_ordered_task` (72-84), the inline done `Task(...)` (160-169), and `_important_task` (396-406) builders so priority is an integer: `_task`/`_ordered_task`/the done task use `priority=0`, `_important_task` uses `priority=4`. Remove `PRIORITY_IMPORTANT, PRIORITY_NORMAL` from the import (line 7) except keep them if `test_parse_add_with_deadline_and_priority` still references `PRIORITY_IMPORTANT` (it does, line 23, on the *Intent* create side): keep the import. No new test needed here beyond the builder fixes; existing `test_write_nag_passes_importance_to_prompt` (high task -> "important" in prompt) and `test_write_nag_normal_is_drop_leaning` (low task) assert the behavior.

- [ ] **Step 12: Run to verify failure**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL (`_importance` compares `task.priority == PRIORITY_IMPORTANT`, now an int-vs-string mismatch, so a priority-4 task is labeled "normal")

- [ ] **Step 13: Update llm.py priority reads to use the band**

In `src/jolt/llm.py`, change the import (line 8) to drop `PRIORITY_IMPORTANT` and add the band helper:

```python
from .models import DailyFocus, STATUS_PENDING, Task
from .selection import BAND_HIGH, nag_stance, priority_band
```

Replace `_importance` (lines 41-42):

```python
def _importance(task: Task) -> str:
    return "important" if priority_band(task) == BAND_HIGH else "normal"
```

Replace the `[important]` tag in `_task_lines` (line 146):

```python
        + (" [important]" if priority_band(t) == BAND_HIGH else "")
```

- [ ] **Step 14: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. If any remaining test constructs `Task(...)` with a string priority (search the failure output), change that literal to the integer equivalent (`PRIORITY_NORMAL` -> `0`, `PRIORITY_IMPORTANT` -> `4`) in `tests/test_render.py`, `tests/test_orchestrator.py`, `tests/test_scheduler.py`, `tests/test_store.py`, `tests/test_bot.py`, `tests/test_migrate.py`.

- [ ] **Step 15: Commit**

```bash
git add src/jolt/models.py src/jolt/config.py src/jolt/vikunja.py src/jolt/selection.py src/jolt/llm.py tests/
git commit -m "feat: read-model raw priority, estimate, description, project + priority bands"
```

---

### Task 3: Duration as an avoidance-buster in focus and rescue ranking

Among stale High-band tasks, the longer-estimated one leads the daily focus and the rescue slots. Applied as a tie-break after band and staleness, before deadline.

**Files:**
- Modify: `src/jolt/selection.py` (`select_daily_focus`; add `avoidance_sort_key`)
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `priority_band`, `is_stale`, `BAND_*` (Task 2)
- Produces: `selection.avoidance_sort_key(task: Task, now: datetime) -> tuple`; `select_daily_focus` now orders stale High-band candidates by longer estimate first

- [ ] **Step 1: Write the failing test**

Add to `tests/test_selection.py`:

```python
def test_longer_stale_high_task_leads_the_focus():
    # Two stale high tasks; the longer-estimated one is the bigger avoided thing and leads.
    short = make(1, priority=4, created=NOW - timedelta(days=5))
    long = make(2, priority=4, created=NOW - timedelta(days=5))
    short = replace(short, estimate_seconds=900)
    long = replace(long, estimate_seconds=14400)
    result = selection.select_daily_focus([short, long], NOW)
    assert result.focus.id == 2


def test_estimate_only_breaks_ties_within_stale_high_not_across_bands():
    # A short stale high task still leads a long stale low task: band wins over duration.
    high_short = replace(make(1, priority=4, created=NOW - timedelta(days=5)), estimate_seconds=300)
    low_long = replace(make(2, priority=0, created=NOW - timedelta(days=5)), estimate_seconds=14400)
    result = selection.select_daily_focus([high_short, low_long], NOW)
    assert result.focus.id == 1
```

Add `from dataclasses import replace` to the imports at the top of `tests/test_selection.py`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_selection.py -k "longer_stale_high or estimate_only" -v`
Expected: FAIL (focus currently ignores `estimate_seconds`, so it picks by created/position order)

- [ ] **Step 3: Implement the avoidance ordering**

In `src/jolt/selection.py`, add after `priority_sort_key`:

```python
def avoidance_sort_key(task: Task, now: datetime) -> tuple:
    """Ranking for what to drag into the light: high band first, then stale-before-fresh,
    then the longer estimate (the bigger avoided thing leads), then the ordinary order."""
    stale_rank = 0 if is_stale(task, now) else 1
    return (
        _BAND_RANK[priority_band(task)],
        stale_rank,
        -(task.estimate_seconds or 0),
        task.deadline or date.max,
        task.position,
        task.created_at,
    )
```

Rewrite the body of `select_daily_focus` (currently lines 32-42) to select and rank rescues by this key:

```python
def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    if not pending:
        return DailyFocus(focus=None, rescues=[])
    by_avoidance = sorted(pending, key=lambda t: avoidance_sort_key(t, now))
    focus = by_avoidance[0]
    rescues = [t for t in by_avoidance if t.id != focus.id and is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)
```

Note: `avoidance_sort_key` places a fresh High task (band 0, stale-rank 1) above a stale Mid/Low task (band 1/2, stale-rank 0), which preserves `test_lead_falls_back_to_top_priority_when_no_important_is_stale`, and places a stale High above a fresh High, preserving `test_stale_important_leads_and_is_not_also_a_rescue`. `is_stale` and `STATUS_PENDING` are already imported/defined in this module.

- [ ] **Step 4: Run the selection suite**

Run: `uv run pytest tests/test_selection.py -v`
Expected: PASS (new tests plus all prior focus/rescue tests)

- [ ] **Step 5: Commit**

```bash
git add src/jolt/selection.py tests/test_selection.py
git commit -m "feat(selection): duration breaks ties for stale high-priority focus"
```

---

### Task 4: Nag and focus wording use band, duration, description, and project

`nag_stance` gains the third "poke" stance for the Mid band. `write_nag` and `write_focus` frame long vs short High tasks, quote the description, name the project, and give Mid tasks a gentle poke.

**Files:**
- Modify: `src/jolt/config.py` (add `LONG_DURATION_SECONDS`)
- Modify: `src/jolt/selection.py` (`nag_stance` -> three-way)
- Modify: `src/jolt/llm.py` (`write_nag` lines 360-397, `write_focus` lines 329-357; add a `_format_duration` helper)
- Test: `tests/test_selection.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `priority_band`, `BAND_MID` (Task 2), `Task.estimate_seconds`, `Task.details`, `Task.project_name`
- Produces: `nag_stance` returns `"start" | "poke" | "drop"`; `config.LONG_DURATION_SECONDS = 3600`; `llm._format_duration(seconds: int | None) -> str | None`

- [ ] **Step 1: Add the long-duration constant**

In `src/jolt/config.py`, after the `PRIORITY_MID_MIN` line added in Task 2, add:

```python
LONG_DURATION_SECONDS = 3600  # nags treat an estimate >= this as "long" (block time, not "just do it")
```

- [ ] **Step 2: Write the failing stance test**

In `tests/test_selection.py`, add:

```python
def test_nag_stance_poke_for_mid_band():
    assert selection.nag_stance(make(1, priority=2)) == "poke"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_selection.py -k "nag_stance" -v`
Expected: FAIL (Mid currently returns "drop")

- [ ] **Step 4: Make nag_stance three-way**

In `src/jolt/selection.py`, replace `nag_stance`:

```python
def nag_stance(task: Task) -> str:
    """How the nag leans by priority band: 'start' pushes a high task toward action,
    'poke' gently checks in on a mid task without drop pressure, 'drop' nudges a low-value
    task toward the exit."""
    band = priority_band(task)
    if band == BAND_HIGH:
        return "start"
    if band == BAND_MID:
        return "poke"
    return "drop"
```

- [ ] **Step 5: Run to verify the stance tests pass**

Run: `uv run pytest tests/test_selection.py -k "nag_stance" -v`
Expected: PASS

- [ ] **Step 6: Write the failing llm wording tests**

In `tests/test_llm.py`, add a builder and tests. Put the builder near `_important_task`:

```python
def _high_task(id=1, estimate_seconds=None, details="", project_name=""):
    return Task(
        id=id,
        text="file the tax return",
        priority=4,
        deadline=None,
        created_at=datetime(2026, 7, 3, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        estimate_seconds=estimate_seconds,
        details=details,
        project_name=project_name,
    )


def test_format_duration_reads_common_estimates():
    assert llm._format_duration(None) is None
    assert llm._format_duration(900) == "15 min"
    assert llm._format_duration(3600) == "1h"
    assert llm._format_duration(7200) == "2h"


def test_write_nag_long_high_task_pushes_a_first_step():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="go")]))
    llm.write_nag(_high_task(estimate_seconds=14400), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    user = str(client.messages.calls[0]["messages"]).lower()
    assert "first" in system  # break it into a first slice
    assert "4h" in user  # the estimate is surfaced


def test_write_nag_short_high_task_says_knock_it_out():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="go")]))
    llm.write_nag(_high_task(estimate_seconds=900), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    user = str(client.messages.calls[0]["messages"]).lower()
    assert "15 min" in user


def test_write_nag_mid_task_is_a_gentle_poke():
    mid = Task(
        id=1, text="reorganize the bookmarks", priority=2, deadline=None,
        created_at=datetime(2026, 7, 3, tzinfo=TZ), status=STATUS_PENDING,
        last_nagged_at=None, completed_at=None,
    )
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="go")]))
    llm.write_nag(mid, datetime(2026, 7, 12, 13, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "gentle" in system or "no pressure" in system or "check in" in system


def test_write_nag_includes_description_and_project():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="go")]))
    llm.write_nag(
        _high_task(details="the 2025 return on the tax-portal PDF", project_name="Backlog"),
        datetime(2026, 7, 12, 13, tzinfo=TZ), client,
    )
    payload = str(client.messages.calls[0]).lower()
    assert "tax-portal" in payload
    assert "backlog" in payload
```

- [ ] **Step 7: Run to verify failure**

Run: `uv run pytest tests/test_llm.py -k "format_duration or write_nag_long or write_nag_short or write_nag_mid or description_and_project" -v`
Expected: FAIL (`_format_duration` missing; `write_nag` ignores estimate/details/project and has no poke branch)

- [ ] **Step 8: Implement the duration formatter and rewrite write_nag**

In `src/jolt/llm.py`, add near the other helpers (after `_importance`):

```python
def _format_duration(seconds: int | None) -> str | None:
    if not seconds:
        return None
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{int(hours)}h" if hours == int(hours) else f"{hours:.1f}h"
```

Replace `write_nag` (lines 360-397) with:

```python
def write_nag(task: Task, now: datetime, client) -> str:
    age = (now - task.created_at).days
    stance = nag_stance(task)
    duration = _format_duration(task.estimate_seconds)
    is_long = bool(task.estimate_seconds and task.estimate_seconds >= config.LONG_DURATION_SECONDS)
    if stance == "start":
        if is_long:
            guidance = (
                "This task matters and is a big one. Do not say 'just do it'. Push toward a "
                "small first slice: ask what is blocking it and name a concrete 15-minute first "
                "step. The older it is and the later in the day, the blunter you get."
            )
        else:
            guidance = (
                "This task matters and is short. Push to knock it out right now: it is small "
                "enough to just finish. The later in the day, the blunter and more insistent."
            )
    elif stance == "poke":
        guidance = (
            "This task is mid-priority. Give it a gentle check-in with no pressure to drop it: "
            "ask if today is the day for it or offer a small next step. Stay light."
        )
    else:
        guidance = (
            "This task is low-stakes and has been sitting untouched. Do not chase it. Nudge "
            "toward dropping it with a zero-based question: if it were not already on the list, "
            "would they add it today? Still want it, or drop it? Stay easy to wave off."
        )
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines) about the task below. "
        "This message arrives on its own, with no other context on the user's screen, so "
        "name the specific task you are nudging about (quote it or refer to it clearly) "
        "rather than assuming the user knows which one you mean. "
        + guidance
        + " Never guilt-trip. Never use an em dash (the '-' character); use a comma, a colon, or a "
        "period instead. This rule has no exceptions."
    )
    lines = [
        f"Task: {task.text}.",
        f"Importance: {_importance(task)}.",
        f"Age: {age} days.",
        f"Current hour: {now.hour}.",
    ]
    if duration:
        lines.append(f"Estimated time: {duration}.")
    if task.details:
        lines.append(f"Notes: {task.details}.")
    if task.project_name:
        lines.append(f"Project: {task.project_name}.")
    user = " ".join(lines)
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_nag", response)
    return _text_of(response)
```

Note the em-dash rule text uses a plain hyphen inside the quotes on purpose: this is copy that ships in the prompt, and the constraint forbids the `—` character in generated copy.

- [ ] **Step 9: Run the nag tests**

Run: `uv run pytest tests/test_llm.py -k "write_nag or format_duration" -v`
Expected: PASS (new tests plus the existing `test_write_nag_*`)

- [ ] **Step 10: Write the failing focus-context test**

In `tests/test_llm.py`, add:

```python
def test_write_focus_includes_duration_details_and_project():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
    focus = DailyFocus(
        focus=_high_task(estimate_seconds=7200, details="on the tax-portal PDF", project_name="Backlog"),
        rescues=[],
    )
    llm.write_focus(focus, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    payload = str(client.messages.calls[0]).lower()
    assert "2h" in payload
    assert "tax-portal" in payload
    assert "backlog" in payload
```

- [ ] **Step 11: Run to verify failure**

Run: `uv run pytest tests/test_llm.py -k "write_focus_includes" -v`
Expected: FAIL (`write_focus` user line omits estimate/details/project)

- [ ] **Step 12: Extend write_focus's user line**

In `src/jolt/llm.py`, replace the `user = ...` line in `write_focus` (line 349) with a builder that appends the extra context for the focus task (keep the rescues summary as-is):

```python
    focus_bits = [f"Focus task: {focus.focus.text} ({age}d old, {_importance(focus.focus)})"]
    focus_duration = _format_duration(focus.focus.estimate_seconds)
    if focus_duration:
        focus_bits.append(f"est {focus_duration}")
    if focus.focus.project_name:
        focus_bits.append(f"in {focus.focus.project_name}")
    focus_line = ", ".join(focus_bits)
    details_line = f" Notes: {focus.focus.details}." if focus.focus.details else ""
    user = f"{focus_line}. Rescues: {rescues}.{details_line}"
```

- [ ] **Step 13: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 14: Commit**

```bash
git add src/jolt/config.py src/jolt/selection.py src/jolt/llm.py tests/test_selection.py tests/test_llm.py
git commit -m "feat(llm): band-aware nags with duration, notes, and project context"
```

---

### Task 5: Read open tasks across all projects

`VikunjaClient.list_open` enumerates every project (skipping archived and pseudo-projects), reads each project's list view, and merges the results, tagging each task with its project name. New projects are picked up automatically because enumeration is live. The write path (task creation) still targets the configured single project.

**Files:**
- Modify: `src/jolt/vikunja.py` (add `list_projects`, `_list_view_id(project_id)`, `_list_open_project`; rewrite `list_open`; change the view cache to per-project)
- Test: `tests/test_vikunja.py` (rewrite the list_open handlers to serve `/projects`)

**Interfaces:**
- Consumes: `vikunja_to_task(raw, project_name)` (Task 2)
- Produces:
  - `VikunjaClient.list_projects(self) -> list[tuple[int, str]]`
  - `VikunjaClient.list_open(self) -> list[Task]` now spans all projects; each task's `project_name` is set

- [ ] **Step 1: Write the failing multi-project tests**

In `tests/test_vikunja.py`, replace the `_views_handler_hit_counts` helper and the list_open tests that depend on it with project-aware versions. Add near the other helpers:

```python
def _multi_project_handler():
    """Two projects: 2 'Backlog' (2 open tasks) and 3 'Personal' (1 open task)."""
    projects = [
        {"id": 2, "title": "Backlog", "is_archived": False},
        {"id": 3, "title": "Personal", "is_archived": False},
        {"id": -1, "title": "Favorites", "is_archived": False},  # pseudo-project, must be skipped
        {"id": 9, "title": "Old", "is_archived": True},  # archived, must be skipped
    ]
    views = {2: 5, 3: 6, 9: 7}
    tasks = {
        2: [{"id": 1, "title": "a", "priority": 0, "position": 1, "project_id": 2},
            {"id": 2, "title": "b", "priority": 4, "position": 2, "project_id": 2}],
        3: [{"id": 3, "title": "c", "priority": 0, "position": 1, "project_id": 3}],
    }

    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=projects)
        for pid, vid in views.items():
            if path.endswith(f"/projects/{pid}/views"):
                return httpx.Response(200, json=[{"id": vid, "view_kind": "list"}])
            if f"/views/{vid}/tasks" in path:
                page = int(request.url.params.get("page", "1"))
                return httpx.Response(200, json=[] if page > 1 else tasks.get(pid, []))
        raise AssertionError(f"unexpected path {path}")

    return handler


def test_list_open_merges_tasks_across_projects_and_tags_project_name():
    c = _client(_multi_project_handler())
    tasks = c.list_open()
    assert sorted(t.id for t in tasks) == [1, 2, 3]
    by_id = {t.id: t for t in tasks}
    assert by_id[1].project_name == "Backlog"
    assert by_id[3].project_name == "Personal"


def test_list_open_skips_archived_and_pseudo_projects():
    c = _client(_multi_project_handler())
    # If a -1 or archived project were read, the handler would 404/AssertionError on its view.
    tasks = c.list_open()
    assert all(t.project_id in (2, 3) for t in tasks)


def test_list_open_tolerates_a_project_with_no_open_tasks():
    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=[{"id": 2, "title": "Backlog", "is_archived": False}])
        if path.endswith("/projects/2/views"):
            return httpx.Response(200, json=[{"id": 5, "view_kind": "list"}])
        return httpx.Response(200, json=[])  # empty first page

    assert _client(handler).list_open() == []
```

Delete the old `_views_handler_hit_counts` helper and the tests that used it: `test_list_open_maps_all_returned_tasks`, `test_list_open_discovers_view_once_and_caches_it`. Keep and adapt `test_list_open_paginates_past_a_server_enforced_page_cap`, `test_list_open_falls_back_to_first_view_when_no_list_kind`, `test_list_open_raises_when_views_empty`, `test_list_open_raises_on_404`, `test_server_error_raises`, and `test_transport_error_surfaces_as_vikunja_error` by giving each handler a `/api/v1/projects` branch returning a single project `[{"id": 3, "title": "P", "is_archived": False}]` (the `_client` write project id is 3), so `list_open` reaches the per-project read.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_vikunja.py -k "list_open or projects" -v`
Expected: FAIL (`list_open` calls the single-project view endpoint directly, never `/api/v1/projects`)

- [ ] **Step 3: Rewrite the read path in vikunja.py**

In `src/jolt/vikunja.py`, change the client's view cache. Replace `self._cached_list_view_id: int | None = None` (line 85) with:

```python
        self._list_view_ids: dict[int, int] = {}
```

Replace `_list_view_id` (lines 109-125) with a project-parameterized version:

```python
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
```

Add `list_projects` and rewrite `list_open` (replace lines 127-165). Keep the pagination loop, now in a per-project helper:

```python
    def list_projects(self) -> list[tuple[int, str]]:
        # Enumerate every real project live, so newly created projects are picked up with
        # no config change. Skip Vikunja's pseudo-projects (negative ids, e.g. Favorites)
        # and archived projects, whose tasks should not be nagged about.
        resp = self._request("GET", "/api/v1/projects")
        if resp is None:
            raise VikunjaError("list projects got 404")
        projects = resp.json() or []
        return [
            (p["id"], p.get("title", ""))
            for p in projects
            if p["id"] > 0 and not p.get("is_archived", False)
        ]

    def list_open(self) -> list[Task]:
        # Read open tasks from every project and merge them. The write path
        # (create_task) still targets the single configured project; only reading spans
        # all of them.
        tasks: list[Task] = []
        for project_id, project_name in self.list_projects():
            tasks.extend(self._list_open_project(project_id, project_name))
        return tasks

    def _list_open_project(self, project_id: int, project_name: str) -> list[Task]:
        # Vikunja enforces its own max_items_per_page regardless of our per_page, so a
        # short page does NOT mean the end. The only reliable stop is an EMPTY page. Cap at
        # _LIST_OPEN_MAX_PAGES so a misbehaving server can never loop forever.
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
```

- [ ] **Step 4: Run the vikunja suite**

Run: `uv run pytest tests/test_vikunja.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. `main.py` needs no change: `VikunjaClient(url, token, project_id)` still constructs fine, `project_id` remains the write project, and `list_open` now ignores it for reading.

- [ ] **Step 6: Commit**

```bash
git add src/jolt/vikunja.py tests/test_vikunja.py
git commit -m "feat(vikunja): read open tasks across all projects"
```

---

## Self-Review

**Spec coverage:**
- All-projects read (discovered live, archived/pseudo skipped) -> Task 5.
- Write to one default project unchanged -> Task 5 (create_task untouched), Global Constraints.
- Duration parsed from mdone sentinel, clean description -> Task 1, populated Task 2.
- Full priority in three bands -> Task 2 (`priority_band`, thresholds in config).
- Duration avoidance-buster in selection -> Task 3.
- Duration long/short wording, description and project into prompts, three-band stance -> Task 4.
- Non-goals (progress, labels, start/end dates, creation flow, webhooks) -> untouched by every task.

**Placeholder scan:** No TBD/TODO. Every code step shows full code; test steps show full test bodies. The one mechanical spillover ("if any test still uses a string priority, change it to the int") in Task 2 Step 14 names the exact files and the exact substitution.

**Type consistency:** `Task.priority: int` everywhere after Task 2. `priority_band(task)` used identically in `selection.py` and `llm.py`. `vikunja_to_task(raw, project_name="")` signature matches all call sites (`create_task`, `get_task`, `mark_done` pass no name; `_list_open_project` passes one). `_format_duration(int | None) -> str | None`, `avoidance_sort_key(task, now) -> tuple`, `list_projects() -> list[tuple[int, str]]` are consistent between definition and use. `nag_stance` returns `start|poke|drop`; `write_nag` handles all three.
