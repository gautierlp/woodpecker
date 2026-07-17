# Bump-aware escalation and a frog-first morning message

**Status: design approved, spec for implementation.**

## Problem

Jolt's whole reason to exist is breaking the postpone / guilt / paralysis loop. But
today it measures avoidance by *creation age* ("stale" = created 3+ days ago), which
is not how the loop actually manifests for this user. The real loop, confirmed against
live data, is **due-date slippage**: an important task gets a due date of "tomorrow",
then tomorrow it is bumped to the day after, then again, indefinitely, while the task
stays undone. Because the bump is pre-emptive (the date is moved forward *before* it
lapses), the task never shows as overdue, so a simple "is it overdue?" check would
never catch it.

Two concrete failings observed:

1. **The morning message is a full backlog dump.** The 06:00 message appends the entire
   backlog (34+ tasks). Jolt should push on what is being avoided, not act as a
   reference list for everything.
2. **Jolt does not push hard enough**, and it cannot push on the right thing because it
   cannot see the bump loop at all.

## Live-data findings (2026-07-17, project 2 "Backlog", 34 open tasks)

- mdone writes the due date straight into Vikunja's `due_date` field. There is **no
  hidden date sentinel and no separate start date** (0 tasks use `start_date`). The
  reschedule signal can be read directly from `due_date`.
- **18 of 34 open tasks are due "today" and 0 are overdue** - the smoking gun of the
  pre-emptive bump loop. "Due today" is therefore ~half the list and useless as a
  noise filter on its own.
- Priorities are real and usable: 21 unset (0), 4 low (1), 6 medium (2), 2 high (3),
  1 urgent (4). "Important" is a trustworthy signal.
- Vikunja has **no due-date history**. To detect slippage, Jolt must snapshot the due
  date itself and compare over time. Counters therefore start at 0 and build from the
  day this ships; day one is blind and the signal sharpens daily.

## Core concept: the bump count is the avoidance signal

For each open task, Jolt remembers the last due date it saw and a **bump counter**. When
a still-open task's due date moves *forward*, the counter increments. That count, not
creation age, becomes the measure of how hard a task is being dodged, and it drives
both which task leads the day (the "frog") and how hard Jolt pushes on it.

This **supersedes creation-age staleness** as the avoidance driver **for important
tasks** (the frog and its escalation): `is_stale` (creation age) no longer selects or
ranks the important task Jolt pushes on. The existing **low-priority slow-resurface
drop-nudge is out of scope here and unchanged** - it keeps keying off creation-age
staleness for priority-0/low tasks (which typically have no due date to bump anyway).

## Non-goals

- Tracking start dates, labels, progress/percent, or task creation flow (unchanged).
- Backfilling historical bump counts (no history exists; we start from zero).
- Nagging tasks that have no due date (they cannot be "bumped"; see Morning message).
- Any UI or config surface for tuning; all knobs are named constants in `config.py`.
- Punishing a user for pulling a due date *in* or completing a task.

## Detailed design

### 1. Detection: watch the due date move (sidecar)

New sidecar table, e.g. `bump_state(task_id INTEGER PRIMARY KEY, last_due_date TEXT,
bump_count INTEGER NOT NULL DEFAULT 0)`. On each scan of open tasks:

- **No stored record** for a task with a due date: insert it with the current due date
  and `bump_count = 0`.
- **Current due date > stored** (moved forward): `bump_count += 1`, update
  `last_due_date`.
- **Current due date < stored** (pulled in) or **equal**: update `last_due_date`, leave
  `bump_count` unchanged. Pulling a date in is not punished.
- **Task has no due date**: not tracked. If a due date is later added, tracking starts
  then (as a fresh record, count 0). If a due date is later removed, the record is
  cleared / left stale (no-op for selection since it needs a date).
- Completed/dropped tasks fall out of `list_open` (done=false filter), so they stop
  being scanned; their rows are harmless leftovers and may be pruned opportunistically.

The scan runs as part of the scheduler tick (below), reading live from Vikunja across
all projects via the existing `list_open`.

Comparison is on the **date**, not the timestamp (mdone stores due dates at `T00:00`
or `T23:59` on the same day; only the calendar day matters).

### 2. The frog

The **frog** is the single most-avoided important task, the lead of the day and the
target of escalation.

- Eligible: open, `priority >= MATTERS_MIN_PRIORITY` (see cutoff below), has a due date.
- Frog = the eligible task with the **highest bump_count**; ties broken by higher
  priority, then oldest `created_at`.
- **Day-one / no-bumps fallback:** if no eligible task has been bumped yet
  (`bump_count == 0` everywhere), the frog falls back to the highest-priority eligible
  task that is due today or overdue (then oldest), so the morning message still leads
  with something sensible while bump data accrues.

### 3. Escalation: blunter and more frequent with the bump count

Two dimensions, both driven by the frog's `bump_count`.

**Bluntness** ramps continuously and starts early - even 1-2 bumps is already pointed,
not gentle. An escalation *level* derived from the bump count is passed to the LLM,
which also receives the raw count so it can name it. Default mapping (tunable):

- 1 bump: firm and specific ("this has already slipped once").
- 2 bumps: blunt, names the streak.
- 3-4 bumps: harsher, calls out the pattern ("you've moved this N days running").
- 5+ bumps: flat ultimatum ("do it today or kill it").

No em dash in any generated copy (project-wide rule).

**Frequency** also scales with the bump count. An ordinary task keeps a light cadence;
a chronic frog can be pinged **up to `MAX_FROG_PINGS_PER_DAY` (default 6) times a day**
at pseudo-random times, not the old fixed 09:00/13:00/19:00 slots. Default mapping
(tunable): `pings_per_day = min(MAX_FROG_PINGS_PER_DAY, 1 + bump_count)`.

