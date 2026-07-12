from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Intent:
    action: str
    text: str | None = None
    priority: str | None = None
    deadline: date | None = None
    task_id: int | None = None
    reply: str | None = None


def parse_intent(tool_input: dict) -> Intent:
    deadline = tool_input.get("deadline")
    return Intent(
        action=tool_input["action"],
        text=tool_input.get("text"),
        priority=tool_input.get("priority"),
        deadline=date.fromisoformat(deadline) if deadline else None,
        task_id=tool_input.get("task_id"),
        reply=tool_input.get("reply"),
    )
