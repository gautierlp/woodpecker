# Multi-project reading and metadata-aware nags

Date: 2026-07-16
Status: approved (design), pending implementation plan

## Context

Since the Vikunja cutover (see `2026-07-16-vikunja-task-hub-design.md`), Jolt is a
thin client over Vikunja: it reads open tasks, applies its selection and nag
logic, and writes back completions. But it reads a deliberately narrow slice of
each task. `vikunja_to_task` maps only title, a binarized priority
(`>= 4` becomes "important", everything else "normal"), due date, created,
done/done_at, and position. It reads a single project (Backlog, `VIKUNJA_PROJECT_ID=2`)
and ignores the task description entirely.

Two gaps follow from that:

1. **Scope.** Jolt sees only the Backlog project. The user manages tasks across
   multiple Vikunja projects (Inbox today, more to come) via the mdone app, and
   wants Jolt to nag about all of them, present and future.
2. **Signal.** The user sets richer metadata that Jolt throws away: the full
   5-level Vikunja priority, an estimated duration, and sometimes a description.
   Duration is not a native Vikunja field: mdone stores it as an HTML-comment
   sentinel `<!-- mdone:estimate=SECONDS -->` inside the `description` field
   (verified live: a task set to "1h" in mdone reads `description =
   '<!-- mdone:estimate=3600 -->'`). So the estimate is reliably parseable, it is
   just hidden inside a field Jolt never reads.

Guiding principle: lean on the metadata the user actually maintains, and make the
nags reflect it in both *what* gets surfaced and *how* it is worded. Keep the
change focused on enrichment plus cross-project reading; do not turn Jolt back
into a task manager.

## Goals

- Jolt reads open tasks from **every** Vikunja project, discovered live each
  cycle, so newly created projects are included automatically with no config
  change.
- Each task carries its full priority, its parsed duration estimate (if any), its
  clean visible description, and its project name.
- Priority drives a **three-band** stance (push / poke / drop-nudge) instead of
  today's binary.
- Duration acts as an **avoidance-buster**: bigger, avoided, important tasks lead
  the daily focus, and the nag wording uses the estimate to force a small first
  step rather than a hollow "just do it".
- The visible description is available to the nag and morning-focus prompts as
  context Jolt can quote.

## Non-goals

- **Progress / `percent_done`** (the mdone progress slider). Easy to add later,
  but out of scope here to keep the surface small.
- Labels, start/end dates, and mdone's "Mark as Current". Not read.
- Changing how tasks are *created*: new tasks added over Telegram still land in
  one default write project (Backlog). Only reading goes multi-project.
- Instant webhook-driven nags. Jolt still reads live on its own cycle.
- A UI or config surface for tuning the band thresholds. They are constants in
  `config`, edited in code (and therefore easy to change), not user-facing knobs.

## Key decisions

Settled during brainstorming:

1. **Read all projects, write to one.** The current single-`project_id` binding
   in `VikunjaClient` is split into two concerns: reading enumerates
   `GET /api/v1/projects` and reads each project's list view, merging results;
   writing (task creation from Telegram) still targets the configured default
   project. `VIKUNJA_PROJECT_ID` is retained as that default write project.
   The global `/api/v1/tasks/all` endpoint is rejected (HTTP 400, code 2004) by
   the deployed Vikunja 2.3.0, so per-project reads via the list-view endpoint
   (the pattern already proven for Backlog) are the reliable route.

2. **Duration is parsed from the description sentinel.** Read
   `<!-- mdone:estimate=SECONDS -->` out of `description` into
   `estimate_seconds: int | None`. Strip the sentinel to produce the clean
   visible description (`details`). No sentinel present means `None`, and Jolt
   simply does not reason about time for that task.

3. **Priority in three bands.** Default thresholds (constants, tunable):
   - **High** (`priority >= 3`, i.e. high / urgent / do-now): push hard, name the
     blocker, break into a first step.
   - **Mid** (`priority` 1-2, low / medium): gentle poke, no drop pressure.
   - **Low / unset** (`priority == 0`): nudge toward dropping with a zero-based
     question.
   This replaces the current binary. Priority-0 tasks (most of today's Backlog)
   keep getting the drop-nudge, so nothing becomes more aggressive by accident.

4. **Duration is an avoidance-buster (not a quick-win engine).**
   - *Selection:* among stale High-band tasks competing for the daily focus and
     rescue slots, longer estimated duration ranks higher. Applied as a tie-break
     after priority band and staleness, before due date.
   - *Wording:* a long High task's push nag uses the estimate to demand a small
     first slice ("this is ~4h, you will not clear it today, what is the
     15-minute first piece?"). A short High task gets "this is only 15 min, knock
     it out now". "Long" defaults to roughly `>= 1h` for the wording split.

5. **Description feeds the prompts.** The cleaned visible description is passed
   into `write_nag` and `write_focus` as extra context.

## Affected components

- `models.py`: `Task` stores the raw integer `priority` (0-5) plus
  `estimate_seconds: int | None`, `details: str` (clean description),
  `project_id`, `project_name`. The three bands are derived from the raw
  priority by a helper in `selection.py`, not stored, so the mapping lives in one
  place. (This replaces the current binary `priority` string on the read model.
  The `PRIORITY_IMPORTANT` / `PRIORITY_NORMAL` constants stay, but only as the
  task-*creation* vocabulary: `Intent.priority`, `task_create_payload`, the LLM
  add-tool enum, and the add default in the orchestrator. Every read-model
  `task.priority == PRIORITY_*` comparison is replaced by a band check.)
- `vikunja.py`: `vikunja_to_task` parses priority band, estimate sentinel, and
  clean description; a new read path enumerates projects and merges their
  list-view tasks; the write path keeps a single default project.
- `selection.py`: `priority_sort_key`, `nag_stance`, and the focus/rescue
  selection move from the priority binary to the three bands and add the duration
  tie-break for High-band stale tasks.
- `llm.py`: `write_nag` and `write_focus` receive band, duration, project, and
  clean description, and the guidance strings gain the long/short duration
  framing.
- `config.py`: band thresholds, the long-duration cutoff, and the default write
  project id as named constants.
- Rendering (`render.py`): the numbered list stays as-is by default. Surfacing
  project or estimate in list output is optional polish, decided in the plan only
  if it is cheap; the feature does not depend on it.

## Testing

Follow the repo's TDD convention (tests first, confirm red, then green):

- `vikunja_to_task`: sentinel parsing (present / absent / malformed), clean
  description extraction, band mapping across priority 0-5.
- Multi-project read: merges tasks across projects, includes a
  newly-appearing project, tolerates a project with zero open tasks (Inbox
  today), and still paginates per project.
- Selection: band ordering, and the duration tie-break picking the longer stale
  High task as focus.
- `write_nag` / `write_focus`: assert the assembled prompt carries band,
  duration framing, project, and description (the Anthropic call itself is
  mocked, as in the existing `test_llm.py`).

## Open defaults (reversible, tune anytime)

- Priority band cutoffs: `>= 3` High, `1-2` Mid, `0` Low.
- Long-duration cutoff for wording: `>= 3600s` (1h).
