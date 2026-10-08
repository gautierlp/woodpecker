# Fewer, Sharper Nudges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 06:00 focus and the 09/13/19 nags with one 09:00 morning message, one 14:00 check-in, and a Sunday review, answered with one letter, with the vault deadlines of the week in the morning.

**Architecture:** Deterministic code does the new rhythm: a new `vault.py` reads the Obsidian vault read-only, `selection.py` picks the frog and the vault lines, `render.py` builds every fixed text, a new `replies.py` answers the one-letter replies with no Claude call, and the sidecar keeps two new tables (`frog_state`, `open_prompt`). Claude only writes the morning opening line plus the first step, and the reframe question.

**Tech Stack:** Python 3.12, python-telegram-bot, APScheduler (AsyncIOScheduler + CronTrigger), anthropic, httpx (Vikunja), sqlite3, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-08-fewer-sharper-nudges-design.md`

## Global Constraints

- Run tests with `uv run --extra dev pytest` (plain `uv run pytest` fails: pytest is a dev extra). Lint with `uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`.
- Every CronTrigger gets `timezone=config.TIMEZONE` explicitly (see the regression note in `main.build_scheduler`).
- Scheduled jobs stay coroutines on the AsyncIOScheduler (the sidecar SQLite connection only works on the loop thread).
- No em dashes anywhere: code, comments, prompts, bot text, docs. Every Claude prompt keeps the "Never use em dashes" line.
- The letter parser makes no Claude call. Vault text is never sent through Claude.
- `t` and the weekly `w` move dates only through `Store.reschedule_task`, never as an add (the bump counter keys off `task_id`).
- Woodpecker never writes to the vault. The mount is `:ro`.
- Exact tunables (config.py): `FOCUS_HOUR = 9`, `CHECKIN_HOUR = 14`, `WEEKLY_REVIEW_DAY = "sun"`, `WEEKLY_REVIEW_HOUR = 10`, `REFRAME_AFTER_BUMPS = 3`, `STALE_REVIEW_DAYS = 14`, `DROP_CONFIRM_MINUTES = 10`, `VAULT_SOON_DAYS = 7`, `STEP_ANSWER_MINUTES = 30`, `vault_path()` from `WOODPECKER_VAULT_PATH`, default `/vault`.
- Exact texts: frog legend `d done · o on it · t tomorrow · x drop`; reframe legend `s smaller step · n not mine to do · x drop`; vault block header `From the vault:`, footer `Tick these in Obsidian.`; vault line `• <text> (📅 2026-10-19, <note>)`, `• <text> (overdue, <note>)`, `• <text> (<note>)` for an undated `⏫`; unreadable vault `Vault: not readable (<reason>)`; no open prompt `Nothing to answer right now.`; drop confirm `Drop "<task>"? Send x again.`
- Log lines for the 2026-11-08 review: `reply <task_id> <action>` per letter reply, `frog <task_id>` per morning frog (INFO level, logger of the module that acts).
- Commits: conventional style, no Co-Authored-By trailer, no generated-by footer.

**User decisions (already made):**
- 2 messages a day at most: 09:00 morning, 14:00 check-in; weekly review Sunday 10:00. The 13:00/19:00 nags and the slow re-surface stop.
- The frog: always one, from all pending tasks, any priority: overdue first, then highest `bump_count`, then oldest.
- Letter replies, not buttons (Beeper does not show Telegram inline buttons, tested 2026-10-08).
- `x` drops only on a second `x` within 10 minutes; weekly drops need no second `x`.
- After the 3rd `t` on a frog, the next morning asks `s` / `n` / `x` instead.
- Vault: read-only bind mount `/home/agent/vault:/vault:ro`, reader copied from the dotclaude `/todo` skill, no shared package.
- Woodpecker never writes to the vault; vault tasks are never the frog.

**Plan decisions (not in the spec; Gautier can veto before execution):**
1. **The morning drops the "Today's priorities" list.** The spec goal is "each names one thing"; the old list with `render_matters` goes. The frog legend sits right after the frog, then the vault block comes last.
2. **The check-in also skips after `t`.** The spec says "only if the frog has no `d` or `x`". A 14:00 "still on for it today?" after "tomorrow" is the kind of nag the spec cuts.
3. **`n` drops the task, with the same second-letter confirm as `x`.** The spec gives `n` no action. A Vikunja delete cannot be undone, so it gets the confirm.
4. **A new `replies.py` holds the letter logic,** not `bot.py`. `bot.py` only routes. Tests go in `tests/test_replies.py` plus routing tests in `tests/test_bot.py`.
5. **The `s` answer window is 30 minutes (`STEP_ANSWER_MINUTES`).** After `s`, the next message becomes the new task title. After 30 minutes the bot stops waiting, so an unrelated later message cannot rename the task.
6. **An `s` rewrite resets the `t` count** for that task, so the next morning does not reframe the rewritten task again.
7. **The check-in, the weekly review, and the fallbacks are fixed text,** with no Claude call. Only the morning line and the reframe question use Claude.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/woodpecker/config.py` | modify | New tunables, `vault_path()`; old nag tunables removed in Task 7 |
| `src/woodpecker/vault.py` | create | Read open checkboxes from the vault (`VaultTask`, `read_vault`) |
| `src/woodpecker/selection.py` | modify | New `select_frog`; `select_stale_for_review`, `select_vault_reminders`, `select_vault_due`; old focus/resurface rules removed in Task 7 |
| `src/woodpecker/render.py` | modify | Legends, vault block, vault due, check-in, weekly review texts; `render_matters` removed in Task 7 |
| `src/woodpecker/sidecar.py` | modify | `frog_state` and `open_prompt` tables and their accessors |
| `src/woodpecker/vikunja.py` | modify | `update_task` accepts `text` (title rewrite for `s`) |
| `src/woodpecker/store.py` | modify | Wrappers for the new sidecar state, `rename_task` |
| `src/woodpecker/llm.py` | modify | New `write_focus`, new `write_reframe`; `write_nag` removed in Task 7 |
| `src/woodpecker/replies.py` | create | Deterministic answers to letter replies and weekly forms |
| `src/woodpecker/bot.py` | modify | Route letter replies to `replies.answer` before Claude |
| `src/woodpecker/scheduler.py` | modify | `send_morning`, `send_checkin`, `send_weekly_review` replace `send_daily_focus`, `send_nags` |
| `src/woodpecker/main.py` | modify | Three cron jobs, vault path wiring |
| `docker-compose.yml`, `.env.example`, `CLAUDE.md`, `README.md` | modify | Vault mount, new rhythm |

---

### Task 1: Config tunables and the vault reader

**Goal:** Add the new tunables to `config.py` and a read-only vault reader `vault.py` that returns the open vault checkboxes as `VaultTask`.

**Files:**
- Modify: `src/woodpecker/config.py`
- Create: `src/woodpecker/vault.py`
- Test: `tests/test_vault.py` (create), `tests/test_config.py`

**Acceptance Criteria:**
- [ ] `config.FOCUS_HOUR == 9`, `CHECKIN_HOUR == 14`, `WEEKLY_REVIEW_DAY == "sun"`, `WEEKLY_REVIEW_HOUR == 10`, `REFRAME_AFTER_BUMPS == 3`, `STALE_REVIEW_DAYS == 14`, `DROP_CONFIRM_MINUTES == 10`, `VAULT_SOON_DAYS == 7`, `STEP_ANSWER_MINUTES == 30`.
- [ ] `config.vault_path()` returns `/vault` by default and `WOODPECKER_VAULT_PATH` when set.
- [ ] `vault.read_vault(path)` returns open `- [ ] ` and `* [ ] ` boxes at any indent; skips ticked boxes, boxes inside code fences, and the folders `.obsidian`, `.git`, `.trash`, `40 Archive`, `50 Journal`, `90 Templates`, `log`, `plans`, `specs`.
- [ ] `📅 YYYY-MM-DD` becomes `VaultTask.due`; `⏫` sets `VaultTask.now`; both are stripped from `text`; `note` is the file name without `.md`.
- [ ] A non-UTF-8 note is skipped with a WARNING log; the other notes still come back.
- [ ] A missing vault folder raises `vault.VaultUnreadable` with the reason `not a folder`.

**Verify:** `uv run --extra dev pytest tests/test_vault.py tests/test_config.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault.py`:

```python
from datetime import date

import pytest

from woodpecker import vault


def _note(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    return path


def test_reads_open_boxes_with_dash_star_and_indent(tmp_path):
    _note(tmp_path, "10 Projects/a/_project.md", "- [ ] one\n  - [ ] two\n* [ ] three\n")
    texts = [t.text for t in vault.read_vault(tmp_path)]
    assert texts == ["one", "two", "three"]


def test_skips_ticked_boxes(tmp_path):
    _note(tmp_path, "n.md", "- [x] done\n- [X] done too\n- [ ] open\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["open"]


def test_skips_boxes_inside_code_fences(tmp_path):
    _note(tmp_path, "n.md", "```\n- [ ] fenced\n```\n- [ ] real\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["real"]


@pytest.mark.parametrize(
    "folder",
    [".obsidian", ".git", ".trash", "40 Archive", "50 Journal", "90 Templates", "log", "plans", "specs"],
)
def test_skips_record_folders(tmp_path, folder):
    _note(tmp_path, f"10 Projects/x/{folder}/n.md", "- [ ] hidden\n")
    _note(tmp_path, "keep.md", "- [ ] shown\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["shown"]


def test_reads_the_due_date_and_strips_it(tmp_path):
    _note(tmp_path, "Taxes.md", "- [ ] file the return 📅 2026-10-19\n")
    [task] = vault.read_vault(tmp_path)
    assert task == vault.VaultTask(text="file the return", note="Taxes", due=date(2026, 10, 19), now=False)


def test_reads_the_this_week_mark_and_strips_it(tmp_path):
    _note(tmp_path, "Car.md", "- [ ] book the service ⏫\n")
    [task] = vault.read_vault(tmp_path)
    assert task.text == "book the service"
    assert task.now is True
    assert task.due is None


def test_an_impossible_date_is_no_date(tmp_path):
    _note(tmp_path, "n.md", "- [ ] odd 📅 2026-13-40\n")
    [task] = vault.read_vault(tmp_path)
    assert task.due is None


def test_a_non_utf8_note_is_skipped_and_logged(tmp_path, caplog):
    _note(tmp_path, "bad.md", b"- [ ] caf\xe9\n")
    _note(tmp_path, "good.md", "- [ ] fine\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["fine"]
    assert "bad.md" in caplog.text


def test_a_missing_folder_is_unreadable(tmp_path):
    with pytest.raises(vault.VaultUnreadable, match="not a folder"):
        vault.read_vault(tmp_path / "nope")
```

