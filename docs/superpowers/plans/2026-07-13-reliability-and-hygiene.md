# Reliability and Hygiene Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jolt fail loudly and safely instead of silently, and clear a handful of correctness and hygiene issues, without changing any happy-path behavior.

**Architecture:** Small, defensive changes across `llm.py`, `scheduler.py`, `main.py`, `config.py`, `db.py`, and `selection.py`. Claude calls stay on the single asyncio event loop (the deliberate single-connection DB-safety invariant from commit `81aca44`); a short client timeout keeps a hung call from wedging the loop forever. Every behavior change is exercised by a test that mocks the Anthropic client at the boundary.

**Tech Stack:** Python 3.12, `anthropic` SDK, `python-telegram-bot`, `APScheduler`, `pytest`, `ruff`, `uv`.

## Global Constraints

- Python 3.12+; dependency management via `uv` (run tests with `uv run pytest`, lint with `uv run ruff check .`).
- All code, comments, and docs in English. No em dashes anywhere (use commas, colons, periods, or parentheses).
- Conventional commit messages (`fix(...)`, `refactor(...)`, `test(...)`, `docs(...)`). No `Co-Authored-By` trailer, no "Generated with Claude Code" footer.
- Behavior tunables are single named constants in `config.py`.
- External APIs (Telegram, Anthropic) are mocked at the boundary in tests. Never test LLM output quality.
- Model id in use is `claude-haiku-4-5-20251001` (valid; do not change it).
- Do NOT move Claude calls off the event loop and do NOT change the single-`sqlite3`-connection model.
- The Anthropic SDK `timeout` is in seconds (Python).
- `strict: true` on a tool guarantees field *types* and rejects unknown fields; it does NOT guarantee a string is a valid ISO date. The per-block try/except in Task 4 is the real crash guard; strict is defense-in-depth.

---

### Task 1: Anthropic client timeout

Give the Anthropic client an explicit short timeout so a hung request fails fast into the existing error handling instead of freezing the single event-loop worker for the SDK's ~10-minute default.

