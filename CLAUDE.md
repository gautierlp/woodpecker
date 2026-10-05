# Woodpecker

**Context card:** `~/vault/10 Projects/woodpecker/_project.md` (status, goals, links). Read it first.
Decisions with their reason and logs of what was done go to that vault folder (never run git there); code docs, specs and plans stay in this repo.

Telegram bot **woodpecker** (called Jolt until 2026-10-05) that fights task avoidance. Holds a personal backlog, nudges toward one
focused thing each day, and gets pointedly insistent about tasks that have been quietly
postponed. Plain-language interface powered by Claude.

Private single-user project, not a SaaS. Built to break a specific personal loop
(postpone, guilt, paralysis), so it leans into persistent nagging by design.

**Status: deployed and running, now backed by Vikunja.** The full app (bot, LLM,
tasks, staleness, scheduler, entry point) plus Docker and the auto-deploy workflow
are in place, and the task store has been refactored onto Vikunja as the source of
truth. Live on `jarvis` as the `jolt` container (bot `@jolt_todo_bot`), with the
self-hosted runner `gh-runner-jolt` auto-deploying on push to `main`. Those three names,
the checkout `/home/gautier/docker/jolt` and the compose service keep the old name for now:
a new service name would make the next deploy start a second bot beside the old one. The sidecar
SQLite file (nag state + display snapshot, not tasks) lives in the bind-mounted
`./data`. See `docs/superpowers/specs/2026-07-12-accountability-bot-design.md` for
the original behavior and rationale, `docs/superpowers/plans/2026-07-12-jolt-implementation.md`
for the original implementation plan, and `docs/CUTOVER.md` for the Vikunja cutover
runbook.

## Architecture

Single Python application in a Docker container on the homelab host `jarvis`.
**Vikunja is the task store** (source of truth for the backlog, reached over its
REST API); **Woodpecker is the nagging brain over it**. Woodpecker keeps a small sidecar
SQLite file for state that is its own, not Vikunja's: nag timestamps and the last
rendered backlog order. No copy of the task list is kept in Woodpecker; every read goes
live to Vikunja.

External services:
- **Vikunja API**: the task backlog (create, list open, get, mark done, delete)
- **Telegram API**: messaging (receive user texts, send daily focus and nags)
- **Anthropic API**: Claude interprets each inbound message and writes the focus,
  nags, and replies

### Division of labour (core principle)

- **Claude** handles anything needing judgment or tone: parsing a text into a task,
  classifying intent (add / complete / drop / list / answer), and writing the
  daily focus, the nags, and the curious/escalating avoidance messages.
- **Deterministic code** handles anything mechanical: the full backlog dump, the
  stale-age calculation, priority ordering, quiet-hours enforcement, and scheduling.

The full-backlog dump is never sent through Claude. A mechanical list must never be
reworded, reordered, or hallucinated, and it is cheaper as plain code.

## Tech Stack

- **Python 3.12+**
- **uv**: dependency management (lockfile, virtualenv)
- **ruff**: linting and formatting
- **pytest**: unit tests per module, external APIs mocked at the boundary
- **Docker + docker-compose**: deployment

Key libraries:
- `python-telegram-bot`: Telegram handler
- `anthropic`: Claude API client
- `httpx`: Vikunja REST client
- `APScheduler`: cron-based scheduler (06:00 focus, midday/evening nags, daily stale-scan)
- `sqlite3` (stdlib): sidecar storage (nag state + display snapshot only)

## Code Conventions

- All code, comments, variable names, commits, and docs in **English**. French only
  appears in runtime content (bot messages to the user), if at all.
- Style enforced by **ruff**: no manual style discussions.
- Commits: conventional style (`feat(...)`, `fix(...)`), concise.
- No over-engineering: single-user personal project. No abstractions for hypothetical
  future needs.
- Behavior tunables (stale threshold = 3 days, quiet hours = 06:00 to 23:00) are single
  named constants, easy to change after living with the bot.

## Module Responsibilities

Following the `billie_bot` shape:

```
src/woodpecker/
  main.py         Entry point: wires up bot + scheduler, builds the clients, starts the app
  bot.py          Telegram handler: receives messages, sends replies and nags
  llm.py          Anthropic client: interprets messages, classifies intent, writes text
  orchestrator.py Applies a parsed intent (via the Store) and returns the reply text
  selection.py    Pure rules: stale detection, daily-focus selection, slow-resurface, quiet hours
  render.py       Deterministic backlog rendering and display ordering
  memory.py       In-process recent-conversation and pending-outbound memory per chat
  scheduler.py    Job bodies: daily focus and nags (wired to APScheduler cron in main.py)
  vikunja.py      VikunjaClient: REST calls to Vikunja (create/list/get/mark done/delete) and
                  the Task <-> Vikunja JSON field mapping
  sidecar.py      SQLite access for Woodpecker's own state: the nag_state table and the
                  display_snapshot table (no task data)
  store.py        Store: the storage seam the rest of Woodpecker talks to. Combines a
                  VikunjaClient (task data) and the sidecar connection (nag state,
                  display snapshot) behind one interface returning Task
  models.py       Task and DailyFocus dataclasses and the status/priority constants
  config.py       Named tunables and environment readers
```

Storage is a `Store` seam over two backends: `vikunja.py`'s `VikunjaClient` holds the
tasks themselves (Vikunja is the source of truth, reached over its REST API), and
`sidecar.py` holds only what is Woodpecker's own: `nag_state` (`task_id`, `last_nagged_at`)
and `display_snapshot` (one row per chat: the ordered task ids of the last list
shown). `store.py` is the only module the rest of Woodpecker (`orchestrator.py`,
`scheduler.py`) talks to; it returns and accepts the `Task` dataclass so the rest of
the app is unaware Vikunja exists. The `Task` dataclass no longer has a `blocked_by`
field, and the intent set is `add` / `complete` / `drop` / `reschedule` / `list` /
`answer` (the earlier `edit`, `block`, and `merge` intents were removed since
rewording and blocker-tracking now live in Vikunja itself).

`reschedule` moves an existing task's due date or raises its priority in place. It
must never be expressed as an `add`: the sidecar's `bump_state` counts forward
due-date moves per `task_id`, so re-adding a task under a fresh id resets
`bump_count`, the avoidance signal the nagging is built on. Dropping the intent
during the Vikunja cutover caused exactly that: "push 2 to tomorrow" was recorded
as an `add`, silently duplicating the task and zeroing its history.

## Development

```bash
# Install dependencies
uv sync

# Run locally (requires .env with API keys)
uv run python src/main.py

# Test
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
```

Environment variables: see `.env.example` (Telegram token, chat ID, Anthropic API key,
Vikunja URL/token/project id, sidecar `WOODPECKER_DB_PATH`, old name `JOLT_DB_PATH` still read). Never commit `.env`.

## Deployment

Runs on the homelab host `jarvis` (SSH alias) as a Docker container under
`/home/gautier/docker/<service>/`, next to a separately deployed Vikunja instance
(`tasks.example.com`) that Woodpecker talks to over its REST API. Auto-deploys on git push via
a self-hosted GitHub runner, the same pattern as the `fitness-data` service.

The sidecar SQLite file (nag state + display snapshot only, no tasks) lives in a
bind-mounted `./data/` directory so it survives restarts and is captured by the
nightly restic to Backblaze B2 backup. The task backlog itself is not backed up by
Woodpecker; it lives and is backed up as part of the Vikunja deployment.

```bash
# Tail logs
ssh jarvis "docker logs <container> --tail 100"
```

## Testing Strategy

- One test file per module.
- External APIs (Telegram, Anthropic) are **mocked at the boundary**: no real HTTP calls
  in tests, so the suite is fast, free, and never spams the user.
- **What to test:** stale-detection rule (boundary at exactly 3 days), daily-focus
  selection (priority / deadline / age tie-breaking, the max-3 surfaced cap), priority
  ordering for the backlog dump, quiet-hours enforcement, task add/complete/drop logic.
- **What NOT to test:** actual LLM output quality, Telegram plumbing.