Append to `tests/test_config.py`:

```python
def test_nudge_rhythm_tunables():
    assert config.FOCUS_HOUR == 9
    assert config.CHECKIN_HOUR == 14
    assert config.WEEKLY_REVIEW_DAY == "sun"
    assert config.WEEKLY_REVIEW_HOUR == 10
    assert config.REFRAME_AFTER_BUMPS == 3
    assert config.STALE_REVIEW_DAYS == 14
    assert config.DROP_CONFIRM_MINUTES == 10
    assert config.VAULT_SOON_DAYS == 7
    assert config.STEP_ANSWER_MINUTES == 30


def test_vault_path_defaults_to_the_container_mount(monkeypatch):
    monkeypatch.delenv("WOODPECKER_VAULT_PATH", raising=False)
    monkeypatch.delenv("JOLT_VAULT_PATH", raising=False)
    assert config.vault_path() == "/vault"


def test_vault_path_reads_the_env(monkeypatch):
    monkeypatch.setenv("WOODPECKER_VAULT_PATH", "/tmp/v")
    assert config.vault_path() == "/tmp/v"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_vault.py tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'vault'` and `AttributeError: ... 'FOCUS_HOUR'`.

- [ ] **Step 3: Write the implementation**

In `src/woodpecker/config.py`, after `DAILY_FOCUS_HOUR = 6` add:

```python
# The nudge rhythm (spec 2026-10-08): one morning message, one check-in, one weekly review.
FOCUS_HOUR = 9
CHECKIN_HOUR = 14
WEEKLY_REVIEW_DAY = "sun"  # APScheduler day_of_week
WEEKLY_REVIEW_HOUR = 10
REFRAME_AFTER_BUMPS = 3  # the 3rd "t" on a frog turns the next morning into the reframe question
STALE_REVIEW_DAYS = 14  # pending this long or more -> listed in the weekly review
DROP_CONFIRM_MINUTES = 10  # a second "x" within this window drops the task
VAULT_SOON_DAYS = 7  # the morning lists vault tasks due within this many days
STEP_ANSWER_MINUTES = 30  # after "s", the next message within this window becomes the new title
```

and after `log_file()` add:

```python
def vault_path() -> str:
    # The Obsidian vault, bind-mounted read-only (see docker-compose.yml).
    return _setting("VAULT_PATH", "/vault")
```

Create `src/woodpecker/vault.py`:

```python
"""Read the open tasks from the Obsidian vault: each open checkbox outside the record
folders. Read only. Copied from the dotclaude /todo skill (skills/todo/todo_vault.py):
a shared package for two personal repos is more than the job needs."""

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

BOX = re.compile(r"^\s*[-*] \[ \] (.*)$")
DUE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
NOW = "⏫"
FENCE = "```"
# Dated records, templates and build plans hold checkboxes that are not tasks.
SKIP_DIRS = {
    ".obsidian",
    ".git",
    ".trash",
    "40 Archive",
    "50 Journal",
    "90 Templates",
    "log",
    "plans",
    "specs",
}


class VaultUnreadable(Exception):
    """The vault folder itself cannot be read (missing mount, not a folder)."""


@dataclass(frozen=True)
class VaultTask:
    text: str
    note: str  # the note's file name without .md
    due: date | None
    now: bool  # marked ⏫: for this week


def _due(body: str) -> date | None:
    match = DUE.search(body)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _task(path: Path, body: str) -> VaultTask:
    text = DUE.sub("", body).replace(NOW, "")
    return VaultTask(text=" ".join(text.split()), note=path.stem, due=_due(body), now=NOW in body)


def read_file(path: Path) -> list[VaultTask]:
    """The open checkboxes of one note, in line order. A box in a code fence is not a task."""
    tasks, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(FENCE):
            fenced = not fenced
            continue
        match = None if fenced else BOX.match(line)
        if match:
            tasks.append(_task(path, match.group(1)))
    return tasks


def _notes(root: Path):
    for path in sorted(root.rglob("*.md")):
        if not SKIP_DIRS.intersection(path.relative_to(root).parts[:-1]):
            yield path


def read_vault(root: Path) -> list[VaultTask]:
    """All open tasks. A note that cannot be read is logged and skipped; a vault folder
    that cannot be read raises VaultUnreadable."""
    if not root.is_dir():
        raise VaultUnreadable("not a folder")
    tasks: list[VaultTask] = []
    for path in _notes(root):
        try:
            tasks += read_file(path)
        except (OSError, ValueError) as exc:
            logger.warning("Vault: cannot read %s (%s)", path.relative_to(root), type(exc).__name__)
    return tasks
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_vault.py tests/test_config.py -v`
Expected: PASS. Then `uv run --extra dev pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/config.py src/woodpecker/vault.py tests/test_vault.py tests/test_config.py
git commit -m "feat(vault): read open vault checkboxes, add the nudge rhythm tunables"
```

---

### Task 2: Selection rules for the frog, the review, and the vault

**Goal:** Rewrite `select_frog` to pick from all pending tasks, and add `select_stale_for_review`, `select_vault_reminders`, and `select_vault_due`.

**Files:**
- Modify: `src/woodpecker/selection.py`
- Test: `tests/test_selection.py`, `tests/test_scheduler.py` (only tests that pin the old frog rule)

**Acceptance Criteria:**
- [ ] `select_frog` returns a task when only priority 0 undated tasks exist.
- [ ] Order: an overdue task beats a task with a higher `bump_count`; a higher `bump_count` beats an older task; the oldest wins a tie; empty or no pending → `None`.
- [ ] `select_stale_for_review(tasks, now, limit=5)` returns pending tasks created 14 days or more ago, oldest first, at most 5.
- [ ] `select_vault_reminders(tasks, today)` keeps overdue, due today, due within 7 days (day 7 in, day 8 out), and `⏫`; drops undated tasks with no `⏫`; sorted by date with undated `⏫` last.
- [ ] `select_vault_due(tasks, today)` keeps only tasks due today or before, sorted by date.

**Verify:** `uv run --extra dev pytest tests/test_selection.py -v` → all pass; `uv run --extra dev pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

In `tests/test_selection.py`, delete every test whose name starts with `test_select_frog` (they pin the old priority-and-due-date rule). Add `from woodpecker.vault import VaultTask` to the imports, then append:

```python
def test_frog_from_priority_zero_undated_tasks():
    task = make(1, priority=0, deadline=None)
    assert selection.select_frog([task], NOW) is task


def test_frog_overdue_beats_bumped():
    overdue = make(1, deadline=NOW.date() - timedelta(days=1))
    bumped = replace(make(2), bump_count=5)
    assert selection.select_frog([bumped, overdue], NOW).id == 1


def test_frog_bumped_beats_old():
    old = make(1, created=NOW - timedelta(days=60))
    bumped = replace(make(2, created=NOW - timedelta(days=1)), bump_count=1)
    assert selection.select_frog([old, bumped], NOW).id == 2


def test_frog_oldest_wins_a_tie():
    newer = make(1, created=NOW - timedelta(days=2))
    older = make(2, created=NOW - timedelta(days=9))
    assert selection.select_frog([newer, older], NOW).id == 2


def test_frog_due_today_is_not_overdue():
    today = make(1, deadline=NOW.date(), created=NOW - timedelta(days=1))
    bumped = replace(make(2, created=NOW - timedelta(days=1)), bump_count=2)
    assert selection.select_frog([today, bumped], NOW).id == 2


def test_frog_empty_backlog_is_none():
    assert selection.select_frog([], NOW) is None
    assert selection.select_frog([make(1, status=STATUS_DONE)], NOW) is None


def test_stale_review_keeps_14_days_and_more_oldest_first():
    at_14 = make(1, created=NOW - timedelta(days=14))
    at_13 = make(2, created=NOW - timedelta(days=13))
    at_40 = make(3, created=NOW - timedelta(days=40))
    got = selection.select_stale_for_review([at_14, at_13, at_40], NOW)
    assert [t.id for t in got] == [3, 1]


def test_stale_review_limit():
    tasks = [make(i, created=NOW - timedelta(days=20 + i)) for i in range(1, 8)]
    got = selection.select_stale_for_review(tasks, NOW, limit=5)
    assert [t.id for t in got] == [7, 6, 5, 4, 3]


def _vt(text, due=None, now=False, note="n"):
    return VaultTask(text=text, note=note, due=due, now=now)


def test_vault_reminders_windows():
    today = date(2026, 10, 8)
    tasks = [
        _vt("overdue", due=today - timedelta(days=2)),
        _vt("today", due=today),
        _vt("day7", due=today + timedelta(days=7)),
        _vt("day8", due=today + timedelta(days=8)),
        _vt("thisweek", now=True),
        _vt("undated"),
    ]
    got = selection.select_vault_reminders(tasks, today)
    assert [t.text for t in got] == ["overdue", "today", "day7", "thisweek"]


def test_vault_due_is_today_and_overdue_only():
    today = date(2026, 10, 8)
    tasks = [
        _vt("tomorrow", due=today + timedelta(days=1)),
        _vt("today", due=today),
        _vt("overdue", due=today - timedelta(days=3)),
        _vt("thisweek", now=True),
    ]
    got = selection.select_vault_due(tasks, today)
    assert [t.text for t in got] == ["overdue", "today"]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_selection.py -v`
Expected: FAIL: `test_frog_from_priority_zero_undated_tasks` returns `None`; the new functions raise `AttributeError`.

- [ ] **Step 3: Write the implementation**

In `src/woodpecker/selection.py`, add `from .vault import VaultTask` to the imports, and replace `select_frog` with:

