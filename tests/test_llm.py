from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jolt import llm
from jolt.models import DailyFocus, PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")


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
