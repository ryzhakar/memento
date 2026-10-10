"""`memento events add` appends one event line to the open span's events file, or refuses and lands nothing."""

import fcntl
import os
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memento import Environment
from memento.__main__ import CHECKS, app
from memento.git import git_exit_status
from memento.store import Store
from tests.memento.conftest import FIXED_NOW, HISTORY, git, replace_once, run_memento

OPEN_EVENTS_FILE = f"{HISTORY}/2026-10-01/events.md"


def test_an_event_lands_as_one_line_at_the_end_of_the_open_spans_events_file(record_tree: Path) -> None:
    before = (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8")
    result = run_memento(record_tree, "events", "add", "agent:J-b", "receipt", "verdict:", "APPROVE, 2 minors")
    line = "2026-10-01T10:30 | agent:J-b | receipt | verdict: APPROVE, 2 minors"
    assert (result.exit_code, result.stdout) == (0, f"{OPEN_EVENTS_FILE}: {line}\n")
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8") == before + line + "\n"


def assert_refused(root: Path, message: str, *args: str, now: datetime = FIXED_NOW) -> None:
    """Assert `events add` refuses with this message on stderr alone and leaves the open events file's bytes untouched."""
    before = (root / OPEN_EVENTS_FILE).read_bytes()
    result = run_memento(root, "events", "add", *args, now=now)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert (root / OPEN_EVENTS_FILE).read_bytes() == before


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("span-event", "span-event lines are written by 'memento events open' or 'memento digest close'"),
        ("failure", "a failure is written by 'memento failure add', which writes its entry and its events line together"),
        ("change", "change goes to the change log: 'memento change add'"),
        ("decided", "'decided' is not an event kind (decision, discovery, commitment, delegation, receipt, attenuation)"),
    ],
)
def test_a_kind_the_events_file_does_not_take_is_refused_naming_where_it_goes(record_tree: Path, kind: str, message: str) -> None:
    assert_refused(record_tree, message, "self", kind, "something happened")


@pytest.mark.parametrize("actor", ["agent:", "agents:J-b", "Owner", "tool:a b"])
def test_an_actor_outside_the_four_forms_is_refused(record_tree: Path, actor: str) -> None:
    assert_refused(record_tree, f"actor '{actor}' is not owner, self, agent:<name> or tool:<name>", actor, "decision", "x")


@pytest.mark.parametrize(
    ("what", "message"),
    [
        ("first\nsecond", "what is one line; it carries a line break"),
        ("first second", "what is one line; it carries a line break"),
        (" indented", "what must start with a visible character"),
    ],
)
def test_a_what_that_is_not_one_visible_line_is_refused(record_tree: Path, what: str, message: str) -> None:
    assert_refused(record_tree, message, "self", "decision", what)


def test_a_clock_behind_the_events_files_last_line_is_refused(record_tree: Path) -> None:
    behind = datetime.fromisoformat("2026-10-01T09:09:59+02:00")
    message = "the clock reads 2026-10-01T09:09, before the events file's last line at 2026-10-01T09:10"
    assert_refused(record_tree, message, "self", "decision", "x", now=behind)


def test_a_clock_at_the_events_files_last_minute_lands(record_tree: Path) -> None:
    same_minute = datetime.fromisoformat("2026-10-01T09:10:00+02:00")
    result = run_memento(record_tree, "events", "add", "self", "decision", "x", now=same_minute)
    assert result.exit_code == 0


def append_to_open_events_file(root: Path, line: str) -> None:
    """Append a line to the open span's events file without committing it."""
    with (root / OPEN_EVENTS_FILE).open("a", encoding="utf-8") as events_file:
        events_file.write(line + "\n")


def test_an_event_after_the_span_closed_is_refused(record_tree: Path) -> None:
    append_to_open_events_file(record_tree, "2026-10-01T09:20 | self | span-event | close; HEAD abc1234 (clean); next: rest")
    append_to_open_events_file(record_tree, "2026-10-01T09:25 | self | span-event | pat-down found the span closed")
    assert_refused(record_tree, "no span is open; 'memento events open <what>'", "self", "decision", "x")


