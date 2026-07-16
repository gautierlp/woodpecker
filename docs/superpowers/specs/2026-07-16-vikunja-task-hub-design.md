# Vikunja as task hub, Jolt as the nagging brain

Date: 2026-07-16
Status: approved (design), pending implementation plan

## Context

Jolt today is a self-contained Telegram accountability bot: it stores its own
tasks in SQLite, renders them, and nags. It has been in use for only a couple of
days. Separately, the wider goal is a "life OS" on the homelab where many sources
(email needing a reply, recurring obligations like the monthly tax
declaration, finance events) automatically create to-do items in one central hub,
with a real UI on mobile/desktop.

A custom bot is a poor central hub: it has no UI, no mobile apps, and no inbound
API for other services to push tasks into. Vikunja is a mature, battle-tested,
self-hostable task manager with a REST API, native outbound webhooks, official
apps, and an official n8n community node. So we split responsibilities:

- **Vikunja owns everything it is good at**: storage, the task model, the UI/apps,
  priorities, due dates, manual ordering, dependencies, and (later) being the
  target that automations write into.
- **Jolt keeps only the thing that makes it unique**: the nagging intelligence
  (daily focus selection, escalating nags, quiet hours) and a conversational
  quick-capture / quick-complete interface over Telegram.

Guiding principle from the design discussion: lean on Vikunja as much as
possible, even where that means tearing down existing Jolt behaviour. Jolt is
young; do not over-engineer to preserve its features. The important part is the
nagging intelligence.

This is piece 1 of a larger sequence. Pieces 2 (stand up n8n) and 3 (the first
glue flow, e.g. taxes or email to task) are out of scope for this spec and get
their own spec/plan cycles.

## Goals

- Stand up Vikunja on jarvis as the single source of truth for tasks, with a
  public UI at `tasks.example.com` and coverage by the existing nightly backup.
- Refactor Jolt so its storage half reads from and writes to Vikunja via the
  REST API instead of its own SQLite tasks table, keeping its nagging logic
  intact.
- Migrate the current live Jolt tasks (and recent history) into Vikunja, and
  preserve the nag cadence across the switch.

## Non-goals

- n8n, any glue flow, or any external source creating tasks (later pieces).
- Instant-on-arrival nagging via Vikunja webhooks (Jolt reads live, so it will
  see externally created tasks on its next cycle; webhook-driven instant nags are
  a possible later enhancement).
- Preserving Jolt as a full task manager. Editing (reprioritise, change deadline,
  rename, dependencies) moves to the Vikunja app.

## Key decisions

Settled during brainstorming:

1. **Data ownership: thin client, Vikunja is truth.** Jolt holds no copy of
   tasks. It queries the Vikunja REST API live, applies its selection/nag logic,
   and writes changes straight back. Jolt keeps only a small sidecar SQLite table
   for its own state (`last_nagged_at` and the numbered `display_snapshot`), keyed
   by Vikunja task id.
2. **Nag scope: one dedicated project.** Jolt governs exactly one Vikunja project
   ("Backlog"). Everything in it is fair game for nagging and daily focus; every
   other Vikunja project is invisible to Jolt. Future automations route
   actionable items into Backlog.
3. **Cutover: import pending + recent (90-day) done history.** One-time script.
   Accepts the trade-off that Vikunja is not a stats tool, so imported history is
   just browsable closed tasks.
4. **Refactor shape: storage swap behind Jolt's existing functions (approach A).**
   No `TaskStore` interface abstraction (YAGNI for a single-user bot with one
   chosen backend). The `Task` dataclass stays as Jolt's internal shape.
5. **`dropped` status: delete the Vikunja task.** No label, no archive project.
   Jolt's three-state status collapses to Vikunja's `done` boolean plus deletion.
6. **`blocked_by`: dropped from Jolt for v1.** Vikunja has native dependencies in
   its UI if ever wanted; Jolt does not model or manage them. Revisit only if
   nagging about a blocked task becomes annoying.
7. **Ordering: Jolt honors Vikunja's manual order as the selection tiebreaker.**
   When Jolt's own signals (priority, deadline, staleness) are equal, the Vikunja
   manual `position` breaks the tie, so dragging a task up in Vikunja influences
   what Jolt surfaces. Vikunja is the control surface; Jolt keeps the intelligence.

## Architecture

### New service: Vikunja (on jarvis)

- Single container at `~/docker/vikunja/`, **SQLite backend** in a bind-mounted
  file. Rationale: single-user, and a bind-mounted SQLite file is captured
  directly by the existing restic to Backblaze B2 run (it lives under
  `~/docker`), so it needs no nightly dump script (unlike finance/inbox-zero/cap,
  which sit in named volumes).
- Bound to `127.0.0.1:3456` plus the tailnet, fronted by nginx-proxy-manager at
  `tasks.example.com` with Let's Encrypt, so the Vikunja web and mobile apps work
  remotely. Same loopback-plus-NPM pattern as the other public services.
- Runs entirely on jarvis. Known trade-off (accepted): the task list is
  unreachable while jarvis is down; restic still protects against data loss.
  Genuinely time-critical items (e.g. tax deadlines) should additionally be
  mirrored off-box (e.g. Google Calendar) in later pieces, not relied on from
  jarvis alone.

### Jolt component map (approach A)