All pings fire only within the **active window 06:00-22:00** (nothing after 22:00 or
before 06:00), and never closer together than `MIN_PING_GAP_MINUTES` (default ~90).

### 4. Scheduler model change: a frequent tick instead of fixed slots

This is the meatiest change. The fixed cron at 09:00/13:00/19:00 is replaced by a
**frequent tick** (`SCHEDULER_TICK_MINUTES`, default 30). On each tick, within the
active window, Jolt:

1. Scans open tasks and updates bump counts (detection, above).
2. Picks the frog.
3. Decides whether to ping now, spreading the frog's pings across the remaining window
   so that roughly `pings_per_day` land over the day, with jitter so times are not
   predictable, respecting `MIN_PING_GAP_MINUTES` (gated by `last_nagged_at` in the
   sidecar).

The 06:00 daily focus job stays a distinct, once-a-day job. Lower-priority / non-frog
tasks retain a light touch (at most the existing slow-resurface behavior); the tick's
extra pings are reserved for the escalating frog.

### 5. Morning message (06:00): frog-first, no dump

- **Heavy lead on the frog**, with its bump count called out ("you've pushed this N
  days in a row"). If duration/notes/project metadata exist, they feed the lead as
  today (from the multi-project metadata work).
- **Then, uncapped:** every open task with `priority >= MATTERS_MIN_PRIORITY` that is
  **due today or overdue**, priority-flagged. No cap (the priority filter already keeps
  it tight - ~9 tasks today).
- A task with **no due date is not shown**, even if high priority.
- **One summary line** for the rest, e.g. "+ 21 low-priority tasks (say 'list' to see
  them)." Unset/low tasks are never listed individually here.
- The full backlog remains one "list" request away, unchanged.

### 6. Priority cutoff

`MATTERS_MIN_PRIORITY = 2`. Vikunja's 0-5 scale maps to mdone as
0 unset, 1 low, 2 medium, 3 high, 4 urgent, 5 critical. "Critical + urgent + high +
medium" = priority >= 2. Low (1) and unset (0) are excluded from the morning "matters"
list and from frog eligibility. (This is a morning-message/frog cutoff and is separate
from the existing three-band nag *stance*, High >= 3 / Mid 1-2 / Low 0, which continues
to shape wording.)

## Data model changes

- New sidecar table `bump_state` (task_id, last_due_date, bump_count) as above.
- `nag_state` (task_id, last_nagged_at) unchanged, now consulted for the per-ping
  minimum-gap check.
- No changes to Vikunja / the task store's source of truth.

## Config tunables (all named constants in `config.py`)

- `MATTERS_MIN_PRIORITY = 2`
- `MAX_FROG_PINGS_PER_DAY = 6`
- `MIN_PING_GAP_MINUTES = 90`
- `SCHEDULER_TICK_MINUTES = 30`
- Active ping window: `06:00`-`22:00` (start reuses `QUIET_START_HOUR`; add a
  `PING_END_HOUR = 22` distinct from the existing `QUIET_END_HOUR = 23` used elsewhere).
- Bump -> bluntness-level thresholds and bump -> pings/day mapping as above.

## Edge cases

- **Day one:** all bump counts 0; frog falls back to top-priority due-today/overdue.
- **A task with no due date:** never tracked, never a frog, never in the morning list.
- **Due date removed after being tracked:** record goes stale; task drops out of
  frog/morning consideration (needs a date).
- **A legitimate reschedule** (a genuinely moved appointment) still counts as a bump.
  Accepted: for a single-user avoidance bot, forward-move == postpone is a fine
  heuristic, and pulling the date back in or finishing clears the pressure.
- **Multiple heavy frogs:** only one frog leads and escalates at a time (the top one);
  the others still appear in the morning "matters" list by priority.
- **Quiet window:** no ping fires outside 06:00-22:00, regardless of bump count.

## What stays the same

- Vikunja as source of truth; multi-project read; add / complete / drop / list intents.
- The full backlog via "list" on demand.
- The three-band nag stance wording and the duration/notes/project prompt context.
- The 06:00 daily-focus job (its *content* changes; its once-a-day timing does not).
- The low-priority slow-resurface drop-nudge (still creation-age based, unchanged).

## Testing strategy

- **Detection:** forward move increments; pull-in and equal do not; no-date not
  tracked; date-added-later starts fresh. (sidecar/store unit tests, boundary on the
  calendar-day comparison.)
- **Frog selection:** highest bump_count wins; tie-breaks; day-one fallback to
  top-priority due-today.
- **Escalation mapping:** bump -> bluntness level and bump -> pings/day are pure
  functions with boundary tests (0, 1, 2, 3, 5, and the cap at 6).
- **Scheduler tick:** given a clock and last_nagged_at, decides ping/no-ping correctly
  inside vs outside the 06:00-22:00 window and respects the min-gap; determinism is
  achieved by injecting the clock and seeding/injecting the jitter source.
- **Morning message:** frog leads with bump count; matters list = priority>=2 due
  today/overdue, uncapped; no-date high-priority excluded; the "+N low-priority"
  summary line present and correct.
- Not tested: actual LLM wording quality, Telegram plumbing (mocked at the boundary),
  real HTTP.

## Suggested build order (for the plan)

1. **Detection** (sidecar `bump_state` + scan/update in the read path) - starts
   accruing bump data immediately, no behavior change yet.
2. **Frog selection + morning message** reshape (uses bump data, day-one fallback).
3. **Escalation engine** (the scheduler-tick rewrite: frequency + bluntness). Meatiest
   and last, so bump data has begun accruing before it goes live.
