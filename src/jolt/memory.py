class ConversationMemory:
    """Short in-process record of recent turns, so Claude can resolve follow-ups
    like "yes" or "the first one" against what was just said. Not persisted: a
    restart wipes it, which is fine since follow-ups happen within seconds."""

    def __init__(self, max_pairs: int = 10):
        self._history: dict[int, list[dict[str, str]]] = {}
        self._max_pairs = max_pairs

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
