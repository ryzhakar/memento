"""The journal verbs write events, digests and failures into the open span, and the journal checks hold them to their shapes."""

import os
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest
import yaml
from typer.testing import CliRunner

from memento import Environment
from memento.__main__ import CHECKS, app
from memento.findings import split_entries
from memento.journal_shapes import CLOSE_HEADING, LineKind, entry_text, parse_close_block, parse_event_line, parse_failure_entry
from memento.schema import KNOWN_EVENT_ROUTES
from memento.git import git_exit_status
from memento.store import Store
from tests.conftest import FIXED_NOW, HISTORY, SCHEMA_PATH, commit_tree, fixture_schema, git, replace_once, run_memento

CLOSED_DAY_EVENTS = f"{HISTORY}/2026-09-30/events.md"
OPEN_DAY = f"{HISTORY}/2026-10-01"
OPEN_EVENTS_FILE = f"{OPEN_DAY}/events.md"
NEXT_MORNING = datetime.fromisoformat("2026-10-02T08:00:12+02:00")


def append_line(root: Path, path: str, line: str) -> None:
    """Append one line to a tree file without committing it."""
    with (root / path).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def close_open_span(root: Path) -> None:
    """Write the open span's close line into its events file by hand."""
    append_line(root, OPEN_EVENTS_FILE, "2026-10-01T18:00 | self | span-event | close; HEAD abc1234 (clean); next: rest")


def test_opening_a_span_after_the_last_one_closed_starts_todays_events_file(record_tree: Path) -> None:
    close_open_span(record_tree)
    result = run_memento(record_tree, "events", "open", "the", "next", "span", now=NEXT_MORNING)
    events_file = f"{HISTORY}/2026-10-02/events.md"
    line = "2026-10-02T08:00 | self | span-event | open; the next span"
    assert (result.exit_code, result.stdout) == (0, f"{events_file}: {line}\n")
    assert (record_tree / events_file).read_text(encoding="utf-8") == line + "\n"


