from dataclasses import dataclass
from datetime import date, datetime

PRIORITY_NORMAL = "normal"
PRIORITY_IMPORTANT = "important"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_DROPPED = "dropped"


@dataclass(frozen=True)
class Task:
    id: int
    text: str
    priority: str
    deadline: date | None
    created_at: datetime
    status: str
    last_nagged_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class DailyFocus:
    focus: Task | None
    rescues: list[Task]
