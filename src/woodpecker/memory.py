class ConversationMemory:
    """Short in-process record of recent turns, so Claude can resolve follow-ups
    like "yes" or "the first one" against what was just said. Not persisted: a
    restart wipes it, which is fine since follow-ups happen within seconds."""

    def __init__(self, max_pairs: int = 10):
        self._history: dict[int, list[dict[str, str]]] = {}
        self._max_pairs = max_pairs
        # The latest message Woodpecker sent on its own (a nag or the daily focus) that the
        # user has not answered yet. Kept apart from _history because it has no
        # preceding user turn, and the Claude API needs the message list to start with
        # one; it rides in the system prompt instead so a reply like "done" resolves.
        self._outbound: dict[int, str] = {}
        # The last-list-shown snapshot used to resolve typed numbers lives in the store
        # (Store.save_display / Store.load_display), not here: it must survive a restart,
        # or a redeploy would silently drop number-resolution back to the live order.

    def get(self, chat_id: int) -> list[dict[str, str]]:
        return list(self._history.get(chat_id, []))

    def add(self, chat_id: int, user_message: str, assistant_message: str) -> None:
        turns = self._history.setdefault(chat_id, [])
        turns.append({"role": "user", "content": user_message})
        turns.append({"role": "assistant", "content": assistant_message})
        max_messages = self._max_pairs * 2
        if len(turns) > max_messages:
            self._history[chat_id] = turns[-max_messages:]

    def clear(self, chat_id: int) -> None:
        self._history.pop(chat_id, None)

    def note_outbound(self, chat_id: int, message: str) -> None:
        self._outbound[chat_id] = message

    def get_outbound(self, chat_id: int) -> str | None:
        return self._outbound.get(chat_id)

    def clear_outbound(self, chat_id: int) -> None:
        self._outbound.pop(chat_id, None)
