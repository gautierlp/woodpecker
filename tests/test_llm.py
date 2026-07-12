from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jolt import llm
from jolt.models import DailyFocus, PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")


def test_parse_add_with_deadline_and_priority():
    intent = llm.parse_intent(
        {
            "action": "add",
            "text": "call vet",
            "priority": "important",
            "deadline": "2026-07-15",
        }
    )
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
    return Task(
        id=id,
        text="taxes",
        priority=PRIORITY_NORMAL,
        deadline=None,
        created_at=datetime(2026, 7, 1, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


NOW = datetime(2026, 7, 12, 15, tzinfo=TZ)


def test_interpret_message_returns_parsed_intent():
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "call vet"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    intents = llm.interpret_message("remind me to call vet", [_task()], NOW, client)
    assert [i.action for i in intents] == ["add"]
    assert intents[0].text == "call vet"
    # the task list was passed into the prompt so Claude can reference ids
    assert "taxes" in str(client.messages.calls[0])


def test_interpret_message_sends_history_before_current_message():
    # The follow-up bug: a bare "Yes" only resolves if Claude sees the prior turn
    # where it offered to act. History must be prepended to the messages array,
    # ahead of the new user message.
    tool_block = SimpleNamespace(
        type="tool_use",
        name="record_intent",
        input={"action": "block", "task_id": 32, "blocked_by": 31},
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    history = [
        {"role": "user", "content": "is 32 blocked by 31?"},
        {"role": "assistant", "content": "Not currently. Want me to set that up?"},
    ]
    llm.interpret_message("Yes", [_task()], NOW, client, history=history)
    messages = client.messages.calls[0]["messages"]
    assert messages == history + [{"role": "user", "content": "Yes"}]


def test_interpret_message_without_history_sends_only_current_message():
    # Default (no history passed) must stay a single-message conversation, so the
    # existing callers and behavior are unaffected.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "call vet"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("call vet", [], NOW, client)
    assert client.messages.calls[0]["messages"] == [{"role": "user", "content": "call vet"}]


def test_prompt_instructs_resolving_followups_from_history():
    # Given prior turns, a short reply like "yes" or "do it" must be read as the
    # action it refers to, not as a fresh, contextless message.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "follow-up" in system or "earlier" in system or "previous" in system


def test_prompt_includes_pending_outbound_nag():
    # A reply to a nag ("done") only resolves if Claude sees the nag it is replying
    # to. The nag has no user turn before it, so it rides in the system prompt.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "complete", "task_id": 1}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message(
        "done", [_task()], NOW, client, recent_outbound="Still the taxes. Two minutes. Go."
    )
    system = client.messages.calls[0]["system"]
    assert "Still the taxes. Two minutes. Go." in system


def test_prompt_omits_outbound_section_when_none():
    # No pending nag -> no dangling "you recently sent" line in the prompt.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    assert "recently sent" not in client.messages.calls[0]["system"].lower()


def test_interpret_message_returns_every_task_in_a_multi_task_message():
    # A single message can hold several tasks (a pasted list). Each must come back as
    # its own intent; the old code stopped after the first tool_use block and dropped
    # the rest (a real bug: five pasted tasks, only "Cancel gym subscription" saved).
    blocks = [
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "add", "text": "cancel gym subscription"},
        ),
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "add", "text": "file late tax returns"},
        ),
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "add", "text": "file late expense reports"},
        ),
    ]
    client = FakeClient(SimpleNamespace(content=blocks))
    intents = llm.interpret_message("(a list of five tasks)", [], NOW, client)
    assert [i.text for i in intents] == [
        "cancel gym subscription",
        "file late tax returns",
        "file late expense reports",
    ]


def test_interpret_message_allows_parallel_tool_calls():
    # tool_choice must permit more than one record_intent call per turn, otherwise a
    # multi-task message can only ever yield a single task.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "call vet"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("call vet", [], NOW, client)
    tool_choice = client.messages.calls[0]["tool_choice"]
    assert tool_choice.get("disable_parallel_tool_use") is not True
    assert tool_choice["type"] != "tool"  # a named tool forces exactly one call


def test_interpret_message_falls_back_when_no_tool_call():
    client = FakeClient(SimpleNamespace(content=[SimpleNamespace(type="text", text="hmm")]))
    intents = llm.interpret_message("???", [], NOW, client)
    assert len(intents) == 1
    assert intents[0].action == "answer"


