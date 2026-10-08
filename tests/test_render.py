from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from woodpecker import render
from woodpecker.models import STATUS_PENDING, Task
from woodpecker.vault import VaultTask

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 4, 12, tzinfo=TZ)  # a Saturday
TODAY = NOW.date()
FRESH = datetime(2026, 7, 3, tzinfo=TZ)  # 1 day old at NOW


def make(id, *, text, priority=0, deadline=None, created_at=FRESH, position=0):
    return Task(
        id=id,
        text=text,
        priority=priority,
        deadline=deadline,
        created_at=created_at,
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        position=position,
    )


def _task(id, *, priority=0, deadline=None, created_at=FRESH, position=0):
    # morning_shown/render_matters tests care about priority/deadline, not the text.
    return make(
        id,
        text=f"task {id}",
        priority=priority,
        deadline=deadline,
        created_at=created_at,
        position=position,
    )


def task_line(task):
    # The task's own line is the last line of a single-task render (after header + group head).
    return render.render_backlog([task], NOW).splitlines()[-1]


def test_empty_backlog_message():
    assert render.render_backlog([], NOW) == "Backlog empty. Nice."


def test_header_shows_task_count():
    tasks = [make(1, text="a"), make(2, text="b")]
    assert render.render_backlog(tasks, NOW).splitlines()[0] == "📋 2 tasks"


def test_header_singular_for_one_task():
    assert render.render_backlog([make(1, text="a")], NOW).splitlines()[0] == "📋 1 task"


def test_lines_are_numbered_by_display_position_not_id():
    # The visible number is the task's position in the list, not its stable db id, so a
    # single task shows as "1." whatever its id. The id is a hidden internal handle.
    task = make(7, text="call vet", priority=4, deadline=date(2026, 12, 1))
    assert task_line(task).startswith("1. ")


def test_numbers_are_sequential_regardless_of_ids():
    # Three tasks with gappy, out-of-order ids still number 1, 2, 3 top-to-bottom.
    tasks = [
        make(7, text="a", deadline=TODAY),
        make(26, text="b", deadline=TODAY),
        make(4, text="c", deadline=TODAY),
    ]
    lines = render.render_backlog(tasks, NOW).splitlines()
    numbered = [line for line in lines if line[:1].isdigit()]
    assert numbered == ["1. a", "2. b", "3. c"]


def test_numbers_span_groups():
    # Numbering runs continuously down the whole message: across group boundaries, so
    # every visible line has a unique reference number.
    tasks = [
        make(10, text="today thing", deadline=TODAY),
        make(20, text="next week", deadline=date(2026, 7, 8)),  # Wednesday
    ]
    lines = render.render_backlog(tasks, NOW).splitlines()
    assert "1. today thing" in lines
    assert "2. next week (Wed)" in lines


def test_flat_numbering_follows_group_order():
    # Two no-deadline pending tasks render as "1." and "2." with no indentation.
    tasks = [
        make(1, text="a", position=0),
        make(2, text="b", position=1),
    ]
    out = render.render_backlog(tasks, NOW)
    assert "1. a" in out.splitlines()
    assert "2. b" in out.splitlines()
    assert "↳" not in out


def test_groups_by_deadline_bucket():
    tasks = [
        make(1, text="overdue thing", deadline=date(2026, 7, 1)),
        make(2, text="today thing", deadline=TODAY),
        make(3, text="tomorrow thing", deadline=date(2026, 7, 5)),
        make(4, text="this week thing", deadline=date(2026, 7, 8)),
        make(5, text="later thing", deadline=date(2026, 8, 1)),
        make(6, text="someday thing"),
    ]
    out = render.render_backlog(tasks, NOW)
    heads = [
        line
        for line in out.splitlines()
        if line and not line[0].isdigit() and not line.startswith("📋")
    ]
    assert heads == [
        "🔴 OVERDUE",
        "🔥 TODAY",
        "📅 TOMORROW",
        "🗓 THIS WEEK",
        "📆 LATER",
        "📥 NO DEADLINE",
    ]


def test_empty_buckets_are_omitted():
    tasks = [make(1, text="today thing", deadline=TODAY)]
    out = render.render_backlog(tasks, NOW)
    assert "🔴 OVERDUE" not in out
    assert "📥 NO DEADLINE" not in out
    assert "🔥 TODAY" in out


def test_overdue_line_shows_days_late():
    task = make(1, text="pay tax", deadline=date(2026, 7, 1))  # 3 days before NOW
    assert task_line(task) == "1. pay tax (3d overdue)"


def test_today_and_tomorrow_lines_have_no_date_suffix():
    assert task_line(make(1, text="a", deadline=TODAY)) == "1. a"
    assert task_line(make(1, text="b", deadline=date(2026, 7, 5))) == "1. b"


def test_this_week_line_shows_weekday():
    # 2026-07-08 is a Wednesday
    assert task_line(make(1, text="a", deadline=date(2026, 7, 8))) == "1. a (Wed)"


def test_later_line_shows_month_and_day():
    assert task_line(make(1, text="a", deadline=date(2026, 8, 1))) == "1. a (Aug 01)"


def test_no_deadline_line_has_no_date_suffix():
    assert task_line(make(1, text="a")) == "1. a"


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
    text = render.render_matters([_task(id=1, priority=0, deadline=NOW.date())], NOW)
    assert "+ 1 more" in text


VAULT_TODAY = date(2026, 10, 8)


def test_legends():
    assert render.FROG_LEGEND == "d done · o on it · t tomorrow · x drop"
    assert render.REFRAME_LEGEND == "s smaller step · n not mine to do · x drop"


def test_vault_block_exact_text():
    tasks = [
        VaultTask(text="call the bank", note="Money", due=date(2026, 10, 6), now=False),
        VaultTask(text="file the return", note="Taxes", due=date(2026, 10, 19), now=True),
        VaultTask(text="book the service", note="Car", due=None, now=True),
    ]
    assert render.render_vault_block(tasks, VAULT_TODAY) == (
        "From the vault:\n"
        "• call the bank (overdue, Money)\n"
        "• file the return (📅 2026-10-19, Taxes)\n"
        "• book the service (Car)\n"
        "Tick these in Obsidian."
    )


def test_vault_block_due_today_shows_the_date():
    tasks = [VaultTask(text="pay rent", note="Home", due=VAULT_TODAY, now=False)]
    assert "• pay rent (📅 2026-10-08, Home)" in render.render_vault_block(tasks, VAULT_TODAY)


def test_vault_block_empty_is_empty():
    assert render.render_vault_block([], VAULT_TODAY) == ""
    assert render.render_vault_due([], VAULT_TODAY) == ""


def test_vault_due_exact_text():
    tasks = [VaultTask(text="pay rent", note="Home", due=VAULT_TODAY, now=False)]
    assert render.render_vault_due(tasks, VAULT_TODAY) == (
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
