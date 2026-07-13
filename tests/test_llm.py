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


def _ordered_task(id, day):
    # A no-deadline task whose age (day-of-month) fixes its position: ascending day ->
    # ascending position, so a test can pin "position N" to a chosen id regardless of id.
    return Task(
        id=id,
        text=f"task {id}",
        priority=PRIORITY_NORMAL,
        deadline=None,
        created_at=datetime(2026, 7, day, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


NOW = datetime(2026, 7, 12, 15, tzinfo=TZ)


def _tool_use(input_dict):
    return SimpleNamespace(type="tool_use", input=input_dict)


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


def test_backlog_in_prompt_shows_positions_without_leaking_ids():
    # Claude sees only the position numbers the user sees, never the db id: exposing the
    # id was what let it emit a position where an id was expected (or vice versa).
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [_task(41)], NOW, client)
    system = client.messages.calls[0]["system"]
    assert "1: taxes" in system
    assert "id=41" not in system


def test_interpret_message_resolves_block_positions_to_ids():
    # The heart of the fix: the LLM outputs the numbers the user typed (list positions);
    # deterministic code maps them to the real db ids the orchestrator acts on. Here the
    # backlog is three tasks with gappy ids, so positions 2/3 -> ids 45/46.
    tasks = [_ordered_task(7, 1), _ordered_task(45, 2), _ordered_task(46, 3)]
    block = SimpleNamespace(
        type="tool_use",
        name="record_intent",
        input={"action": "block", "task_id": 3, "blocked_by": 2},
    )
    client = FakeClient(SimpleNamespace(content=[block]))
    intents = llm.interpret_message("3 blocked by 2", tasks, NOW, client)
    assert (intents[0].task_id, intents[0].blocked_by) == (46, 45)


def test_interpret_message_resolves_complete_position_to_id():
    tasks = [_ordered_task(7, 1), _ordered_task(45, 2)]
    complete = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "complete", "task_id": 2}
    )
    client = FakeClient(SimpleNamespace(content=[complete]))
    intents = llm.interpret_message("done 2", tasks, NOW, client)
    assert intents[0].task_id == 45


def test_interpret_resolves_numbers_against_shown_snapshot_not_live_order():
    # The stale-position bug: after tasks move, the live order differs from the list the
    # user is looking at. A number must resolve against the snapshot of the last list
    # shown. Live order here is [46, 45] (pos 2 -> 45), but the snapshot showed [45, 46]
    # (pos 2 -> 46), so "complete 2" must hit 46.
    tasks = [_ordered_task(46, 1), _ordered_task(45, 2)]
    complete = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "complete", "task_id": 2}
    )
    client = FakeClient(SimpleNamespace(content=[complete]))
    intents = llm.interpret_message("done 2", tasks, NOW, client, display_ids=[45, 46])
    assert intents[0].task_id == 46


def test_prompt_numbers_tasks_by_snapshot_order():
    # Claude must see the same numbering the user saw, so a text reference resolves to the
    # right number. The snapshot order [45, 46] wins over the live order.
    tasks = [_ordered_task(46, 1), _ordered_task(45, 2)]
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", tasks, NOW, client, display_ids=[45, 46])
    system = client.messages.calls[0]["system"]
    assert "1: task 45" in system
    assert "2: task 46" in system


def test_prompt_drops_snapshot_task_that_is_no_longer_pending():
    # A task completed since the list was shown falls out of the numbered list, but the
    # surviving lines keep their original numbers (each carries an explicit number).
    done = Task(
        id=45,
        text="task 45",
        priority=PRIORITY_NORMAL,
        deadline=None,
        created_at=datetime(2026, 7, 1, tzinfo=TZ),
        status="done",
        last_nagged_at=None,
        completed_at=NOW,
    )
    tool_block = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "add", "text": "x"}
    )
    client = FakeClient(SimpleNamespace(content=[tool_block]))
    llm.interpret_message("x", [done, _ordered_task(46, 2)], NOW, client, display_ids=[45, 46])
    system = client.messages.calls[0]["system"]
    assert "1: task 45" not in system
    assert "2: task 46" in system


def test_interpret_message_out_of_range_position_becomes_clarification():
    # An out-of-range number (past the end of the list) is converted to an action="answer"
    # clarification asking the user to resend with a valid position, rather than silently
    # landing on some unrelated task or creating a confused state.
    tasks = [_ordered_task(7, 1)]
    complete = SimpleNamespace(
        type="tool_use", name="record_intent", input={"action": "complete", "task_id": 9}
    )
    client = FakeClient(SimpleNamespace(content=[complete]))
    intents = llm.interpret_message("done 9", tasks, NOW, client)
    assert intents[0].action == "answer"
    assert intents[0].task_id is None


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


def test_out_of_range_number_becomes_a_clarification():
    tasks = [_task(id=10)]
    display_ids = [10]  # only position 1 exists
    intent = llm.Intent(action="complete", task_id=45)  # user referenced "45"
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert len(resolved) == 1
    assert resolved[0].action == "answer"
    assert "45" in resolved[0].reply
    assert resolved[0].task_id is None


def test_in_range_number_still_resolves_to_its_id():
    tasks = [_task(id=10)]
    display_ids = [10]
    intent = llm.Intent(action="complete", task_id=1)  # position 1 -> id 10
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "complete"
    assert resolved[0].task_id == 10