def test_interpret_prompt_includes_today_for_relative_dates():
    # "tomorrow evening" only resolves if Claude knows today's date; without it the
    # deadline is hallucinated (a real bug: it once saved 2025-01-09 for "demain soir").
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "invoices"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("invoices by tomorrow evening", [], NOW, client)
    assert "2026-07-12" in client.messages.calls[0]["system"]


def test_interpret_prompt_instructs_english_task_text():
    # Tasks are often typed in French but must be stored in English.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "invoices"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("factures client", [], NOW, client)
    assert "english" in client.messages.calls[0]["system"].lower()


def test_interpret_prompt_instructs_importance_inference():
    # Most tasks are captured casually with no explicit priority, so the prompt must
    # tell Claude to judge importance rather than only copy an explicit signal.
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "book the vet"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("book the vet", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "importance" in system or "important" in system


def _important_task(id=1):
    return Task(
        id=id,
        text="file the tax return",
        priority=PRIORITY_IMPORTANT,
        deadline=None,
        created_at=datetime(2026, 7, 3, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


def test_write_nag_passes_importance_to_prompt():
    # An important + old task must push harder than a normal one, so the prompt has to
    # know the task is important, not just its age.
    text_block = SimpleNamespace(type="text", text="go")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    llm.write_nag(_important_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    assert "important" in str(client.messages.calls[0]).lower()


def test_write_focus_passes_importance_to_prompt():
    text_block = SimpleNamespace(type="text", text="ok")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    focus = DailyFocus(focus=_important_task(1), rescues=[_important_task(2)])
    llm.write_focus(focus, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    assert "important" in str(client.messages.calls[0]).lower()


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


def test_parse_block_intent():
    intent = llm.parse_intent({"action": "block", "task_id": 1, "blocked_by": 3})
    assert intent.action == "block"
    assert intent.task_id == 1
    assert intent.blocked_by == 3


def test_parse_unblock_intent():
    intent = llm.parse_intent({"action": "unblock", "task_id": 2})
    assert intent.action == "unblock"
    assert intent.task_id == 2
    assert intent.blocked_by is None


def test_tool_enum_includes_block_and_unblock():
    actions = llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "block" in actions
    assert "unblock" in actions


def test_prompt_mentions_dependencies():
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "block" in system


def test_interpret_message_maps_dependency_to_two_block_intents():
    # "1 and 2 need 3 first" must become one block intent per blocked task.
    blocks = [
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "block", "task_id": 1, "blocked_by": 3},
        ),
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "block", "task_id": 2, "blocked_by": 3},
        ),
    ]
    client = FakeClient(SimpleNamespace(content=blocks))
    intents = llm.interpret_message("1 and 2 need 3 first", [_task()], NOW, client)
    assert [(i.action, i.task_id, i.blocked_by) for i in intents] == [
        ("block", 1, 3),
        ("block", 2, 3),
    ]


def test_parse_edit_intent_with_deadline():
    intent = llm.parse_intent({"action": "edit", "task_id": 4, "deadline": "2026-07-12"})
    assert intent.action == "edit"
    assert intent.task_id == 4
    assert intent.deadline == date(2026, 7, 12)
    assert intent.clear_deadline is False


def test_parse_edit_intent_with_clear_deadline():
    intent = llm.parse_intent({"action": "edit", "task_id": 4, "clear_deadline": True})
    assert intent.action == "edit"
    assert intent.clear_deadline is True
    assert intent.deadline is None


def test_tool_enum_includes_edit():
    actions = llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "edit" in actions


def test_tool_schema_exposes_clear_deadline():
    assert "clear_deadline" in llm._TOOL["input_schema"]["properties"]


def test_prompt_mentions_edit():
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [], NOW, client)
    system = client.messages.calls[0]["system"].lower()
    assert "edit" in system


def test_interpret_message_maps_bulk_reschedule_to_edit_per_task():
    # "change all due dates to today" -> one edit intent per pending task, each with today.
    blocks = [
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "edit", "task_id": 1, "deadline": "2026-07-12"},
        ),
        SimpleNamespace(
            type="tool_use",
            name="record_intent",
            input={"action": "edit", "task_id": 2, "deadline": "2026-07-12"},
        ),
    ]
    client = FakeClient(SimpleNamespace(content=blocks))
    intents = llm.interpret_message(
        "change all due dates to today", [_task(1), _task(2)], NOW, client
    )
    assert [(i.action, i.task_id, i.deadline) for i in intents] == [
        ("edit", 1, date(2026, 7, 12)),
        ("edit", 2, date(2026, 7, 12)),
    ]
