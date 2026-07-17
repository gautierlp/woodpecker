import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from jolt import vikunja
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING


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
    assert (
        vikunja.clean_description("call the accountant <!-- mdone:estimate=1800 -->")
        == "call the accountant"
    )
    assert vikunja.clean_description(None) == ""


def test_clean_description_leaves_a_malformed_sentinel_in_place():
    # A non-numeric estimate is not a valid sentinel, so it is left as visible text.
    assert vikunja.clean_description("<!-- mdone:estimate=abc -->") == "<!-- mdone:estimate=abc -->"


def _raw(**over):
    base = {
        "id": 7,
        "title": "Call the vet",
        "priority": 0,
        "due_date": "0001-01-01T00:00:00Z",  # Vikunja's "unset" sentinel
        "created": "2026-07-10T08:00:00Z",
        "done": False,
        "done_at": "0001-01-01T00:00:00Z",
        "position": 12,
    }
    base.update(over)
    return base


def test_maps_basic_open_task():
    t = vikunja.vikunja_to_task(_raw())
    assert t.id == 7
    assert t.text == "Call the vet"
    assert t.priority == 0
    assert t.status == STATUS_PENDING
    assert t.deadline is None
    assert t.position == 12
    assert t.last_nagged_at is None
    assert t.completed_at is None


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


def test_due_date_round_trips_to_local_date():
    t = vikunja.vikunja_to_task(_raw(due_date="2026-07-20T21:59:00Z"))  # 23:59 Paris
    assert t.deadline == date(2026, 7, 20)


def test_done_task_maps_status_and_completed_at():
    t = vikunja.vikunja_to_task(_raw(done=True, done_at="2026-07-11T09:30:00Z"))
    assert t.status == STATUS_DONE
    assert t.completed_at == datetime(2026, 7, 11, 9, 30, tzinfo=ZoneInfo("UTC"))


def test_create_payload_important_with_deadline():
    p = vikunja.task_create_payload("Pay taxes", PRIORITY_IMPORTANT, date(2026, 7, 31))
    assert p["title"] == "Pay taxes"
    assert p["priority"] == 4
    # End of day Paris (23:59:59) expressed in UTC.
    assert p["due_date"] == "2026-07-31T21:59:59Z"


def test_create_payload_no_deadline_omits_field():
    p = vikunja.task_create_payload("tidy desk", PRIORITY_NORMAL, None)
    assert p == {"title": "tidy desk", "priority": 0}


def _client(handler):
    transport = httpx.MockTransport(handler)
    c = vikunja.VikunjaClient("http://vk", "tok", project_id=3)
    c._http = httpx.Client(transport=transport, base_url="http://vk")
    return c


def test_create_task_posts_payload_and_returns_task():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 5, "title": "Pay taxes", "priority": 4})

    c = _client(handler)
    task = c.create_task("Pay taxes", PRIORITY_IMPORTANT, None)
    assert seen["method"] == "PUT"
    assert "/api/v1/projects/3/tasks" in seen["url"]
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["priority"] == 4
    assert task.id == 5


def test_create_task_raises_on_404():
    c = _client(lambda r: httpx.Response(404, json={"message": "project not found"}))
    with pytest.raises(vikunja.VikunjaError):
        c.create_task("Pay taxes", PRIORITY_NORMAL, None)


_GANTT_VIEW_ID = 10
_LIST_VIEW_ID = 11


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
        2: [
            {"id": 1, "title": "a", "priority": 0, "position": 1, "project_id": 2},
            {"id": 2, "title": "b", "priority": 4, "position": 2, "project_id": 2},
        ],
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


def test_list_open_skips_a_project_whose_read_fails_but_keeps_the_others():
    # Project 2's list view is missing (404), which makes its read fail. Project 3's
    # read must still succeed and its tasks must still come back; the failure must not
    # raise out of list_open.
    projects = [
        {"id": 2, "title": "Broken", "is_archived": False},
        {"id": 3, "title": "Personal", "is_archived": False},
    ]

    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=projects)
        if path.endswith("/projects/2/views"):
            return httpx.Response(404, json={"message": "not found"})
        if path.endswith("/projects/3/views"):
            return httpx.Response(200, json=[{"id": 6, "view_kind": "list"}])
        if "/views/6/tasks" in path:
            page = int(request.url.params.get("page", "1"))
            tasks = [{"id": 3, "title": "c", "priority": 0, "position": 1, "project_id": 3}]
            return httpx.Response(200, json=[] if page > 1 else tasks)
        raise AssertionError(f"unexpected path {path}")

    tasks = _client(handler).list_open()
    assert [t.id for t in tasks] == [3]


def test_list_open_tolerates_a_project_with_no_open_tasks():
    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=[{"id": 2, "title": "Backlog", "is_archived": False}])
        if path.endswith("/projects/2/views"):
            return httpx.Response(200, json=[{"id": 5, "view_kind": "list"}])
        return httpx.Response(200, json=[])  # empty first page

    assert _client(handler).list_open() == []


