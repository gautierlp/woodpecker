from datetime import date

import pytest

from woodpecker import vault


def _note(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    return path


def test_reads_open_boxes_with_dash_star_and_indent(tmp_path):
    _note(tmp_path, "10 Projects/a/_project.md", "- [ ] one\n  - [ ] two\n* [ ] three\n")
    texts = [t.text for t in vault.read_vault(tmp_path)]
    assert texts == ["one", "two", "three"]


def test_skips_ticked_boxes(tmp_path):
    _note(tmp_path, "n.md", "- [x] done\n- [X] done too\n- [ ] open\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["open"]


def test_skips_boxes_inside_code_fences(tmp_path):
    _note(tmp_path, "n.md", "```\n- [ ] fenced\n```\n- [ ] real\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["real"]


@pytest.mark.parametrize(
    "folder",
    [
        ".obsidian",
        ".git",
        ".trash",
        "40 Archive",
        "50 Journal",
        "90 Templates",
        "log",
        "plans",
        "specs",
    ],
)
def test_skips_record_folders(tmp_path, folder):
    _note(tmp_path, f"10 Projects/x/{folder}/n.md", "- [ ] hidden\n")
    _note(tmp_path, "keep.md", "- [ ] shown\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["shown"]


def test_reads_the_due_date_and_strips_it(tmp_path):
    _note(tmp_path, "Taxes.md", "- [ ] file the return 📅 2026-10-19\n")
    [task] = vault.read_vault(tmp_path)
    assert task == vault.VaultTask(
        text="file the return", note="Taxes", due=date(2026, 10, 19), now=False
    )


def test_reads_the_this_week_mark_and_strips_it(tmp_path):
    _note(tmp_path, "Car.md", "- [ ] book the service ⏫\n")
    [task] = vault.read_vault(tmp_path)
    assert task.text == "book the service"
    assert task.now is True
    assert task.due is None


def test_an_impossible_date_is_no_date(tmp_path):
    _note(tmp_path, "n.md", "- [ ] odd 📅 2026-13-40\n")
    [task] = vault.read_vault(tmp_path)
    assert task.due is None


def test_a_non_utf8_note_is_skipped_and_logged(tmp_path, caplog):
    _note(tmp_path, "bad.md", b"- [ ] caf\xe9\n")
    _note(tmp_path, "good.md", "- [ ] fine\n")
    assert [t.text for t in vault.read_vault(tmp_path)] == ["fine"]
    assert "bad.md" in caplog.text


def test_a_missing_folder_is_unreadable(tmp_path):
    with pytest.raises(vault.VaultUnreadable, match="not a folder"):
        vault.read_vault(tmp_path / "nope")


def test_a_card_is_named_by_its_folder(tmp_path):
    _note(tmp_path, "10 Projects/relaunch/_project.md", "- [ ] ship it\n")
    _note(tmp_path, "20 Areas/health/_area.md", "- [ ] book it\n")
    assert [t.note for t in vault.read_vault(tmp_path)] == ["relaunch", "health"]


def test_a_plain_note_keeps_its_file_name(tmp_path):
    _note(tmp_path, "10 Projects/relaunch/malt-profile.md", "- [ ] read the stats\n")
    [task] = vault.read_vault(tmp_path)
    assert task.note == "malt-profile"


def test_short_text_strips_bold_and_links():
    text = "**File it** with __care__, see [[notes/x/profile]] and [[positioning|the pitch]]"
    assert vault.short_text(text) == "File it with care, see profile and the pitch"


def test_short_text_keeps_the_first_sentence():
    assert vault.short_text("Keep LBP funded. The rest waits.") == "Keep LBP funded."
    assert vault.short_text("Done? Then rest.") == "Done?"
    assert vault.short_text("Go now! Later is late.") == "Go now!"


def test_short_text_ignores_a_dot_inside_a_word():
    assert vault.short_text("move lepoher.co to Cloudflare") == "move lepoher.co to Cloudflare"


def test_short_text_cuts_a_long_sentence_at_a_space():
    text = "word " * 30
    out = vault.short_text(text)
    assert len(out) <= 80
    assert out.endswith("word…")


def test_short_text_drops_a_trailing_comma_before_the_cut():
    text = "a" * 70 + " bcdef, ijklmnopqrstu"
    assert vault.short_text(text) == "a" * 70 + " bcdef…"


def test_short_text_collapses_whitespace():
    assert vault.short_text("  one   two\tthree ") == "one two three"
