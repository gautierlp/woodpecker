# Jolt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Jolt, a single-user Telegram bot that captures tasks, surfaces one focused thing a day, nags on it (respecting quiet hours), hunts stale tasks, and marks work done, with Claude doing interpretation and tone and plain code doing everything mechanical.

**Architecture:** One Python package (`src/jolt/`). Pure logic (storage, selection, rendering, intent parsing) is unit-tested with in-memory SQLite and no network. The three side-effect boundaries (Telegram, Claude, the clock) are injected as callables/objects so tests mock them. APScheduler fires the daily focus and nags; python-telegram-bot handles inbound messages.

**Tech Stack:** Python 3.12, uv, ruff, pytest, python-telegram-bot, anthropic, APScheduler, SQLite (stdlib `sqlite3`), Docker.

## Global Constraints

- **Python ≥ 3.12** (exact floor).
- **Dependencies:** `python-telegram-bot`, `anthropic`, `APScheduler` only. Storage is stdlib `sqlite3`. No ORM, no other DB, no Redis.
- **Claude model:** `claude-haiku-4-5-20251001` (cheap, ample for this volume).
- **Stale threshold = 3 days.** Single constant `STALE_THRESHOLD_DAYS`.
- **Quiet hours: pings only when `06:00 <= hour < 23:00`** local time. Single constants.
- **Nag times = 09:00 / 13:00 / 19:00; daily focus = 06:00.** Timezone `Europe/Paris`.
- **Completion acknowledgment is plain** ("Done, nice."), no cheerleading.
- **Division of labour:** the full backlog dump is rendered by plain code and NEVER sent to Claude. Claude handles parsing, the `answer` free-text replies, the focus prose, and the nag prose.
- **Copy rule:** no em dashes in any generated user-facing string, code, or comment. Use commas, colons, periods, or parentheses.
- **Secrets** come from environment variables only; never hard-coded, never committed.
- **Every task is TDD:** write the failing test, watch it fail, implement minimally, watch it pass, commit.

## File Structure

```
pyproject.toml            # uv project, deps, ruff, pytest config, package + script entry
.env.example              # required env vars, no real values
src/jolt/
  __init__.py
  config.py               # constants + env accessors + the clock helper (now_paris)
  models.py               # Task, DailyFocus dataclasses + status/priority constants
  db.py                   # SQLite: connect, init, add/get/list/complete/drop/mark_nagged
  selection.py            # pure: ordering, is_stale, select_daily_focus, is_quiet_hours
  render.py               # pure: render_backlog (the mechanical dump)
  llm.py                  # Intent + parse_intent (pure) + interpret_message/write_focus/write_nag
  orchestrator.py         # apply_intent: Intent + db -> reply string
  bot.py                  # Telegram handle_message wiring
  scheduler.py            # send_daily_focus / send_nags jobs
  main.py                 # entry point: wires db + telegram + scheduler, starts polling
tests/
  test_db.py
  test_selection.py
  test_render.py
  test_llm.py
  test_orchestrator.py
  test_bot.py
  test_scheduler.py
Dockerfile
docker-compose.yml
.github/workflows/deploy.yml
```

---

### Task 1: Project scaffolding, models, config

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/jolt/__init__.py` (empty)
- Create: `src/jolt/config.py`
- Create: `src/jolt/models.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Constants in `models.py`: `PRIORITY_NORMAL="normal"`, `PRIORITY_IMPORTANT="important"`, `STATUS_PENDING="pending"`, `STATUS_DONE="done"`, `STATUS_DROPPED="dropped"`.
  - `Task(id:int, text:str, priority:str, deadline:date|None, created_at:datetime, status:str, last_nagged_at:datetime|None, completed_at:datetime|None)` frozen dataclass.
  - `DailyFocus(focus:Task|None, rescues:list[Task])` frozen dataclass.
  - `config.py`: `STALE_THRESHOLD_DAYS=3`, `QUIET_START_HOUR=6`, `QUIET_END_HOUR=23`, `NAG_HOURS=(9,13,19)`, `DAILY_FOCUS_HOUR=6`, `TIMEZONE="Europe/Paris"`, `MODEL="claude-haiku-4-5-20251001"`; accessors `telegram_token()`, `telegram_chat_id()->int`, `anthropic_api_key()`, `db_path()->str`; `now_paris()->datetime` (tz-aware).

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "jolt"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-telegram-bot>=21",
    "anthropic>=0.40",
    "APScheduler>=3.10",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.6"]

[project.scripts]
jolt = "jolt.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/jolt"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create `.env.example`**

```bash
# Telegram bot token from @BotFather
TELEGRAM_BOT_TOKEN=
# Your personal Telegram chat id (numeric)
TELEGRAM_CHAT_ID=
# Anthropic API key
ANTHROPIC_API_KEY=
# SQLite file path (defaults to data/jolt.db)
JOLT_DB_PATH=data/jolt.db
```

- [ ] **Step 3: Create `src/jolt/__init__.py`** (empty file)

