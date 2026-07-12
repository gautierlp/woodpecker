# Editing an existing task — design spec

**Date:** 2026-07-12
**Status:** Approved (2026-07-12).

## Problem

The user asked Jolt to *"change all due dates to today"* and Jolt correctly refused: it
has no way to modify a task after it is created. The `record_intent` action menu
(`add / complete / drop / block / unblock / list / answer`) exposes creation, status
changes, and dependencies, but nothing that edits a task's own fields. The data layer
mirrors this: `db.py` can flip `status` or set `blocked_by`, but never touches `text`,
`priority`, or `deadline` once a row exists.

The only workaround Jolt could offer was drop-and-re-add, which loses the task's age and
history and is clumsy for a bulk change. This is a real missing capability, not a bug.

## Goal

Let the user change a task's **due date, priority, or wording** in place, in plain
language, including bulk edits like "change all due dates to today." Editing must not
create a new task or lose the original's age.

## Non-goals

- No editing of `status` through this action (that is what `complete` / `drop` are for),
  and no editing of `blocked_by` (that is `block` / `unblock`).
- No undo / edit history. A single-user backlog does not need it.
- No new priority values. The binary `normal / important` model is unchanged; edit can
  only move a task between the two existing values.

## Intent: `edit`

One new action on the `record_intent` tool enum: `edit`.

An `edit` call carries a `task_id` (the task to change) plus **only the fields that
change** — any subset of `text`, `priority`, `deadline`. Fields left out mean "leave
as-is." This reuses the tool fields that already exist (`text`, `priority`, `deadline`,
`task_id` are all present today for `add` / `complete` / `drop`), so no new field is
needed for the common case.

**Bulk edits** fall out for free. Claude already receives the full pending backlog in
the `interpret_message` system prompt, and already fans a pasted list into one `add`
call per line. *"Change all due dates to today"* becomes **one `edit` call per pending
task**, each with `deadline` set to today, consistent with the existing
one-intent-per-action rule.

### Clearing a deadline

"Remove the due date" must be distinguishable from "don't touch the due date," because
both otherwise look like an absent `deadline`. One new boolean on the tool,
`clear_deadline`, disambiguates:

- `deadline` present → set the deadline to that date.
- `clear_deadline: true` → set the deadline to `NULL`.
- neither → leave the deadline unchanged.

`text` and `priority` need no equivalent, since a task always has both and neither can
be meaningfully cleared.

The system prompt gains a short instruction teaching Claude to map "change / move /
reschedule the deadline", "make it important / normal", and "reword / rename this task"
to `edit`, and "remove / clear the due date" to `edit` with `clear_deadline`, resolving
the target to its current backlog id. It also notes that editing is preferred over
drop-and-re-add when the task already exists.

## Data layer: `db.update_task`

New function performing a **partial update**:

```
update_task(conn, task_id, *, text=None, priority=None, deadline=_UNSET) -> Task | None
```

It builds the SQL `SET` clause from only the fields that were actually provided and runs
it against `id = ? AND status = 'pending'`. `text` / `priority` use `None` as their
"leave alone" default. `deadline` uses a private `_UNSET` sentinel as its default so the
three deadline cases stay unambiguous with one argument: a real `date` sets it,
`deadline=None` clears it to `NULL`, and `_UNSET` (the default) leaves it alone. The
orchestrator maps the intent's `clear_deadline` flag to `deadline=None` when calling in.

- No fields to change → no-op, returns the task unchanged (or `None` if not found).
- Not found or not pending → returns `None`, no write.
- Otherwise → applies the update, returns the refreshed `Task`.

Same return shape as `block_task`. Editing only ever touches `text` / `priority` /
`deadline`; it never writes `created_at`, `last_nagged_at`, `completed_at`, `status`, or
`blocked_by`.

## Orchestrator handling

`apply_intent` gains an `edit` branch:

- **Not found or not pending** → `"Couldn't find that one."`, no write.
- **Empty edit** (no `text`, no `priority`, no `deadline`, no `clear_deadline`) →
  `"Nothing to change."`, no write.
- **Valid edit** → apply and confirm what changed in a short plain line, e.g.
  `Updated. Due 2026-07-12.` for a deadline change, or `Updated. Due date removed.` for
  a clear. No cheerleading, no em dashes.

## Deliberate non-effects

Editing a deadline does **not** reset the task's age or nagging clock. Staleness and
daily-focus age both key off `created_at` (`selection.py`), which `edit` never writes.
So rescheduling a due date will not make an avoided task look fresh, and cannot be used
to silence a nag. This is intentional and is the main reason edit is preferred over
drop-and-re-add: the latter *would* reset the age.

Editing does not touch `blocked_by`, so a blocked task stays blocked (and quiet) across
an edit.

## Rendering

No rendering change. Edited fields flow through the existing backlog dump unchanged: a
new deadline shows in the `(due …)` tag, a priority change moves the task in the
computed ordering, reworded text shows as-is. The mechanical list is still never sent
through Claude.

## Testing

Following the existing strategy (external APIs mocked at the boundary, one concern per
test):

- **db**: a partial update touches only the given field(s) and leaves the rest intact;
  a real `date` sets the deadline; `clear_deadline=True` sets it to `NULL`; an empty
  update is a harmless no-op; a non-pending (done / dropped) or missing task returns
  `None` with no write; `created_at` is never modified by an edit.
- **Orchestrator**: a valid edit writes and returns the right confirmation (deadline
  set, deadline cleared); an empty edit replies `"Nothing to change."` with no write; a
  missing / non-pending task replies `"Couldn't find that one."` with no write.
- **llm** (Anthropic mocked): the `record_intent` schema exposes `edit` in the action
  enum and a `clear_deadline` boolean; a "change all due dates to today" style message
  is parsed into one `edit` intent per pending task, each carrying today's `deadline`.

Not tested (per strategy): actual LLM phrasing quality, Telegram plumbing.