def journal_bytes(root: Path) -> dict[str, bytes]:
    """Every file under the history directory, by path, with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted((root / HISTORY).rglob("*")) if path.is_file()}


def assert_refused_landing_nothing(root: Path, message: str, *args: str, now: datetime = NEXT_MORNING) -> None:
    """Assert a verb refuses with this message on stderr alone and leaves every journal file as it was."""
    before = journal_bytes(root)
    result = run_memento(root, *args, now=now)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert journal_bytes(root) == before


def test_opening_a_span_while_the_newest_is_open_is_refused(record_tree: Path) -> None:
    message = "span 2026-10-01 is open since 2026-10-01T09:00; resume it or close it"
    assert_refused_landing_nothing(record_tree, message, "events", "open", "another")


def test_opening_a_span_with_a_what_of_two_lines_is_refused(record_tree: Path) -> None:
    close_open_span(record_tree)
    assert_refused_landing_nothing(record_tree, "what is one line; it carries a line break", "events", "open", "first\nsecond")


def test_opening_a_span_with_a_clock_behind_the_newest_events_file_is_refused(record_tree: Path) -> None:
    close_open_span(record_tree)
    message = "the clock reads 2026-10-01T17:59, before the events file's last line at 2026-10-01T18:00"
    behind = datetime.fromisoformat("2026-10-01T17:59:00+02:00")
    assert_refused_landing_nothing(record_tree, message, "events", "open", "x", now=behind)


def test_reopening_on_the_day_the_last_span_closed_extends_that_days_events_file(record_tree: Path) -> None:
    close_open_span(record_tree)
    before = (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8")
    evening = datetime.fromisoformat("2026-10-01T19:00:00+02:00")
    result = run_memento(record_tree, "events", "open", "evening", "work", now=evening)
    assert result.exit_code == 0
    line = "2026-10-01T19:00 | self | span-event | open; evening work"
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8") == before + line + "\n"


def test_the_tail_runs_from_the_open_spans_last_span_event_to_its_end(record_tree: Path) -> None:
    later = [
        "2026-10-01T09:20 | owner | span-event | owner ordered visible re-binding",
        "2026-10-01T09:30 | self | decision | the tail starts above",
    ]
    for line in later:
        append_line(record_tree, OPEN_EVENTS_FILE, line)
    result = run_memento(record_tree, "events", "tail")
    assert (result.exit_code, result.stdout) == (0, "\n".join(later) + "\n")


def test_the_tail_of_a_closed_newest_span_is_its_close_line(record_tree: Path) -> None:
    close_open_span(record_tree)
    result = run_memento(record_tree, "events", "tail")
    assert (result.exit_code, result.stdout) == (0, "2026-10-01T18:00 | self | span-event | close; HEAD abc1234 (clean); next: rest\n")


def test_the_tail_with_no_events_file_prints_nothing(record_tree: Path) -> None:
    for events_file in (record_tree / HISTORY).glob("*/events.md"):
        events_file.unlink()
    result = run_memento(record_tree, "events", "tail")
    assert (result.exit_code, result.stdout) == (0, "")


SESSION = f"{OPEN_DAY}/session.md"
FAILURES = f"{OPEN_DAY}/failures.md"


def test_each_paragraph_lands_in_the_open_spans_session_record_under_its_date(record_tree: Path) -> None:
    result = run_memento(record_tree, "digest", "add", "The span began.", "It went on.")
    assert (result.exit_code, result.stdout) == (0, f"{SESSION}: # 2026-10-01\n{SESSION}: The span began.\n{SESSION}: It went on.\n")
    assert (record_tree / SESSION).read_text(encoding="utf-8") == "# 2026-10-01\n\nThe span began.\n\nIt went on.\n"


def test_a_later_paragraph_extends_the_session_record(record_tree: Path) -> None:
    run_memento(record_tree, "digest", "add", "The span began.")
    run_memento(record_tree, "digest", "add", "It went on.")
    assert (record_tree / SESSION).read_text(encoding="utf-8") == "# 2026-10-01\n\nThe span began.\n\nIt went on.\n"


def test_narrative_after_a_close_block_opens_under_a_heading_naming_the_close(record_tree: Path) -> None:
    (record_tree / SESSION).write_text("# 2026-10-01\n\n## Close — 2026-10-01T18:00\n\nHEAD    abc1234 (clean)\n", encoding="utf-8")
    close_open_span(record_tree)
    append_line(record_tree, OPEN_EVENTS_FILE, "2026-10-01T19:00 | self | span-event | open; evening")
    run_memento(record_tree, "digest", "add", "The evening went on.")
    assert (record_tree / SESSION).read_text(encoding="utf-8").endswith(
        "HEAD    abc1234 (clean)\n\n## After the 18:00 close\n\nThe evening went on.\n"
    )


def test_a_paragraph_holding_a_line_break_is_refused(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "paragraph is one line; it carries a line break", "digest", "add", "fine", "one\ntwo")


def test_a_paragraph_starting_as_a_close_block_is_refused(record_tree: Path) -> None:
    message = "a paragraph starting '## Close' would read as a close block"
    assert_refused_landing_nothing(record_tree, message, "digest", "add", "## Close — 2026-10-01T10:00")


def test_a_paragraph_starting_as_an_entry_heading_is_refused(record_tree: Path) -> None:
    message = "a paragraph starting '## ' would read as an entry heading"
    assert_refused_landing_nothing(record_tree, message, "digest", "add", "## Notes")


def test_a_paragraph_with_no_open_span_is_refused(record_tree: Path) -> None:
    close_open_span(record_tree)
    assert_refused_landing_nothing(record_tree, "no span is open; 'memento events open <what>'", "digest", "add", "late")


def short_head(root: Path) -> str:
    """HEAD's short sha in a tree."""
    return git(root, "rev-parse", "--short", "HEAD").strip()


def test_closing_the_span_writes_its_close_block_and_close_line_together(record_tree: Path) -> None:
    sha = short_head(record_tree)
    result = run_memento(record_tree, "digest", "close", "the verbs stand\nthe checks hold", "none", "rest")
    block = f"## Close — 2026-10-01T10:30\n\nHEAD    {sha} (clean)\nstate   the verbs stand\n        the checks hold\nopen    none\nnext    rest\n"
    line = f"2026-10-01T10:30 | self | span-event | close; HEAD {sha} (clean); next: rest"
    assert result.exit_code == 0
    assert (record_tree / SESSION).read_text(encoding="utf-8") == "# 2026-10-01\n\n" + block
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith(line + "\n")
    printed = [f"{SESSION}: {text}" for text in ["# 2026-10-01", *block.splitlines()] if text] + [f"{OPEN_EVENTS_FILE}: {line}"]
    assert result.stdout == "\n".join(printed) + "\n"