- [ ] **Step 4: Create `src/jolt/models.py`**

```python
from dataclasses import dataclass
from datetime import date, datetime

PRIORITY_NORMAL = "normal"
PRIORITY_IMPORTANT = "important"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_DROPPED = "dropped"


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


@dataclass(frozen=True)
class DailyFocus:
    focus: Task | None
    rescues: list[Task]
```

- [ ] **Step 5: Create `src/jolt/config.py`**

```python
import os
from datetime import datetime
from zoneinfo import ZoneInfo

STALE_THRESHOLD_DAYS = 3
QUIET_START_HOUR = 6   # first hour of the day pings are allowed
QUIET_END_HOUR = 23    # pings stop at 23:00 (hour 23 and later is quiet)
NAG_HOURS = (9, 13, 19)
DAILY_FOCUS_HOUR = 6
TIMEZONE = "Europe/Paris"
MODEL = "claude-haiku-4-5-20251001"


def now_paris() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def telegram_token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def telegram_chat_id() -> int:
    return int(os.environ["TELEGRAM_CHAT_ID"])


def anthropic_api_key() -> str:
    return os.environ["ANTHROPIC_API_KEY"]


def db_path() -> str:
    return os.environ.get("JOLT_DB_PATH", "data/jolt.db")
```

- [ ] **Step 6: Write the test `tests/test_config.py`**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import config
from jolt.models import Task, DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING


def test_constants_present():
    assert config.STALE_THRESHOLD_DAYS == 3
    assert config.QUIET_START_HOUR == 6
    assert config.QUIET_END_HOUR == 23
    assert config.NAG_HOURS == (9, 13, 19)


def test_now_paris_is_timezone_aware():
    assert config.now_paris().tzinfo is not None


def test_task_and_focus_construct():
    t = Task(
        id=1, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 15),
        created_at=datetime(2026, 7, 12, tzinfo=ZoneInfo("Europe/Paris")),
        status=STATUS_PENDING, last_nagged_at=None, completed_at=None,
    )
    focus = DailyFocus(focus=t, rescues=[])
    assert focus.focus.text == "call vet"
    assert focus.rescues == []
```

- [ ] **Step 7: Install and run the test**

Run: `uv sync --extra dev && uv run pytest tests/test_config.py -v`
Expected: 3 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example src/jolt tests/test_config.py uv.lock
git commit -m "feat: scaffold jolt package with models and config"
```

---

### Task 2: SQLite storage (`db.py`)

**Files:**
- Create: `src/jolt/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Task`, status/priority constants from `models.py`.
- Produces:
  - `connect(path:str)->sqlite3.Connection`
  - `init_db(conn)->None`
  - `add_task(conn, text:str, priority:str, deadline:date|None, created_at:datetime)->Task`
  - `get_task(conn, task_id:int)->Task|None`
  - `list_pending(conn)->list[Task]`
  - `list_all(conn)->list[Task]`
  - `complete_task(conn, task_id:int, completed_at:datetime)->Task|None`
  - `drop_task(conn, task_id:int)->Task|None`
  - `mark_nagged(conn, task_id:int, when:datetime)->None`

- [ ] **Step 1: Write the test `tests/test_db.py`**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_DROPPED, STATUS_PENDING

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def test_add_and_get_roundtrip():
    conn = fresh()
    created = datetime(2026, 7, 12, 8, tzinfo=TZ)
    t = db.add_task(conn, "call vet", PRIORITY_IMPORTANT, date(2026, 7, 15), created)
    assert t.id == 1
    got = db.get_task(conn, 1)
    assert got.text == "call vet"
    assert got.priority == PRIORITY_IMPORTANT
    assert got.deadline == date(2026, 7, 15)
    assert got.created_at == created
    assert got.status == STATUS_PENDING


def test_add_without_deadline():
    conn = fresh()
    t = db.add_task(conn, "tidy desk", PRIORITY_NORMAL, None, datetime(2026, 7, 12, tzinfo=TZ))
    assert t.deadline is None


def test_list_pending_excludes_done_and_dropped():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    c = db.add_task(conn, "c", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, b.id, now)
    db.drop_task(conn, c.id)
    pending = db.list_pending(conn)
    assert [t.id for t in pending] == [a.id]
    assert len(db.list_all(conn)) == 3


def test_complete_sets_status_and_timestamp():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "taxes", PRIORITY_NORMAL, None, now)
    done = db.complete_task(conn, t.id, datetime(2026, 7, 13, tzinfo=TZ))
    assert done.status == STATUS_DONE
    assert done.completed_at == datetime(2026, 7, 13, tzinfo=TZ)


def test_complete_missing_returns_none():
    conn = fresh()
    assert db.complete_task(conn, 999, datetime(2026, 7, 12, tzinfo=TZ)) is None


def test_drop_sets_status():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    dropped = db.drop_task(conn, t.id)
    assert dropped.status == STATUS_DROPPED


