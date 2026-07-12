# Accountability bot — design spec

**Date:** 2026-07-12
**Status:** Approved (2026-07-12), implementation plan written

## Problem

The user falls into a recurring loop: tasks (especially boring or unpleasant ones)
get postponed, postponing breeds guilt and anxiety, anxiety reduces output, and the
backlog grows. Standard todo lists make it worse: a long list is itself the source of
overwhelm. The failure mode is **avoidance**, not forgetting. The user knows what to
do; they don't start.

A key personal fact drives the design: **persistent nagging genuinely works on this
user** (they cave and feel relief, not resentment). So the tool leans into persistence
rather than gentle passivity.

## Goal

A Telegram bot that holds the user's backlog, nudges them toward one focused thing each
day, and gets pointedly insistent about tasks they've been quietly avoiding, all in
plain language. It must fight overwhelm (small daily surface) and avoidance
(escalation on stale tasks) at the same time.

## Non-goals

- Not a scheduling/calendar app or a recurring-habit tracker.
- Not a full project manager. One flat backlog of tasks, no projects/subtasks/tags
  beyond a light priority + optional deadline.
- No web UI. Telegram is the only interface.

## Behavior

### Capture (must stay frictionless)

- The user texts the bot naturally at any time: *"call the accountant by friday"*,
  *"book the vet, important"*.
- Claude parses the message and extracts: task text, optional priority signal,
  optional deadline. Saves it to the backlog.
- If an apparently important task is missing context, the bot asks **once**. If the
  user doesn't answer, the task is saved with **no urgency**. Capture never blocks on
  a follow-up.
- Rationale: friction on capture kills the system. Adding a task must be as easy as
  sending a text.

### Daily focus message (06:00 every day)

Two parts in one message:

1. **Focus (Claude-written).** A short, human message naming the single top-priority
   task ("if you do one thing today, this") plus **at most 2** tasks that have gone
   stale and need rescuing. Max ~3 items surfaced with intent.
2. **Full backlog (plain code, no LLM).** A clean, deterministic dump of every pending
   task, ordered by priority, appended below the focus. This is rendered
   programmatically — never sent through Claude — so it is cheap and, more importantly,
   can never be reworded, reordered, or hallucinated.

Ordering: focus at the top, full list underneath as reference.

### Nagging (within the day)

- The bot pings about the active focus task up to 3 times a day: **morning, midday,
  evening**.
- If the focus task is still untouched by evening, the tone gets more direct.
- **Quiet hours, absolute:** never before 06:00, never after 23:00. The bot must never
  add to what keeps the user up at night.

### The avoidance hunter (core differentiator)

- **Detection: pure age.** Any pending task that has existed **3 days** without being
  completed is flagged as stale. (Threshold is a single tunable constant.)
- **Response: get curious first, then get louder** (user chose "A + B"):
  1. On first flagging, the bot asks what is actually blocking the task and offers to
     break it down or kill it: *"'Sort the insurance' has sat 4 days. What's actually
     blocking it? Want to break it down or drop it?"*
  2. If the user keeps dodging, the bot escalates: more frequent, blunter
     (*"9 days now. It's a 2-minute call. Do it or delete it."*).
- The "get curious" step is the humane guard against pure-age false positives: a
  genuine "someday" task just gets *"nothing, it's a someday thing"* and the bot
  backs off (de-prioritizes / stops escalating that task).

### Completion

- The user says *"done with the taxes"* and the bot marks it complete.
- Acknowledgment is **plain and brief** (*"Done, nice."*). No cheerleading, no
  positive-reinforcement theater.

### Interaction style

- **Natural language, Claude-powered.** No rigid command syntax required. Every
  inbound message is interpreted by Claude with the current task list as context, and
  classified into an intent: add task / complete task / defer or answer a nag /
  ask a question (e.g. "show me everything") / explain a blocker.
- The one deterministic exception is the **full backlog dump**, which is always
  rendered by plain code.

## Architecture

One small program, four cooperating pieces, modeled on existing homelab services.

| Piece | Responsibility | Template on `jarvis` |
|-------|----------------|----------------------|
| Telegram layer | Receive user messages, send pings | `billie-bot` |
| Brain (Claude) | Interpret each message against the task list; classify intent; write focus + nags + replies | `billie-bot` |
| Storage (SQLite) | Persist the backlog | `fitness-data` |
| Scheduler (APScheduler) | Fire 06:00 focus, midday/evening nags, daily stale-scan | `fitness-data` |

**Division of labour (important):** Claude handles anything needing judgment or tone
(parsing, intent, focus text, nag text, the curious/escalating messages).
Deterministic code handles anything mechanical (the backlog dump, the stale-age
calculation, priority ordering, quiet-hours enforcement, scheduling).

### Data model (SQLite, single `tasks` table)

- `id`
- `text`
- `priority` (nullable light signal, e.g. normal / important)
- `deadline` (nullable date)
- `created_at`
- `status` (pending / done / dropped)
- `last_nagged_at` (nullable)
- `completed_at` (nullable)

Escalation intensity is derived from age past the 3-day threshold; no separate
defer-count column is required for the chosen design, though `last_nagged_at` governs
nag cadence.

### Stale detection rule

A task is stale when `status = pending` and `created_at` is more than **3 days** ago.
This is a pure function of the row + current time, so it is unit-testable without any
Telegram or Claude calls.

### Daily-focus selection rule

From the pending tasks:
- **Focus item:** the single highest-priority task (ties broken by nearest deadline,
  then oldest).
- **Rescues:** up to 2 stale tasks not already the focus item.
- These feed the Claude-written focus; the full ordered list feeds the plain dump.

Also a pure, testable function.

## Deploy & data flow

- Code lives in this repo (`to_do`), pushed to a new GitHub repo.
- A self-hosted GitHub runner on `jarvis` auto-deploys the container on push (same
  pattern as `fitness-data`).
- The container runs under `/home/gautier/docker/<service>/`; the SQLite file lives in
  a bind-mounted `./data/` dir so it survives restarts and is captured by the nightly
  restic → Backblaze B2 backup.
- Secrets (Telegram bot token, Anthropic API key) live in a `.env` file on the server,
  never committed to git.

## Testing

TDD on the pure logic, which is where the real behavior lives:

- Task parsing (given a Claude-extracted structure → correct row). Claude call mocked.
- Stale-detection rule (age boundary at exactly 3 days).
- Daily-focus selection (priority / deadline / age tie-breaking; rescue selection;
  the ≤3 surfaced cap).
- Priority ordering for the backlog dump.
- Quiet-hours enforcement (no ping scheduled or sent before 06:00 / after 23:00).

Telegram and Claude are mocked in tests so the suite is fast, free, and never spams the
user.

## Tunable defaults (chosen)

1. **Stale threshold = 3 days.**
2. **Quiet hours = 06:00–23:00.**
3. **Nag times = morning / midday / evening; daily focus at 06:00.**
4. **Completion acknowledgment = plain and brief, no cheerleading.**

## Open items for the plan

- Exact Telegram library and whether to use polling or webhook (billie-bot's choice is
  the default to follow).
- Exact Claude prompt/schema for message interpretation and intent classification.
- The precise container/compose layout and runner wiring (copy `fitness-data`).
