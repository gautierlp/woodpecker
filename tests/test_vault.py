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
