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