def test_the_close_block_names_the_paths_git_reports_changed(record_tree: Path) -> None:
    (record_tree / "docs/capabilities.md").write_text("# Capabilities\n\nChanged.\n", encoding="utf-8")
    (record_tree / "notes.txt").write_text("untracked\n", encoding="utf-8")
    run_memento(record_tree, "digest", "close", "s", "o", "n")
    head = f"{short_head(record_tree)} (dirty: docs/capabilities.md, notes.txt)"
    assert f"HEAD    {head}\n" in (record_tree / SESSION).read_text(encoding="utf-8")
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith(f"close; HEAD {head}; next: n\n")


def test_a_close_block_lands_after_the_spans_narrative(record_tree: Path) -> None:
    run_memento(record_tree, "digest", "add", "The span began.")
    run_memento(record_tree, "digest", "close", "s", "o", "n")
    assert (record_tree / SESSION).read_text(encoding="utf-8").startswith("# 2026-10-01\n\nThe span began.\n\n## Close — 2026-10-01T10:30\n\n")


def test_closing_with_no_open_span_is_refused(record_tree: Path) -> None:
    close_open_span(record_tree)
    assert_refused_landing_nothing(record_tree, "no span is open; 'memento events open <what>'", "digest", "close", "s", "o", "n")


def test_a_state_over_the_schemas_line_limit_is_refused(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "state runs 4 lines, over 3", "digest", "close", "a\nb\nc\nd", "o", "n", now=FIXED_NOW)


def test_an_empty_close_field_is_refused(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "open is empty", "digest", "close", "s", "", "n", now=FIXED_NOW)


def test_a_close_field_line_starting_with_a_blank_is_refused(record_tree: Path) -> None:
    message = "state has a line not starting with a visible character"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s\n  indented", "o", "n", now=FIXED_NOW)


def test_a_next_step_of_two_lines_is_refused(record_tree: Path) -> None:
    message = "next is one line; the span's close line in the events file carries it"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n\nm", now=FIXED_NOW)


def test_closing_with_a_clock_behind_the_events_file_is_refused(record_tree: Path) -> None:
    behind = datetime.fromisoformat("2026-10-01T09:09:00+02:00")
    message = "the clock reads 2026-10-01T09:09, before the events file's last line at 2026-10-01T09:10"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n", now=behind)


CLOSED_BLOCK = "## Close — 2026-09-30T18:00\n\nHEAD    abc1234 (clean)\nstate   the fixture stands\nopen    none\nnext    rest\n"


def test_the_baseline_is_the_newest_close_block_as_recorded(record_tree: Path) -> None:
    result = run_memento(record_tree, "digest", "baseline")
    assert (result.exit_code, result.stdout) == (0, CLOSED_BLOCK)


def test_the_baseline_moves_to_a_close_block_written_later(record_tree: Path) -> None:
    run_memento(record_tree, "digest", "close", "s", "o", "n")
    result = run_memento(record_tree, "digest", "baseline")
    assert result.stdout.startswith("## Close — 2026-10-01T10:30\n")


def test_the_baseline_with_no_close_block_prints_nothing(record_tree: Path) -> None:
    (record_tree / f"{HISTORY}/2026-09-30/session.md").write_text("# 2026-09-30\n", encoding="utf-8")
    result = run_memento(record_tree, "digest", "baseline")
    assert (result.exit_code, result.stdout) == (0, "")


FAILURE_PARTS = ("A line written twice", "the writer appended twice", "nothing refused the second write", "the writer refuses it")
FAILURE_ENTRY = (
    "## A line written twice\n\nWhat happened: the writer appended twice\n\n"
    "Mechanism: nothing refused the second write\n\nCorrection: the writer refuses it\n"
)


def test_a_failure_lands_as_its_entry_and_its_events_line_together(record_tree: Path) -> None:
    result = run_memento(record_tree, "failure", "add", *FAILURE_PARTS)
    line = "2026-10-01T10:30 | self | failure | A line written twice"
    assert result.exit_code == 0
    assert (record_tree / FAILURES).read_text(encoding="utf-8") == "# 2026-10-01 — failures\n\n" + FAILURE_ENTRY
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith(line + "\n")
    assert result.stdout.endswith(f"{FAILURES}: Correction: the writer refuses it\n{OPEN_EVENTS_FILE}: {line}\n")


