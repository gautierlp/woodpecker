from dataclasses import dataclass
from datetime import date, datetime

# Task-creation vocabulary only (add flow, task_create_payload, LLM add-tool enum). The
# read model's Task.priority is the raw Vikunja integer; see selection.priority_band.
PRIORITY_NORMAL = "normal"
PRIORITY_IMPORTANT = "important"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_DROPPED = "dropped"


@dataclass(frozen=True)
class Task:
    id: int
    text: str
    priority: int  # raw Vikunja priority 0-5; bands are derived in selection.py
    deadline: date | None
    created_at: datetime
    status: str
    last_nagged_at: datetime | None
    completed_at: datetime | None
    position: int = 0
    estimate_seconds: int | None = None  # from the mdone sentinel in the description
    details: str = ""  # the visible description, sentinel stripped
    project_id: int = 0
    project_name: str = ""
    bump_count: int = 0  # forward due-date moves counted in the sidecar; the avoidance signal


@dataclass(frozen=True)
class DailyFocus:
    focus: Task | None
    rescues: list[Task]