def test_mark_nagged_updates_timestamp():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    db.mark_nagged(conn, t.id, datetime(2026, 7, 12, 19, tzinfo=TZ))
    assert db.get_task(conn, t.id).last_nagged_at == datetime(2026, 7, 12, 19, tzinfo=TZ)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.db'`.

- [ ] **Step 3: Implement `src/jolt/db.py`**

```python
import sqlite3
from datetime import date, datetime

from .models import STATUS_DONE, STATUS_DROPPED, STATUS_PENDING, Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    priority TEXT NOT NULL,
    deadline TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    last_nagged_at TEXT,
    completed_at TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.commit()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


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
    )


def add_task(conn, text: str, priority: str, deadline: date | None, created_at: datetime) -> Task:
    cur = conn.execute(
        "INSERT INTO tasks (text, priority, deadline, created_at, status) VALUES (?, ?, ?, ?, ?)",
        (text, priority, deadline.isoformat() if deadline else None, created_at.isoformat(), STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, cur.lastrowid)


def get_task(conn, task_id: int) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_pending(conn) -> list[Task]:
    rows = conn.execute("SELECT * FROM tasks WHERE status = ?", (STATUS_PENDING,)).fetchall()
    return [_row_to_task(r) for r in rows]


def list_all(conn) -> list[Task]:
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    return [_row_to_task(r) for r in rows]


def complete_task(conn, task_id: int, completed_at: datetime) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ? AND status = ?",
        (STATUS_DONE, completed_at.isoformat(), task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def drop_task(conn, task_id: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ? AND status = ?",
        (STATUS_DROPPED, task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None


def mark_nagged(conn, task_id: int, when: datetime) -> None:
    conn.execute("UPDATE tasks SET last_nagged_at = ? WHERE id = ?", (when.isoformat(), task_id))
    conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/db.py tests/test_db.py
git commit -m "feat: add sqlite storage layer"
```

---

### Task 3: Selection logic (`selection.py`)

**Files:**
- Create: `src/jolt/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: `Task`, `DailyFocus`, constants from `models.py`; `config.STALE_THRESHOLD_DAYS`, `config.QUIET_START_HOUR`, `config.QUIET_END_HOUR`.
- Produces:
  - `priority_sort_key(task:Task)->tuple`
  - `order_backlog(tasks:list[Task])->list[Task]` (pending only, sorted)
  - `is_stale(task:Task, now:datetime, threshold_days:int=STALE_THRESHOLD_DAYS)->bool`
  - `select_daily_focus(tasks:list[Task], now:datetime)->DailyFocus`
  - `is_quiet_hours(now:datetime, start_hour=QUIET_START_HOUR, end_hour=QUIET_END_HOUR)->bool`

- [ ] **Step 1: Write the test `tests/test_selection.py`**

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jolt import selection
from jolt.models import (
    PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING, Task,
)

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def make(id, *, priority=PRIORITY_NORMAL, deadline=None, created=NOW, status=STATUS_PENDING):
    return Task(id=id, text=f"t{id}", priority=priority, deadline=deadline,
                created_at=created, status=status, last_nagged_at=None, completed_at=None)


def test_important_sorts_before_normal():
    normal = make(1, priority=PRIORITY_NORMAL)
    important = make(2, priority=PRIORITY_IMPORTANT)
    assert [t.id for t in selection.order_backlog([normal, important])] == [2, 1]


def test_earlier_deadline_wins_within_same_priority():
    late = make(1, deadline=date(2026, 8, 1))
    soon = make(2, deadline=date(2026, 7, 14))
    assert [t.id for t in selection.order_backlog([late, soon])] == [2, 1]


def test_older_wins_when_no_deadline():
    newer = make(1, created=datetime(2026, 7, 12, tzinfo=TZ))
    older = make(2, created=datetime(2026, 7, 1, tzinfo=TZ))
    assert [t.id for t in selection.order_backlog([newer, older])] == [2, 1]


def test_order_backlog_excludes_non_pending():
    a = make(1)
    b = make(2, status=STATUS_DONE)
    assert [t.id for t in selection.order_backlog([a, b])] == [1]


def test_is_stale_boundary_exactly_three_days():
    created = NOW - timedelta(days=3)
    assert selection.is_stale(make(1, created=created), NOW) is True
    almost = NOW - timedelta(days=3) + timedelta(minutes=1)
    assert selection.is_stale(make(2, created=almost), NOW) is False


def test_is_stale_false_for_done():
    created = NOW - timedelta(days=10)
    assert selection.is_stale(make(1, created=created, status=STATUS_DONE), NOW) is False


def test_select_daily_focus_picks_top_and_two_rescues():
    focus = make(1, priority=PRIORITY_IMPORTANT, created=NOW)
    old_a = make(2, created=NOW - timedelta(days=5))
    old_b = make(3, created=NOW - timedelta(days=4))
    old_c = make(4, created=NOW - timedelta(days=6))
    fresh = make(5, created=NOW)
    result = selection.select_daily_focus([focus, old_a, old_b, old_c, fresh], NOW)
    assert result.focus.id == 1
    assert len(result.rescues) == 2
    assert 1 not in [t.id for t in result.rescues]  # focus never doubled as a rescue


def test_select_daily_focus_empty():
    result = selection.select_daily_focus([], NOW)
    assert result.focus is None
    assert result.rescues == []


def test_quiet_hours():
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 5, tzinfo=TZ)) is True
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 6, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 22, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 23, tzinfo=TZ)) is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_selection.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.selection'`.

