# Reliability and hygiene pass

**Date:** 2026-07-13
**Status:** Approved, ready for implementation plan

## Motivation

A deep-dive review of the deployed bot (code line-by-line plus the live container
logs) found no showstopper bugs, but a consistent weakness: the code that runs when
the *bot* messages the *user* (the 06:00 focus and the nags) can fail silently. If the
Anthropic API hiccups during a scheduled job, the user gets nothing, with no error and
no log the user ever sees. Since sending those messages is the bot's entire job, that
is the highest-leverage thing to fix.

Alongside it, a handful of correctness and hygiene issues are worth clearing while we
are in here: a crash-on-bad-date path that drops whole messages, silent truncation of
large pastes, an empty-reply send that throws, an out-of-range task-number reference
that acts on the wrong task, some dead code, an inconsistent DB mutator, and doc drift.

Out of scope (deferred to a separate product/UX spec): understanding "what is my frog?",
a merge/group intent, and any redesign of how the user points at tasks. This spec adds
only a defensive guard for out-of-range numbers, not a new reference system.

## Design principle

Make the bot fail **loudly and safely** instead of silently. This pass must not change
what the bot says or does on the happy path: same focus text, same nags, same backlog
rendering, same intent behavior. It only changes what happens when something goes wrong.

One deliberate non-change: Claude calls stay **on the asyncio event loop** (the single
worker thread). Moving them off-loop would let two inbound messages overlap and touch
the shared single `sqlite3` connection concurrently, which commit `81aca44` deliberately
avoided. For a single user a brief freeze during a call is acceptable; the only real
danger is an *infinite* hang, which the request timeout below removes. Keeping calls
single-threaded preserves the DB-safety invariant.

## Part A: reliability

### A1. Scheduled jobs never fail silently

**Files:** `src/jolt/scheduler.py` (`send_daily_focus`, `send_nags`).

Today an exception inside either job body escapes into APScheduler, which logs it and
moves on; the user sees nothing. Wrap each job's work so any exception is caught,
logged at ERROR, and turned into a short plain-text message to the user via the existing
`send` callable, for example: "My morning brief broke, I'll try again at the next nag."

- The daily focus is one unit of work: on failure, send one fallback line.
- `send_nags` fires up to two independent nags (focus nag, then tadpole nag). A failure
  on the first must not prevent the second from being attempted. Guard them
  independently so one failing nag still lets the other through, and only emit a single
  user-facing failure note per job run (not one per nag) to avoid spam.
- The fallback message itself is deterministic plain text (no Claude call), so it cannot
  fail the same way the thing it is reporting on failed.

### A2. A hung Claude call cannot wedge the bot

**File:** `src/jolt/main.py` (Anthropic client construction).

The client is built with SDK defaults, whose per-request timeout is ~10 minutes. Because
calls run on the single event loop, a stuck request freezes polling, inbound handling,
and every other job for that whole window. Construct the client with an explicit short
timeout (target 30 seconds; final value is a single named constant in `config.py`, easy
to tune after living with it). A timed-out request then raises promptly and is handled
by A1 (for scheduled jobs) or the existing `bot.handle_error` (for inbound messages).

### A3. One bad date does not drop the whole message

**File:** `src/jolt/llm.py` (`interpret_message`, `parse_intent`, `_TOOL`).

`parse_intent` does `date.fromisoformat(deadline)` and reads required keys directly.
Because parsing runs inside a list comprehension over all tool-use blocks, one block
that raises (non-ISO date such as "next week", or a missing `action`) aborts *every*
intent in the message. The message is then lost with only a generic "say it again".

Two independent hardening changes:

1. Add `"strict": true` and `"additionalProperties": false` to the `_TOOL` input schema
   so the model's tool input is schema-validated at the API boundary, making malformed
   input far less likely in the first place.
2. Wrap each block's `parse_intent` in try/except so a single malformed block degrades
   to a clarification reply for that one item, while the other valid intents in the same
   message still apply. Log the malformed block at WARNING.

### A4. Big pastes are not truncated silently

**Files:** `src/jolt/llm.py` (`interpret_message`), `src/jolt/config.py`.

`max_tokens` is currently 1000. A paste of many tasks emits that many parallel
`record_intent` tool calls and can exceed the cap, truncating the response so later
tasks are dropped and the final tool-use block may be partial, silently reintroducing
the "pasted five, saved some" bug.

- Raise `max_tokens` to a comfortable ceiling (target 4000; a single named constant in
  `config.py`).
- Check `response.stop_reason`. If it is `max_tokens`, still apply whatever intents were
  parsed, but append a note to the reply telling the user the message was too long to
  capture fully so they can re-send the remainder. Log it at WARNING.

### A5. Empty replies cannot crash a send

**File:** `src/jolt/llm.py` (`_text_of`, and its callers `write_focus`, `write_nag`).

`_text_of` returns `""` when a response has no text block (a refusal, or truncation
before any text). Telegram's `sendMessage` rejects empty text, so a nag built from that
raises. `_text_of` must fall back to a safe, non-empty default string and log at WARNING
when it does. The daily focus is partly shielded (backlog is appended) but should not
rely on that.

