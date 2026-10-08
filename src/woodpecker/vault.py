"""Read the open tasks from the Obsidian vault: each open checkbox outside the record
folders. Read only. Copied from the dotclaude /todo skill (skills/todo/todo_vault.py):
a shared package for two personal repos is more than the job needs."""

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

BOX = re.compile(r"^\s*[-*] \[ \] (.*)$")
DUE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
NOW = "⏫"
FENCE = "```"
# Dated records, templates and build plans hold checkboxes that are not tasks.
SKIP_DIRS = {
    ".obsidian",
    ".git",
    ".trash",
    "40 Archive",
    "50 Journal",
    "90 Templates",
    "log",
    "plans",
    "specs",
}


class VaultUnreadable(Exception):
    """The vault folder itself cannot be read (missing mount, not a folder)."""


@dataclass(frozen=True)
class VaultTask:
    text: str
    note: str  # the note's file name without .md
    due: date | None
    now: bool  # marked ⏫: for this week


def _due(body: str) -> date | None:
    match = DUE.search(body)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _task(path: Path, body: str) -> VaultTask:
    text = DUE.sub("", body).replace(NOW, "")
    return VaultTask(text=" ".join(text.split()), note=path.stem, due=_due(body), now=NOW in body)


def read_file(path: Path) -> list[VaultTask]:
    """The open checkboxes of one note, in line order. A box in a code fence is not a task."""
    tasks, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(FENCE):
            fenced = not fenced
            continue
        match = None if fenced else BOX.match(line)
        if match:
            tasks.append(_task(path, match.group(1)))
    return tasks


def _notes(root: Path):
    for path in sorted(root.rglob("*.md")):
        if not SKIP_DIRS.intersection(path.relative_to(root).parts[:-1]):
            yield path


def read_vault(root: Path) -> list[VaultTask]:
    """All open tasks. A note that cannot be read is logged and skipped; a vault folder
    that cannot be read raises VaultUnreadable."""
    if not root.is_dir():
        raise VaultUnreadable("not a folder")
    tasks: list[VaultTask] = []
    for path in _notes(root):
        try:
            tasks += read_file(path)
        except (OSError, ValueError) as exc:
            logger.warning("Vault: cannot read %s (%s)", path.relative_to(root), type(exc).__name__)
    return tasks