def test_a_second_failure_extends_the_failures_file(record_tree: Path) -> None:
    run_memento(record_tree, "failure", "add", *FAILURE_PARTS)
    run_memento(record_tree, "failure", "add", "Another", "a", "m", "c")
    expected = "# 2026-10-01 — failures\n\n" + FAILURE_ENTRY + "\n## Another\n\nWhat happened: a\n\nMechanism: m\n\nCorrection: c\n"
    assert (record_tree / FAILURES).read_text(encoding="utf-8") == expected


def test_an_empty_failure_part_is_refused(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "Mechanism is empty", "failure", "add", "t", "w", "", "c", now=FIXED_NOW)


def test_a_failure_part_of_two_lines_is_refused(record_tree: Path) -> None:
    message = "Correction is one line; it carries a line break"
    assert_refused_landing_nothing(record_tree, message, "failure", "add", "t", "w", "m", "c\nd", now=FIXED_NOW)


def test_a_failure_title_already_in_the_failures_file_is_refused(record_tree: Path) -> None:
    run_memento(record_tree, "failure", "add", *FAILURE_PARTS)
    message = f"failure 'A line written twice' is already in {FAILURES}"
    assert_refused_landing_nothing(record_tree, message, "failure", "add", *FAILURE_PARTS, now=FIXED_NOW)


def test_a_failure_with_no_open_span_is_refused(record_tree: Path) -> None:
    close_open_span(record_tree)
    assert_refused_landing_nothing(record_tree, "no span is open; 'memento events open <what>'", "failure", "add", *FAILURE_PARTS)


def test_a_fault_between_the_failures_two_writes_leaves_both_files_as_they_were(record_tree: Path) -> None:
    replaced: list[Path] = []

    def replace_then_fail(source: Path, target: Path) -> None:
        """Replace the first file, then fail before the second lands."""
        if replaced:
            raise OSError("injected fault")
        replaced.append(target)
        os.replace(source, target)

    run_memento(record_tree, "failure", "add", "First", "w", "m", "c")
    before = journal_bytes(record_tree)
    environment = Environment(store=Store(root=record_tree, replace_file=replace_then_fail), now=lambda: FIXED_NOW, checks=CHECKS, run_git=git_exit_status)
    result = CliRunner().invoke(app, ["failure", "add", *FAILURE_PARTS], obj=environment)
    assert (type(result.exception), replaced) == (OSError, [record_tree / FAILURES])
    assert journal_bytes(record_tree) == before


CLOSED_SESSION = f"{HISTORY}/2026-09-30/session.md"
CLOSED_FAILURES = f"{HISTORY}/2026-09-30/failures.md"
CLOSE_LAYOUT = "a close block is its header, one blank line, then its fields with no blank line among them"
FAILURE_LAYOUT = "a failure entry is its title, then a blank line before each one-line paragraph"


def check_findings(root: Path) -> list[str]:
    """The finding lines `memento check` prints for a tree, its summary left out."""
    return run_memento(root, "check").stdout.splitlines()[:-1]


def rewrite_schema(root: Path, kind: str, key: str, value: object) -> None:
    """Set one key of one kind in the tree's schema."""
    schema = yaml.safe_load((root / SCHEMA_PATH).read_text(encoding="utf-8"))
    schema["kinds"][kind][key] = value
    (root / SCHEMA_PATH).write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_a_schema_renaming_a_close_label_fails_the_structure_agreement(record_tree: Path) -> None:
    rewrite_schema(record_tree, "digest", "close_labels", ["HEAD", "state", "waits", "next"])
    assert (
        f"FAIL  {SCHEMA_PATH}  schema declares digest close_labels ['HEAD', 'state', 'waits', 'next']; "
        "memento knows ['HEAD', 'state', 'open', 'next']; amend the module with the schema"
    ) in check_findings(record_tree)


