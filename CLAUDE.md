# Accountability Bot

Telegram bot that fights task avoidance. Holds a personal backlog, nudges toward one
focused thing each day, and gets pointedly insistent about tasks that have been quietly
postponed. Plain-language interface powered by Claude.

Private single-user project, not a SaaS. Built to break a specific personal loop
(postpone, guilt, paralysis), so it leans into persistent nagging by design.

**Status: pre-implementation.** The design is fixed; no code exists yet. See
`docs/superpowers/specs/2026-07-12-accountability-bot-design.md` for the full behavior,
architecture, and rationale. The plan (once written) lives in `docs/superpowers/plans/`.

## Architecture

Single Python application in a Docker container on the homelab host `jarvis`. One
SQLite file for storage. No external database, no Redis, no vector store.

External services:
- **Telegram API**: messaging (receive user texts, send daily focus and nags)
- **Anthropic API**: Claude interprets each inbound message and writes the focus,
  nags, and replies

### Division of labour (core principle)

- **Claude** handles anything needing judgment or tone: parsing a text into a task,
  classifying intent (add / complete / defer / question / blocker), and writing the
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
- `APScheduler`: cron-based scheduler (06:00 focus, midday/evening nags, daily stale-scan)
- `sqlite3` (stdlib): backlog storage

## Code Conventions

- All code, comments, variable names, commits, and docs in **English**. French only
  appears in runtime content (bot messages to the user), if at all.
- Style enforced by **ruff**: no manual style discussions.
- Commits: conventional style (`feat(...)`, `fix(...)`), concise.
- No over-engineering: single-user personal project. No abstractions for hypothetical
  future needs.
- Behavior tunables (stale threshold = 3 days, quiet hours = 06:00 to 23:00) are single
  named constants, easy to change after living with the bot.

## Module Responsibilities (planned)

Layout to be finalized in the implementation plan, following the `billie_bot` shape:

```
src/
  main.py       Entry point: wires up bot + scheduler, starts the app
  bot.py        Telegram handler: receives messages, sends replies and nags
  llm.py        Anthropic client: interprets messages, classifies intent, writes text
  tasks.py      Backlog logic: add/complete/drop, priority ordering, backlog dump
  staleness.py  Pure rules: stale detection (age), daily-focus selection
  scheduler.py  APScheduler jobs: 06:00 focus, midday/evening nags, daily stale-scan
  db.py         SQLite access: the single tasks table
```

Data model (single `tasks` table): `id`, `text`, `priority`, `deadline`, `created_at`,
`status` (pending/done/dropped), `last_nagged_at`, `completed_at`.

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

Environment variables: see `.env.example` (Telegram token, chat ID, Anthropic API key).
Never commit `.env`.

## Deployment

Runs on the homelab host `jarvis` (SSH alias) as a Docker container under
`/home/gautier/docker/<service>/`. Auto-deploys on git push via a self-hosted GitHub
runner, the same pattern as the `fitness-data` service.

The SQLite file lives in a bind-mounted `./data/` directory so it survives restarts and
is captured by the nightly restic to Backblaze B2 backup.

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
