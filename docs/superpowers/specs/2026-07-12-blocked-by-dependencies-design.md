# Blocked-by dependencies — design spec

**Date:** 2026-07-12
**Status:** Approved (2026-07-12).

## Problem

The user asked Jolt to reprioritize by explaining a dependency in plain language
(*"1 and 2 first need 3 to be done, so 3 is absolute highest prio"*). Jolt silently
re-dumped the identical backlog and did nothing. Two gaps caused this:

1. The intent menu (`add / complete / drop / list / answer`) has no way to express a
   relationship between tasks, so Claude best-matched the message to `list`.
2. Priority is binary (`normal / important`) and ordering is fully computed, so there
   is no lever to hoist one important task above its equally-important peers, and no
   concept of "X must happen before Y".

The failure mode is worse than a missing feature: Jolt gave zero signal that it could
not obey, which reads as being ignored.

## Goal

Let the user express, in plain language, that one task must be finished before another,
and have Jolt act on it: redirect all its pressure onto the task that is actually
actionable (the blocker), and stop nagging about tasks that cannot be started yet.

This directly serves Jolt's core job (push at the thing the user should do next)
instead of pushing at tasks that are physically blocked.

## Non-goals

- Not a project manager. No subtasks, no task groups, no arbitrary dependency graphs
  with multiple prerequisites per task.
- No manual drag-to-reorder or numeric ranking. Ordering stays computed; dependencies
  are the only new ordering input.
- No change to the binary `normal / important` priority model.

## Data model

One new nullable column on the existing `tasks` table:

```
blocked_by INTEGER   -- id of the task that must finish first; NULL = not blocked
```

**A task has at most one blocker.** The motivating case ("1 and 2 both need 3") fits:
task 1 → `blocked_by=3`, task 2 → `blocked_by=3`. The rarer reverse ("this needs two
different things first") is intentionally unsupported to keep a single column and avoid
a join table. Revisit only if it comes up in real use.

The column is added in two places so both a fresh install and the live deployed DB are
covered: it goes into the `CREATE TABLE IF NOT EXISTS` (for new SQLite files), and
`init_db` also runs an idempotent `ALTER TABLE tasks ADD COLUMN blocked_by INTEGER`
guarded by a `PRAGMA table_info` check (for the already-existing table on `jarvis`,
which `CREATE TABLE IF NOT EXISTS` would otherwise leave untouched). Existing rows get
`blocked_by = NULL`. No other data migration is needed.

## Behavior

### Effectively blocked

A pending task is **effectively blocked** when its `blocked_by` points to a task that is
still `pending`. If the blocker is `done` or `dropped`, or `blocked_by` is `NULL`, the
task is not effectively blocked.

Effectively-blocked tasks are:

- **excluded from daily-focus selection** (they can never be the lead or a rescue),
- **skipped by nags** (the scheduler never sends a nag about them),
- **excluded from stale / urgent scanning** (a task you cannot start cannot rot, so it
  earns no urgency from age).

The effect: pressure lands on the blocker, with no artificial boosting. In the
motivating case task 3 is already important and due today, so it leads on its own once
tasks 1 and 2 step aside.

**No urgency inheritance.** A blocker does not absorb the deadline or importance of the
tasks it gates. It rises purely because its dependents go quiet. (If a no-deadline
blocker ever gates an urgent task, revisit then.)

### Chains

Chains fall out of the "effectively blocked" rule for free: if task 3 is itself blocked
by task 4, then 3 is also effectively blocked and quiet, and 4 gets the heat. Urgency
flows to the deepest actionable task in a chain. No special chain-walking code is needed
for nag/focus suppression, because each task's blocked state is evaluated against its
own blocker's status.

### Auto-unblock

When a task is **completed** or **dropped**, every task whose `blocked_by` equals that
task's id has its `blocked_by` cleared (set to `NULL`) in the same operation. Those
dependents immediately re-enter normal nagging, focus eligibility, and stale scanning.

This is done by clearing the pointer rather than relying only on the "blocker no longer
pending" check, so the backlog stays clean and a dependent never keeps a stale pointer
to a finished task.

## Intents

Two new actions on the `record_intent` tool enum: `block` and `unblock`.

- `block` uses `task_id` (the task being blocked) and a new `blocked_by` field (the
  prerequisite task's id). Example: *"1 and 2 need 3 first"* → two `block` intents,
  `{task_id:1, blocked_by:3}` and `{task_id:2, blocked_by:3}`, one per blocked task
  (consistent with the existing one-intent-per-action rule).
- `unblock` uses `task_id` and clears that task's `blocked_by`. Example: *"taxes no
  longer waits on the accounts"* → `{action:"unblock", task_id:1}`.

The system prompt gains a short instruction teaching Claude to recognize dependency
language ("X needs Y first", "can't do X until Y", "Y blocks X") and map it to `block` /
`unblock`, resolving the intended tasks to their current backlog ids.

### Orchestrator handling and edge cases

`apply_intent` gains `block` and `unblock` branches:

- **Valid block** → set `blocked_by`, reply plainly (e.g. `"Noted: <A> waits on <B>."`).
- **Self-block** (`task_id == blocked_by`) → rejected, no DB write, plain reply.
- **Dangling** (either id is not a pending task) → rejected, no DB write, plain reply.
- **Cycle** (the block would create a loop, e.g. A blocked by B while B is already
  blocked by A, directly or through a chain) → rejected, no DB write, plain reply.
  Detected by walking the `blocked_by` chain from the proposed blocker and refusing if
  the blocked task is reached.
- **unblock on a task that is not blocked** → harmless, plain reply.

All rejections reply in words, so Jolt never again silently ignores a dependency
message.

## Rendering

In the backlog dump:

- Effectively-blocked tasks sort **directly below their blocker**, so the list reads
  top-down as "do this, then these unlock".
- A blocked task shows a **neutral dot** (not the urgency scale, since it is not
  actionable) and a `(blocked by N)` tag, where N is the blocker's position in the
  rendered list.

The blocker keeps its normal urgency-aware dot. The mechanical list is still never sent
through Claude.

## Testing

Following the existing strategy (external APIs mocked at the boundary, one concern per
test):

- **Selection / staleness**: an effectively-blocked task is never chosen as focus or
  rescue; is excluded from stale and urgent scanning; a blocked-by-done and a
  blocked-by-dropped task *are* treated as normal (not blocked).
- **Ordering / render**: a blocked task renders directly below its blocker, with the
  neutral dot and `(blocked by N)` tag.
- **db**: `block` sets the column; completing a blocker clears dependents'
  `blocked_by`; dropping a blocker clears dependents' `blocked_by`.
- **Orchestrator**: valid block/unblock replies and writes; self-block, dangling id,
  and cycle are each rejected with no write and a plain reply.
- **llm** (Anthropic mocked): a two-task dependency sentence is parsed into two `block`
  intents with the right `task_id` / `blocked_by` pairing.

Not tested (per strategy): actual LLM phrasing quality, Telegram plumbing.