- [ ] **Step 3: Implement `src/jolt/selection.py`**

```python
from datetime import date, datetime, timedelta

from . import config
from .models import DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING, Task


def priority_sort_key(task: Task) -> tuple:
    prio_rank = 0 if task.priority == PRIORITY_IMPORTANT else 1
    deadline_rank = task.deadline or date.max
    return (prio_rank, deadline_rank, task.created_at)


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    return sorted(pending, key=priority_sort_key)


def is_stale(task: Task, now: datetime, threshold_days: int = config.STALE_THRESHOLD_DAYS) -> bool:
    if task.status != STATUS_PENDING:
        return False
    return now - task.created_at >= timedelta(days=threshold_days)


def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    ordered = order_backlog(tasks)
    if not ordered:
        return DailyFocus(focus=None, rescues=[])
    focus = ordered[0]
    rescues = [t for t in ordered[1:] if is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)


def is_quiet_hours(
    now: datetime,
    start_hour: int = config.QUIET_START_HOUR,
    end_hour: int = config.QUIET_END_HOUR,
) -> bool:
    return now.hour < start_hour or now.hour >= end_hour
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_selection.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/selection.py tests/test_selection.py
git commit -m "feat: add pure selection and quiet-hours logic"
```

---

### Task 4: Backlog rendering (`render.py`)

**Files:**
- Create: `src/jolt/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Task`, `PRIORITY_IMPORTANT`; `selection.order_backlog`.
- Produces: `render_backlog(tasks:list[Task])->str`.

- [ ] **Step 1: Write the test `tests/test_render.py`**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import render
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")


def make(id, *, text, priority=PRIORITY_NORMAL, deadline=None):
    return Task(id=id, text=text, priority=priority, deadline=deadline,
                created_at=datetime(2026, 7, 1, tzinfo=TZ), status=STATUS_PENDING,
                last_nagged_at=None, completed_at=None)


def test_empty_backlog_message():
    assert render.render_backlog([]) == "Backlog empty. Nice."


def test_render_orders_and_marks_important_and_deadline():
    tasks = [
        make(1, text="tidy desk"),
        make(2, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 15)),
    ]
    out = render.render_backlog(tasks)
    lines = out.splitlines()
    assert lines[0] == "Backlog:"
    # important task sorts first
    assert lines[1] == "1. ‼️ call vet (due 2026-07-15)"
    assert lines[2] == "2. • tidy desk"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.render'`.

- [ ] **Step 3: Implement `src/jolt/render.py`**

```python
from .models import PRIORITY_IMPORTANT, Task
from .selection import order_backlog


def render_backlog(tasks: list[Task]) -> str:
    ordered = order_backlog(tasks)
    if not ordered:
        return "Backlog empty. Nice."
    lines = ["Backlog:"]
    for i, task in enumerate(ordered, 1):
        mark = "‼️" if task.priority == PRIORITY_IMPORTANT else "•"
        due = f" (due {task.deadline.isoformat()})" if task.deadline else ""
        lines.append(f"{i}. {mark} {task.text}{due}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/render.py tests/test_render.py
git commit -m "feat: add deterministic backlog renderer"
```

---

### Task 5: Intent model and parsing (`llm.py`, pure part)

**Files:**
- Create: `src/jolt/llm.py` (Intent + parse_intent only in this task)
- Test: `tests/test_llm.py` (parse tests in this task)

**Interfaces:**
- Consumes: `date` from stdlib.
- Produces:
  - `Intent(action:str, text:str|None, priority:str|None, deadline:date|None, task_id:int|None, reply:str|None)` frozen dataclass with defaults `None`.
  - `parse_intent(tool_input:dict)->Intent` (maps Claude tool input dict to Intent; parses `deadline` ISO string to `date`).

- [ ] **Step 1: Write the test `tests/test_llm.py`**

```python
from datetime import date

from jolt import llm
from jolt.models import PRIORITY_IMPORTANT


def test_parse_add_with_deadline_and_priority():
    intent = llm.parse_intent({
        "action": "add", "text": "call vet",
        "priority": "important", "deadline": "2026-07-15",
    })
    assert intent.action == "add"
    assert intent.text == "call vet"
    assert intent.priority == PRIORITY_IMPORTANT
    assert intent.deadline == date(2026, 7, 15)


def test_parse_add_without_optional_fields():
    intent = llm.parse_intent({"action": "add", "text": "tidy desk"})
    assert intent.deadline is None
    assert intent.priority is None


def test_parse_complete_with_task_id():
    intent = llm.parse_intent({"action": "complete", "task_id": 3})
    assert intent.action == "complete"
    assert intent.task_id == 3


def test_parse_answer_carries_reply():
    intent = llm.parse_intent({"action": "answer", "reply": "Focus on the taxes today."})
    assert intent.reply == "Focus on the taxes today."
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.llm'`.

