# Fewer, sharper nudges, with vault reminders

Date: 2026-10-08. Status: draft, waits on Gautier's review. Replaces the vault reminders draft of the same day.

## Why

Woodpecker works, and it does not change what Gautier does. The logs from 2026-07-14 to 2026-10-08 (87 days) show it:

- The bot sent 538 proactive messages, about 7 a day (81 morning focus messages, 457 nags).
- 0 of 81 focus messages got a reply within an hour. 11 of 243 nag rounds did (5%), the same rate as a random hour (4.7%). None of those 11 replies acted on the nagged task.
- 58% of focus messages (47 of 81) named no task, and every one since 2026-09-23 says "Nothing high-priority is on the hook today". `select_frog` only accepts tasks with priority 2 or more and a due date, and 19 of the 22 open tasks have priority 0.
- "Check the quote for Pierre" got 66 nags before Gautier dropped it. Both doctor tasks are 87 days old, 26 days past due, and were moved forward 6 times each.
- Tasks still get done: the backlog fell from about 38 to 22, and 33 of 75 completions happened in Vikunja directly.

Gautier on why he skips them: he either reads a message and feels bad, or does not open it in his Beeper inbox at all. Guilt without a next step feeds avoidance, and a stream of 7 messages a day trains him to skip the chat.

Separately, the vault now holds tasks with context (checkboxes in notes, with `📅 YYYY-MM-DD` or `⏫`). Rules: `~/vault/CLAUDE.md`, section "Tasks". Those deadlines never reach the phone today.

## Goal

Two messages a day at most. Each names one thing, asks for one small step, and takes one tap to answer. The morning message also lists the vault deadlines of the week.

## Non-goals

- Woodpecker never writes to the vault. Vault tasks are never the frog.
- No change to how Gautier adds tasks in chat, or to the free-text flow and the "list" intent.
- No streaks, points or scores.
- Beeper settings stay Gautier's (see Open questions).

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Messages per day | 2: the morning message at `FOCUS_HOUR = 9` and one check-in at `CHECKIN_HOUR = 14`, plus a weekly review on Sunday at 10:00. The 13:00 and 19:00 nags and the slow re-surface nags stop | Volume trained Gautier to skip the chat. 19:00 had the lowest answer rate (4%). 06:00 got no answer within an hour in 81 days |
| The frog | Always one, from all pending Vikunja tasks, any priority: overdue first, then highest `bump_count`, then oldest. None only when the backlog is empty | The priority filter left the morning empty 58% of the time |
| The ask | The morning message names the frog and one first step that takes under 10 minutes ("open the Doctolib page", not "book the doctor"). Claude writes the step. No guilt lines, no count of days avoided | A small step is easier to start than the whole task; guilt made Gautier close the message |
| Answer | Four inline buttons under the frog: Done, On it, Tomorrow, Drop. Done completes the task in Vikunja. On it records a start and makes the check-in ask how it went. Tomorrow moves the due date one day through the existing reschedule path, so `bump_count` still counts. Drop drops it after a second tap ("Sure? Drop") | One tap instead of a typed reply. Free text still works |
| Check-in at 14:00 | Only if the frog has no Done or Drop yet: one short line and the same buttons. If vault tasks are due today or overdue, it lists them in the same message. If neither applies, no message | One reminder, only when it is useful |
| Repeat avoidance | When a frog gets Tomorrow for the 3rd time, the next morning asks a different question with buttons: Smaller step, Not mine to do, Drop | Postponing 6 times is a signal the task is wrong as written, not a lack of nagging |
| Weekly review | Sunday 10:00: the stale tasks (pending 14 days or more), up to 5, each with Keep, Next week, Drop buttons | Handles the stacking backlog in one sitting instead of daily pokes |
| Vault in the morning | After the frog: a plain block "From the vault:", one line per vault task that is overdue, due within `VAULT_SOON_DAYS = 7`, or marked `⏫`; overdue first, then by date. Line: `• <text> (📅 2026-10-19, <note>)`, "overdue" in place of a past date, no date part for `⏫`. The block ends "Tick these in Obsidian." Lines carry no number and no button | Deterministic, never sent through Claude. Woodpecker cannot write there |
| Reading the vault | Read-only bind mount `/home/agent/vault:/vault:ro`, read live at each job, with a new `vault.py` that copies the dotclaude reader (`skills/todo/todo_vault.py`): open box `- [ ] ` or `* [ ] ` at any indent, none inside code fences, skip `.obsidian`, `.git`, `.trash`, `40 Archive`, `50 Journal`, `90 Templates`, `log`, `plans`, `specs`, strip `📅` and `⏫` | About 50 lines; a shared package for two personal repos is more than the job needs |
| Vault unreadable | The block becomes "Vault: not readable (<reason>)". The frog part always goes out | A missing mount must never silence the frog |
| Measure | Log one line per button press (`button task_id action`) and per frog of the day | So the next review can count taps against frogs, which the current logs cannot show |

