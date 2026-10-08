# Fewer, sharper nudges, with vault reminders

Date: 2026-10-08. Status: draft, waits on Gautier's review. Hours confirmed 2026-10-08. Replaces the vault reminders draft of the same day.

2026-10-08: weekly review removed at Gautier's request (Sunday is a day like any other).

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

Two messages a day at most. Each names one thing, asks for one small step, and takes one letter to answer. The morning message also lists the vault deadlines of the week.

## Non-goals

- Woodpecker never writes to the vault. Vault tasks are never the frog.
- No change to how Gautier adds tasks in chat, or to the free-text flow and the "list" intent.
- No streaks, points or scores.
- Beeper settings stay Gautier's (see Open questions).

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Messages per day | 2: the morning message at `FOCUS_HOUR = 9` and one check-in at `CHECKIN_HOUR = 14`, every day, Sunday included. The 13:00 and 19:00 nags and the slow re-surface nags stop | Volume trained Gautier to skip the chat. 19:00 had the lowest answer rate (4%). 06:00 got no answer within an hour in 81 days |
| The frog | Always one, from all pending Vikunja tasks, any priority: overdue first, then highest `bump_count`, then oldest. None only when the backlog is empty | The priority filter left the morning empty 58% of the time |
| The ask | The morning message names the frog and one first step that takes under 10 minutes ("open the Doctolib page", not "book the doctor"). Claude writes the step. No guilt lines, no count of days avoided | A small step is easier to start than the whole task; guilt made Gautier close the message |
| Answer | A one-letter reply to the frog: `d` done, `o` on it, `t` tomorrow, `x` drop. The morning message ends with that legend on one line. `d` completes the task in Vikunja. `o` records a start and makes the check-in ask how it went. `t` moves the due date one day through the existing reschedule path, so `bump_count` still counts. `x` asks "Drop <task>? Send x again" and drops on the second `x` within 10 minutes. The bot parses these letters itself, with no Claude call; anything longer goes to the normal free-text flow | Beeper shows no Telegram inline buttons (tested 2026-10-08: the message arrived, the buttons did not). One letter is the nearest thing to one tap, and works in any chat app |
| Check-in at 14:00 | Only if the frog has no `d` or `x` yet: one short line and the same letter legend. If vault tasks are due today or overdue, it lists them in the same message. If neither applies, no message | One reminder, only when it is useful |
| Repeat avoidance | When a frog gets `t` for the 3rd time, the next morning asks a different question: `s` smaller step, `n` not mine to do, `x` drop. For `s`, the bot asks for the smaller step in free text and rewrites the task title with it | Postponing 6 times is a signal the task is wrong as written, not a lack of nagging |
| Vault in the morning | After the frog: a plain block "From the vault:", one line per vault task that is overdue, due within `VAULT_SOON_DAYS = 7`, or marked `⏫`; overdue first, then by date. Line: `• <text> (📅 2026-10-19, <note>)`, "overdue" in place of a past date, no date part for `⏫`. The block ends "Tick these in Obsidian." Lines carry no number and no button | Deterministic, never sent through Claude. Woodpecker cannot write there |
| Reading the vault | Read-only bind mount `/home/agent/vault:/vault:ro`, read live at each job, with a new `vault.py` that copies the dotclaude reader (`skills/todo/todo_vault.py`): open box `- [ ] ` or `* [ ] ` at any indent, none inside code fences, skip `.obsidian`, `.git`, `.trash`, `40 Archive`, `50 Journal`, `90 Templates`, `log`, `plans`, `specs`, strip `📅` and `⏫` | About 50 lines; a shared package for two personal repos is more than the job needs |
| Vault unreadable | The block becomes "Vault: not readable (<reason>)". The frog part always goes out | A missing mount must never silence the frog |
| Measure | Log one line per letter reply (`reply task_id action`) and per frog of the day | So the next review can count answers against frogs, which the current logs cannot show |

## What changes

- `selection.py`: rewrite `select_frog` to the rule above. Remove the 13:00/19:00 use of `select_daily_focus` and `select_slow_resurface` (delete them if nothing else uses them). Add `select_vault_reminders(tasks, today)` and `select_vault_due(tasks, today)`.
- `llm.py`: `write_focus(frog, now, client)` returns the opening line plus one first step for the frog. A new `write_reframe(frog, client)` for the 3rd-`t` question. Prompts drop guilt and day counts. `write_nag` goes.
- `bot.py`: before the Claude call in `handle_message`, a deterministic parser for the letter replies (`d`, `o`, `t`, `x`, `s`, `n`), case-insensitive, trimmed. A letter applies to the last prompt the bot sent (frog or reframe), read from the sidecar. A letter with no open prompt gets "Nothing to answer right now." Anything else goes to `llm.interpret_message` as today.
- `sidecar.py`: a `frog_state` table (`day`, `task_id`, `started_at`, `answered`) for the check-in rule and the 3rd-`t` count, and an `open_prompt` row (kind, task ids, sent at, pending drop) for the letter parser.
- `scheduler.py`: `send_morning` and `send_checkin` replace `send_daily_focus` and `send_nags`. Quiet hours still apply.
- `main.py`: the two cron jobs, and the handler registration.
- `vault.py` (new), `render.py` (`render_vault_block`, `render_vault_due`, the letter legend), `config.py` (`FOCUS_HOUR`, `CHECKIN_HOUR`, `REFRAME_AFTER_BUMPS = 3`, `DROP_CONFIRM_MINUTES = 10`, `VAULT_SOON_DAYS = 7`, `vault_path()` from `WOODPECKER_VAULT_PATH`, default `/vault`).
- `docker-compose.yml`: `- /home/agent/vault:/vault:ro`. `.env.example`, `CLAUDE.md`, `README.md`: describe the new rhythm and the vault source.

## Testing

- `test_selection.py`: the frog with priority 0 tasks only; overdue beats bumped; bumped beats old; empty backlog. Vault reminder windows (overdue, today, 7 and 8 days, `⏫`, undated).
- `test_vault.py`: the same cases as the dotclaude reader tests (open, ticked, indented, `* [ ]`, fenced, skipped folders, dated, `⏫`, non-UTF-8).
- `test_bot.py`: each letter calls the right `Store` method once and skips Claude; `x` drops only on the second `x` within 10 minutes; a letter with no open prompt is refused; `t` goes through `reschedule_task`; a longer message still goes to Claude; a foreign chat is ignored.
- `test_scheduler.py`: the morning message always names a frog when tasks exist; the check-in is skipped after Done; the check-in carries vault tasks due today; a vault read error still sends the frog; the 3rd `t` triggers the reframe; quiet hours send nothing.
- `test_render.py`: exact text of the vault block and the letter legend.

## Success, checked on 2026-11-08

The same log analysis as above, run again on the 4 weeks after deploy:
- A letter reply or a free-text answer on at least half of the frogs, the same day.
- Fewer than 3 frogs that reach the reframe question without an answer to it.
- The two doctor tasks are done, rewritten, or dropped.

If these fail, the next step is a different channel or a human commitment, not more messages.

## Open questions for Gautier

1. **Beeper.** Gautier reads Telegram only through Beeper, where the bot chat sits among all other chats. Pin it at the top, or give it its own notification setting if Beeper offers one. Outside the code; the spec does not depend on it.
