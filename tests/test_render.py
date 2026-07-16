from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import render
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 4, 12, tzinfo=TZ)  # a Saturday
TODAY = NOW.date()
FRESH = datetime(2026, 7, 3, tzinfo=TZ)  # 1 day old at NOW


def make(id, *, text, priority=PRIORITY_NORMAL, deadline=None, created_at=FRESH, position=0):
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
    task = make(7, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 12, 1))
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