- [ ] **Step 3: Implement the pure part of `src/jolt/llm.py`**

```python
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Intent:
    action: str
    text: str | None = None
    priority: str | None = None
    deadline: date | None = None
    task_id: int | None = None
    reply: str | None = None


def parse_intent(tool_input: dict) -> Intent:
    deadline = tool_input.get("deadline")
    return Intent(
        action=tool_input["action"],
        text=tool_input.get("text"),
        priority=tool_input.get("priority"),
        deadline=date.fromisoformat(deadline) if deadline else None,
        task_id=tool_input.get("task_id"),
        reply=tool_input.get("reply"),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_llm.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat: add intent model and parser"
```

---

### Task 6: Claude client wrappers (`llm.py`, side-effect part)

**Files:**
- Modify: `src/jolt/llm.py` (add tool schema + three client functions)
- Test: `tests/test_llm.py` (add wrapper tests with a fake client)

**Interfaces:**
- Consumes: `parse_intent`, `Intent`; `config.MODEL`; `Task` list for context.
- Produces:
  - `interpret_message(message:str, tasks:list[Task], client)->Intent`
  - `write_focus(focus:DailyFocus, now:datetime, client)->str`
  - `write_nag(task:Task, now:datetime, client)->str`
  - `client` is any object exposing `client.messages.create(...)` returning an object with a `.content` list of blocks. Tool-use blocks have `.type=="tool_use"`, `.name`, `.input`; text blocks have `.type=="text"`, `.text`.

- [ ] **Step 1: Add the wrapper tests to `tests/test_llm.py`**

```python
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

from jolt.models import DailyFocus, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def _task(id=1):
    return Task(id=id, text="taxes", priority=PRIORITY_NORMAL, deadline=None,
                created_at=datetime(2026, 7, 1, tzinfo=TZ), status=STATUS_PENDING,
                last_nagged_at=None, completed_at=None)


def test_interpret_message_returns_parsed_intent():
    tool_block = SimpleNamespace(type="tool_use", name="record_intent",
                                 input={"action": "add", "text": "call vet"})
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    intent = llm.interpret_message("remind me to call vet", [_task()], client)
    assert intent.action == "add"
    assert intent.text == "call vet"
    # the task list was passed into the prompt so Claude can reference ids
    assert "taxes" in str(client.messages.calls[0])


def test_write_focus_returns_text():
    text_block = SimpleNamespace(type="text", text="One thing today: taxes.")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    focus = DailyFocus(focus=_task(), rescues=[])
    out = llm.write_focus(focus, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    assert out == "One thing today: taxes."


def test_write_nag_returns_text():
    text_block = SimpleNamespace(type="text", text="Still the taxes. Two minutes. Go.")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    out = llm.write_nag(_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    assert "taxes" in out
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -k "interpret or write_" -v`
Expected: FAIL, `AttributeError: module 'jolt.llm' has no attribute 'interpret_message'`.

- [ ] **Step 3: Extend `src/jolt/llm.py` (append below `parse_intent`)**