def test_write_nag_important_is_start_leaning():
    # An important task's nag pushes toward starting: ask what is blocking it / break it down.
    text_block = SimpleNamespace(type="text", text="go")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    llm.write_nag(_important_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "blocking" in system or "break it down" in system


def test_write_nag_normal_is_drop_leaning():
    # A low-value task's nag leans toward dropping it with a zero-based question, not scheduling it.
    text_block = SimpleNamespace(type="text", text="go")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    llm.write_nag(_task(), datetime(2026, 7, 12, 19, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "drop" in system or "still want" in system or "add it today" in system


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
    # "1 and 2 need 3 first" must become one block intent per blocked task, each with its
    # positions resolved to ids. Positions 1/2/3 -> ids 11/12/13 here.
    tasks = [_ordered_task(11, 1), _ordered_task(12, 2), _ordered_task(13, 3)]
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
    intents = llm.interpret_message("1 and 2 need 3 first", tasks, NOW, client)
    assert [(i.action, i.task_id, i.blocked_by) for i in intents] == [
        ("block", 11, 13),
        ("block", 12, 13),
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


def test_write_focus_leans_drop_for_low_value_rescue():
    # A normal (low-value) rescue should be framed as "still worth keeping?", not
    # "what is blocking it?", so the morning digest matches the drop-leaning nag stance.
    text_block = SimpleNamespace(type="text", text="ok")
    client = FakeClient(SimpleNamespace(content=[text_block]))
    focus = DailyFocus(focus=_important_task(1), rescues=[_task(2)])  # _task is normal
    llm.write_focus(focus, datetime(2026, 7, 12, 6, tzinfo=TZ), client)
    system = client.messages.calls[0]["system"].lower()
    assert "drop" in system or "worth keeping" in system or "still want" in system


def test_text_of_falls_back_when_no_text_block():
    # A response whose content has no text block (refusal / truncation) must not yield "",
    # because Telegram rejects an empty message and the send would raise.
    response = SimpleNamespace(content=[], stop_reason="end_turn")
    assert llm._text_of(response) != ""


def test_build_client_sets_a_short_timeout():
    from jolt import config, llm

    client = llm.build_client("sk-test-not-a-real-key")
    assert client.timeout == config.ANTHROPIC_TIMEOUT_SECONDS
    assert config.ANTHROPIC_TIMEOUT_SECONDS <= 60


def test_tool_schema_is_strict():
    assert llm._TOOL["strict"] is True
    assert llm._TOOL["input_schema"]["additionalProperties"] is False


def test_one_malformed_block_does_not_drop_the_others():
    # Two intents: a valid add, and a block with a non-ISO date that would crash parse_intent.
    # The valid add must survive; the bad block becomes a clarification, not a total failure.
    response = SimpleNamespace(
        content=[
            _tool_use({"action": "add", "text": "buy milk"}),
            _tool_use({"action": "add", "text": "call bank", "deadline": "next week"}),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    intents = llm.interpret_message(
        "buy milk; call bank next week", [_task()], datetime(2026, 7, 13, tzinfo=TZ),
        FakeClient(response),
    )
    actions = [i.action for i in intents]
    assert "add" in actions  # the valid one survived
    # the malformed one degraded to an answer, not a raised exception
    assert any(i.action == "answer" for i in intents)


def test_truncated_response_appends_a_note():
    response = SimpleNamespace(
        content=[_tool_use({"action": "add", "text": "task one"})],
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    intents = llm.interpret_message(
        "a very long paste", [_task()], datetime(2026, 7, 13, tzinfo=TZ), FakeClient(response),
    )
    assert intents[-1].action == "answer"
    assert "too long" in intents[-1].reply.lower()


def test_truncation_note_when_no_block_parses():
    # When a response is truncated (stop_reason="max_tokens") and no tool_use blocks parse,
    # the user must still get the "too long" message, not just "Sorry, I did not catch that."
    response = SimpleNamespace(
        content=[],
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    intents = llm.interpret_message(
        "a very long unparseable paste", [_task()], datetime(2026, 7, 13, tzinfo=TZ), FakeClient(response),
    )
    assert len(intents) == 1
    assert intents[0].action == "answer"
    assert "too long" in intents[0].reply.lower()


def test_parse_merge_with_merge_from():
    intent = llm.parse_intent(
        {"action": "merge", "task_id": 2, "merge_from": 5, "text": "combined task text"}
    )
    assert intent.action == "merge"
    assert intent.task_id == 2
    assert intent.merge_from == 5
    assert intent.text == "combined task text"


def test_tool_schema_supports_merge():
    assert "merge" in llm._TOOL["input_schema"]["properties"]["action"]["enum"]
    assert "merge_from" in llm._TOOL["input_schema"]["properties"]


def test_resolve_positions_resolves_merge_from():
    tasks = [_task(id=10), _task(id=20)]
    display_ids = [10, 20]
    intent = llm.Intent(action="merge", task_id=1, merge_from=2, text="combined")
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "merge"
    assert resolved[0].task_id == 10
    assert resolved[0].merge_from == 20


def test_resolve_positions_out_of_range_merge_from_clarifies():
    tasks = [_task(id=10)]
    display_ids = [10]
    intent = llm.Intent(action="merge", task_id=1, merge_from=9, text="combined")
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "answer"
    assert "9" in resolved[0].reply
    assert resolved[0].merge_from is None
