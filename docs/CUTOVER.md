# Cutover runbook: moving Jolt's backlog into Vikunja

This is the ordered, user-run procedure to switch Jolt from its own SQLite `tasks`
table to Vikunja as the task store. Code and tests are already in place (Tasks 1
through 9 of the refactor); this is the one-time deploy step on `jarvis`.

Nothing here runs automatically. Follow the steps in order, on `jarvis`, over SSH.

## Before you start

- Vikunja is deployed and reachable (its own homelab setup, tracked separately, not
  in this repo).
- A **Backlog** project exists in Vikunja for Jolt to use.
- You have a Vikunja API token with access to that project, and the project's numeric
  id.
- The Backlog project is **empty**. The migration script refuses to run otherwise
  (it aborts rather than risk creating duplicate tasks).

## Steps

1. **Confirm Vikunja is up.**
   Open the Vikunja web UI (or `curl` its API) and confirm the Backlog project
   exists and is empty. Note its project id and have an API token ready.

2. **Stop Jolt.**
   ```bash
   cd ~/docker/jolt
   docker compose stop
   ```
   This prevents Jolt from writing to the old SQLite file while the migration reads
   it, and stops the bot from answering Telegram messages mid-cutover.

3. **Run the migration into a NEW sidecar file.**
   Use a filename that does not already exist, so the old `jolt.db` (which holds the
   `tasks` table) is left untouched on disk as an archive rather than overwritten:
   ```bash
   cd ~/docker/jolt
   VIKUNJA_URL=https://tasks.example.com \
   VIKUNJA_TOKEN=<your-token> \
   VIKUNJA_PROJECT_ID=<your-project-id> \
   uv run python -m scripts.migrate_to_vikunja \
     ~/docker/jolt/data/jolt.db \
     ~/docker/jolt/data/sidecar.db
   ```
   What this does:
   - Reads every row from the old `tasks` table in `jolt.db`.
   - Creates one Vikunja task per **pending** task, carrying over text, priority, and
     deadline, and seeds its nag state (`last_nagged_at`) into the new sidecar file
     if it had one.
   - Creates one Vikunja task per **done** task completed in the last 90 days, then
     immediately marks it done in Vikunja (recent history is kept visible; older
     done tasks are not migrated).
   - Skips `dropped` tasks entirely.
   - Aborts with an error and makes no changes if the Backlog project already has
     any open tasks (safety check against double-running).

   The script prints a summary, e.g. `Migration complete: {'pending': 12, 'done': 4,
   'skipped_dropped': 3}`. Check the counts look right before continuing.

   The old file `~/docker/jolt/data/jolt.db` is not touched by this step: keep it
   around as a fallback/archive until you are confident the cutover worked.

4. **Point Jolt at Vikunja and the new sidecar.**
   Edit `~/docker/jolt/.env` and set:
   ```
   VIKUNJA_URL=https://tasks.example.com
   VIKUNJA_TOKEN=<your-token>
   VIKUNJA_PROJECT_ID=<your-project-id>
   JOLT_DB_PATH=data/sidecar.db
   ```
   `JOLT_DB_PATH` must point at the **new** sidecar file from step 3, not the old
   `jolt.db`, so Jolt does not try to read the old `tasks` table as its sidecar.

5. **Deploy.**
   Either push to `main` (the self-hosted runner `gh-runner-jolt` auto-deploys), or
   from the service directory:
   ```bash
   cd ~/docker/jolt
   docker compose up -d
   ```
   Prefer `up -d` over `docker restart` so the new `.env` values actually take
   effect.

6. **Verify end to end.**
   - Send a task to the bot in Telegram (e.g. "buy milk"). Confirm it shows up as a
     task in the Vikunja web UI under the Backlog project.
   - Complete it from Telegram (e.g. "done with the milk"). Confirm it is marked
     done in Vikunja.
   - Wait for, or manually trigger, a nag cycle and confirm the bot still nags off
     the live Vikunja backlog (a stale pending task still surfaces).

If anything looks wrong, stop Jolt again, leave `~/docker/jolt/data/jolt.db`
in place, and investigate before retrying. The old file is the recovery path back
to the pre-cutover state; it is not consumed or modified by the migration.