def test_get_task_returns_none_on_404():
    c = _client(lambda r: httpx.Response(404, json={"message": "not found"}))
    assert c.get_task(99) is None


def test_mark_done_does_read_modify_write():
    seen = {"requests": []}
    existing = {
        "id": 5,
        "title": "Call the vet",
        "priority": 4,
        "due_date": "2026-07-20T21:59:00Z",
        "done": False,
    }

    def handler(request):
        if request.method == "GET":
            seen["requests"].append(("GET", str(request.url)))
            return httpx.Response(200, json=existing)
        seen["requests"].append(("POST", str(request.url)))
        seen["post_body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": 5,
                "title": "Call the vet",
                "priority": 4,
                "due_date": "2026-07-20T21:59:00Z",
                "done": True,
                "done_at": "2026-07-16T09:00:00Z",
            },
        )

    c = _client(handler)
    task = c.mark_done(5)
    assert [m for m, _ in seen["requests"]] == ["GET", "POST"]
    assert all("/api/v1/tasks/5" in url for _, url in seen["requests"])
    # The full object is round-tripped, not a bare {"done": true}: title/priority survive.
    assert seen["post_body"]["title"] == "Call the vet"
    assert seen["post_body"]["priority"] == 4
    assert seen["post_body"]["done"] is True
    assert task.completed_at is not None


def test_mark_done_returns_none_when_task_missing():
    c = _client(lambda r: httpx.Response(404, json={"message": "not found"}))
    assert c.mark_done(5) is None


def test_delete_task_true_on_success_false_on_404():
    assert _client(lambda r: httpx.Response(200, json={})).delete_task(5) is True
    assert _client(lambda r: httpx.Response(404, json={})).delete_task(5) is False


_SINGLE_PROJECT = [{"id": 3, "title": "P", "is_archived": False}]


def test_server_error_raises():
    # An error enumerating projects at all (not a single project's read) is a total
    # failure: list_open must still raise rather than silently return an empty list.
    c = _client(lambda r: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(vikunja.VikunjaError):
        c.list_open()


def test_list_open_paginates_past_a_server_enforced_page_cap():
    # Live Vikunja caps page size at its own max_items_per_page (50 by default) no
    # matter what per_page we request. The old "stop when the page is shorter than
    # our requested per_page" logic would stop after page 1 here and silently
    # truncate the backlog. The only correct stop condition is an empty page.
    server_page_cap = 2
    total_items = 5  # spans 3 server-capped pages (2, 2, 1) before the empty page

    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=_SINGLE_PROJECT)
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": _LIST_VIEW_ID, "view_kind": "list"}])
        page = int(request.url.params.get("page", "1"))
        start = (page - 1) * server_page_cap + 1
        end = min(start + server_page_cap, total_items + 1)
        ids = list(range(start, end)) if start <= total_items else []
        items = [{"id": i, "title": f"t{i}", "priority": 0, "position": i} for i in ids]
        return httpx.Response(200, json=items)

    c = _client(handler)
    tasks = c.list_open()
    assert [t.id for t in tasks] == list(range(1, total_items + 1))


def test_list_open_falls_back_to_first_view_when_no_list_kind():
    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1/projects"):
            return httpx.Response(200, json=_SINGLE_PROJECT)
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": _GANTT_VIEW_ID, "view_kind": "gantt"}])
        assert f"/views/{_GANTT_VIEW_ID}/tasks" in path
        page = int(request.url.params.get("page", "1"))
        if page > 1:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"id": 1, "title": "a", "priority": 0, "position": 1}])

    c = _client(handler)
    tasks = c.list_open()
    assert [t.id for t in tasks] == [1]


def test_list_view_id_raises_when_views_empty():
    # A project with no views at all is still a hard failure of _list_view_id itself;
    # list_open just no longer lets one project's failure raise past the loop (see
    # test_list_open_skips_a_project_whose_read_fails_but_keeps_the_others).
    c = _client(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(vikunja.VikunjaError):
        c._list_view_id(3)


def test_list_open_raises_on_404():
    # A 404 enumerating projects at all (not a single project's read) means a
    # misconfigured Vikunja URL or token: it must raise rather than silently return an
    # empty list.
    def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    c = _client(handler)
    with pytest.raises(vikunja.VikunjaError):
        c.list_open()


def test_transport_error_surfaces_as_vikunja_error():
    # A genuinely unreachable Vikunja (container down, DNS failure, timeout) makes
    # httpx raise a transport error, not an HTTP-status error. That must also
    # surface as VikunjaError, so callers only ever need to catch one type.
    def handler(request):
        raise httpx.ConnectError("boom")

    c = _client(handler)
    with pytest.raises(vikunja.VikunjaError):
        c.list_open()
    with pytest.raises(vikunja.VikunjaError):
        c.get_task(1)
