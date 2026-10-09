# A sharper morning message

Date: 2026-10-09. Status: approved by Gautier in chat on 2026-10-09 ("give it more context then, go").

## Why

The first morning message under the new rhythm (2026-10-09 09:00) was poor:

- **The first step was generic.** "Open your email and find the doctor's contact information or patient portal address." Claude got 140 input tokens: the task title and nothing else. It cannot write a specific step from a title alone.
- **The vault block was raw Markdown.** Telegram gets plain text (no `parse_mode`), so `**bold**` and `[[links]]` showed as typed.
- **The vault block was too long.** Six items, each a full paragraph copied from the note, 1304 characters in all. The source showed as `_project` (the file name of every project card) instead of the project name.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Focus context | `write_focus` also gets: the date and weekday, the titles of the other open Vikunja tasks (at most 25), and the vault lines of the morning (text and source) | Related tasks and vault notes often hold the real next step: "Send documents to the doctor" sits next to "Gather documents for the doctor" and the vault task about the ALD file |
| No invention | The system prompt says to build the step from the context, and to not invent a tool, site or place that the context does not name. If the context gives nothing specific, the step is to write down what the task needs | The 2026-10-09 step guessed a "patient portal" |
| Vault text | Strip `**` and `__`; `[[a\|b]]` becomes `b`, `[[path/a]]` becomes `a`. Keep only the first sentence, cut at 80 characters with `…` | The phone shows plain text; the note holds the detail |
| Vault source | A card (`_project.md`, `_area.md`) shows its folder name, for example `relaunch` | `_project` names nothing |
| Vault dates | `Oct 12` instead of `📅 2026-10-12`; `overdue` stays | Shorter, same as the backlog list |
| Vault length | The morning shows at most 3 vault lines. If more match, the footer says `N more in Obsidian.` instead of `Tick these in Obsidian.` | One message, read in seconds |

## Non-goals

No change to the rhythm, the frog selection, the letters, or the check-in logic. No Telegram formatting (`parse_mode`): plain text is safer than escaped MarkdownV2.

## Cost

About 600 input tokens a morning instead of 140, about $0.0008 a day with Haiku 4.5.
