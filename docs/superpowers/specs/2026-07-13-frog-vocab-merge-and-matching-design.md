# Frog vocabulary, task merging, and steadier matching

**Date:** 2026-07-13
**Status:** Approved, ready for implementation plan

## Motivation

The deep-dive review of the live bot surfaced three product/UX gaps in the conversation
layer, deliberately deferred from the reliability pass because they touch prompts and
add a behavior rather than fix a correctness bug:

1. "What is my frog?" was answered literally ("I don't see any frog in your task list"),
   even though the whole project is themed on Eat That Frog and the daily focus IS the
   frog.
2. "Group 45 and 12 together" (meaning "make these one task") had no matching action, so
   Claude improvised a wrong dependency and took two correction messages to undo.
3. Task references drift: the backlog numbers tasks by display position, and a number
   that is stale or out of range could be remapped to an unrelated task.

This spec addresses all three. Two are prompt-only; one (merge) is a small new action.

## Design principle

Keep the division of labour: Claude handles the judgment and tone (understanding "frog",
matching a task from a text description, writing the merged task's text and picking which
task survives); deterministic code handles the mechanical part (folding two tasks into
one, choosing the more urgent deadline and priority, dropping the other task). Happy-path
behavior for existing intents does not change.

## Part 1: Frog / tadpole vocabulary (prompt only)

**File:** `src/jolt/llm.py` (the `interpret_message` system prompt).

Teach Claude the Eat That Frog vocabulary already implied by the current
"chase frogs, shed tadpoles" model:

- The "frog" is the single most important thing to hit today, the lead task.
- A "tadpole" is a low-value task that has been sitting untouched and is a candidate to
  drop rather than chase.

"What is my frog?", "what should I do today?", and similar are answered conversationally
through the existing `answer` intent. Claude already receives the current backlog in the
prompt, so it names a task from that list; there is no new deterministic focus path and
no new intent. This is a decision to let Claude answer freely rather than route the
answer through `select_daily_focus`; the daily 06:00 brief is unaffected.

No unit test (LLM output quality is not tested per the project testing strategy).

## Part 2: Steadier task-matching (prompt only)

**File:** `src/jolt/llm.py` (the `interpret_message` system prompt).

Keep the existing 1..N display numbers (already snapshot-backed and out-of-range-guarded
from the reliability pass). Strengthen the prompt guidance so Claude:

- Prefers matching a task by its **text** when the user describes it in words rather than
  typing a number, using the numbered backlog lines to find the matching task.
- Uses a number as `task_id` only when the user actually typed that number.

This reduces the "user described a task, Claude guessed a number, the number resolved to
the wrong task" failure. No structural change to numbering or resolution. No unit test
(prompt behavior).

## Part 3: Merge intent (new action)

Fold two existing tasks into one.

### Intent

**File:** `src/jolt/llm.py`.

- Add `"merge"` to the `_TOOL` action enum, with a description: fold two tasks into one
  single task (use when the user says to group, combine, or merge tasks, or that two
  entries are really the same task).
- Add a `merge_from` integer field to the tool schema and to the `Intent` dataclass: the
  backlog number of the task to fold into `task_id` (the survivor). `task_id` is the task
  that remains.
- `text`, for `action="merge"`, carries Claude's combined text for the survivor.
- `parse_intent` reads `merge_from` like the other integer fields.
- `_resolve_positions` resolves `merge_from` through the same number/snapshot logic it
  uses for `task_id` and `blocked_by`. An out-of-range `merge_from` (or `task_id`) yields
  the existing clarification, exactly as the reliability pass added.

### Database

**File:** `src/jolt/db.py`.

Add `merge_tasks(conn, survivor_id, from_id, text, now) -> Task | None`:

- Guard first (safe on its own, mirroring `block_task`): return `None` if
  `survivor_id == from_id`, or if either task is missing or not pending.
- Compute the merged attributes mechanically:
  - deadline: the earlier of the two deadlines, where a real date always beats `None`
    (so if only one task has a deadline, the merged task keeps it; if both do, keep the
    sooner; if neither, `None`).
  - priority: `important` if either task is important, else `normal`.
- Update the survivor: set its `text` to the provided combined text, its `deadline` to
  the computed deadline, its `priority` to the computed priority. The survivor keeps its
  own `id`, `created_at` (age), `last_nagged_at`.
- Drop the other task via the existing `drop_task` path so any dangling `blocked_by`
  references to it are cleared (its `drop_task` already does
  `UPDATE ... SET blocked_by = NULL WHERE blocked_by = ?`).
- Return the updated survivor, or `None` if a guard rejected the merge.

If Claude omitted the combined text, the caller (orchestrator) passes a deterministic
fallback: the survivor's text and the other task's text joined with " and ".

### Orchestrator

**File:** `src/jolt/orchestrator.py`.

Add a `merge` branch to `apply_intent`:

- Look up both tasks; if either is missing or not pending, or `task_id == merge_from`,
  return "Couldn't find those tasks." (matching the `block` branch's clarification).
- Otherwise call `db.merge_tasks` with the survivor id (`intent.task_id`), the other id
  (`intent.merge_from`), the merged text (`intent.text` or the " and " fallback built
  from the two task texts), and `now`.
- On success return a short confirmation, for example: `Merged into "<merged text>".`
- Log the action like the other branches.

## Success criteria

- "What is my frog?" and "what should I do today?" get a sensible task-based answer, not
  a literal "no frog found" reply. (Verified by inspection / live check, not a unit test.)
- "Group X and Y together" folds the two tasks into one: one task remains with the
  combined text, the more urgent deadline, and the higher priority; the other is gone;
  no dependency is created.
- Merging a task with itself, or referencing a missing or non-pending task, yields a
  clarification and changes nothing.
- An out-of-range `merge_from` or `task_id` yields the existing out-of-range
  clarification, no mutation.
- All existing tests still pass; new tests cover `parse_intent` for merge,
  `_resolve_positions` on `merge_from`, `db.merge_tasks` (earlier-deadline,
  higher-priority, drop-the-other, self-merge guard, missing/non-pending guard, text
  fallback), and the orchestrator merge branch. `ruff check` clean.

## Non-goals

- No stable short-code or text-only task-reference system: display numbers stay, matching
  is only hardened via the prompt.
- No new deterministic "focus" path for the frog question: Claude answers freely.
- No parent/subtask grouping for merge: merge folds into a single task, it does not nest.
- No change to the 06:00 daily focus, the nags, quiet hours, staleness, or rendering.
- No change to happy-path behavior of existing intents (add/complete/drop/edit/block/
  unblock/list/answer).