## What changes

- `selection.py`: rewrite `select_frog` to the rule above. Remove the 13:00/19:00 use of `select_daily_focus` and `select_slow_resurface` (delete them if nothing else uses them). Add `select_stale_for_review(tasks, now, limit=5)`, `select_vault_reminders(tasks, today)` and `select_vault_due(tasks, today)`.
- `llm.py`: `write_focus(frog, now, client)` returns the opening line plus one first step for the frog. A new `write_reframe(frog, client)` for the 3rd-Tomorrow question. Prompts drop guilt and day counts. `write_nag` goes.
- `bot.py`: a `CallbackQueryHandler` for buttons with data `f:<task_id>:<action>`, `r:<task_id>:<action>` (reframe) and `w:<task_id>:<action>` (weekly). Each answer edits the message to show what happened ("Done. Nice.") so a second tap cannot repeat it. Same chat-id guard as `handle_message`.
- `sidecar.py`: a `frog_state` table (`day`, `task_id`, `started_at`, `answered`) for the check-in rule and the 3rd-Tomorrow count.
- `scheduler.py`: `send_morning`, `send_checkin`, `send_weekly_review` replace `send_daily_focus` and `send_nags`. Quiet hours still apply.
- `main.py`: the three cron jobs, and the handler registration.
- `vault.py` (new), `render.py` (`render_vault_block`, `render_vault_due`, button keyboards), `config.py` (`FOCUS_HOUR`, `CHECKIN_HOUR`, `WEEKLY_REVIEW` day and hour, `REFRAME_AFTER_BUMPS = 3`, `STALE_REVIEW_DAYS = 14`, `VAULT_SOON_DAYS = 7`, `vault_path()` from `WOODPECKER_VAULT_PATH`, default `/vault`).
- `docker-compose.yml`: `- /home/agent/vault:/vault:ro`. `.env.example`, `CLAUDE.md`, `README.md`: describe the new rhythm and the vault source.

## Testing

- `test_selection.py`: the frog with priority 0 tasks only; overdue beats bumped; bumped beats old; empty backlog. Stale review limit and order. Vault reminder windows (overdue, today, 7 and 8 days, `⏫`, undated).
- `test_vault.py`: the same cases as the dotclaude reader tests (open, ticked, indented, `* [ ]`, fenced, skipped folders, dated, `⏫`, non-UTF-8).
- `test_bot.py`: each button action calls the right `Store` method once; a second tap does nothing; a foreign chat is ignored; Tomorrow goes through `reschedule_task`.
- `test_scheduler.py`: the morning message always names a frog when tasks exist; the check-in is skipped after Done; the check-in carries vault tasks due today; a vault read error still sends the frog; the 3rd Tomorrow triggers the reframe; Sunday sends the review; quiet hours send nothing.
- `test_render.py`: exact text of the vault block and the keyboards.

## Success, checked on 2026-11-08

The same log analysis as above, run again on the 4 weeks after deploy:
- A button press or a reply on at least half of the frogs, the same day.
- Fewer than 3 frogs that reach the reframe question without an answer to it.
- The two doctor tasks are done, rewritten, or dropped.

If these fail, the next step is a different channel or a human commitment, not more messages.

## Open questions for Gautier

1. **Hours.** 09:00 and 14:00 are guesses from the data (no answer to 06:00 in 81 days). Pick other hours if your day is different.
2. **Beeper.** The bot chat sits among all your chats. Two options outside the code: mark it as a priority chat with its own sound in Beeper, or read the bot in the Telegram app only and mute it in Beeper. Your choice; the spec does not depend on it.
3. **Drop confirmation.** A second tap for Drop protects against a slip, and costs one tap. Keep it?