def test_an_event_with_no_events_file_under_history_is_refused(record_tree: Path) -> None:
    for events_file in (record_tree / HISTORY).glob("*/events.md"):
        events_file.unlink()
    result = run_memento(record_tree, "events", "add", "self", "decision", "x")
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "refused: no span is open; 'memento events open <what>'\n")


def test_a_span_event_neither_opening_nor_closing_leaves_the_span_open(record_tree: Path) -> None:
    append_to_open_events_file(record_tree, "2026-10-01T09:25 | owner | span-event | owner ordered visible re-binding")
    result = run_memento(record_tree, "events", "add", "self", "decision", "x")
    assert result.exit_code == 0


def test_an_event_in_a_linked_worktree_is_refused(record_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(record_tree, "worktree", "add", "-q", "--detach", str(worktree))
    message = f"records are written in the root checkout only; this is worktree {worktree}"
    assert_refused(worktree, message, "self", "decision", "x")


def test_an_uncommitted_line_already_appended_is_extended(record_tree: Path) -> None:
    append_to_open_events_file(record_tree, "2026-10-01T09:20 | self | decision | not yet committed")
    result = run_memento(record_tree, "events", "add", "self", "decision", "x")
    assert result.exit_code == 0
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith(
        "not yet committed\n2026-10-01T10:30 | self | decision | x\n"
    )


@pytest.mark.parametrize(
    ("old", "new", "line"),
    [
        ("verdict delivered\n", "verdict rewritten\n", 2),
        ("2026-10-01T09:10 | agent:J-b | receipt | verdict delivered\n", "", 2),
        ("verdict delivered\n", "verdict delivered", 2),
    ],
)
def test_an_events_file_whose_working_copy_edits_a_committed_line_is_refused(
    record_tree: Path, old: str, new: str, line: int
) -> None:
    replace_once(record_tree, OPEN_EVENTS_FILE, old, new)
    message = f"{OPEN_EVENTS_FILE} is not an extension of HEAD's copy (first difference at line {line}); written lines are never edited"
    assert_refused(record_tree, message, "self", "decision", "x")


def test_a_schema_whose_actors_memento_does_not_know_is_refused(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "    - tool:<name>\n", "")
    message = (
        "schema declares events actors ['owner', 'self', 'agent:<name>']; "
        "memento knows ['owner', 'self', 'agent:<name>', 'tool:<name>']; amend the module with the schema"
    )
    assert_refused(record_tree, message, "self", "decision", "x")


def test_a_schema_declaring_a_kind_memento_does_not_know_is_refused(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "kinds:\n", "kinds:\n  ledger:\n    home: docs/ledger.md\n    load: need\n")
    result = run_memento(record_tree, "events", "add", "self", "decision", "x")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith("refused: schema declares kinds ['ledger', 'charter',")
    assert result.stderr.endswith("; amend the module with the schema\n")


def record_lock_held_elsewhere(root: Path) -> bool:
    """Whether the record lock is held, probed by trying to take it without waiting."""
    with (root / ".git/memento.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def test_the_clock_is_read_while_the_record_lock_is_held(record_tree: Path) -> None:
    observed: list[bool] = []

    def clock_probing_the_lock() -> datetime:
        """The fixed time, after noting whether the record lock is held at the moment of reading."""
        observed.append(record_lock_held_elsewhere(record_tree))
        return FIXED_NOW

    environment = Environment(store=Store(root=record_tree, replace_file=os.replace), now=clock_probing_the_lock, checks=CHECKS, run_git=git_exit_status)
    result = CliRunner().invoke(app, ["events", "add", "self", "decision", "x"], obj=environment)
    assert (result.exit_code, observed) == (0, [True])
