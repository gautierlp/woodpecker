from jolt.memory import ConversationMemory


def test_get_unknown_chat_returns_empty():
    mem = ConversationMemory()
    assert mem.get(42) == []


def test_add_then_get_returns_user_then_assistant():
    mem = ConversationMemory()
    mem.add(42, "is 32 blocked by 31?", "Not currently. Want me to set it up?")
    assert mem.get(42) == [
        {"role": "user", "content": "is 32 blocked by 31?"},
        {"role": "assistant", "content": "Not currently. Want me to set it up?"},
    ]


def test_trims_to_max_pairs_keeping_most_recent():
    mem = ConversationMemory(max_pairs=2)
    mem.add(42, "u1", "a1")
    mem.add(42, "u2", "a2")
    mem.add(42, "u3", "a3")
    # only the last two pairs survive, oldest dropped
    assert mem.get(42) == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]


def test_chats_are_isolated():
    mem = ConversationMemory()
    mem.add(1, "u1", "a1")
    mem.add(2, "u2", "a2")
    assert mem.get(1) == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert mem.get(2) == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_get_returns_a_copy_not_internal_list():
    mem = ConversationMemory()
    mem.add(42, "u1", "a1")
    got = mem.get(42)
    got.append({"role": "user", "content": "tampered"})
    # mutating the returned list must not corrupt stored history
    assert len(mem.get(42)) == 2


def test_clear_forgets_a_chat():
    mem = ConversationMemory()
    mem.add(42, "u1", "a1")
    mem.clear(42)
    assert mem.get(42) == []


def test_get_outbound_defaults_to_none():
    mem = ConversationMemory()
    assert mem.get_outbound(42) is None


def test_note_outbound_then_get_returns_it():
    mem = ConversationMemory()
    mem.note_outbound(42, "Still the taxes. Two minutes. Go.")
    assert mem.get_outbound(42) == "Still the taxes. Two minutes. Go."


def test_note_outbound_keeps_only_the_latest():
    # A later nag supersedes an earlier one; only the most recent matters for a reply.
    mem = ConversationMemory()
    mem.note_outbound(42, "morning focus")
    mem.note_outbound(42, "midday nag")
    assert mem.get_outbound(42) == "midday nag"


def test_clear_outbound_forgets_pending_nag():
    mem = ConversationMemory()
    mem.note_outbound(42, "midday nag")
    mem.clear_outbound(42)
    assert mem.get_outbound(42) is None


def test_outbound_is_per_chat():
    mem = ConversationMemory()
    mem.note_outbound(1, "nag one")
    assert mem.get_outbound(2) is None


def test_get_display_defaults_to_none():
    mem = ConversationMemory()
    assert mem.get_display(42) is None


def test_note_display_then_get_returns_the_shown_order():
    # The snapshot of the last list shown: the task ids in the exact order the user saw,
    # so a later "complete 2" resolves to the second task on that list.
    mem = ConversationMemory()
    mem.note_display(42, [45, 46, 47])
    assert mem.get_display(42) == [45, 46, 47]


def test_note_display_keeps_only_the_latest_list():
    mem = ConversationMemory()
    mem.note_display(42, [1, 2, 3])
    mem.note_display(42, [9, 8])
    assert mem.get_display(42) == [9, 8]


def test_display_snapshot_is_per_chat():
    mem = ConversationMemory()
    mem.note_display(1, [1, 2])
    assert mem.get_display(2) is None