def test_a_schema_renaming_a_failure_label_fails_the_structure_agreement(record_tree: Path) -> None:
    rewrite_schema(record_tree, "failure", "entry_labels", ["What happened", "Cause", "Correction"])
    assert (
        f"FAIL  {SCHEMA_PATH}  schema declares failure entry_labels ['What happened', 'Cause', 'Correction']; "
        "memento knows ['What happened', 'Mechanism', 'Correction']; amend the module with the schema"
    ) in check_findings(record_tree)


def test_a_close_block_with_a_blank_line_between_its_fields_does_not_render_back(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "state   the fixture stands\n", "state   the fixture stands\n\n")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  {CLOSE_LAYOUT}"]


def test_a_close_block_with_no_blank_line_under_its_header_does_not_render_back(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "2026-09-30T18:00\n\nHEAD", "2026-09-30T18:00\nHEAD")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  {CLOSE_LAYOUT}"]


def test_a_close_block_whose_head_is_no_head_fails(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "abc1234 (clean)", "abc1234, clean")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  HEAD is not '<sha> (clean)' or '<sha> (dirty: <paths>)'"]


def test_a_close_block_repeating_a_field_does_not_render_back(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "state   the fixture stands\n", "state   the fixture stands\nstate   and again\n")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  close field 'state' repeats"]


def test_a_failure_paragraph_wrapped_onto_a_second_line_does_not_render_back(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_FAILURES, "written twice.\n", "written\ntwice.\n")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_FAILURES}:3  {FAILURE_LAYOUT}"]


def test_a_failure_entry_with_a_paragraph_past_its_three_fails(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_FAILURES, "the writer refuses it.\n", "the writer refuses it.\n\nNotes: kept.\n")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_FAILURES}:3  4 paragraphs, not 3"]


def test_a_failure_label_not_followed_by_a_space_fails(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_FAILURES, "Correction: the", "Correction:the")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_FAILURES}:3  'Correction:' is not followed by a space"]


def test_a_failure_paragraph_left_empty_fails(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_FAILURES, "Correction: the writer refuses it.", "Correction:")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_FAILURES}:3  Correction is empty"]


def written_after_ratification(root: Path, pattern: str) -> list[str]:
    """The text of every journal file in a tree's history directory matching a pattern, written after ratification."""
    ratified_on = fixture_schema()["project"]["ratified_on"]
    return [path.read_text(encoding="utf-8") for path in sorted((root / HISTORY).glob(f"*/{pattern}")) if path.parent.name > ratified_on]


def test_every_written_events_line_close_block_and_failure_entry_renders_back_to_itself(record_tree: Path) -> None:
    label_width = fixture_schema()["kinds"]["digest"]["close_label_width"]
    lines = [line for text in written_after_ratification(record_tree, "events*.md") for line in text.splitlines()]
    closes = [entry for text in written_after_ratification(record_tree, "session*.md") for entry in split_entries(text) if entry[1].startswith(CLOSE_HEADING)]
    failures = [entry for text in written_after_ratification(record_tree, "failures.md") for entry in split_entries(text)]
    assert all((lines, closes, failures))
    assert [line for line in lines if parse_event_line(line).render() != line] == []
    assert [header for _n, header, body in closes if parse_close_block(header, body, label_width).render(label_width) != entry_text(header, body)] == []
    assert [header for _n, header, body in failures if parse_failure_entry(header, body).render() != entry_text(header, body)] == []


def commit_recipes_at(root: Path, author_date: str) -> None:
    """Commit a memento recipes file into the tree, dated as the start of the journal's append-only history."""
    (root / "memento.just").write_text("check:\n", encoding="utf-8")
    git(root, "add", "memento.just")
    git(root, "commit", "-q", f"--date={author_date}", "-m", "recipes")


