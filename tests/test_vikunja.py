import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from jolt import vikunja
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING


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
    assert t.priority == PRIORITY_NORMAL
    assert t.status == STATUS_PENDING
    assert t.deadline is None
    assert t.position == 12
    assert t.last_nagged_at is None
    assert t.completed_at is None


def test_high_priority_maps_to_important():
    assert vikunja.vikunja_to_task(_raw(priority=4)).priority == PRIORITY_IMPORTANT
    assert vikunja.vikunja_to_task(_raw(priority=5)).priority == PRIORITY_IMPORTANT
    assert vikunja.vikunja_to_task(_raw(priority=3)).priority == PRIORITY_NORMAL


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
    assert p["due_date"].startswith("2026-07-31T")  # end of day Paris


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


def test_list_open_maps_all_returned_tasks():
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {"id": 1, "title": "a", "priority": 0, "position": 2},
                {"id": 2, "title": "b", "priority": 4, "position": 1},
            ],
        )

    c = _client(handler)
    tasks = c.list_open()
    assert [t.id for t in tasks] == [1, 2]
    assert tasks[1].priority == PRIORITY_IMPORTANT


def test_get_task_returns_none_on_404():
    c = _client(lambda r: httpx.Response(404, json={"message": "not found"}))
    assert c.get_task(99) is None


def test_mark_done_posts_done_true():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": 5, "title": "x", "done": True, "done_at": "2026-07-16T09:00:00Z"},
        )

    c = _client(handler)
    task = c.mark_done(5)
    assert seen["method"] == "POST"
    assert "/api/v1/tasks/5" in seen["url"]
    assert seen["body"]["done"] is True
    assert task.completed_at is not None


def test_delete_task_true_on_success_false_on_404():
    assert _client(lambda r: httpx.Response(200, json={})).delete_task(5) is True
    assert _client(lambda r: httpx.Response(404, json={})).delete_task(5) is False


def test_server_error_raises():
    c = _client(lambda r: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(vikunja.VikunjaError):
        c.list_open()