```python
def select_frog(tasks: list[Task], now: datetime) -> Task | None:
    """The one thing of the day, from all pending tasks at any priority: overdue first,
    then the highest bump_count (the avoidance signal), then the oldest. None only when
    nothing is pending."""
    today = now.date()
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    if not pending:
        return None

    def key(task: Task) -> tuple:
        overdue = task.deadline is not None and task.deadline < today
        return (0 if overdue else 1, -task.bump_count, task.created_at)

    return min(pending, key=key)


def select_stale_for_review(tasks: list[Task], now: datetime, limit: int = 5) -> list[Task]:
    """The tasks for the weekly review: pending STALE_REVIEW_DAYS or more, oldest first."""
    stale = [t for t in tasks if is_stale(t, now, config.STALE_REVIEW_DAYS)]
    return sorted(stale, key=lambda t: t.created_at)[:limit]


def select_vault_reminders(tasks: list[VaultTask], today: date) -> list[VaultTask]:
    """The vault lines of the morning: overdue, due within VAULT_SOON_DAYS, or marked ⏫.
    Sorted by date, so overdue comes first and an undated ⏫ comes last."""
    soon = today + timedelta(days=config.VAULT_SOON_DAYS)
    picked = [t for t in tasks if (t.due is not None and t.due <= soon) or t.now]
    return sorted(picked, key=lambda t: (t.due or date.max, t.note, t.text))


def select_vault_due(tasks: list[VaultTask], today: date) -> list[VaultTask]:
    """The vault lines of the check-in: due today or overdue."""
    due = [t for t in tasks if t.due is not None and t.due <= today]
    return sorted(due, key=lambda t: (t.due, t.note, t.text))
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_selection.py -v` → PASS.
Then run `uv run --extra dev pytest -q`. If a test in `tests/test_scheduler.py` fails because it expected no frog for a priority 0 or undated task, change only its expectation to the new rule (a frog now always exists when a task is pending). Do not change `send_daily_focus` itself: Task 7 deletes it.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/selection.py tests/test_selection.py tests/test_scheduler.py
git commit -m "feat(selection): pick the frog from every pending task, add review and vault rules"
```

---

### Task 3: Render the fixed texts

**Goal:** Add every deterministic text of the new rhythm to `render.py`: the two legends, the vault block, the vault due block, the unreadable line, the check-in, and the weekly review.

**Files:**
- Modify: `src/woodpecker/render.py`
- Test: `tests/test_render.py`

**Acceptance Criteria:**
- [ ] `render.FROG_LEGEND == "d done · o on it · t tomorrow · x drop"` and `render.REFRAME_LEGEND == "s smaller step · n not mine to do · x drop"`.
- [ ] `render_vault_block` gives exactly: `From the vault:`, one line per task (`• file the return (📅 2026-10-19, Taxes)`, `• call the bank (overdue, Money)`, `• book the service (Car)`), then `Tick these in Obsidian.`; an empty list gives `""`.
- [ ] `render_vault_due` gives the same lines under `Due in the vault today:`, then `Tick these in Obsidian.`; empty gives `""`.
- [ ] `render_vault_unreadable("not a folder") == "Vault: not readable (not a folder)"`.
- [ ] `render_checkin` gives `Still on for "<task>" today?` (or `How is "<task>" going?` after `o`), a newline, then the legend it gets.
- [ ] `render_weekly_review` gives a header, numbered lines `1. <task> (<n>d)`, a blank line, and the reply hint.

**Verify:** `uv run --extra dev pytest tests/test_render.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py` (add `from datetime import date, timedelta` and `from woodpecker.vault import VaultTask` to the imports if missing):

```python
TODAY = date(2026, 10, 8)


def test_legends():
    assert render.FROG_LEGEND == "d done · o on it · t tomorrow · x drop"
    assert render.REFRAME_LEGEND == "s smaller step · n not mine to do · x drop"


def test_vault_block_exact_text():
    tasks = [
        VaultTask(text="call the bank", note="Money", due=date(2026, 10, 6), now=False),
        VaultTask(text="file the return", note="Taxes", due=date(2026, 10, 19), now=True),
        VaultTask(text="book the service", note="Car", due=None, now=True),
    ]
    assert render.render_vault_block(tasks, TODAY) == (
        "From the vault:\n"
        "• call the bank (overdue, Money)\n"
        "• file the return (📅 2026-10-19, Taxes)\n"
        "• book the service (Car)\n"
        "Tick these in Obsidian."
    )


def test_vault_block_due_today_shows_the_date():
    tasks = [VaultTask(text="pay rent", note="Home", due=TODAY, now=False)]
    assert "• pay rent (📅 2026-10-08, Home)" in render.render_vault_block(tasks, TODAY)


def test_vault_block_empty_is_empty():
    assert render.render_vault_block([], TODAY) == ""
    assert render.render_vault_due([], TODAY) == ""


def test_vault_due_exact_text():
    tasks = [VaultTask(text="pay rent", note="Home", due=TODAY, now=False)]
    assert render.render_vault_due(tasks, TODAY) == (
        "Due in the vault today:\n• pay rent (📅 2026-10-08, Home)\nTick these in Obsidian."
    )


def test_vault_unreadable():
    assert render.render_vault_unreadable("not a folder") == "Vault: not readable (not a folder)"


def test_checkin_text():
    task = _task(1)
    assert render.render_checkin(task, started=False, legend=render.FROG_LEGEND) == (
        'Still on for "task 1" today?\nd done · o on it · t tomorrow · x drop'
    )
    assert render.render_checkin(task, started=True, legend=render.FROG_LEGEND).startswith(
        'How is "task 1" going?\n'
    )


def test_weekly_review_text():
    old = _task(1, created_at=NOW - timedelta(days=30))
    older = _task(2, created_at=NOW - timedelta(days=45))
    assert render.render_weekly_review([older, old], NOW) == (
        "Weekly review: these have waited 14 days or more.\n"
        "1. task 2 (45d)\n"
        "2. task 1 (30d)\n"
        "\n"
        "Reply like x 1 3 to drop 1 and 3, w 2 to move 2 to next week. The rest stay."
    )
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_render.py -v`
Expected: FAIL with `AttributeError: module 'woodpecker.render' has no attribute 'FROG_LEGEND'`.

- [ ] **Step 3: Write the implementation**

Append to `src/woodpecker/render.py` (add `from .vault import VaultTask` to the imports):

```python
# One-letter replies: Beeper shows no Telegram inline buttons, so one letter is the
# nearest thing to one tap. replies.py parses them.
FROG_LEGEND = "d done · o on it · t tomorrow · x drop"
REFRAME_LEGEND = "s smaller step · n not mine to do · x drop"
_VAULT_FOOTER = "Tick these in Obsidian."


def _vault_line(task: VaultTask, today: date) -> str:
    if task.due is None:
        when = ""
    elif task.due < today:
        when = "overdue, "
    else:
        when = f"📅 {task.due.isoformat()}, "
    return f"• {task.text} ({when}{task.note})"


def _vault_lines(header: str, tasks: list[VaultTask], today: date) -> str:
    if not tasks:
        return ""
    lines = [header] + [_vault_line(t, today) for t in tasks] + [_VAULT_FOOTER]
    return "\n".join(lines)


def render_vault_block(tasks: list[VaultTask], today: date) -> str:
    """The morning's vault block. Plain lines with no number: Woodpecker cannot tick a
    vault box, so nothing here is answerable in the chat."""
    return _vault_lines("From the vault:", tasks, today)


def render_vault_due(tasks: list[VaultTask], today: date) -> str:
    return _vault_lines("Due in the vault today:", tasks, today)


def render_vault_unreadable(reason: str) -> str:
    return f"Vault: not readable ({reason})"


def render_checkin(task: Task, started: bool, legend: str) -> str:
    line = f'How is "{task.text}" going?' if started else f'Still on for "{task.text}" today?'
    return f"{line}\n{legend}"


