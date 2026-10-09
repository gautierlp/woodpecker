# Sharper morning: implementation plan

Spec: `docs/superpowers/specs/2026-10-09-sharper-morning-design.md`. TDD: each task writes its tests first, sees them fail, then makes them pass. Run `uv run pytest` at the end of each task.

## Task 1: clean vault text and source (vault.py)

- `VaultTask.note`: for a file whose stem starts with `_` (a card), use the parent folder name.
- New `vault.short_text(text: str, limit: int = 80) -> str`: strip `**` and `__`; `[[a|b]]` to `b`; `[[x/y/a]]` to `a`; keep the first sentence (up to and including the first `. `, `! ` or `? `, without the trailing space); if longer than `limit`, cut at the last space before `limit - 1` and add `…`. Collapse whitespace.
- Tests in `tests/test_vault.py`.

## Task 2: shorter vault block (render.py)

- `_vault_line` uses `vault.short_text(task.text)` and a date as `Oct 12` (no year, no emoji; `overdue` stays).
- `render_vault_block` shows at most `config.VAULT_MORNING_MAX = 3` lines. If more tasks were passed, the footer is `N more in Obsidian.`; else `Tick these in Obsidian.`.
- `render_vault_due` (check-in) uses the same line format, no cap.
- Update the exact-text tests in `tests/test_render.py`.

## Task 3: more context for the first step (llm.py, scheduler.py)

- `write_focus(frog, now, client, others: list[Task] = (), vault_lines: list[VaultTask] = ())`.
- The user content adds: `Today: Friday 2026-10-09.`; `Other open tasks:` with up to 25 titles (excluding the frog), one per line with `- `; `Vault notes:` with each vault task as `- <short_text> (<note>)`.
- System prompt adds: build the step from the context; do not invent a tool, site or place that the context does not name; if nothing specific is known, the step is to write down what the task needs.
- `scheduler.send_morning` reads the vault once, passes the pending tasks and the selected vault reminders to `write_focus`, and renders the block from the same list.
- Tests: `tests/test_llm.py` (payload has the weekday, another task title, a vault line, and the no-invent rule), `tests/test_scheduler.py` (send_morning still works with an unreadable vault).