```python
from datetime import datetime

from . import config
from .models import DailyFocus, Task

_TOOL = {
    "name": "record_intent",
    "description": "Record what the user's Telegram message means for their task list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "list", "answer"],
                "description": "add a task, complete/drop an existing one, list the backlog, or answer a question / reply to the user.",
            },
            "text": {"type": "string", "description": "Task text, for action=add."},
            "priority": {"type": "string", "enum": ["normal", "important"]},
            "deadline": {"type": "string", "description": "ISO date YYYY-MM-DD, if the user gave one."},
            "task_id": {"type": "integer", "description": "The id of the existing task, for complete/drop."},
            "reply": {"type": "string", "description": "For action=answer: the exact message to send back to the user."},
        },
        "required": ["action"],
    },
}


def _task_lines(tasks: list[Task]) -> str:
    pending = [t for t in tasks if t.status == "pending"]
    if not pending:
        return "(backlog is empty)"
    return "\n".join(
        f"- id={t.id}: {t.text}"
        + (f" [important]" if t.priority == "important" else "")
        + (f" (due {t.deadline.isoformat()})" if t.deadline else "")
        for t in pending
    )


def interpret_message(message: str, tasks: list[Task], client) -> Intent:
    system = (
        "You are Jolt, a personal accountability bot. Read the user's message and record "
        "what it means by calling record_intent exactly once. To complete or drop a task, "
        "pick the matching task_id from the current backlog. For a question or a blocker "
        "conversation, use action=answer and write a short, plain reply (no cheerleading, "
        "no em dashes). Current backlog:\n" + _task_lines(tasks)
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=400,
        system=system,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_intent"},
        messages=[{"role": "user", "content": message}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return parse_intent(block.input)
    return Intent(action="answer", reply="Sorry, I did not catch that. Try again?")


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def write_focus(focus: DailyFocus, now: datetime, client) -> str:
    if focus.focus is None:
        return "Nothing on the list today. Enjoy it."
    age = (now - focus.focus.created_at).days
    rescues = "; ".join(f"{t.text} ({(now - t.created_at).days}d old)" for t in focus.rescues) or "none"
    system = (
        "You are Jolt. Write a short morning message (2 to 4 lines, no em dashes). Name the "
        "one focus task as the single thing to do today. If there are rescue tasks that have "
        "gone stale, mention them and ask what is blocking them. Be plain and direct, never "
        "guilt-tripping."
    )
    user = f"Focus task: {focus.focus.text} ({age}d old). Rescues: {rescues}."
    return _text_of(client.messages.create(
        model=config.MODEL, max_tokens=300, system=system,
        messages=[{"role": "user", "content": user}],
    ))


def write_nag(task: Task, now: datetime, client) -> str:
    age = (now - task.created_at).days
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines, no em dashes) about the task below. "
        "The older it is and the later in the day, the more direct and blunt you get: gentle in "
        "the morning, pointed by evening. If it is several days old, first ask what is actually "
        "blocking it before pushing. Never guilt-trip."
    )
    user = f"Task: {task.text}. Age: {age} days. Current hour: {now.hour}."
    return _text_of(client.messages.create(
        model=config.MODEL, max_tokens=200, system=system,
        messages=[{"role": "user", "content": user}],
    ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: all `test_llm.py` tests PASS (parse tests from Task 5 plus the 3 wrapper tests).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "feat: add claude interpret and prose wrappers"
```

---

### Task 7: Orchestrator (`orchestrator.py`)

**Files:**
- Create: `src/jolt/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `db` module, `render.render_backlog`, `Intent`, `PRIORITY_NORMAL`.
- Produces: `apply_intent(conn, intent:Intent, now:datetime)->str` (returns the reply string; performs the DB write for add/complete/drop; reads for list; passes through `reply` for answer/unknown).

- [ ] **Step 1: Write the test `tests/test_orchestrator.py`**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db, orchestrator
from jolt.llm import Intent

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def test_add_intent_creates_task_and_confirms():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="add", text="call vet", deadline=date(2026, 7, 15)), NOW)
    assert "call vet" in reply
    assert "2026-07-15" in reply
    assert len(db.list_pending(conn)) == 1


def test_complete_intent_marks_done_with_plain_ack():
    conn = fresh()
    t = db.add_task(conn, "taxes", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="complete", task_id=t.id), NOW)
    assert reply == "Done, nice."
    assert db.get_task(conn, t.id).status == "done"


def test_complete_unknown_id_is_graceful():
    conn = fresh()
    reply = orchestrator.apply_intent(conn, Intent(action="complete", task_id=999), NOW)
    assert reply == "Couldn't find that one."


def test_drop_intent():
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="drop", task_id=t.id), NOW)
    assert reply == "Dropped."
    assert db.get_task(conn, t.id).status == "dropped"


def test_list_intent_renders_backlog():
    conn = fresh()
    db.add_task(conn, "call vet", "important", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="list"), NOW)
    assert "call vet" in reply
    assert reply.startswith("Backlog:")


def test_answer_intent_passes_reply_through():
    conn = fresh()
    reply = orchestrator.apply_intent(conn, Intent(action="answer", reply="Do the taxes first."), NOW)
    assert reply == "Do the taxes first."
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.orchestrator'`.

- [ ] **Step 3: Implement `src/jolt/orchestrator.py`**

```python
from datetime import datetime

from . import db
from .llm import Intent
from .models import PRIORITY_NORMAL
from .render import render_backlog


def apply_intent(conn, intent: Intent, now: datetime) -> str:
    if intent.action == "add":
        task = db.add_task(conn, intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now)
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack
    if intent.action == "complete":
        task = db.complete_task(conn, intent.task_id, now)
        return "Done, nice." if task else "Couldn't find that one."
    if intent.action == "drop":
        task = db.drop_task(conn, intent.task_id)
        return "Dropped." if task else "Couldn't find that one."
    if intent.action == "list":
        return render_backlog(db.list_all(conn))
    return intent.reply or "Not sure what you mean. Try rephrasing?"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add intent orchestrator"
```

---

### Task 8: Telegram handler (`bot.py`)