def render_weekly_review(tasks: list[Task], now: datetime) -> str:
    lines = [f"Weekly review: these have waited {config.STALE_REVIEW_DAYS} days or more."]
    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. {task.text} ({(now - task.created_at).days}d)")
    lines.append("")
    lines.append("Reply like x 1 3 to drop 1 and 3, w 2 to move 2 to next week. The rest stay.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_render.py -v` → PASS. Then `uv run --extra dev pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/render.py tests/test_render.py
git commit -m "feat(render): legends, vault block, check-in and weekly review texts"
```

---

### Task 4: Sidecar state for the frog and the open prompt

**Goal:** Add the `frog_state` and `open_prompt` sidecar tables with their accessors, expose them on `Store`, and let `Store.rename_task` rewrite a task title through Vikunja.

**Files:**
- Modify: `src/woodpecker/sidecar.py`, `src/woodpecker/store.py`, `src/woodpecker/vikunja.py:278-294`
- Test: `tests/test_sidecar.py`, `tests/test_store.py`, `tests/test_vikunja.py`

**Acceptance Criteria:**
- [ ] `init_db` creates `frog_state(day TEXT PK, task_id, started_at, answered)` and `open_prompt(id=1 only, kind, task_ids, sent_at, pending_drop_at)`; it runs twice with no error on an existing database.
- [ ] `record_frog` / `frog_of_day` round-trip; `mark_frog_started` sets `started_at`; `mark_frog_answered` sets `answered`.
- [ ] `tomorrow_count(task_id)` counts the days that task got `t`; `clear_tomorrows(task_id)` sets it back to 0.
- [ ] `set_open_prompt` replaces the one open prompt; `get_open_prompt` returns `OpenPrompt` or `None`; `set_pending_drop` and `clear_open_prompt` work.
- [ ] `VikunjaClient.update_task(5, text="new")` posts `title == "new"` and keeps the due date.
- [ ] `Store` exposes each accessor above plus `rename_task(task_id, text)`.

**Verify:** `uv run --extra dev pytest tests/test_sidecar.py tests/test_store.py tests/test_vikunja.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add `timedelta` to the existing `from datetime import ...` line of `tests/test_sidecar.py`, then append:

```python
DAY = date(2026, 10, 8)
AT = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)


def test_init_db_twice_is_safe(tmp_conn):
    sidecar.init_db(tmp_conn)


def test_frog_round_trip(tmp_conn):
    sidecar.record_frog(tmp_conn, DAY, 7)
    row = sidecar.frog_of_day(tmp_conn, DAY)
    assert row == sidecar.FrogDay(day=DAY, task_id=7, started_at=None, answered=None)
    sidecar.mark_frog_started(tmp_conn, DAY, AT)
    sidecar.mark_frog_answered(tmp_conn, DAY, "d")
    row = sidecar.frog_of_day(tmp_conn, DAY)
    assert row.started_at == AT
    assert row.answered == "d"


def test_frog_of_an_unknown_day_is_none(tmp_conn):
    assert sidecar.frog_of_day(tmp_conn, DAY) is None


def test_tomorrow_count_counts_days_with_t(tmp_conn):
    for offset in range(3):
        day = DAY + timedelta(days=offset)
        sidecar.record_frog(tmp_conn, day, 7)
        sidecar.mark_frog_answered(tmp_conn, day, "t")
    sidecar.record_frog(tmp_conn, DAY + timedelta(days=3), 7)
    sidecar.mark_frog_answered(tmp_conn, DAY + timedelta(days=3), "d")
    assert sidecar.tomorrow_count(tmp_conn, 7) == 3
    assert sidecar.tomorrow_count(tmp_conn, 8) == 0
    sidecar.clear_tomorrows(tmp_conn, 7)
    assert sidecar.tomorrow_count(tmp_conn, 7) == 0


def test_open_prompt_round_trip_and_replace(tmp_conn):
    assert sidecar.get_open_prompt(tmp_conn) is None
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    sidecar.set_open_prompt(tmp_conn, "weekly", [3, 4], AT)
    prompt = sidecar.get_open_prompt(tmp_conn)
    assert prompt == sidecar.OpenPrompt(kind="weekly", task_ids=[3, 4], sent_at=AT, pending_drop_at=None)


def test_open_prompt_pending_drop_and_clear(tmp_conn):
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    sidecar.set_pending_drop(tmp_conn, AT)
    assert sidecar.get_open_prompt(tmp_conn).pending_drop_at == AT
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    assert sidecar.get_open_prompt(tmp_conn).pending_drop_at is None  # a new prompt resets it
    sidecar.clear_open_prompt(tmp_conn)
    assert sidecar.get_open_prompt(tmp_conn) is None
```

Append to `tests/test_vikunja.py`:

```python
def test_update_task_rewrites_the_title_and_keeps_the_due_date():
    seen = {}
    existing = {
        "id": 5,
        "title": "Book the doctor",
        "priority": 0,
        "due_date": "2026-07-29T21:59:59Z",
        "done": False,
    }
    task = _client(_update_handler(seen, existing)).update_task(5, text="Open the Doctolib page")
    assert seen["post_body"]["title"] == "Open the Doctolib page"
    assert seen["post_body"]["due_date"] == "2026-07-29T21:59:59Z"
    assert task.text == "Open the Doctolib page"
```

In `tests/test_store.py`, read the file's existing fake Vikunja. If it has no `update_task`, add this method to it (add `from dataclasses import replace` to the imports):

```python
    def update_task(self, task_id, deadline=None, priority=None, text=None):
        task = self._open.get(task_id)
        if task is None:
            return None
        changes = {}
        if deadline is not None:
            changes["deadline"] = deadline
        if text is not None:
            changes["text"] = text
        task = replace(task, **changes)
        self._open[task_id] = task
        return task
```

Then append (use the file's existing helper that builds a `Store` over the fake and an in-memory sidecar, and its task builder; rename `fresh` / `_task` below to match them):

```python
def test_store_rename_task():
    store = fresh([_task(1)])
    task = store.rename_task(1, "smaller step")
    assert task.text == "smaller step"
    assert store.get_task(1).text == "smaller step"


def test_store_frog_and_prompt_state():
    from datetime import date, datetime, timezone

    store = fresh([_task(1)])
    day = date(2026, 10, 8)
    at = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)
    store.record_frog(day, 1)
    store.mark_frog_answered(day, "t")
    assert store.tomorrow_count(1) == 1
    assert store.frog_of_day(day).answered == "t"
    store.set_open_prompt("frog", [1], at)
    assert store.get_open_prompt().task_ids == [1]
    store.clear_open_prompt()
    assert store.get_open_prompt() is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_sidecar.py tests/test_store.py tests/test_vikunja.py -v`
Expected: FAIL with `AttributeError: module 'woodpecker.sidecar' has no attribute 'record_frog'` and `TypeError: update_task() got an unexpected keyword argument 'text'`.

- [ ] **Step 3: Write the implementation**

In `src/woodpecker/sidecar.py`, add `from dataclasses import dataclass` to the imports, then add after `_BUMP_SCHEMA`:

```python
# One row per day: the frog the morning named, and what happened to it. The check-in
# reads it, and the count of days with "t" drives the reframe question.
_FROG_SCHEMA = """
CREATE TABLE IF NOT EXISTS frog_state (
    day TEXT PRIMARY KEY,
    task_id INTEGER NOT NULL,
    started_at TEXT,
    answered TEXT
);
"""

# The one prompt a letter reply answers: the last frog, reframe, weekly or step prompt
# the bot sent. A single row (id = 1); a new prompt replaces it.
_PROMPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS open_prompt (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kind TEXT NOT NULL,
    task_ids TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    pending_drop_at TEXT
);
"""


@dataclass(frozen=True)
class FrogDay:
    day: date
    task_id: int
    started_at: datetime | None
    answered: str | None  # the closing letter: d, t, x or n


@dataclass(frozen=True)
class OpenPrompt:
    kind: str  # replies.FROG, REFRAME, WEEKLY or STEP
    task_ids: list[int]
    sent_at: datetime
    pending_drop_at: datetime | None  # set by a first "x" or "n"


def _when(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None
```

In `init_db`, add `conn.execute(_FROG_SCHEMA)` and `conn.execute(_PROMPT_SCHEMA)` before `conn.commit()`. Then append:

```python
def record_frog(conn: sqlite3.Connection, day: date, task_id: int) -> None:
    conn.execute(
        "INSERT INTO frog_state (day, task_id) VALUES (?, ?) "
        "ON CONFLICT(day) DO UPDATE SET task_id = excluded.task_id",
        (day.isoformat(), task_id),
    )
    conn.commit()


def frog_of_day(conn: sqlite3.Connection, day: date) -> FrogDay | None:
    row = conn.execute("SELECT * FROM frog_state WHERE day = ?", (day.isoformat(),)).fetchone()
    if row is None:
        return None
    return FrogDay(
        day=date.fromisoformat(row["day"]),
        task_id=row["task_id"],
        started_at=_when(row["started_at"]),
        answered=row["answered"],
    )


def mark_frog_started(conn: sqlite3.Connection, day: date, when: datetime) -> None:
    conn.execute(
        "UPDATE frog_state SET started_at = ? WHERE day = ?", (when.isoformat(), day.isoformat())
    )
    conn.commit()


def mark_frog_answered(conn: sqlite3.Connection, day: date, letter: str) -> None:
    conn.execute("UPDATE frog_state SET answered = ? WHERE day = ?", (letter, day.isoformat()))
    conn.commit()


def tomorrow_count(conn: sqlite3.Connection, task_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM frog_state WHERE task_id = ? AND answered = 't'", (task_id,)
    ).fetchone()
    return row["n"]


def clear_tomorrows(conn: sqlite3.Connection, task_id: int) -> None:
    # A rewritten task starts its "t" count again, so it is not reframed the next morning.
    conn.execute(
        "UPDATE frog_state SET answered = NULL WHERE task_id = ? AND answered = 't'", (task_id,)
    )
    conn.commit()


def set_open_prompt(
    conn: sqlite3.Connection, kind: str, task_ids: list[int], when: datetime
) -> None:
    conn.execute(
        "INSERT INTO open_prompt (id, kind, task_ids, sent_at, pending_drop_at) "
        "VALUES (1, ?, ?, ?, NULL) ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, "
        "task_ids = excluded.task_ids, sent_at = excluded.sent_at, pending_drop_at = NULL",
        (kind, ",".join(str(i) for i in task_ids), when.isoformat()),
    )
    conn.commit()


def get_open_prompt(conn: sqlite3.Connection) -> OpenPrompt | None:
    row = conn.execute("SELECT * FROM open_prompt WHERE id = 1").fetchone()
    if row is None:
        return None
    raw = row["task_ids"]
    return OpenPrompt(
        kind=row["kind"],
        task_ids=[int(part) for part in raw.split(",")] if raw else [],
        sent_at=datetime.fromisoformat(row["sent_at"]),
        pending_drop_at=_when(row["pending_drop_at"]),
    )


def set_pending_drop(conn: sqlite3.Connection, when: datetime | None) -> None:
    conn.execute(
        "UPDATE open_prompt SET pending_drop_at = ? WHERE id = 1",
        (when.isoformat() if when else None,),
    )
    conn.commit()


def clear_open_prompt(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM open_prompt")
    conn.commit()
```

In `src/woodpecker/vikunja.py`, change `update_task` to:

```python
    def update_task(
        self,
        task_id: int,
        deadline: date | None = None,
        priority: str | None = None,
        text: str | None = None,
    ) -> Task | None:
        """Change an existing task's due date, priority and/or title in place. An argument
        left at None means "leave that field as it is", so this never clears a date the user
        did not ask to clear. Read-modify-write like mark_done: Vikunja's POST replaces the
        object, so the stored task is round-tripped with only the named fields overwritten."""
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
```

In `src/woodpecker/store.py`, append to `class Store`:

```python
    def rename_task(self, task_id: int, text: str) -> Task | None:
        """Rewrite the title in place (same task_id, so the bump history stays)."""
        task = self._vk.get_task(task_id)
        if task is None or task.status == STATUS_DONE:
            return None
        return self._vk.update_task(task_id, text=text)

    def record_frog(self, day: date, task_id: int) -> None:
        sidecar.record_frog(self._conn, day, task_id)

    def frog_of_day(self, day: date) -> sidecar.FrogDay | None:
        return sidecar.frog_of_day(self._conn, day)

    def mark_frog_started(self, day: date, when: datetime) -> None:
        sidecar.mark_frog_started(self._conn, day, when)

    def mark_frog_answered(self, day: date, letter: str) -> None:
        sidecar.mark_frog_answered(self._conn, day, letter)

    def tomorrow_count(self, task_id: int) -> int:
        return sidecar.tomorrow_count(self._conn, task_id)

    def clear_tomorrows(self, task_id: int) -> None:
        sidecar.clear_tomorrows(self._conn, task_id)

    def set_open_prompt(self, kind: str, task_ids: list[int], when: datetime) -> None:
        sidecar.set_open_prompt(self._conn, kind, task_ids, when)

    def get_open_prompt(self) -> sidecar.OpenPrompt | None:
        return sidecar.get_open_prompt(self._conn)

    def set_pending_drop(self, when: datetime | None) -> None:
        sidecar.set_pending_drop(self._conn, when)

    def clear_open_prompt(self) -> None:
        sidecar.clear_open_prompt(self._conn)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_sidecar.py tests/test_store.py tests/test_vikunja.py -v` → PASS. Then `uv run --extra dev pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/sidecar.py src/woodpecker/store.py src/woodpecker/vikunja.py tests/test_sidecar.py tests/test_store.py tests/test_vikunja.py
git commit -m "feat(sidecar): frog of the day and open prompt state, title rewrite"
```

---

### Task 5: Claude writes the first step and the reframe question

**Goal:** Rewrite `llm.write_focus` to return a plain opening line plus one first step under 10 minutes, with no guilt and no day count, and add `llm.write_reframe` for the 3rd-`t` question.

**Files:**
- Modify: `src/woodpecker/llm.py:358-388`
- Test: `tests/test_llm.py`

**Acceptance Criteria:**
- [ ] The `write_focus` system prompt asks for 2 lines, line 2 starts with `First step:`, a step under 10 minutes, and forbids guilt, day counts and the postpone count.
- [ ] The `write_focus` user payload has the task text and contains no age (`d old`) and no bump count.
- [ ] `write_reframe(frog, client)` makes one Claude call with the task text, and the prompt says the bot adds the options itself.
- [ ] Both prompts keep the "Never use em dashes" rule.

**Verify:** `uv run --extra dev pytest tests/test_llm.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

In `tests/test_llm.py`, delete `test_write_focus_leads_on_the_frog_and_names_the_bump_count` and `test_write_focus_handles_no_frog`, then append:

```python
def _ok_client():
    return FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))