### A6. Out-of-range / stale task-number guard

**Files:** `src/jolt/llm.py` (number-to-id resolution) and/or `src/jolt/orchestrator.py`.

Task numbers are resolved in code against the last display snapshot shown to the user
(commits `1647fe7`, `bb6c6c2`, `f0c3c31`). When the user references a number that is not
present in that snapshot (for example "45" when the last list stopped at 44, or a stale
snapshot after a restart), the current path can resolve to an unrelated task id and act
on it. The live logs show exactly this: "Group 45 and 12 together" acted on task ids 76
and 40, which the user never named.

Add a guard: if a referenced number has no entry in the saved display snapshot, do not
resolve it to any id. Instead reply with a short clarification ("I don't have a task 45,
here is your current list") rather than acting on a guessed id. This is a pure code
check (no Claude involvement), so it is deterministic and unit-testable.

This is explicitly a defensive guard only. A redesign of how users point at tasks
(stable short codes, etc.) is deferred to the product/UX spec.

## Part B: hygiene

### B1. Delete dead code

**Files:** `src/jolt/selection.py` (`is_urgent`), `src/jolt/config.py` (`DUE_SOON_DAYS`).

Both are defined but referenced nowhere in `src/` or `tests/`. They wrongly imply
urgency feeds selection (only staleness and importance do). Delete both. Confirm there
are no remaining references before removing.

### B2. Fix `unblock_task` to match its siblings

**File:** `src/jolt/db.py` (`unblock_task`).

Every other mutator scopes its `WHERE` to `status = STATUS_PENDING` and reports a no-op
via `rowcount` (returning `None`). `unblock_task` unconditionally updates and always
reports a Task-shaped success, so it will "unblock" a done, dropped, or nonexistent task
and report success. Add `AND status = ?` (pending) and return `get_task(...) if
cur.rowcount else None`, matching the peers.

### B3. Guard `block_task`

**File:** `src/jolt/db.py` (`block_task`), with context in `src/jolt/orchestrator.py`.

`block_task` writes `blocked_by` with no existence, self-reference, or status check; its
correctness rests entirely on the single orchestrator caller validating first. Make it
safe on its own: at minimum document the precondition explicitly, and add a defensive
self-reference and target-exists guard in `block_task` itself. Cycle detection can stay
in the orchestrator, where it has the full graph. Preserve current behavior for the
existing valid-input path.

### B4. Refresh CLAUDE.md to match reality

**File:** `CLAUDE.md`.

The "Module Responsibilities (planned)" section still lists a layout (`tasks.py`,
`staleness.py`) that no longer matches the code (`orchestrator.py`, `render.py`,
`selection.py`, `memory.py`, `models.py`, `config.py`). The status line says "40 tests"
(actual count is higher). The data model description omits the `blocked_by` column.
Update all three to match the shipped code. Do not add em dashes when editing.

### B5. Fill the test gaps

**Files:** `tests/` (matching each module).

Add tests, written first (TDD) for the Part A behavior changes:

- Scheduled job raises internally (mock Claude client raising) -> user receives a
  fallback message, and in `send_nags` a first-nag failure still attempts the second.
- Malformed / non-ISO deadline in one intent block -> that block degrades to a
  clarification, other valid blocks in the same message still apply (A3).
- `stop_reason == "max_tokens"` -> reply carries the too-long note (A4).
- Response with no text block -> `_text_of` returns the safe fallback, not "" (A5).
- Out-of-range task number -> clarification reply, no id resolved, no mutation (A6).
- `block_task` / `unblock_task` guard behavior: block a non-pending task returns None;
  unblock a done task returns None; `save_display(conn, id, [])` round-trips to `[]`
  (B2, B3).
- Slow-resurface exactly at `SLOW_RESURFACE_DAYS` (7) is eligible (boundary test).
- `config` env-readers: `db_path` default, `log_level` default and `.upper()`.

External APIs (Telegram, Anthropic) stay mocked at the boundary, per the project's
testing strategy. We do not test LLM output quality.

## Success criteria

- A scheduled job whose Claude call fails sends the user a plain fallback message; the
  failure is logged; the process keeps running. Verified by test and by inspection.
- A message containing one malformed intent still applies the valid intents in it.
- An over-long paste applies what it can and tells the user it was truncated.
- No send is ever attempted with empty text.
- An out-of-range task number produces a clarification, never a mutation of an
  unrelated task.
- `unblock_task` and `block_task` behave consistently with the other mutators and are
  safe independent of their callers.
- Dead code is gone; `CLAUDE.md` matches the shipped code.
- The full test suite passes, including the new tests above, and `ruff check` is clean.

## Non-goals

- No change to happy-path behavior, wording, rendering, or scheduling times.
- No move of Claude calls off the event loop; no change to the single-connection DB
  model.
- No new task-reference system, no "frog" understanding, no merge/group intent (separate
  product/UX spec).
- No prompt-caching, no model change.