**Files:**
- Modify: `src/jolt/config.py` (add constant)
- Modify: `src/jolt/llm.py` (add a `build_client` factory)
- Modify: `src/jolt/main.py:75` (use the factory)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `config.ANTHROPIC_TIMEOUT_SECONDS: int` and `llm.build_client(api_key: str) -> Anthropic` (an `Anthropic` client whose `.timeout` equals `config.ANTHROPIC_TIMEOUT_SECONDS`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm.py`:

```python
def test_build_client_sets_a_short_timeout():
    from jolt import config, llm

    client = llm.build_client("sk-test-not-a-real-key")
    assert client.timeout == config.ANTHROPIC_TIMEOUT_SECONDS
    assert config.ANTHROPIC_TIMEOUT_SECONDS <= 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm.py::test_build_client_sets_a_short_timeout -v`
Expected: FAIL with `AttributeError: module 'jolt.llm' has no attribute 'build_client'`

- [ ] **Step 3: Add the constant to `config.py`**

After the `MODEL` line (`config.py:13`), add:

```python
ANTHROPIC_TIMEOUT_SECONDS = 30  # a Claude call that hangs past this fails fast, freeing the loop
```

- [ ] **Step 4: Add the factory to `llm.py`**

The `Anthropic` class is already imported nowhere in `llm.py` (it is imported in `main.py`). Add the import and factory near the top of `llm.py`, after the existing imports:

```python
from anthropic import Anthropic


def build_client(api_key: str) -> Anthropic:
    """The Anthropic client, with an explicit short timeout. Calls run on the bot's single
    event loop, so without a tight timeout one hung request would freeze polling and every
    scheduled job for the SDK's ~10-minute default."""
    return Anthropic(api_key=api_key, timeout=config.ANTHROPIC_TIMEOUT_SECONDS)
```

- [ ] **Step 5: Use the factory in `main.py`**

Replace `main.py:75`:

```python
    client = Anthropic(api_key=config.anthropic_api_key())
```

with:

```python
    client = llm.build_client(config.anthropic_api_key())
```

Then remove the now-unused `from anthropic import Anthropic` import at `main.py:4` (ruff will flag it if left).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py::test_build_client_sets_a_short_timeout -v && uv run ruff check .`
Expected: PASS, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/jolt/config.py src/jolt/llm.py src/jolt/main.py tests/test_llm.py
git commit -m "fix(llm): give the Anthropic client an explicit short timeout"
```

---

### Task 2: Non-empty fallback in `_text_of`

A response with no text block (a refusal, or truncation before any text) currently makes `_text_of` return `""`, and Telegram rejects an empty message, so a nag built from it raises. Return a safe non-empty fallback and log when it happens.

**Files:**
- Modify: `src/jolt/llm.py` (`_text_of` at lines 234-238)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `_text_of(response) -> str` never returns `""`; on no text block it returns a fixed fallback string and logs at WARNING.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm.py` (reuse the existing `FakeClient`/`FakeMessages` and `SimpleNamespace` import at the top):

```python
def test_text_of_falls_back_when_no_text_block():
    # A response whose content has no text block (refusal / truncation) must not yield "",
    # because Telegram rejects an empty message and the send would raise.
    response = SimpleNamespace(content=[], stop_reason="end_turn")
    assert llm._text_of(response) != ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm.py::test_text_of_falls_back_when_no_text_block -v`
Expected: FAIL (`assert '' != ''`).

- [ ] **Step 3: Implement the fallback**

Replace `_text_of` (llm.py:234-238):

```python
_EMPTY_REPLY_FALLBACK = "I hit a snag putting that into words. Try me again in a moment."


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    logger.warning("Claude returned no text block; using fallback text")
    return _EMPTY_REPLY_FALLBACK
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (existing `write_focus`/`write_nag` tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "fix(llm): fall back to non-empty text so a send never fails on empty output"
```

---

### Task 3: Strict intent tool schema

Mark the `record_intent` tool `strict` and forbid unknown properties, so Claude's tool input is schema-validated at the API boundary. This reduces malformed input; it does not by itself guarantee a valid date (Task 4 handles that).

**Files:**
- Modify: `src/jolt/llm.py` (`_TOOL` at lines 55-97)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `llm._TOOL` has top-level `"strict": True` and `_TOOL["input_schema"]["additionalProperties"] is False`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm.py`:

```python
def test_tool_schema_is_strict():
    assert llm._TOOL["strict"] is True
    assert llm._TOOL["input_schema"]["additionalProperties"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm.py::test_tool_schema_is_strict -v`
Expected: FAIL with `KeyError: 'strict'`.

- [ ] **Step 3: Add the fields**

In `_TOOL` (llm.py:55), add `"strict": True,` as a top-level key (sibling of `"name"`, `"description"`, `"input_schema"`). Inside `"input_schema"`, alongside `"type": "object"` and `"properties"`, add `"additionalProperties": False,`. The `"required": ["action"]` line stays as is. The result:

```python
_TOOL = {
    "name": "record_intent",
    "description": "Record what the user's Telegram message means for their task list.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            # ... unchanged ...
        },
        "required": ["action"],
    },
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "fix(llm): make the intent tool schema strict"
```

---

### Task 4: Resilient `interpret_message` (per-block guard + truncation note)

One malformed intent block (a non-ISO date, a missing `action`) currently aborts every intent in the message via the list comprehension. Wrap each block's parse so a bad block degrades to a per-item clarification while the good blocks still apply. Also raise `max_tokens` and, when the response is truncated (`stop_reason == "max_tokens"`), append a note telling the user the message was too long to fully capture.

**Files:**
- Modify: `src/jolt/config.py` (add constant)
- Modify: `src/jolt/llm.py` (`interpret_message`, lines 211-231)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `parse_intent(tool_input: dict) -> Intent` (unchanged), `Intent` dataclass with `action` and `reply` fields.
- Produces: `config.INTERPRET_MAX_TOKENS: int`. `interpret_message(...)` never raises on a malformed block; a malformed block becomes an `Intent(action="answer", reply=...)`, and a truncated response appends a trailing `Intent(action="answer", reply=<truncation note>)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py`. These build a fake response with tool_use blocks whose `.input` dicts drive `parse_intent`. Use the existing `FakeClient(response)` and `_task()` helpers:

```python
def _tool_use(input_dict):
    return SimpleNamespace(type="tool_use", input=input_dict)


def test_one_malformed_block_does_not_drop_the_others():
    # Two intents: a valid add, and a block with a non-ISO date that would crash parse_intent.
    # The valid add must survive; the bad block becomes a clarification, not a total failure.
    response = SimpleNamespace(
        content=[
            _tool_use({"action": "add", "text": "buy milk"}),
            _tool_use({"action": "add", "text": "call bank", "deadline": "next week"}),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    intents = llm.interpret_message(
        "buy milk; call bank next week", [_task()], datetime(2026, 7, 13, tzinfo=TZ),
        FakeClient(response),
    )
    actions = [i.action for i in intents]
    assert "add" in actions  # the valid one survived
    # the malformed one degraded to an answer, not a raised exception
    assert any(i.action == "answer" for i in intents)


def test_truncated_response_appends_a_note():
    response = SimpleNamespace(
        content=[_tool_use({"action": "add", "text": "task one"})],
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    intents = llm.interpret_message(
        "a very long paste", [_task()], datetime(2026, 7, 13, tzinfo=TZ), FakeClient(response),
    )
    assert intents[-1].action == "answer"
    assert "too long" in intents[-1].reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::test_one_malformed_block_does_not_drop_the_others tests/test_llm.py::test_truncated_response_appends_a_note -v`
Expected: the first FAILs with a `ValueError` from `date.fromisoformat("next week")` escaping; the second FAILs (`assert 'add' == 'answer'`).

- [ ] **Step 3: Add the constant to `config.py`**

After the `ANTHROPIC_TIMEOUT_SECONDS` line added in Task 1, add:

```python
INTERPRET_MAX_TOKENS = 4000  # room for many record_intent calls in one pasted list
```

- [ ] **Step 4: Rewrite the parse + truncation handling in `interpret_message`**

Replace the `max_tokens=1000,` argument (llm.py:213) with `max_tokens=config.INTERPRET_MAX_TOKENS,`.

Replace the block that builds `intents` (llm.py:222-231):

```python
    _log_usage("interpret_message", response)
    intents = [
        parse_intent(block.input)
        for block in response.content
        if getattr(block, "type", None) == "tool_use"
    ]
    if not intents:
        logger.warning("Claude returned no tool_use block; falling back to clarification reply")
        return [Intent(action="answer", reply="Sorry, I did not catch that. Try again?")]
    intents = _resolve_positions(intents, display_ids, tasks, now)
    return intents
```

with:

```python
    _log_usage("interpret_message", response)
    intents = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        try:
            intents.append(parse_intent(block.input))
        except (ValueError, KeyError, TypeError) as exc:
            # One malformed block (a non-ISO date, a missing field) must not sink the rest of
            # a multi-task message. Degrade just this item to a clarification.
            logger.warning("Could not parse an intent block (%s): %r", exc, block.input)
            intents.append(
                Intent(action="answer", reply="I couldn't make sense of part of that. Try rephrasing it?")
            )
    if not intents:
        logger.warning("Claude returned no tool_use block; falling back to clarification reply")
        return [Intent(action="answer", reply="Sorry, I did not catch that. Try again?")]
    intents = _resolve_positions(intents, display_ids, tasks, now)
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("interpret_message hit max_tokens; response may be truncated")
        intents.append(
            Intent(
                action="answer",
                reply="That message was too long for me to capture all at once. "
                "Please re-send anything that didn't land.",
            )
        )
    return intents
```

Note: `_resolve_positions` runs before appending the truncation note, so the note (task_id None) is untouched by resolution and the malformed-block answers pass through unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (all, including the pre-existing multi-intent tests).

- [ ] **Step 6: Commit**

```bash
git add src/jolt/config.py src/jolt/llm.py tests/test_llm.py
git commit -m "fix(llm): survive a malformed intent block and flag truncated pastes"
```

---

### Task 5: Out-of-range task-number guard

When the user references a number that is not in the last displayed list (for example "45" when the list stopped at 44, or a stale snapshot after a restart), resolve it to a clarification rather than acting on an unrelated task id.

**Files:**
- Modify: `src/jolt/llm.py` (`_resolve_positions`, lines 132-154)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Intent` with `task_id` / `blocked_by` holding display positions; `render.display_order`.
- Produces: `_resolve_positions(...)` converts any intent that references an out-of-range position into `Intent(action="answer", reply=...)` naming the number, and leaves in-range references resolving to their ids as before.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm.py` (the file already imports `Task`, `datetime`, `TZ`, and defines `_ordered_task`; check the top of the file for the exact helper and reuse it. If `_ordered_task` is not present, use `_task(id=...)`):

```python
def test_out_of_range_number_becomes_a_clarification():
    tasks = [_task(id=10)]
    display_ids = [10]  # only position 1 exists
    intent = llm.Intent(action="complete", task_id=45)  # user referenced "45"
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert len(resolved) == 1
    assert resolved[0].action == "answer"
    assert "45" in resolved[0].reply
    assert resolved[0].task_id is None


def test_in_range_number_still_resolves_to_its_id():
    tasks = [_task(id=10)]
    display_ids = [10]
    intent = llm.Intent(action="complete", task_id=1)  # position 1 -> id 10
    resolved = llm._resolve_positions([intent], display_ids, tasks, datetime(2026, 7, 13, tzinfo=TZ))
    assert resolved[0].action == "complete"
    assert resolved[0].task_id == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm.py::test_out_of_range_number_becomes_a_clarification -v`
Expected: FAIL (current code sets `task_id` to `None` and keeps `action == "complete"`, so `action == "answer"` fails).

- [ ] **Step 3: Rewrite `_resolve_positions`**

Replace the body of `_resolve_positions` (llm.py:142-154, keeping the signature and the leading comment):

```python
    if display_ids is None:
        pos_to_id = {pos: t.id for pos, t in enumerate(render.display_order(tasks, now), 1)}
    else:
        pos_to_id = {pos: tid for pos, tid in enumerate(display_ids, 1)}
    resolved = []
    for intent in intents:
        missing = [
            n for n in (intent.task_id, intent.blocked_by) if n is not None and n not in pos_to_id
        ]
        if missing:
            numbers = " or ".join(str(n) for n in missing)
            logger.warning("Task number(s) %s not on the last list shown; asking to clarify", missing)
            resolved.append(
                replace(
                    intent,
                    action="answer",
                    reply=f"I don't have a task numbered {numbers} on the current list. "
                    "Send 'list' to see the current numbers.",
                    task_id=None,
                    blocked_by=None,
                )
            )
            continue
        changes = {}
        if intent.task_id is not None:
            changes["task_id"] = pos_to_id[intent.task_id]
        if intent.blocked_by is not None:
            changes["blocked_by"] = pos_to_id[intent.blocked_by]
        resolved.append(replace(intent, **changes) if changes else intent)
    return resolved
```

`replace` is already imported at `llm.py:2` (`from dataclasses import dataclass, replace`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/llm.py tests/test_llm.py
git commit -m "fix(llm): clarify out-of-range task numbers instead of acting on the wrong task"
```

---

### Task 6: Scheduled jobs fail safely

Wrap the daily-focus and nag job bodies so any error (API down, empty output, anything) is caught, logged, and turned into a short plain-text message to the user via the existing `send`, instead of vanishing into APScheduler's logs. In `send_nags`, a failure on the focus nag must not prevent the tadpole nag, and the job emits at most one user-facing failure note per run.

**Files:**
- Modify: `src/jolt/scheduler.py` (`send_daily_focus`, `send_nags`)
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `send(text: str)`, `db.list_all`, `selection.select_daily_focus`, `selection.select_slow_resurface`, `llm.write_focus`, `llm.write_nag`.
- Produces: `send_daily_focus` and `send_nags` never raise; on internal failure they call `send` once with a fallback line.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scheduler.py`. The existing `FakeClient` returns canned prose; add a raising client and use it. Put the helper near the top, after the existing `FakeClient`:

```python
class RaisingClient:
    """A client whose create() always raises, to exercise the failure path."""

    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("boom")


def test_daily_focus_sends_a_fallback_when_the_llm_fails():
    conn = fresh()
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    db.add_task(conn, "call vet", "important", None, now)
    sent, send = collector()
    scheduler.send_daily_focus(conn, send, RaisingClient(), now, chat_id=42)
    assert len(sent) == 1  # the user hears about it rather than silence
    assert sent[0]  # non-empty fallback text


def test_nags_attempt_the_tadpole_even_if_the_frog_nag_fails():
    # An important stale frog and a normal stale tadpole. The frog nag raises; the tadpole
    # nag must still be attempted, and the user gets exactly one failure note.
    conn = fresh()
    created = datetime(2026, 7, 5, tzinfo=TZ)  # 7 days before now
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    db.add_task(conn, "file taxes", "important", None, created)
    db.add_task(conn, "sort old photos", "normal", None, created)
    sent, send = collector()
    scheduler.send_nags(conn, send, RaisingClient(), now)
    # Both nags raise, so no real nags go out, but the user is told once, not zero times.
    assert len(sent) == 1
    assert sent[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scheduler.py::test_daily_focus_sends_a_fallback_when_the_llm_fails tests/test_scheduler.py::test_nags_attempt_the_tadpole_even_if_the_frog_nag_fails -v`
Expected: both FAIL with `RuntimeError: boom` escaping the job.

- [ ] **Step 3: Wrap `send_daily_focus`**

Replace the body of `send_daily_focus` (scheduler.py:11-21) so the prose/LLM work is guarded. The task read, focus selection, and display snapshot are deterministic and safe; only the LLM prose can fail, and the send must always carry something:

```python
def send_daily_focus(conn, send, client, now: datetime, chat_id: int) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    logger.info("Selected focus task_id=%s", focus.focus.id if focus.focus else None)
    backlog = render_backlog(tasks, now)
    # Record the exact order shown, so a number the user types after the focus resolves
    # against this list, not a live order that later completions may have renumbered.
    db.save_display(conn, chat_id, [t.id for t in display_order(tasks, now)])
    try:
        prose = llm.write_focus(focus, now, client)
    except Exception:
        logger.exception("Daily focus prose failed; sending the backlog with a plain lead")
        prose = "Morning. I couldn't write today's lead, but here's where things stand."
    send(f"{prose}\n\n{backlog}")
```

- [ ] **Step 4: Wrap `send_nags`**

Replace the body of `send_nags` (scheduler.py:24-42) so each nag is attempted independently and one failure note is emitted per run:

```python
def send_nags(conn, send, client, now: datetime) -> None:
    logger.info("Nag job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping nag: quiet hours")
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    focus_id = focus.focus.id if focus.focus else None
    tadpole = select_slow_resurface(tasks, now, exclude_id=focus_id)
    failed = False

    def _nag(task):
        nonlocal failed
        try:
            send(llm.write_nag(task, now, client))
            db.mark_nagged(conn, task.id, now)
        except Exception:
            logger.exception("Nag failed for task_id=%s", task.id)
            failed = True

    if focus.focus is not None:
        logger.info("Nagging about focus task_id=%s", focus.focus.id)
        _nag(focus.focus)
    if tadpole is not None:
        logger.info("Slow re-surface of task_id=%s", tadpole.id)
        _nag(tadpole)
    if focus.focus is None and tadpole is None:
        logger.info("Skipping nag: nothing to nag about")
    elif failed:
        send("I tried to nudge you but something on my end broke. I'll try again next time.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS (existing focus/nag/quiet-hours tests still green: the happy-path `FakeClient` never raises, so `failed` stays False and no extra note is sent).

- [ ] **Step 6: Commit**

```bash
git add src/jolt/scheduler.py tests/test_scheduler.py
git commit -m "fix(scheduler): tell the user when a focus or nag job breaks instead of failing silently"
```

---

### Task 7: `unblock_task` matches its siblings

Every other mutator scopes to `status = pending` and reports a no-op via `rowcount`. `unblock_task` currently updates unconditionally and always reports success. Make it consistent so it cannot "unblock" a done, dropped, or nonexistent task and report success.

**Files:**
- Modify: `src/jolt/db.py` (`unblock_task`, lines 156-159)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `unblock_task(conn, task_id) -> Task | None` returns `None` when no pending task with that id was updated.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py` (it already has a `fresh()`-style setup; match the existing pattern in that file for creating a connection and adding a task):

```python
def test_unblock_returns_none_for_a_done_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "wax the car", "normal", None, now)
    db.complete_task(conn, t.id, now)  # now done, not pending
    assert db.unblock_task(conn, t.id) is None


def test_unblock_returns_none_for_a_missing_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    assert db.unblock_task(conn, 9999) is None
```

Check the top of `tests/test_db.py` for the existing `datetime` / `TZ` imports and reuse them; add them if the file does not already import them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py::test_unblock_returns_none_for_a_done_task tests/test_db.py::test_unblock_returns_none_for_a_missing_task -v`
Expected: the done-task test FAILs (returns a `Task`, not `None`); the missing-task test already passes (`get_task` returns None) but keep it as a guard.

- [ ] **Step 3: Fix `unblock_task`**

Replace `unblock_task` (db.py:156-159):

```python
def unblock_task(conn, task_id: int) -> Task | None:
    cur = conn.execute(
        "UPDATE tasks SET blocked_by = NULL WHERE id = ? AND status = ?",
        (task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (the existing unblock happy-path test, if any, still green: a pending task is still unblocked and returned).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/db.py tests/test_db.py
git commit -m "fix(db): scope unblock_task to pending tasks like the other mutators"
```

---

### Task 8: Defensive guard in `block_task`

`block_task` writes `blocked_by` with no self-reference or target-exists check; its correctness rests entirely on the orchestrator caller. Make it safe on its own: reject a self-block and a nonexistent blocker, so a future second caller or refactor cannot reintroduce a self-block or a dangling reference. Cycle detection stays in the orchestrator (it has the full graph).

**Files:**
- Modify: `src/jolt/db.py` (`block_task`, lines 123-129)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `block_task(conn, task_id, blocked_by) -> Task | None` returns `None` (and writes nothing) when `task_id == blocked_by` or when no pending task exists at `blocked_by`; otherwise behaves as before.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
def test_block_rejects_self_reference():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "paint fence", "normal", None, now)
    assert db.block_task(conn, t.id, t.id) is None
    assert db.get_task(conn, t.id).blocked_by is None


def test_block_rejects_missing_blocker():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "paint fence", "normal", None, now)
    assert db.block_task(conn, t.id, 9999) is None
    assert db.get_task(conn, t.id).blocked_by is None


def test_block_still_works_for_valid_input():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy paint", "normal", None, now)
    b = db.add_task(conn, "paint fence", "normal", None, now)
    result = db.block_task(conn, b.id, a.id)
    assert result is not None
    assert result.blocked_by == a.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py::test_block_rejects_self_reference tests/test_db.py::test_block_rejects_missing_blocker -v`
Expected: both FAIL (current `block_task` writes the value and returns the task).

- [ ] **Step 3: Add the guards to `block_task`**

Replace `block_task` (db.py:123-129):

```python
def block_task(conn, task_id: int, blocked_by: int) -> Task | None:
    """Make task_id wait on blocked_by. The orchestrator validates cycles and both statuses
    before calling; these guards make the function safe on its own against a self-block or a
    blocker that does not exist."""
    if task_id == blocked_by:
        return None
    blocker = get_task(conn, blocked_by)
    if blocker is None or blocker.status != STATUS_PENDING:
        return None
    cur = conn.execute(
        "UPDATE tasks SET blocked_by = ? WHERE id = ? AND status = ?",
        (blocked_by, task_id, STATUS_PENDING),
    )
    conn.commit()
    return get_task(conn, task_id) if cur.rowcount else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py tests/test_orchestrator.py -v`
Expected: PASS (the orchestrator block path already validates the same things upstream, so its behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/jolt/db.py tests/test_db.py
git commit -m "fix(db): guard block_task against self-blocks and missing blockers"
```

---

### Task 9: Delete dead code

`is_urgent` and `DUE_SOON_DAYS` are defined but referenced nowhere in `src/` or `tests/`. They wrongly imply urgency feeds selection (only staleness and importance do). Remove both.

**Files:**
- Modify: `src/jolt/selection.py` (remove `is_urgent`, lines 53-58)
- Modify: `src/jolt/config.py` (remove `DUE_SOON_DAYS`, line 7)

**Interfaces:**
- Produces: nothing new. Removes `selection.is_urgent` and `config.DUE_SOON_DAYS`.

- [ ] **Step 1: Confirm there are no references**

Run: `grep -rn "is_urgent\|DUE_SOON_DAYS" src tests`
Expected: matches only the definition site in `selection.py` and `config.py` (and `is_urgent`'s own body). If any other file references them, STOP and reconsider: the assumption that they are dead is wrong.

- [ ] **Step 2: Remove `is_urgent` from `selection.py`**

Delete the `is_urgent` function (selection.py:53-58) entirely, including its comment lines.

- [ ] **Step 3: Remove `DUE_SOON_DAYS` from `config.py`**

Delete the line `DUE_SOON_DAYS = 2  # a deadline this close (or past) counts as urgent` (config.py:7).

- [ ] **Step 4: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS, ruff clean (no unused-import or undefined-name errors). If ruff reports `config` is now an unused import in `selection.py`, remove that import too; if `config` is still used elsewhere in `selection.py` (it is, for `STALE_THRESHOLD_DAYS` and `SLOW_RESURFACE_DAYS`), leave it.

- [ ] **Step 5: Commit**

```bash
git add src/jolt/selection.py src/jolt/config.py
git commit -m "refactor: remove unused is_urgent and DUE_SOON_DAYS"
```

---

### Task 10: Fill the remaining test gaps

Add the pure-test coverage the spec calls for that is not already covered by earlier tasks: the slow-resurface cadence boundary at exactly 7 days, the `config` env-readers' defaults, and `save_display` round-tripping an empty list.

**Files:**
- Test: `tests/test_selection.py`
- Test: `tests/test_config.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `selection.select_slow_resurface`, `config.db_path`, `config.log_level`, `db.save_display`, `db.load_display`.

- [ ] **Step 1: Add the 7-day cadence boundary test**

Add to `tests/test_selection.py` (reuse the file's existing `Task`/`datetime`/`TZ` helpers and `PRIORITY_NORMAL`; match the shape of the existing slow-resurface tests):

```python
def test_slow_resurface_eligible_at_exactly_the_cadence_boundary():
    from jolt import config, selection

    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    created = now - timedelta(days=10)  # stale
    last_nagged = now - timedelta(days=config.SLOW_RESURFACE_DAYS)  # exactly the cadence
    task = Task(
        id=1, text="sort photos", priority=PRIORITY_NORMAL, deadline=None,
        created_at=created, status=STATUS_PENDING, last_nagged_at=last_nagged, completed_at=None,
    )
    assert selection.select_slow_resurface([task], now) is task
```

Ensure `timedelta` and `STATUS_PENDING` are imported at the top of `tests/test_selection.py`; add them if missing.

- [ ] **Step 2: Add config env-reader tests**

Add to `tests/test_config.py`:

```python
def test_db_path_defaults_when_unset(monkeypatch):
    from jolt import config

    monkeypatch.delenv("JOLT_DB_PATH", raising=False)
    assert config.db_path() == "data/jolt.db"


def test_log_level_defaults_to_info_and_uppercases(monkeypatch):
    from jolt import config

    monkeypatch.delenv("JOLT_LOG_LEVEL", raising=False)
    assert config.log_level() == "INFO"
    monkeypatch.setenv("JOLT_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"
```

- [ ] **Step 3: Add the empty-display round-trip test**

Add to `tests/test_db.py`:

```python
def test_save_display_round_trips_empty_list():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.save_display(conn, 42, [])
    assert db.load_display(conn, 42) == []
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_selection.py tests/test_config.py tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_selection.py tests/test_config.py tests/test_db.py
git commit -m "test: cover slow-resurface boundary, config defaults, and empty display snapshot"
```

---

### Task 11: Refresh CLAUDE.md to match the shipped code

The "Module Responsibilities (planned)" layout, the test count, and the data model in `CLAUDE.md` no longer match the code. Update them. Do not add em dashes when editing.

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Get the real test count**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: a line like `NNN passed in ...`. Note the number `NNN` for Step 3.

- [ ] **Step 2: Update the module layout**

In `CLAUDE.md`, in the "Module Responsibilities (planned)" section, replace the planned `src/` tree (which lists `tasks.py` and `staleness.py`) with the real modules, and drop the word "(planned)" from the heading. The real layout:

```
src/jolt/
  main.py         Entry point: wires up bot + scheduler, builds the client, starts the app
  bot.py          Telegram handler: receives messages, sends replies and nags
  llm.py          Anthropic client: interprets messages, classifies intent, writes text
  orchestrator.py Applies a parsed intent to the backlog and returns the reply text
  selection.py    Pure rules: stale detection, daily-focus selection, slow-resurface, quiet hours
  render.py       Deterministic backlog rendering and display ordering
  memory.py       In-process recent-conversation and pending-outbound memory per chat
  scheduler.py    Job bodies: daily focus and nags (wired to APScheduler cron in main.py)
  db.py           SQLite access: the tasks table and the display_snapshot table
  models.py       Task and DailyFocus dataclasses and the status/priority constants
  config.py       Named tunables and environment readers
```

- [ ] **Step 3: Update the status line and data model**

In the "Status" paragraph, replace the "40 tests pass" claim with the real count from Step 1 (write it as, for example, "164 tests pass"). In the data-model description, add the `blocked_by` column to the `tasks` table field list (it sits alongside `id`, `text`, `priority`, `deadline`, `created_at`, `status`, `last_nagged_at`, `completed_at`), and mention the second table, `display_snapshot` (one row per chat: the ordered task ids of the last list shown).

- [ ] **Step 4: Verify no em dashes were introduced**

Run: `git diff CLAUDE.md | grep -n "^+" | grep "—"`
Expected: no output (no added line contains an em dash). If any appears, replace it with a comma, colon, period, or parentheses.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: align CLAUDE.md module layout, test count, and data model with the code"
```

---

## Final verification

- [ ] Run the whole suite and lint:

```bash
uv run pytest -q && uv run ruff check .
```

Expected: all tests pass, ruff clean.

- [ ] Confirm no happy-path behavior changed: the pre-existing tests in `tests/test_scheduler.py`, `tests/test_llm.py`, `tests/test_orchestrator.py`, and `tests/test_db.py` all still pass unmodified (only additions were made to them).