def test_write_focus_asks_for_a_small_first_step_without_guilt():
    client = _ok_client()
    frog = replace(_high_task(estimate_seconds=7200, project_name="Backlog"), bump_count=4)
    out = llm.write_focus(frog, datetime(2026, 10, 8, 9, tzinfo=TZ), client)
    assert out == "ok"
    call = client.messages.calls[0]
    system = call["system"].lower()
    assert "first step:" in system
    assert "10 minutes" in system
    assert "no guilt" in system
    assert "em dash" in system
    user = call["messages"][0]["content"]
    assert frog.text in user
    assert "d old" not in user
    assert "pushed forward" not in user


def test_write_reframe_asks_size_or_owner():
    client = _ok_client()
    out = llm.write_reframe(_task(), client)
    assert out == "ok"
    call = client.messages.calls[0]
    assert "taxes" in call["messages"][0]["content"]
    system = call["system"].lower()
    assert "the bot adds" in system
    assert "em dash" in system
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_llm.py -k "write_focus or write_reframe" -v`
Expected: FAIL: `"first step:"` is not in the old prompt, and `write_reframe` does not exist.

- [ ] **Step 3: Write the implementation**

In `src/woodpecker/llm.py`, replace `write_focus` with:

```python
_NO_EM_DASH = (
    "Never use em dashes; use a comma, a colon, or a period instead. This rule has no exceptions."
)


def write_focus(frog: Task, now: datetime, client) -> str:
    """The morning lead: the frog in plain words plus one first step that takes under 10
    minutes. No guilt and no count of days avoided: guilt made Gautier close the message."""
    system = (
        "You are Woodpecker. Write exactly two short lines about the ONE task below. "
        "Line 1: name the task plainly as today's one thing. "
        "Line 2: start with 'First step:' and give one concrete action that takes under "
        "10 minutes, for example 'open the Doctolib page', not 'book the doctor'. "
        "No guilt, no count of days, no mention of how often it was put off. "
        + _NO_EM_DASH
    )
    bits = [f"Task: {frog.text}"]
    duration = _format_duration(frog.estimate_seconds)
    if duration:
        bits.append(f"est {duration}")
    if frog.project_name:
        bits.append(f"in {frog.project_name}")
    details_line = f" Notes: {frog.details}." if frog.details else ""
    user = ", ".join(bits) + "." + details_line
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_focus", response)
    return _text_of(response)


def write_reframe(frog: Task, client) -> str:
    """The question for a frog moved to tomorrow REFRAME_AFTER_BUMPS times: the task is
    probably wrong as written, so ask about its size or its owner instead of nagging."""
    system = (
        "You are Woodpecker. The task below was moved to tomorrow three times. Write one or "
        "two short lines, without blame, that ask whether it is too big as written or not "
        "really the user's to do. Do not list options: the bot adds them after your text. "
        + _NO_EM_DASH
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=150,
        system=system,
        messages=[{"role": "user", "content": f"Task: {frog.text}."}],
    )
    _log_usage("write_reframe", response)
    return _text_of(response)
```

Note: `scheduler.send_daily_focus` can still call `write_focus(None, ...)` until Task 7 deletes it. That call raises inside its `try`, and the existing fallback text goes out. This is expected for one task only.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_llm.py -v` → PASS. Then `uv run --extra dev pytest -q`. If a test in `tests/test_scheduler.py` fails because it expected the old no-frog text from `write_focus`, delete that test: Task 7 deletes `send_daily_focus` and its tests.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/llm.py tests/test_llm.py tests/test_scheduler.py
git commit -m "feat(llm): first step in the morning, reframe question, no guilt lines"
```

---

### Task 6: Letter replies, answered with no Claude call

**Goal:** Add `replies.py`, which answers `d`, `o`, `t`, `x`, `s`, `n` and the weekly `x 1 3` / `w 2` forms against the open prompt, and route `bot.handle_message` through it before Claude.

**Files:**
- Create: `src/woodpecker/replies.py`
- Modify: `src/woodpecker/bot.py:574-630`
- Test: `tests/test_replies.py` (create), `tests/test_bot.py`

**Acceptance Criteria:**
- [ ] `d` completes the frog in Vikunja once, marks the day `d`, clears the prompt, replies `Done, nice.`
- [ ] `o` sets `started_at` and keeps the prompt open.
- [ ] `t` moves the due date to tomorrow through `Store.reschedule_task` and marks the day `t`.
- [ ] The first `x` replies `Drop "<task>"? Send x again.` and drops nothing; a second `x` within 10 minutes drops; a second `x` after 10 minutes asks again.
- [ ] On a reframe prompt: `s` asks for the smaller step; the next message within 30 minutes becomes the title and resets the `t` count; `n` drops with the same confirm as `x`.
- [ ] On a weekly prompt: `x 1 3` drops tasks 1 and 3 with no confirm, `w 2` moves task 2 to today + 7 days, out-of-range numbers are named in the reply.
- [ ] A letter with no open prompt replies `Nothing to answer right now.`
- [ ] Letters are case-insensitive and trimmed (`" D "` works). A longer message returns `None` (free-text flow).
- [ ] Each applied reply logs `reply <task_id> <action>` at INFO.
- [ ] In `bot.handle_message`, a letter reply never calls `llm.interpret_message`; a longer message still does; a foreign chat is still ignored.

**Verify:** `uv run --extra dev pytest tests/test_replies.py tests/test_bot.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replies.py`:

```python
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from woodpecker import replies, sidecar
from woodpecker.models import STATUS_PENDING, Task
from woodpecker.store import Store

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 8, 9, 30, tzinfo=TZ)


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self.calls = []

    def list_open(self):
        return list(self._open.values())

    def get_task(self, task_id):
        return self._open.get(task_id)

    def mark_done(self, task_id):
        self.calls.append(("done", task_id))
        return self._open.pop(task_id, None)

    def delete_task(self, task_id):
        self.calls.append(("delete", task_id))
        return self._open.pop(task_id, None) is not None

    def update_task(self, task_id, deadline=None, priority=None, text=None):
        self.calls.append(("update", task_id, deadline, text))
        task = self._open.get(task_id)
        if task is None:
            return None
        changes = {}
        if deadline is not None:
            changes["deadline"] = deadline
        if text is not None:
            changes["text"] = text
        task = replace(task, **changes)
        self._open[task_id] = task
        return task