| File | Change |
|---|---|
| `vikunja.py` | New. REST client scoped to the Backlog project: create, list-open, get, mark-done, delete, plus reading priority / due_date / position. Auth via API token. |
| `db.py` -> `sidecar.py` | Repurposed. SQLite holding only `last_nagged_at` (keyed by Vikunja task id) and the `display_snapshot` numbered-list table. No task rows. |
| `orchestrator.py` (storage calls) | Rewired to compose `vikunja.py` + `sidecar.py`, returning the same `Task` objects the rest of the code expects. |
| `models.py` | `Task` dataclass unchanged; `id` now carries the Vikunja task id. `blocked_by` removed. |
| `selection.py` | Unchanged logic plus one addition: Vikunja `position` as the tiebreaker. |
| `render.py` | Trimmed: Jolt shows only what it surfaces (focus + rescues), not a full browsable backlog list. |
| `llm.py`, `bot.py`, `scheduler.py`, `memory.py` | Untouched. They consume `Task` and storage functions. |
| `config.py` | Adds `VIKUNJA_URL`, `VIKUNJA_TOKEN`, `VIKUNJA_PROJECT_ID`. |

## Field mapping (Jolt `Task` <-> Vikunja task)

| Jolt field | Vikunja field | Notes |
|---|---|---|
| `id` | task `id` | Vikunja assigns it. |
| `text` | `title` | Description left empty for now. |
| `priority` (`normal`/`important`) | `priority` (0-5) | `important` -> 4 (High), `normal` -> 0 (Unset). Reverse: `>= 4` reads back as `important`. |
| `deadline` (date) | `due_date` (datetime) | date -> end-of-day Europe/Paris. Reverse: take the date part. |
| `created_at` | `created` | Vikunja owns it (read-only). |
| `completed_at` | `done_at` | Set by Vikunja on completion. |
| `status = pending` | `done = false` | The open pool. |
| `status = done` | `done = true` | Completion. |
| `status = dropped` | task deleted | No Vikunja equivalent; remove it. |
| (n/a) | `position` | Read-only input to selection tiebreaker. |
| `last_nagged_at` | not in Vikunja | Lives in `sidecar.py`, keyed by Vikunja task id. |

`blocked_by` is not mapped (removed from Jolt for v1).

## Data flow

```
1. Quick-capture (Telegram):
   "add call plumber !important" -> llm.py parses -> orchestrator
   -> vikunja.create_task(Backlog, priority=High) -> reply

2. Daily focus (06:00) / nags (09:00, 13:00, 19:00):
   scheduler -> vikunja.list_open(Backlog) -> merge last_nagged_at from sidecar
   -> selection.py picks focus + rescues (priority, deadline, staleness,
      Vikunja position as tiebreaker) -> render -> Telegram
   -> write last_nagged_at back to sidecar

3. Complete from a nag ("1 done"):
   resolve "1" via display_snapshot (sidecar) -> vikunja.mark_done(id) -> reply

4. External source (future pieces: n8n / email-to-task):
   creates a task directly in Vikunja Backlog -> Jolt's next read (path 2)
   picks it up automatically. No webhook needed, because Jolt reads live.
```

Path 4 is the payoff of the thin-client choice and is why this piece unblocks the
rest: anything that writes into Backlog is visible to Jolt for free.

## Migration (one-time script, jolt repo)

- Reads the current `jolt.db`. Pending tasks -> created in Vikunja Backlog
  (title, mapped priority, due_date). Done tasks from the last 90 days -> created
  then marked done. Dropped tasks -> skipped.
- Seeds the new sidecar with `last_nagged_at`, mapped old-id -> new Vikunja-id,
  so the nag cadence continues across the switch.
- Safety: aborts if the Backlog project is not empty, so a re-run cannot create
  duplicates. Prints a summary.
- Known one-time effect (accepted, not worked around): Vikunja sets `created`
  itself on import, so migrated tasks' staleness clock resets to migration day.
  Negligible given only a couple of days of prior use. Future tasks use Vikunja's
  real `created`.

## Error handling

Jolt now depends on Vikunja, so this is load-bearing:

- **Vikunja unreachable, interactive message:** retry once (Jolt's existing
  transient-error pattern), then a friendly reply to the user plus the detailed
  error to the private debug chat.
- **Vikunja unreachable, scheduled job (focus/nags):** log, ping the debug chat,
  skip that run cleanly. The scheduler must never crash; the next cycle retries.
- **Task in a nag no longer exists** (deleted in the Vikunja app): completion
  returns 404 -> Jolt says it is already gone and moves on.
- **Bad/expired API token:** clear error to the debug chat.

## Testing

Same philosophy Jolt already uses: mock external APIs at the boundary, no real
HTTP.

- New `vikunja.py` client tested against recorded/mocked HTTP responses.
- Field-mapping round-trip tests (`Task` <-> Vikunja JSON): priority,
  date <-> datetime, done, delete. Main new risk surface.
- `selection.py`: add a test for the Vikunja-`position` tiebreaker.
- Migration script: run against a fixture `jolt.db`, assert the right
  create / mark-done calls plus sidecar seeding.
- Existing `selection` / `render` / `llm` tests stay green (still operate on
  `Task`).

## Deployment

Order:

1. Stand up Vikunja (compose in the homelab repo, `~/docker/vikunja/`), bound to
   loopback + tailnet, NPM host `tasks.example.com`.
2. Create the "Backlog" project and an API token.
3. Run the migration script.
4. Push the refactored Jolt; its existing self-hosted GitHub runner auto-deploys.
   Add the new Vikunja env vars to `jolt/.env` on jarvis.

Vikunja's SQLite DB joins the restic set automatically via the bind mount.

## Future (out of scope here)

- Piece 2: stand up n8n as the deterministic glue layer (loopback + tailnet).
- Piece 3: first glue flow (taxes recurring obligation, or email-to-task from
  inbox-zero) writing into the Vikunja Backlog.
- Optional later: Vikunja webhook -> Jolt for instant-on-arrival nagging.
- Optional later: revisit task dependencies if blocked-task nagging becomes a
  problem.
- Mirror genuinely time-critical items off-box (e.g. Google Calendar).