def test_a_commit_removing_a_written_journal_line_after_the_recipes_landed_fails(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-10-01T12:00:00")
    replace_once(record_tree, CLOSED_DAY_EVENTS, "2026-09-30T09:05 | owner | decision | the fixture is decided\n", "")
    sha = commit_tree(record_tree, "drop a line")
    assert check_findings(record_tree) == [
        f"FAIL  {CLOSED_DAY_EVENTS}  commit {sha[:8]} removes a written line: '2026-09-30T09:05 | owner | decision | the fixture is decided'"
    ]


def test_a_journal_line_removed_before_the_recipes_landed_is_history_the_check_leaves_alone(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_DAY_EVENTS, "2026-09-30T09:05 | owner | decision | the fixture is decided\n", "")
    commit_tree(record_tree, "drop a line")
    commit_recipes_at(record_tree, "2026-10-01T12:00:00")
    assert check_findings(record_tree) == []


def test_lines_appended_to_the_journal_after_the_recipes_landed_pass(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-10-01T12:00:00")
    run_memento(record_tree, "events", "add", "self", "decision", "appended")
    commit_tree(record_tree, "append a line")
    assert check_findings(record_tree) == []


def test_a_span_opened_while_open_after_the_recipes_landed_fails_the_alternation(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    append_line(record_tree, OPEN_EVENTS_FILE, "2026-10-01T09:20 | self | span-event | open; again")
    assert check_findings(record_tree) == [f"FAIL  {OPEN_EVENTS_FILE}:3  span-event 'open;' where 'close;' is due; spans open and close in turn"]


def test_a_span_closed_twice_after_the_recipes_landed_fails_the_alternation(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    replace_once(record_tree, CLOSED_DAY_EVENTS, "2026-09-30T09:00 | self | span-event | open; fixture span\n", "")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_DAY_EVENTS}:3  span-event 'close;' where 'open;' is due; spans open and close in turn"]


def test_a_broken_alternation_before_the_recipes_landed_is_history_the_check_leaves_alone(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-10-02T12:00:00")
    append_line(record_tree, OPEN_EVENTS_FILE, "2026-10-01T09:20 | self | span-event | open; again")
    assert check_findings(record_tree) == []


def test_a_suffixed_span_alternates_apart_from_the_main_one(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    (record_tree / f"{OPEN_DAY}/events-side.md").write_text("2026-10-01T09:30 | self | span-event | open; a side span\n", encoding="utf-8")
    assert check_findings(record_tree) == []


def test_a_failure_with_a_clock_behind_the_events_file_is_refused(record_tree: Path) -> None:
    behind = datetime.fromisoformat("2026-10-01T09:09:00+02:00")
    message = "the clock reads 2026-10-01T09:09, before the events file's last line at 2026-10-01T09:10"
    assert_refused_landing_nothing(record_tree, message, "failure", "add", *FAILURE_PARTS, now=behind)


def test_a_close_field_breaking_its_line_other_than_by_newline_is_refused(record_tree: Path) -> None:
    message = "state carries a line break other than a newline"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "first\rsecond", "o", "n", now=FIXED_NOW)


def test_an_events_line_naming_an_unknown_actor_fails_the_events_shape(record_tree: Path) -> None:
    append_line(record_tree, OPEN_EVENTS_FILE, "2026-10-01T09:20 | someone | decision | x")
    message = "not 'YYYY-MM-DDTHH:MM | actor | kind | what': actor 'someone' is not owner, self, agent:<name> or tool:<name>"
    assert check_findings(record_tree) == [f"FAIL  {OPEN_EVENTS_FILE}:3  {message}"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("## Close — 2026-09-30T18:00", "## Close at 2026-09-30T18:00", "close header is not '## Close — YYYY-MM-DDTHH:MM'"),
        ("open    none\n", "", "close fields ['HEAD', 'state', 'next'] are not ['HEAD', 'state', 'open', 'next']"),
        ("open    none\n", "open    none\nnotes   kept\n", "line outside the close fields: 'notes   kept'"),
        ("next    rest\n", "next    \n", "next is empty"),
    ],
)
def test_a_close_block_off_the_close_shape_fails(record_tree: Path, old: str, new: str, message: str) -> None:
    replace_once(record_tree, CLOSED_SESSION, old, new)
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  {message}"]


def test_opening_a_span_on_a_new_date_leaves_nothing_behind_when_its_write_fails(record_tree: Path) -> None:
    close_open_span(record_tree)

    def failing_replace(source: Path, target: Path) -> None:
        """Fail every replacement."""
        raise OSError("injected fault")

    environment = Environment(store=Store(root=record_tree, replace_file=failing_replace), now=lambda: NEXT_MORNING, checks=CHECKS, run_git=git_exit_status)
    result = CliRunner().invoke(app, ["events", "open", "x"], obj=environment)
    assert type(result.exception) is OSError
    assert not (record_tree / f"{HISTORY}/2026-10-02").exists()


def test_closing_under_a_schema_whose_close_labels_differ_is_refused(record_tree: Path) -> None:
    rewrite_schema(record_tree, "digest", "close_labels", ["HEAD", "state", "waits", "next"])
    message = (
        "schema declares digest close_labels ['HEAD', 'state', 'waits', 'next']; "
        "memento knows ['HEAD', 'state', 'open', 'next']; amend the module with the schema"
    )
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n", now=FIXED_NOW)


def test_a_failure_under_a_schema_whose_failure_labels_differ_is_refused(record_tree: Path) -> None:
    rewrite_schema(record_tree, "failure", "entry_labels", ["What happened", "Cause", "Correction"])
    message = (
        "schema declares failure entry_labels ['What happened', 'Cause', 'Correction']; "
        "memento knows ['What happened', 'Mechanism', 'Correction']; amend the module with the schema"
    )
    assert_refused_landing_nothing(record_tree, message, "failure", "add", *FAILURE_PARTS, now=FIXED_NOW)


def test_an_events_line_dated_in_digits_other_than_ascii_fails_the_events_shape(record_tree: Path) -> None:
    append_line(record_tree, OPEN_EVENTS_FILE, "２０２６-10-01T09:20 | self | decision | x")
    assert check_findings(record_tree) == [f"FAIL  {OPEN_EVENTS_FILE}:3  not 'YYYY-MM-DDTHH:MM | actor | kind | what'"]


def test_of_close_blocks_stamped_one_minute_in_one_record_the_baseline_is_the_later_written(record_tree: Path) -> None:
    run_memento(record_tree, "digest", "close", "s", "o", "first")
    run_memento(record_tree, "events", "open", "again")
    run_memento(record_tree, "digest", "close", "s", "o", "second")
    result = run_memento(record_tree, "digest", "baseline")
    assert (result.exit_code, result.stdout.splitlines()[0], result.stdout.splitlines()[-1]) == (0, "## Close — 2026-10-01T10:30", "next    second")


def test_of_close_blocks_stamped_one_minute_the_baseline_is_the_later_spans_though_it_sits_higher_in_its_record(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "## Close — 2026-09-30T18:00", "## Close — 2026-10-01T10:30")
    run_memento(record_tree, "digest", "close", "s", "o", "the later span")
    result = run_memento(record_tree, "digest", "baseline")
    assert result.stdout.endswith("next    the later span\n")


def test_of_one_days_session_records_the_baseline_is_the_later_close_though_it_sits_higher_in_its_record(record_tree: Path) -> None:
    side_close = "## Close — 2026-10-01T10:00\n\nHEAD    abc1234 (clean)\nstate   s\nopen    o\nnext    the side span\n"
    (record_tree / f"{OPEN_DAY}/session-side.md").write_text(f"# 2026-10-01\n\nA.\n\nB.\n\n{side_close}", encoding="utf-8")
    run_memento(record_tree, "digest", "close", "s", "o", "the main span")
    result = run_memento(record_tree, "digest", "baseline")
    assert result.stdout.endswith("next    the main span\n")


def test_a_span_closed_on_a_later_date_closes_in_the_directory_of_the_date_it_opened(record_tree: Path) -> None:
    sha = short_head(record_tree)
    result = run_memento(record_tree, "digest", "close", "s", "o", "n", now=NEXT_MORNING)
    assert result.exit_code == 0
    expected = f"# 2026-10-01\n\n## Close — 2026-10-02T08:00\n\nHEAD    {sha} (clean)\nstate   s\nopen    o\nnext    n\n"
    assert (record_tree / SESSION).read_text(encoding="utf-8") == expected
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith(f"2026-10-02T08:00 | self | span-event | close; HEAD {sha} (clean); next: n\n")
    assert not (record_tree / f"{HISTORY}/2026-10-02").exists()


def test_narrative_written_on_a_later_date_lands_in_the_directory_of_the_date_the_span_opened(record_tree: Path) -> None:
    assert run_memento(record_tree, "digest", "add", "Past midnight.", now=NEXT_MORNING).exit_code == 0
    assert (record_tree / SESSION).read_text(encoding="utf-8") == "# 2026-10-01\n\nPast midnight.\n"
    assert not (record_tree / f"{HISTORY}/2026-10-02").exists()


def test_a_failure_written_on_a_later_date_lands_in_the_directory_of_the_date_the_span_opened(record_tree: Path) -> None:
    assert run_memento(record_tree, "failure", "add", *FAILURE_PARTS, now=NEXT_MORNING).exit_code == 0
    assert (record_tree / FAILURES).read_text(encoding="utf-8") == "# 2026-10-01 — failures\n\n" + FAILURE_ENTRY
    assert (record_tree / OPEN_EVENTS_FILE).read_text(encoding="utf-8").endswith("2026-10-02T08:00 | self | failure | A line written twice\n")
    assert not (record_tree / f"{HISTORY}/2026-10-02").exists()


def test_a_close_block_stamped_in_digits_other_than_ascii_fails_the_close_shape(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_SESSION, "## Close — 2026-09-30T18:00", "## Close — ２０２６-10-01T09:20")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_SESSION}:5  close header is not '## Close — YYYY-MM-DDTHH:MM'"]


def test_closing_while_a_changed_path_holds_the_dirty_path_separator_is_refused(record_tree: Path) -> None:
    (record_tree / "a,  b.md").write_text("untracked\n", encoding="utf-8")
    message = """dirty path "a,  b.md" holds ', ', which separates one dirty path from the next"""
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n", now=FIXED_NOW)


def test_a_schema_close_label_width_narrower_than_the_longest_close_label_fails_the_check(record_tree: Path) -> None:
    rewrite_schema(record_tree, "digest", "close_label_width", 4)
    assert f"FAIL  {SCHEMA_PATH}  schema declares digest close_label_width 4, narrower than the close label 'state'" in check_findings(record_tree)


@pytest.mark.parametrize("width", [0, 1, 2, 3, 4])
def test_closing_under_a_close_label_width_narrower_than_the_longest_close_label_is_refused(record_tree: Path, width: int) -> None:
    rewrite_schema(record_tree, "digest", "close_label_width", width)
    message = f"schema declares digest close_label_width {width}, narrower than the close label 'state'"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n", now=FIXED_NOW)


def test_closing_under_a_close_label_width_as_wide_as_the_longest_close_label_lands(record_tree: Path) -> None:
    rewrite_schema(record_tree, "digest", "close_label_width", 5)
    result = run_memento(record_tree, "digest", "close", "s", "o", "n")
    assert result.exit_code == 0
    assert (record_tree / SESSION).read_text(encoding="utf-8").endswith(f"HEAD {short_head(record_tree)} (dirty: {SCHEMA_PATH})\nstates\nopen o\nnext n\n")


def test_a_decision_whose_what_holds_the_failure_kind_does_not_stand_for_a_failure_line(record_tree: Path) -> None:
    replace_once(record_tree, CLOSED_DAY_EVENTS, "| self | failure | A fixture failure", "| self | decision | x | failure | y")
    assert check_findings(record_tree) == [f"FAIL  {CLOSED_FAILURES}  1 entries but 0 failure lines in {CLOSED_DAY_EVENTS}"]


def test_the_kinds_an_events_line_may_carry_are_the_schemas_event_routes() -> None:
    assert sorted(get_args(LineKind.__value__)) == sorted(KNOWN_EVENT_ROUTES)


def test_closing_where_head_names_no_commit_is_refused(record_tree: Path) -> None:
    git(record_tree, "checkout", "-q", "--orphan", "unborn")
    message = "HEAD names no commit yet; git status has nothing to measure against"
    assert_refused_landing_nothing(record_tree, message, "digest", "close", "s", "o", "n", now=FIXED_NOW)


def test_the_file_the_project_block_names_starts_the_append_only_history(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "history_start_file: memento.just\n", "history_start_file: recipes.just\n")
    commit_tree(record_tree, "name the recipes file")
    (record_tree / "recipes.just").write_text("check:\n", encoding="utf-8")
    git(record_tree, "add", "recipes.just")
    git(record_tree, "commit", "-q", "--date=2026-10-01T12:00:00", "-m", "recipes")
    replace_once(record_tree, CLOSED_DAY_EVENTS, "2026-09-30T09:05 | owner | decision | the fixture is decided\n", "")
    sha = commit_tree(record_tree, "drop a line")
    assert check_findings(record_tree) == [
        f"FAIL  {CLOSED_DAY_EVENTS}  commit {sha[:8]} removes a written line: '2026-09-30T09:05 | owner | decision | the fixture is decided'"
    ]