def _task(id):
    return Task(
        id=id,
        text=f"task {id}",
        priority=0,
        deadline=None,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


def fresh(*ids):
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    vk = FakeVikunja([_task(i) for i in ids])
    return Store(vk, conn), vk


def with_frog(kind=replies.FROG, task_id=1, *ids):
    store, vk = fresh(task_id, *ids)
    store.record_frog(NOW.date(), task_id)
    store.set_open_prompt(kind, [task_id], NOW)
    return store, vk


def test_d_completes_the_frog_once():
    store, vk = with_frog()
    assert replies.answer(store, "d", NOW) == "Done, nice."
    assert vk.calls == [("done", 1)]
    assert store.frog_of_day(NOW.date()).answered == "d"
    assert store.get_open_prompt() is None


def test_letters_are_trimmed_and_case_insensitive():
    store, vk = with_frog()
    assert replies.answer(store, "  D ", NOW) == "Done, nice."


def test_o_records_a_start_and_keeps_the_prompt():
    store, vk = with_frog()
    replies.answer(store, "o", NOW)
    assert store.frog_of_day(NOW.date()).started_at == NOW
    assert store.get_open_prompt() is not None
    assert vk.calls == []


def test_t_reschedules_to_tomorrow():
    store, vk = with_frog()
    replies.answer(store, "t", NOW)
    assert vk.calls == [("update", 1, NOW.date() + timedelta(days=1), None)]
    assert store.frog_of_day(NOW.date()).answered == "t"


def test_x_needs_a_second_x_within_ten_minutes():
    store, vk = with_frog()
    assert replies.answer(store, "x", NOW) == 'Drop "task 1"? Send x again.'
    assert vk.calls == []
    assert replies.answer(store, "x", NOW + timedelta(minutes=10)) == "Dropped."
    assert vk.calls == [("delete", 1)]
    assert store.frog_of_day(NOW.date()).answered == "x"


def test_a_late_second_x_asks_again():
    store, vk = with_frog()
    replies.answer(store, "x", NOW)
    assert replies.answer(store, "x", NOW + timedelta(minutes=11)).startswith("Drop ")
    assert vk.calls == []


def test_no_open_prompt_is_refused():
    store, vk = fresh(1)
    assert replies.answer(store, "d", NOW) == "Nothing to answer right now."
    assert vk.calls == []


def test_a_longer_message_is_not_a_letter_reply():
    store, vk = with_frog()
    assert replies.answer(store, "done with the taxes", NOW) is None


def test_a_reframe_letter_on_a_frog_prompt_gets_the_legend():
    store, vk = with_frog()
    assert "d done" in replies.answer(store, "s", NOW)
    assert vk.calls == []


def test_s_then_the_next_message_rewrites_the_title_and_resets_t():
    store, vk = with_frog(replies.REFRAME)
    store.mark_frog_answered(NOW.date(), "t")
    assert "smaller step" in replies.answer(store, "s", NOW).lower()
    reply = replies.answer(store, "Open the Doctolib page", NOW + timedelta(minutes=5))
    assert reply == 'Now the task is "Open the Doctolib page".'
    assert vk.get_task(1).text == "Open the Doctolib page"
    assert store.tomorrow_count(1) == 0
    assert store.get_open_prompt() is None


def test_the_step_window_closes_after_30_minutes():
    store, vk = with_frog(replies.REFRAME)
    replies.answer(store, "s", NOW)
    assert replies.answer(store, "buy milk", NOW + timedelta(minutes=31)) is None
    assert vk.get_task(1).text == "task 1"
    assert store.get_open_prompt() is None


def test_n_drops_with_a_confirm():
    store, vk = with_frog(replies.REFRAME)
    assert replies.answer(store, "n", NOW).startswith("Not yours?")
    assert vk.calls == []
    assert replies.answer(store, "n", NOW + timedelta(minutes=1)) == "Dropped."
    assert vk.calls == [("delete", 1)]


def test_weekly_drops_and_moves_by_number():
    store, vk = fresh(4, 5, 6)
    store.set_open_prompt(replies.WEEKLY, [4, 5, 6], NOW)
    reply = replies.answer(store, "x 1 3 w 2", NOW)
    assert ("delete", 4) in vk.calls and ("delete", 6) in vk.calls
    assert ("update", 5, NOW.date() + timedelta(days=7), None) in vk.calls
    assert reply == "Dropped 1, 3. Moved 2 to next week."
    assert store.get_open_prompt() is None


def test_weekly_names_numbers_out_of_range():
    store, vk = fresh(4)
    store.set_open_prompt(replies.WEEKLY, [4], NOW)
    assert replies.answer(store, "x 9", NOW) == "No 9 in the list."
    assert vk.calls == []


def test_a_single_letter_on_the_weekly_prompt_gets_a_hint():
    store, vk = fresh(4)
    store.set_open_prompt(replies.WEEKLY, [4], NOW)
    assert "x 1 3" in replies.answer(store, "d", NOW)


def test_each_reply_logs_task_and_action(caplog):
    store, vk = with_frog()
    with caplog.at_level(logging.INFO, logger="woodpecker.replies"):
        replies.answer(store, "t", NOW)
    assert "reply 1 t" in caplog.text
```

Append to `tests/test_bot.py`:

```python
def test_a_letter_reply_skips_claude(monkeypatch):
    store = fresh([_task(1)])
    store.record_frog(datetime(2026, 10, 8, tzinfo=TZ).date(), 1)
    store.set_open_prompt("frog", [1], datetime(2026, 10, 8, 9, tzinfo=TZ))

    def boom(*args, **kwargs):
        raise AssertionError("Claude must not be called for a letter reply")

    monkeypatch.setattr(bot.llm, "interpret_message", boom)
    monkeypatch.setattr(bot.config, "now_paris", lambda: datetime(2026, 10, 8, 9, 5, tzinfo=TZ))
    update = make_update("d")
    asyncio.run(bot.handle_message(update, make_context(store)))
    assert update.message.reply_text.call_args.args[0] == "Done, nice."


def test_a_letter_with_no_prompt_is_refused(monkeypatch):
    store = fresh([_task(1)])
    monkeypatch.setattr(bot.llm, "interpret_message", lambda *a, **k: [])
    update = make_update("x")
    asyncio.run(bot.handle_message(update, make_context(store)))
    assert update.message.reply_text.call_args.args[0] == "Nothing to answer right now."
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_replies.py tests/test_bot.py -v`
Expected: FAIL with `ImportError: cannot import name 'replies'`; the two new bot tests fail (Claude is called).

- [ ] **Step 3: Write the implementation**

Create `src/woodpecker/replies.py`:

```python
"""Deterministic answers to the one-letter replies (d, o, t, x, s, n) and the weekly review
forms (x 1 3, w 2). No Claude call: the letter is parsed here and applied through the
Store, against the last prompt the bot sent (the sidecar's open_prompt). answer() returns
None for any text that is not such a reply, so the caller sends it to the free-text flow."""

import logging
import re
from datetime import datetime, timedelta

from . import config, render

logger = logging.getLogger(__name__)

# The kinds of open prompt a letter can answer.
FROG = "frog"
REFRAME = "reframe"
WEEKLY = "weekly"
STEP = "step"  # after "s": the next message is the smaller step

NOTHING_OPEN = "Nothing to answer right now."
NOT_FOUND = "Couldn't find that one."
WEEKLY_HINT = "For the review, reply like x 1 3 or w 2."

_ALLOWED = {FROG: {"d", "o", "t", "x"}, REFRAME: {"s", "n", "x"}}
_LEGEND = {FROG: render.FROG_LEGEND, REFRAME: render.REFRAME_LEGEND}
_LETTERS = {"d", "o", "t", "x", "s", "n"}
_WEEKLY_FORM = re.compile(r"^(?:[xw](?:\s+\d+)+\s*)+$")
_WEEKLY_GROUP = re.compile(r"([xw])((?:\s+\d+)+)")


def answer(store, text: str, now: datetime) -> str | None:
    prompt = store.get_open_prompt()
    if prompt is not None and prompt.kind == STEP:
        reply = _smaller_step(store, prompt, text.strip(), now)
        if reply is not None:
            return reply
        prompt = None  # the step window closed; read the text as usual
    reply = text.strip().lower()
    weekly = bool(_WEEKLY_FORM.match(reply))
    if reply not in _LETTERS and not weekly:
        return None
    if prompt is None:
        return NOTHING_OPEN
    if prompt.kind == WEEKLY:
        return _weekly(store, prompt.task_ids, reply, now) if weekly else WEEKLY_HINT
    if weekly or reply not in _ALLOWED[prompt.kind]:
        return f"Reply with one letter: {_LEGEND[prompt.kind]}"
    return _letter(store, prompt, reply, now)


def _letter(store, prompt, letter: str, now: datetime) -> str:
    task_id = prompt.task_ids[0]
    day = prompt.sent_at.date()
    if letter == "d":
        task = store.complete_task(task_id, now)
        _close(store, task_id, day, "d")
        return "Done, nice." if task else NOT_FOUND
    if letter == "o":
        store.mark_frog_started(day, now)
        logger.info("reply %s o", task_id)
        return "Good. One step, then see how it goes."
    if letter == "t":
        task = store.reschedule_task(task_id, now.date() + timedelta(days=1), None)
        store.mark_frog_answered(day, "t")
        logger.info("reply %s t", task_id)
        return "Moved to tomorrow." if task else NOT_FOUND
    if letter == "s":
        store.set_open_prompt(STEP, [task_id], now)
        logger.info("reply %s s", task_id)
        return "What is the smaller step? Send it and it becomes the task."
    return _drop(store, prompt, letter, now)  # "x" or "n"


def _drop(store, prompt, letter: str, now: datetime) -> str:
    task_id = prompt.task_ids[0]
    window = timedelta(minutes=config.DROP_CONFIRM_MINUTES)
    if prompt.pending_drop_at is not None and now - prompt.pending_drop_at <= window:
        ok = store.drop_task(task_id)
        _close(store, task_id, prompt.sent_at.date(), letter)
        return "Dropped." if ok else NOT_FOUND
    task = store.get_task(task_id)
    if task is None:
        store.clear_open_prompt()
        return NOT_FOUND
    store.set_pending_drop(now)
    if letter == "n":
        return f'Not yours? Send n again to drop "{task.text}".'
    return f'Drop "{task.text}"? Send x again.'


def _close(store, task_id: int, day, letter: str) -> None:
    store.mark_frog_answered(day, letter)
    store.clear_open_prompt()
    logger.info("reply %s %s", task_id, letter)


def _smaller_step(store, prompt, text: str, now: datetime) -> str | None:
    store.clear_open_prompt()
    if now - prompt.sent_at > timedelta(minutes=config.STEP_ANSWER_MINUTES):
        return None
    task_id = prompt.task_ids[0]
    task = store.rename_task(task_id, text)
    store.clear_tomorrows(task_id)
    logger.info("reply %s rewrite", task_id)
    return f'Now the task is "{task.text}".' if task else NOT_FOUND


def _weekly(store, task_ids: list[int], reply: str, now: datetime) -> str:
    dropped, moved, missing = [], [], []
    for letter, numbers in _WEEKLY_GROUP.findall(reply):
        for n in (int(part) for part in numbers.split()):
            if not 1 <= n <= len(task_ids):
                missing.append(n)
                continue
            task_id = task_ids[n - 1]
            if letter == "x":
                store.drop_task(task_id)
                dropped.append(n)
            else:
                store.reschedule_task(task_id, now.date() + timedelta(days=7), None)
                moved.append(n)
            logger.info("reply %s weekly-%s", task_id, letter)
    if dropped or moved:
        store.clear_open_prompt()
    parts = []
    if dropped:
        parts.append(f"Dropped {', '.join(map(str, dropped))}.")
    if moved:
        parts.append(f"Moved {', '.join(map(str, moved))} to next week.")
    if missing:
        parts.append(f"No {', '.join(map(str, missing))} in the list.")
    return " ".join(parts)
```

In `src/woodpecker/bot.py`, change the import line to `from . import config, llm, orchestrator, render, replies`, and replace the body of `handle_message` from `try:` to the end with:

```python
    try:
        # A one-letter reply (or the weekly "x 1 3" form) is answered here, with no Claude
        # call. Anything else goes to the free-text flow below.
        reply = replies.answer(store, text, now)
        if reply is None:
            reply = _free_text(store, client, memory, chat_id, text, now)
    except VikunjaError:
        logger.exception("Vikunja unreachable while handling message")
        await update.message.reply_text(
            "My task list is unreachable right now, try again in a moment."
        )
        return
    memory.add(chat_id, text, reply)
    # The pending nag has now been answered (or superseded by real conversation), so
    # it must not colour the next, unrelated message.
    memory.clear_outbound(chat_id)
    await update.message.reply_text(reply)


def _free_text(store, client, memory, chat_id, text, now) -> str:
    tasks = store.list_pending()
    history = memory.get(chat_id)
    outbound = memory.get_outbound(chat_id)
    display_ids = store.load_display(chat_id)
    intents = llm.interpret_message(
        text,
        tasks,
        now,
        client,
        history=history,
        recent_outbound=outbound,
        display_ids=display_ids,
    )
    logger.info("Interpreted into %d intent(s): %s", len(intents), [i.action for i in intents])
    # A "list" intent needs a snapshot of the backlog to both render the reply and
    # save as the display order for the next message's numbered references. Fetch it
    # once, at the point the intent is processed (so any earlier intents in this same
    # batch have already mutated the backlog), and reuse it for both instead of
    # letting each step fetch its own copy.
    replies_out = []
    shown = None
    for intent in intents:
        if intent.action == "list":
            shown = store.list_pending()
            replies_out.append(render.render_backlog(shown, now))
        else:
            replies_out.append(orchestrator.apply_intent(store, intent, now))
    reply = "\n".join(replies_out)
    logger.debug("Reply body: %s", reply)
    # If we just printed the backlog, remember the exact order shown, so the numbers in
    # the next message resolve against this list rather than a later, shifted order.
    if shown is not None:
        store.save_display(chat_id, [t.id for t in render.display_order(shown, now)])
    return reply
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest tests/test_replies.py tests/test_bot.py -v` → PASS. Then `uv run --extra dev pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/woodpecker/replies.py src/woodpecker/bot.py tests/test_replies.py tests/test_bot.py
git commit -m "feat(bot): answer one-letter replies without a Claude call"
```

---

### Task 7: The new jobs, the cron wiring, and the removal of the old nags

**Goal:** Replace `send_daily_focus` and `send_nags` with `send_morning`, `send_checkin`, and `send_weekly_review`, wire them as three cron jobs, and delete the code that only the old nags used.

**Files:**
- Modify: `src/woodpecker/scheduler.py` (rewrite), `src/woodpecker/main.py:716-772`, `src/woodpecker/selection.py`, `src/woodpecker/render.py`, `src/woodpecker/llm.py`, `src/woodpecker/config.py`
- Test: `tests/test_scheduler.py`, `tests/test_main.py`, `tests/test_selection.py`, `tests/test_render.py`, `tests/test_llm.py`, `tests/test_config.py`

**Acceptance Criteria:**
- [ ] The morning message names a frog whenever a task is pending, ends the frog part with the frog legend, records `frog_state`, opens a `frog` prompt, and logs `frog <task_id>`.
- [ ] When the frog has 3 `t` days, the morning sends the reframe text with the reframe legend and opens a `reframe` prompt.
- [ ] The vault block follows the frog; a vault read error gives `Vault: not readable (<reason>)` and the frog still goes out; a Claude error gives the fallback line `Today: "<task>".` and the message still goes out.
- [ ] The check-in sends nothing when the frog has an answer (`d`, `t`, `x`, `n`) and no vault task is due today; it lists vault tasks due today or overdue; it uses the reframe legend on a reframe day.
- [ ] The weekly review lists up to 5 stale tasks and opens a `weekly` prompt; with no stale task it sends nothing.
- [ ] Every job sends nothing in quiet hours.
- [ ] `main.build_scheduler(store, send, client, vault_path)` returns 3 coroutine jobs: 09:00 daily, 14:00 daily, Sunday 10:00, each with the Europe/Paris zone.
- [ ] `grep -rn "send_nags\|send_daily_focus\|select_daily_focus\|select_slow_resurface\|nag_stance\|write_nag\|render_matters\|morning_shown\|NAG_HOURS\|DAILY_FOCUS_HOUR\|SLOW_RESURFACE_DAYS\|MATTERS_MIN_PRIORITY\|LONG_DURATION_SECONDS\|avoidance_sort_key" src tests` prints nothing.

**Verify:** `uv run --extra dev pytest -q` → all pass, and the grep above prints nothing

**Steps:**

- [ ] **Step 1: Write the failing tests**

In `tests/test_scheduler.py`:
- Delete every test that calls `scheduler.send_daily_focus` or `scheduler.send_nags`.
- Add `update_task` to `FakeVikunja` (add `from dataclasses import replace`):

```python
    def update_task(self, task_id, deadline=None, priority=None, text=None):
        task = self._open.get(task_id)
        if task is None:
            return None
        changes = {}
        if deadline is not None:
            changes["deadline"] = deadline
        if text is not None:
            changes["text"] = text
        task = replace(task, **changes)
        self._open[task_id] = task
        return task
```

- Change the `build_scheduler` test to expect 3 jobs: replace `main.build_scheduler(store, send, FakeClient(), chat_id=42)` with `main.build_scheduler(store, send, FakeClient(), vault_path="/nope")` everywhere in the file, and `assert len(jobs) == 1 + len(config.NAG_HOURS)` with `assert len(jobs) == 3`.
- Append (add `timedelta` to the `from datetime import ...` line and `replies, render` to the `from woodpecker import ...` line):

```python
MORNING = datetime(2026, 10, 8, 9, tzinfo=TZ)
CHECKIN = datetime(2026, 10, 8, 14, tzinfo=TZ)
SUNDAY = datetime(2026, 10, 11, 10, tzinfo=TZ)


def _vault(tmp_path, body):
    (tmp_path / "Taxes.md").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_morning_names_a_frog_with_the_legend_then_the_vault(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    vault_path = _vault(tmp_path, "- [ ] file the return 📅 2026-10-10\n")
    scheduler.send_morning(store, send, FakeClient(), MORNING, vault_path)
    [text] = sent
    assert text.startswith("canned prose\n\n" + render.FROG_LEGEND)
    assert text.endswith("• file the return (📅 2026-10-10, Taxes)\nTick these in Obsidian.")
    assert store.frog_of_day(MORNING.date()).task_id == 1
    assert store.get_open_prompt().kind == replies.FROG


def test_morning_logs_the_frog(tmp_path, caplog):
    store = fresh([_task(1)])
    sent, send = collector()
    with caplog.at_level("INFO", logger="woodpecker.scheduler"):
        scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert "frog 1" in caplog.text


def test_morning_with_an_unreadable_vault_still_sends_the_frog(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path / "missing"))
    [text] = sent
    assert "canned prose" in text
    assert text.endswith("Vault: not readable (not a folder)")


def test_morning_with_a_claude_error_uses_the_plain_line(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    scheduler.send_morning(store, send, RaisingClient(), MORNING, str(tmp_path))
    assert sent[0].startswith('Today: "task 1".')


def test_morning_with_an_empty_backlog(tmp_path):
    store = fresh([])
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert sent == ["Backlog empty. Nothing to chase today."]
    assert store.get_open_prompt() is None


def test_the_third_t_turns_the_next_morning_into_the_reframe(tmp_path):
    store = fresh([_task(1)])
    for offset in (3, 2, 1):
        day = MORNING.date() - timedelta(days=offset)
        store.record_frog(day, 1)
        store.mark_frog_answered(day, "t")
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert render.REFRAME_LEGEND in sent[0]
    assert store.get_open_prompt().kind == replies.REFRAME


def test_checkin_asks_when_the_frog_has_no_answer(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    sent, send = collector()
    scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
    assert sent == ['Still on for "task 1" today?\n' + render.FROG_LEGEND]


def test_checkin_after_o_asks_how_it_goes(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    store.mark_frog_started(CHECKIN.date(), MORNING)
    sent, send = collector()
    scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
    assert sent[0].startswith('How is "task 1" going?')


def test_checkin_is_skipped_after_an_answer(tmp_path):
    for letter in ("d", "t", "x", "n"):
        store = fresh([_task(1)])
        store.record_frog(CHECKIN.date(), 1)
        store.mark_frog_answered(CHECKIN.date(), letter)
        sent, send = collector()
        scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
        assert sent == [], letter


def test_checkin_carries_vault_tasks_due_today(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    store.mark_frog_answered(CHECKIN.date(), "d")
    sent, send = collector()
    vault_path = _vault(tmp_path, "- [ ] pay rent 📅 2026-10-08\n- [ ] later 📅 2026-10-09\n")
    scheduler.send_checkin(store, send, CHECKIN, vault_path)
    assert sent == [
        "Due in the vault today:\n• pay rent (📅 2026-10-08, Taxes)\nTick these in Obsidian."
    ]


def test_weekly_review_lists_stale_tasks_and_opens_the_prompt():
    old = _task(1, created_at=SUNDAY - timedelta(days=20))
    new = _task(2, created_at=SUNDAY - timedelta(days=2))
    store = fresh([old, new])
    sent, send = collector()
    scheduler.send_weekly_review(store, send, SUNDAY)
    assert "1. task 1 (20d)" in sent[0]
    assert "task 2" not in sent[0]
    assert store.get_open_prompt().task_ids == [1]


def test_weekly_review_with_nothing_stale_sends_nothing():
    store = fresh([_task(1, created_at=SUNDAY - timedelta(days=1))])
    sent, send = collector()
    scheduler.send_weekly_review(store, send, SUNDAY)
    assert sent == []


def test_quiet_hours_send_nothing(tmp_path):
    late = datetime(2026, 10, 8, 23, 30, tzinfo=TZ)
    store = fresh([_task(1, created_at=late - timedelta(days=30))])
    store.record_frog(late.date(), 1)
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), late, str(tmp_path))
    scheduler.send_checkin(store, send, late, str(tmp_path))
    scheduler.send_weekly_review(store, send, late)
    assert sent == []


def test_cron_times():
    store = fresh()
    sent, send = collector()
    sched = main.build_scheduler(store, send, FakeClient(), vault_path="/nope")
    fields = sorted(
        (str(job.trigger.fields[4]), str(job.trigger.fields[5])) for job in sched.get_jobs()
    )  # (day_of_week, hour)
    assert fields == [("*", "14"), ("*", "9"), ("sun", "10")]
```

In `tests/test_main.py`, replace any `build_scheduler(..., chat_id=...)` call with `build_scheduler(..., vault_path="/nope")`, and any count based on `NAG_HOURS` with `3`.

Delete these tests, because their code goes:
- `tests/test_selection.py`: every test that calls `select_daily_focus`, `select_slow_resurface`, or `nag_stance`.
- `tests/test_render.py`: every test that calls `morning_shown` or `render_matters`.
- `tests/test_llm.py`: every test that calls `write_nag`, and any helper used only by them (for example `_mid_task` if nothing else uses it).
- `tests/test_config.py`: the `NAG_HOURS` assertion.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --extra dev pytest tests/test_scheduler.py tests/test_main.py -v`
Expected: FAIL with `AttributeError: module 'woodpecker.scheduler' has no attribute 'send_morning'` and `TypeError` on `vault_path`.

- [ ] **Step 3: Write the implementation**

Replace `src/woodpecker/scheduler.py` with:

```python
import logging
from datetime import date, datetime
from pathlib import Path

from . import config, llm, render, vault
from .replies import FROG, REFRAME, WEEKLY
from .selection import (
    is_quiet_hours,
    select_frog,
    select_stale_for_review,
    select_vault_due,
    select_vault_reminders,
)

logger = logging.getLogger(__name__)


def _vault_part(vault_path: str, today: date, select, render_lines) -> str:
    """The vault text of a message. A read error becomes one line: a missing mount must
    never silence the frog."""
    try:
        tasks = vault.read_vault(Path(vault_path))
    except (vault.VaultUnreadable, OSError) as exc:
        logger.warning("Vault unreadable at %s: %s", vault_path, exc)
        return render.render_vault_unreadable(str(exc))
    return render_lines(select(tasks, today), today)


def _is_reframe(store, task_id: int) -> bool:
    return store.tomorrow_count(task_id) >= config.REFRAME_AFTER_BUMPS


def _frog_part(store, client, frog, now: datetime) -> str:
    store.record_frog(now.date(), frog.id)
    if _is_reframe(store, frog.id):
        try:
            lead = llm.write_reframe(frog, client)
        except Exception:
            logger.exception("Reframe prose failed; sending the plain question")
            lead = f'"{frog.text}" keeps moving to tomorrow. Too big as written, or not yours to do?'
        store.set_open_prompt(REFRAME, [frog.id], now)
        return f"{lead}\n\n{render.REFRAME_LEGEND}"
    try:
        lead = llm.write_focus(frog, now, client)
    except Exception:
        logger.exception("Morning prose failed; sending the plain line")
        lead = f'Today: "{frog.text}".'
    store.set_open_prompt(FROG, [frog.id], now)
    return f"{lead}\n\n{render.FROG_LEGEND}"


def send_morning(store, send, client, now: datetime, vault_path: str) -> None:
    logger.info("Morning job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping morning: quiet hours")
        return
    parts = []
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Morning job could not load pending tasks")
        parts.append("My task list is unreachable this morning.")
    else:
        frog = select_frog(tasks, now)
        logger.info("frog %s", frog.id if frog else None)
        if frog is None:
            parts.append("Backlog empty. Nothing to chase today.")
        else:
            parts.append(_frog_part(store, client, frog, now))
    block = _vault_part(vault_path, now.date(), select_vault_reminders, render.render_vault_block)
    if block:
        parts.append(block)
    send("\n\n".join(parts))


def send_checkin(store, send, now: datetime, vault_path: str) -> None:
    """One reminder, only when useful: the frog has no answer yet, or a vault task is due."""
    logger.info("Check-in job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping check-in: quiet hours")
        return
    parts = []
    frog_day = store.frog_of_day(now.date())
    if frog_day is not None and frog_day.answered is None:
        try:
            task = store.get_task(frog_day.task_id)
        except Exception:
            logger.exception("Check-in could not load the frog")
            task = None
        if task is not None:
            kind = REFRAME if _is_reframe(store, task.id) else FROG
            legend = render.REFRAME_LEGEND if kind == REFRAME else render.FROG_LEGEND
            parts.append(render.render_checkin(task, frog_day.started_at is not None, legend))
            store.set_open_prompt(kind, [task.id], now)
    due = _vault_part(vault_path, now.date(), select_vault_due, render.render_vault_due)
    if due:
        parts.append(due)
    if not parts:
        logger.info("Skipping check-in: nothing useful to say")
        return
    send("\n\n".join(parts))


def send_weekly_review(store, send, now: datetime) -> None:
    logger.info("Weekly review job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping weekly review: quiet hours")
        return
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Weekly review could not load pending tasks")
        return
    stale = select_stale_for_review(tasks, now)
    if not stale:
        logger.info("Skipping weekly review: nothing stale")
        return
    store.set_open_prompt(WEEKLY, [t.id for t in stale], now)
    send(render.render_weekly_review(stale, now))
```

In `src/woodpecker/main.py`, replace `build_scheduler` with:

```python
def build_scheduler(store, send, client, vault_path) -> AsyncIOScheduler:
    """Build the cron scheduler: the morning message, the check-in, the weekly review.

    The jobs are coroutines on purpose: AsyncIOScheduler runs coroutine jobs on the
    bot's own asyncio event loop, the same thread that owns the sidecar connection and
    the Telegram send. A BackgroundScheduler with plain sync jobs ran them on a worker
    thread instead, where the SQLite connection is unusable and there is no running loop
    for the send, so every scheduled message crashed silently."""
    sched = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def _morning():
        scheduler.send_morning(store, send, client, config.now_paris(), vault_path)

    async def _checkin():
        scheduler.send_checkin(store, send, config.now_paris(), vault_path)

    async def _weekly():
        scheduler.send_weekly_review(store, send, config.now_paris())

    # Pass the timezone to every CronTrigger explicitly. APScheduler does NOT stamp the
    # scheduler's timezone onto a trigger; a trigger built without one defaults to the
    # machine's local zone (UTC in the container), so every job fired 2 hours off Paris.
    tz = config.TIMEZONE
    sched.add_job(_morning, CronTrigger(hour=config.FOCUS_HOUR, minute=0, timezone=tz))
    sched.add_job(_checkin, CronTrigger(hour=config.CHECKIN_HOUR, minute=0, timezone=tz))
    sched.add_job(
        _weekly,
        CronTrigger(
            day_of_week=config.WEEKLY_REVIEW_DAY,
            hour=config.WEEKLY_REVIEW_HOUR,
            minute=0,
            timezone=tz,
        ),
    )
    return sched
```

In `main()._post_init`, replace `build_scheduler(store, send, client, chat_id)` with `build_scheduler(store, send, client, config.vault_path())`, and the `logger.info("Scheduler started: ...")` call with:

```python
        logger.info(
            "Scheduler started: morning at %02d:00, check-in at %02d:00, review %s %02d:00, "
            "vault at %s",
            config.FOCUS_HOUR,
            config.CHECKIN_HOUR,
            config.WEEKLY_REVIEW_DAY,
            config.WEEKLY_REVIEW_HOUR,
            config.vault_path(),
        )
```

Delete the old code:
- `selection.py`: `avoidance_sort_key`, `nag_stance`, `select_daily_focus`, `select_slow_resurface`. Remove `DailyFocus` from the import.
- `models.py`: `DailyFocus` (nothing else uses it; check with `grep -rn DailyFocus src tests`).
- `render.py`: `_PRIO_FLAG`, `_matters_key`, `morning_shown`, `render_matters`.
- `llm.py`: `write_nag`; remove `nag_stance` from the `from .selection import` line.
- `config.py`: `SLOW_RESURFACE_DAYS`, `NAG_HOURS`, `MATTERS_MIN_PRIORITY` and its comment, `LONG_DURATION_SECONDS`, `DAILY_FOCUS_HOUR`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --extra dev pytest -q` → all pass.
Run: `grep -rn "send_nags\|send_daily_focus\|select_daily_focus\|select_slow_resurface\|nag_stance\|write_nag\|render_matters\|morning_shown\|NAG_HOURS\|DAILY_FOCUS_HOUR\|SLOW_RESURFACE_DAYS\|MATTERS_MIN_PRIORITY\|LONG_DURATION_SECONDS\|avoidance_sort_key" src tests` → no output.
Run: `uv run --extra dev ruff check . && uv run --extra dev ruff format --check .` → clean (run `ruff format .` if needed).

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat(scheduler): morning, check-in and weekly review replace the nags"
```

---

### Task 8: Vault mount and docs

**Goal:** Mount the vault read-only in the container and describe the new rhythm and the vault source in `.env.example`, `CLAUDE.md`, and `README.md`.

**Files:**
- Modify: `docker-compose.yml`, `.env.example`, `CLAUDE.md`, `README.md`

**Acceptance Criteria:**
- [ ] `docker-compose.yml` has `- /home/agent/vault:/vault:ro` under `volumes`, with a comment that Woodpecker only reads it.
- [ ] `docker compose config` parses the file with no error (if `docker` is not available to this user, `python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml'))"` instead).
- [ ] `.env.example` documents `WOODPECKER_VAULT_PATH` (default `/vault`).
- [ ] `CLAUDE.md` and `README.md` describe 09:00 morning, 14:00 check-in, Sunday 10:00 review, the letter replies, the vault block, and `vault.py` / `replies.py` in the module list; no text still says 06:00 focus or 09/13/19 nags.
- [ ] No em dash in any line this task adds.

**Verify:** `grep -n "06:00\|13:00\|19:00\|NAG_HOURS" CLAUDE.md README.md .env.example docker-compose.yml` → no hits about the current schedule; `uv run --extra dev pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Edit `docker-compose.yml`**

Under `volumes:` add:

```yaml
      # The Obsidian vault, read-only: the morning lists its dated tasks. Woodpecker never
      # writes there (the vault is synced by its own job on jarvis).
      - /home/agent/vault:/vault:ro
```

Check: `docker compose config >/dev/null && echo ok` (or the yaml fallback in the criteria).

- [ ] **Step 2: Edit `.env.example`**

Append:

```bash
# Obsidian vault mount inside the container (read-only), defaults to /vault.
# The morning message lists vault tasks due within 7 days or marked ⏫.
WOODPECKER_VAULT_PATH=/vault
```

- [ ] **Step 3: Edit `CLAUDE.md` and `README.md`**

In `CLAUDE.md`:
- In the APScheduler library line, replace `(06:00 focus, midday/evening nags, daily stale-scan)` with `(09:00 morning, 14:00 check-in, Sunday 10:00 weekly review)`.
- In "Module Responsibilities", add `vault.py  Read-only reader of the Obsidian vault's open checkboxes` and `replies.py  Deterministic one-letter replies (d o t x s n, weekly x 1 3 / w 2)`; change the `selection.py` line to `Pure rules: the frog, the weekly stale list, the vault reminders, quiet hours`; change the `scheduler.py` line to `Job bodies: morning, check-in, weekly review`; change the `sidecar.py` text to list `frog_state` and `open_prompt` too.
- In "Testing Strategy", replace the daily-focus bullet text with: frog selection (overdue, bump count, age), the letter replies, the vault reader and windows.
- Add a short "Rhythm" paragraph under Architecture: two messages a day at most; the morning names one frog with one first step and the letter legend, then the vault block; the check-in only when the frog has no answer or a vault task is due; the Sunday review of tasks pending 14 days or more. Link the spec path.

In `README.md`, find each place that describes the 06:00 focus or the 09/13/19 nags (`grep -n "06:00\|nag" README.md`) and rewrite it to the same rhythm, plus the letter legend `d done · o on it · t tomorrow · x drop`.

- [ ] **Step 4: Check**

Run the Verify grep and `uv run --extra dev pytest -q`. Run `grep -n "—" docker-compose.yml .env.example` and check that the lines this task added have no em dash.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example CLAUDE.md README.md
git commit -m "docs: new nudge rhythm, read-only vault mount"
```

---

## After the plan (not tasks for the executor)

- **Deploy is outward-facing.** A push to `main` auto-deploys to jarvis. The executor stops at "branch ready" and Gautier says when to merge and push.
- **First live check after deploy:** the next 09:00 message names a frog, ends the frog part with the legend, and shows the vault block; `docker logs woodpecker --tail 50` shows `frog <id>` and `Scheduler started: morning at 09:00, check-in at 14:00, review sun 10:00, vault at /vault`.
- **Vault log and card:** write `10 Projects/woodpecker/log/<deploy date>.md` and update the Goal line of `_project.md` (it still says 06:00 and 09/13/19).
- **Review on 2026-11-08:** count `reply` lines against `frog` lines in `logs/woodpecker.log`, per the spec's "Success" section.