**Files:**
- Create: `src/jolt/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `db.list_all`, `llm.interpret_message`, `orchestrator.apply_intent`, `config.now_paris`.
- Produces: `async handle_message(update, context)->None`. Reads `context.bot_data["conn"]` and `context.bot_data["client"]`; replies via `await update.message.reply_text(...)`. Ignores messages from any chat id other than `context.bot_data["chat_id"]`.

- [ ] **Step 1: Write the test `tests/test_bot.py`**

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jolt import bot, db
from jolt.llm import Intent


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def make_update(text, chat_id=42):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat_id))


def make_context(conn, intent, chat_id=42):
    # client is unused because interpret_message is monkeypatched in the test
    return SimpleNamespace(bot_data={"conn": conn, "client": object(), "chat_id": chat_id})


def test_handle_message_adds_task_and_replies(monkeypatch):
    conn = fresh()
    monkeypatch.setattr(bot.llm, "interpret_message",
                        lambda msg, tasks, client: Intent(action="add", text="call vet"))
    update = make_update("remind me to call vet")
    context = make_context(conn, None)
    asyncio.run(bot.handle_message(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "call vet" in update.message.reply_text.call_args.args[0]
    assert len(db.list_pending(conn)) == 1


def test_handle_message_ignores_foreign_chat(monkeypatch):
    conn = fresh()
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(bot.llm, "interpret_message", spy)
    update = make_update("hello", chat_id=999)
    context = make_context(conn, None, chat_id=42)
    asyncio.run(bot.handle_message(update, context))
    assert called is False
    update.message.reply_text.assert_not_awaited()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.bot'`.

- [ ] **Step 3: Implement `src/jolt/bot.py`**

```python
from . import config, db, llm, orchestrator


async def handle_message(update, context) -> None:
    chat_id = context.bot_data["chat_id"]
    if update.effective_chat.id != chat_id:
        return
    conn = context.bot_data["conn"]
    client = context.bot_data["client"]
    now = config.now_paris()
    tasks = db.list_all(conn)
    intent = llm.interpret_message(update.message.text, tasks, client)
    reply = orchestrator.apply_intent(conn, intent, now)
    await update.message.reply_text(reply)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_bot.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/bot.py tests/test_bot.py
git commit -m "feat: add telegram message handler"
```

---

### Task 9: Scheduler jobs (`scheduler.py`)

**Files:**
- Create: `src/jolt/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `db`, `selection.select_daily_focus`, `selection.is_quiet_hours`, `render.render_backlog`, `llm.write_focus`, `llm.write_nag`.
- Produces:
  - `send_daily_focus(conn, send, client, now)->None` where `send` is `Callable[[str], None]`. Sends focus prose then the mechanical backlog dump in one message.
  - `send_nags(conn, send, client, now)->None`. No-op during quiet hours or when there is no focus task; otherwise sends one nag and records `mark_nagged`.

- [ ] **Step 1: Write the test `tests/test_scheduler.py`**

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jolt import db, scheduler

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


class FakeClient:
    """Records prose calls; returns canned text so no network is hit."""
    def __init__(self):
        self.messages = self
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="canned prose")])


def collector():
    sent = []
    return sent, lambda msg: sent.append(msg)


def test_daily_focus_sends_prose_then_backlog():
    conn = fresh()
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    db.add_task(conn, "call vet", "important", None, now)
    sent, send = collector()
    scheduler.send_daily_focus(conn, send, FakeClient(), now)
    assert len(sent) == 1
    assert "canned prose" in sent[0]
    assert "Backlog:" in sent[0]
    assert "call vet" in sent[0]


def test_nags_skipped_during_quiet_hours():
    conn = fresh()
    now = datetime(2026, 7, 12, 5, tzinfo=TZ)  # before 06:00
    db.add_task(conn, "taxes", "normal", None, now)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_nags_skipped_when_no_pending_task():
    conn = fresh()
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_nag_sends_and_marks_nagged():
    conn = fresh()
    created = datetime(2026, 7, 8, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    t = db.add_task(conn, "taxes", "normal", None, created)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert len(sent) == 1
    assert db.get_task(conn, t.id).last_nagged_at == now
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jolt.scheduler'`.

- [ ] **Step 3: Implement `src/jolt/scheduler.py`**

```python
from datetime import datetime

from . import db, llm
from .render import render_backlog
from .selection import is_quiet_hours, select_daily_focus


def send_daily_focus(conn, send, client, now: datetime) -> None:
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    prose = llm.write_focus(focus, now, client)
    backlog = render_backlog(tasks)
    send(f"{prose}\n\n{backlog}")


def send_nags(conn, send, client, now: datetime) -> None:
    if is_quiet_hours(now):
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    if focus.focus is None:
        return
    send(llm.write_nag(focus.focus, now, client))
    db.mark_nagged(conn, focus.focus.id, now)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/scheduler.py tests/test_scheduler.py
git commit -m "feat: add daily focus and nag scheduler jobs"
```

---

### Task 10: Entry point, Docker, deploy

**Files:**
- Create: `src/jolt/main.py`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: everything above; `config` accessors; `anthropic.Anthropic`; `telegram.ext.Application`; `apscheduler.schedulers.background.BackgroundScheduler`.
- Produces: `main()->None` console-script entry (wired in `pyproject.toml` Task 1).

- [ ] **Step 1: Implement `src/jolt/main.py`**

