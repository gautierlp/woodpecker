# Woodpecker

**Context card:** `~/vault/10 Projects/woodpecker/_project.md` (status, goals, links). Read it first.
Decisions with their reason and logs of what was done go to that vault folder (never run git there); code docs, specs and plans stay in this repo.

Telegram bot **woodpecker** (called Jolt until 2026-10-05) that fights task avoidance. Holds a personal backlog, nudges toward one
focused thing each day, and asks whether a task that keeps moving to tomorrow is too big
or not yours to do. Plain-language interface powered by Claude.

Private single-user project, not a SaaS. Built to break a specific personal loop
(postpone, guilt, paralysis), so it sends few messages, each one sharp, by design.

**Status: deployed and running, now backed by Vikunja.** The full app (bot, LLM,
tasks, staleness, scheduler, entry point) plus Docker and the auto-deploy workflow
are in place, and the task store has been refactored onto Vikunja as the source of
truth. Live on `jarvis` as the `woodpecker` container, checked out at
`/home/gautier/docker/woodpecker`, with the self-hosted runner `gh-runner-woodpecker`
(in `/home/gautier/docker/gh-runner-woodpecker`) auto-deploying on push to `main`. The
Telegram handle is still `@jolt_todo_bot` until it is changed in BotFather. The sidecar
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
- **Telegram API**: messaging (receive user texts, send the morning and the check-in)
- **Anthropic API**: Claude interprets each inbound message and writes the morning
  first step, the reframe question, and replies

### Rhythm

Two messages a day at most. At 09:00 the morning names one frog (any pending task:
overdue first, then most postponed, then oldest) with a Claude-written first step and
the letter legend `d done · o on it · t tomorrow · x drop`, then a "From the vault:"
block (vault tasks overdue, due within 7 days, or marked ⏫). The 14:00 check-in fires
only when the frog has no answer yet, or a vault task is due today or overdue. Sunday is
a day like any other: no weekly review. Gautier answers in plain words ("done", "push it
to tomorrow"): when a free-text intent completes, drops or reschedules the open frog's
task, `bot._answer_frog` marks it `d`, `x` or `t` like the letter. The letters are a
shortcut. After the third `t` on a frog, the next morning asks
`s smaller step · n not mine to do · x drop`. The vault is mounted read-only at `/vault`
(`WOODPECKER_VAULT_PATH`); Woodpecker never writes to it. Spec:
`docs/superpowers/specs/2026-10-08-fewer-sharper-nudges-design.md`.

### Division of labour (core principle)

- **Claude** handles anything needing judgment or tone: parsing a text into a task,
  classifying intent (add / complete / drop / list / answer), and writing the
  morning frog line with its first step and the reframe question after the third `t`.
- **Deterministic code** handles anything mechanical: the full backlog dump, the
  letter replies, the vault block, the stale-age calculation, priority ordering, quiet-hours enforcement, and scheduling.

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
- `APScheduler`: cron-based scheduler (09:00 morning, 14:00 check-in, every day)
- `sqlite3` (stdlib): sidecar storage (nag state + display snapshot only)

## Code Conventions

- All code, comments, variable names, commits, and docs in **English**. French only
  appears in runtime content (bot messages to the user), if at all.
- Style enforced by **ruff**: no manual style discussions.
- Commits: conventional style (`feat(...)`, `fix(...)`), concise.
- No over-engineering: single-user personal project. No abstractions for hypothetical
  future needs.
- Behavior tunables (reframe after 3 `t`, drop confirm = 10 minutes,
  quiet hours = 06:00 to 23:00) are single named constants in `config.py`, easy to
  change after living with the bot.

## Module Responsibilities

Following the `billie_bot` shape:

```
src/woodpecker/
  main.py         Entry point: wires up bot + scheduler, builds the clients, starts the app
  bot.py          Telegram handler: receives messages, sends replies, counts a plain
                  answer about the open frog like its letter
  llm.py          Anthropic client: interprets messages, classifies intent, writes text
  orchestrator.py Applies a parsed intent (via the Store) and returns the reply text
  selection.py    Pure rules: the frog, the vault reminders, quiet hours
  vault.py        Read-only reader of the Obsidian vault's open checkboxes
  replies.py      Deterministic one-letter replies (d o t x s n), a shortcut for plain words
  render.py       Deterministic backlog rendering and display ordering
  memory.py       In-process recent-conversation and pending-outbound memory per chat
  scheduler.py    Job bodies: morning and check-in (wired to APScheduler cron in main.py)
  vikunja.py      VikunjaClient: REST calls to Vikunja (create/list/get/mark done/delete) and
                  the Task <-> Vikunja JSON field mapping
  sidecar.py      SQLite access for Woodpecker's own state: the nag_state, display_snapshot,
                  bump_state, frog_state and open_prompt tables (no task data); open_prompt
                  is one row: the frog, reframe or step prompt a letter answers
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
- **What to test:** frog selection (overdue, bump count, age), the letter replies, plain-word
  answers to the frog, the
  vault reader and windows, priority ordering for the backlog dump, quiet-hours
  enforcement, task add/complete/drop logic.
- **What NOT to test:** actual LLM output quality, Telegram plumbing.
