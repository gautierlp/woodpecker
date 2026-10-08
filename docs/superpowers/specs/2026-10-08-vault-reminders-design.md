# Vault reminders

Date: 2026-10-08. Status: draft, waits on Gautier's review.

## Goal

The Obsidian vault now holds tasks that need context, each as a checkbox in the note it is about, with an optional `📅 YYYY-MM-DD` due date or a `⏫` mark for "this week". The rules are in `~/vault/CLAUDE.md` (section "Tasks") and the design is in the vault at `10 Projects/vault-setup/specs/2026-10-07-tasks-design.md`.

Those tasks never reach the phone today. Woodpecker only reads Vikunja, so a tax deadline that lives in the vault gets no reminder. After this change, Woodpecker also reminds about the vault tasks that have a date or a `⏫`, so Telegram is the one place that reminds Gautier about everything.

## Non-goals

- Woodpecker never writes to the vault. Ticking a vault task stays a job for Obsidian.
- No copy of vault tasks into Vikunja, and no sync between the two.
- Vault tasks are never the frog. The frog stays the most avoided Vikunja task.
- No Claude call for vault reminders. They are a mechanical list (see "Division of labour" in `CLAUDE.md`).
- Vault tasks with neither a date nor `⏫` are ignored. There are about 170 of them, mostly open questions in finance notes; `/todo` covers them.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| How Woodpecker reads the vault | A read-only bind mount of `/home/agent/vault` at `/vault` in the container, read live at each job | Woodpecker runs on jarvis next to the vault. A live read matches how it reads Vikunja: no copy |
| Parser | A new `vault.py`, a copy of the reader in dotclaude `skills/todo/todo_vault.py` (same task syntax, same skipped folders) | About 50 lines. A shared package for two personal repos is more than the job needs. The comment names the source, and the tests pin the same cases |
| Which tasks | Overdue, due within `VAULT_SOON_DAYS = 7`, or marked `⏫` | Matches the 14-day Home view in spirit, tighter for the phone |
| Morning message | A plain block after the priorities: "From the vault:", one line per task with its date and note name, overdue first, then by date, then the `⏫` ones | Deterministic, never reworded. Gautier sees the week's deadlines with his frog |
| Nags at 09, 13, 19 | If a vault task is overdue or due today, one plain message lists them. Otherwise nothing | A due date is the one thing worth nagging about from the vault. Quiet otherwise |
| Numbers | Vault lines carry no number, and `save_display` stays Vikunja-only | "done 2" must keep pointing at a Vikunja task. A vault line ends with the note name, so Gautier knows where to tick it |
| "done" on a vault task in chat | Out of scope. The morning block ends with "Tick these in Obsidian." | Woodpecker cannot write there. One line of copy is enough for now |
| Vault unreadable | The morning block becomes one line, "Vault: not readable (<reason>)", and the nags skip the vault part. The Vikunja part always runs | A missing mount must never silence the frog |

## What changes

- `src/woodpecker/vault.py` (new): `VaultTask` dataclass (`text`, `note`, `due: date | None`, `now: bool`) and `read_vault_tasks(root: Path) -> list[VaultTask]`. Rules, from the dotclaude reader: an open box is `- [ ] ` or `* [ ] ` at any indent; a box inside a fenced code block is not a task; skip folders by name at any depth: `.obsidian`, `.git`, `.trash`, `40 Archive`, `50 Journal`, `90 Templates`, `log`, `plans`, `specs`; strip `📅 YYYY-MM-DD` and `⏫` from the text. `note` is the file name without `.md`. An unreadable file is skipped and logged; a missing root raises.
- `src/woodpecker/selection.py`: `select_vault_reminders(tasks, today) -> list[VaultTask]` (overdue, due within `VAULT_SOON_DAYS`, or `now`, in the order above) and `select_vault_due(tasks, today)` (overdue or due today).
- `src/woodpecker/render.py`: `render_vault_block(tasks, today) -> str` for the morning, and `render_vault_due(tasks, today) -> str` for the nag. Format of a line: `• <text> (📅 2026-10-19, <note>)`, with `overdue` in place of the date when it is past, and no date part for a `⏫` task.
- `src/woodpecker/scheduler.py`: `send_daily_focus` appends the vault block; `send_nags` sends the vault message when `select_vault_due` is not empty, before or without the Vikunja nags, and still respects quiet hours.
- `src/woodpecker/config.py`: `VAULT_SOON_DAYS = 7` and `vault_path()` from `WOODPECKER_VAULT_PATH`, default `/vault`.
- `docker-compose.yml`: add `- /home/agent/vault:/vault:ro` under `volumes`. `.env.example`: document `WOODPECKER_VAULT_PATH`.
- `CLAUDE.md` and `README.md`: one paragraph on the vault as a second, read-only source.

## Testing

- `tests/test_vault.py`: a fixture vault with an open box, a ticked box, an indented box, a `* [ ]` box, a box in a code fence, a box in each skipped folder, a dated box, a `⏫` box, and a non-UTF-8 file. Same expectations as the dotclaude tests.
- `tests/test_selection.py`: overdue, due today, due in 7 and 8 days, `⏫`, undated.
- `tests/test_render.py`: exact text of both blocks, including the overdue and `⏫` forms.
- `tests/test_scheduler.py`: the morning message holds the vault block; a vault read error still sends the frog and the priorities; a nag hour with a vault task due today sends the vault message; quiet hours send nothing.
- After deploy: the next 06:00 message shows the block. Check today's list against `python3 ~/.claude/skills/todo/todo.py gather`.

## Risks

- The container user must be able to read `/home/agent/vault`. The folder is `rwxrwxr-x` with an ACL. Check with `docker exec woodpecker ls /vault` after the deploy, before trusting the morning message.
- The two parsers can drift. If the task syntax changes, change both, and the tests in both repos.
- Another session or Obsidian Sync can be mid-write when Woodpecker reads. A half-written file gives at worst one missing line for one message.