```python
import os

from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application, MessageHandler, filters

from . import bot, config, db, scheduler


def _make_send(application, chat_id):
    async def _send_async(text):
        await application.bot.send_message(chat_id=chat_id, text=text)

    def send(text):
        application.create_task(_send_async(text))

    return send


def main() -> None:
    os.makedirs(os.path.dirname(config.db_path()) or ".", exist_ok=True)
    conn = db.connect(config.db_path())
    db.init_db(conn)
    client = Anthropic(api_key=config.anthropic_api_key())
    chat_id = config.telegram_chat_id()

    application = Application.builder().token(config.telegram_token()).build()
    application.bot_data.update({"conn": conn, "client": client, "chat_id": chat_id})
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))

    send = _make_send(application, chat_id)
    sched = BackgroundScheduler(timezone=config.TIMEZONE)
    sched.add_job(
        lambda: scheduler.send_daily_focus(conn, send, client, config.now_paris()),
        CronTrigger(hour=config.DAILY_FOCUS_HOUR, minute=0),
    )
    for nag_hour in config.NAG_HOURS:
        sched.add_job(
            lambda: scheduler.send_nags(conn, send, client, config.now_paris()),
            CronTrigger(hour=nag_hour, minute=0),
        )
    sched.start()

    application.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the app imports and wires without a network**

Run: `uv run python -c "import jolt.main; print('import ok')"`
Expected: prints `import ok` (no exception). This proves all modules import and the entry wiring is syntactically sound. Do NOT run `main()` here (it needs real tokens and would start polling).

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -v`
Expected: every test from Tasks 1 to 9 PASSES.

- [ ] **Step 4: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "jolt"]
```

- [ ] **Step 5: Create `docker-compose.yml`**

```yaml
services:
  jolt:
    build: .
    container_name: jolt
    restart: unless-stopped
    env_file: .env
    environment:
      JOLT_DB_PATH: /app/data/jolt.db
    volumes:
      - ./data:/app/data
```

- [ ] **Step 6: Create `.github/workflows/deploy.yml`** (mirrors the `fitness-data` self-hosted-runner auto-deploy)

```yaml
name: deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - name: Build and restart the container
        run: docker compose up -d --build
```

> The self-hosted runner and the `.env` file live on `jarvis`, exactly like `fitness-data` and `gh-runner-fitness-data`. Register a runner for this repo (`gautierlp/jolt`) and place `.env` in the service directory before the first push-to-deploy. This mirrors the pattern documented in the homelab repo; no secrets are committed.

- [ ] **Step 7: Commit**

```bash
git add src/jolt/main.py Dockerfile docker-compose.yml .github/workflows/deploy.yml
git commit -m "feat: add entry point, docker, and deploy workflow"
```

---

## Self-Review

**Spec coverage:**
- Capture (frictionless, ask-once-else-no-urgency) → Task 6 `interpret_message` prompt + Task 7 add path. (The "ask once" nuance lives in Claude's prompt via `answer`; no schema needed.) ✓
- Daily focus at 06:00 = Claude prose + programmatic backlog dump → Task 9 `send_daily_focus`, Task 4 renderer, Task 10 cron at `DAILY_FOCUS_HOUR`. ✓
- Nags morning/midday/evening, blunter when older/later → Task 9 `send_nags`, Task 6 `write_nag` prompt, Task 10 cron at `NAG_HOURS`. ✓
- Quiet hours 06:00 to 23:00 → Task 3 `is_quiet_hours`, enforced in Task 9. ✓
- Avoidance hunter: pure age, 3-day threshold, curious-then-louder → Task 3 `is_stale` + rescues in `select_daily_focus`; the curious/escalating tone is carried by the `write_focus`/`write_nag` prompts (age passed in). ✓
- Completion, plain ack → Task 7 returns "Done, nice." ✓
- Natural language via Claude; backlog dump never via Claude → Tasks 5/6 (Claude) vs Task 4 (plain code); wired so `render_backlog` output is concatenated, never sent to `client`. ✓
- SQLite single `tasks` table with the specified columns → Task 2. ✓
- Deploy: Docker on jarvis, git auto-deploy, SQLite in bind-mounted `./data` (backed up by restic) → Task 10. ✓
- Testing: pure logic TDD, Telegram/Claude mocked → every task. ✓

**Placeholder scan:** No TBD/TODO; every code and test step contains complete, runnable content. The only prose note (Task 10 Step 6) documents runner registration, an ops action outside the code, not a code placeholder.

**Type consistency:** `Task`, `DailyFocus`, `Intent` field names are identical across Tasks 1, 5, 6, 7, 9. `apply_intent(conn, intent, now)` (Task 7) matches its call in `bot.handle_message` (Task 8). `send_daily_focus`/`send_nags` signatures (Task 9) match their cron wiring (Task 10). `write_focus(focus, now, client)` / `write_nag(task, now, client)` signatures match between Task 6 definitions and Task 9 calls. `interpret_message(message, tasks, client)` matches between Task 6 and Task 8. ✓
