"""Parallel spans: a suffixed span opens beside the main one, every journal verb names its span, and a linked worktree writes its own suffixed span."""

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from tests.conftest import FIXED_NOW, HISTORY, SCHEMA_PATH, commit_tree, git, replace_once, run_memento
from tests.test_journal import FAILURE_PARTS, assert_refused_landing_nothing, check_findings, close_open_span, commit_recipes_at, rewrite_schema

DAY = f"{HISTORY}/2026-10-01"
MAIN = "2026-10-01"
SIDE = "2026-10-01-side"
MAIN_EVENTS = f"{DAY}/events.md"
SIDE_EVENTS = f"{DAY}/events-side.md"
SIDE_SESSION = f"{DAY}/session-side.md"
SIDE_FAILURES = f"{DAY}/failures-side.md"
SIDE_OPENED = "2026-10-01T10:30 | self | span-event | open; a side span"
NEXT_MORNING = datetime.fromisoformat("2026-10-02T08:00:12+02:00")
EVENING = datetime.fromisoformat("2026-10-01T19:00:00+02:00")
SEVERAL_OPEN = "several spans are open: 2026-10-01, 2026-10-01-side; name one with --span <date>[-<suffix>]"
SPAN_SHAPE = "is not YYYY-MM-DD or YYYY-MM-DD-<suffix>, the suffix lowercase letters and digits joined by single hyphens"
ROOT_ONLY = "records are written in the root checkout only; this is worktree {worktree}"
JOURNAL_VERBS = {
    "events add": ("events", "add", "self", "decision", "x"),
    "events tail": ("events", "tail"),
    "digest add": ("digest", "add", "a paragraph"),
    "digest close": ("digest", "close", "s", "o", "n"),
    "digest baseline": ("digest", "baseline"),
    "failure add": ("failure", "add", *FAILURE_PARTS),
}


def open_side(root: Path, now: datetime = FIXED_NOW) -> None:
    """Open the suffixed span `side` through the CLI."""
    result = run_memento(root, "events", "open", "--suffix", "side", "a", "side", "span", now=now)
    assert result.exit_code == 0, result.stderr


def with_span(command: tuple[str, ...], span: str) -> tuple[str, ...]:
    """A journal command with its `--span` option set after its group and verb."""
    return (*command[:2], "--span", span, *command[2:])


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file of the tree outside git's directory, by path, with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.parts}


@pytest.fixture
def linked(record_tree: Path, tmp_path: Path) -> Path:
    """A linked worktree of the record tree, detached at its HEAD."""
    worktree = tmp_path / "linked"
    git(record_tree, "worktree", "add", "-q", "--detach", str(worktree))
    return worktree


def test_a_suffixed_span_opens_its_own_events_file_beside_the_open_main_span(record_tree: Path) -> None:
    main_before = (record_tree / MAIN_EVENTS).read_bytes()
    result = run_memento(record_tree, "events", "open", "--suffix", "side", "a", "side", "span")
    assert (result.exit_code, result.stdout) == (0, f"{SIDE_EVENTS}: {SIDE_OPENED}\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8") == SIDE_OPENED + "\n"
    assert (record_tree / MAIN_EVENTS).read_bytes() == main_before


@pytest.mark.parametrize("suffix", ["Side", "a_b", "-a", "a-", "a--b", "", "a b", "é"])
def test_a_suffix_off_the_grammar_is_refused_and_lands_nothing(record_tree: Path, suffix: str) -> None:
    message = f"suffix '{suffix}' is not lowercase letters and digits joined by single hyphens"
    assert_refused_landing_nothing(record_tree, message, "events", "open", "--suffix", suffix, "x", now=FIXED_NOW)


def test_opening_a_suffixed_span_while_that_suffix_is_open_is_refused(record_tree: Path) -> None:
    open_side(record_tree)
    message = f"span {SIDE} is open since 2026-10-01T10:30; resume it or close it"
    assert_refused_landing_nothing(record_tree, message, "events", "open", "--suffix", "side", "again", now=FIXED_NOW)


def test_a_closed_suffixed_span_reopens_on_a_later_date_while_the_main_span_stays_open(record_tree: Path) -> None:
    open_side(record_tree)
    assert run_memento(record_tree, "digest", "close", "--span", SIDE, "s", "o", "n").exit_code == 0
    main_before = (record_tree / MAIN_EVENTS).read_bytes()
    open_side(record_tree, now=NEXT_MORNING)
    reopened = f"{HISTORY}/2026-10-02/events-side.md"
    assert (record_tree / reopened).read_text(encoding="utf-8") == "2026-10-02T08:00 | self | span-event | open; a side span\n"
    assert (record_tree / MAIN_EVENTS).read_bytes() == main_before


def test_the_main_span_opens_while_a_suffixed_span_is_open(record_tree: Path) -> None:
    close_open_span(record_tree)
    open_side(record_tree)
    result = run_memento(record_tree, "events", "open", "main", "again", now=EVENING)
    line = "2026-10-01T19:00 | self | span-event | open; main again"
    assert (result.exit_code, result.stdout) == (0, f"{MAIN_EVENTS}: {line}\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8") == SIDE_OPENED + "\n"


def test_an_omitted_span_resolves_to_the_only_open_span_when_it_is_suffixed(record_tree: Path) -> None:
    close_open_span(record_tree)
    open_side(record_tree)
    result = run_memento(record_tree, "events", "add", "owner", "decision", "side decided")
    line = "2026-10-01T10:30 | owner | decision | side decided"
    assert (result.exit_code, result.stdout) == (0, f"{SIDE_EVENTS}: {line}\n")


@pytest.mark.parametrize("verb", JOURNAL_VERBS.values(), ids=JOURNAL_VERBS.keys())
def test_an_omitted_span_with_several_open_is_refused_naming_every_open_span(record_tree: Path, verb: tuple[str, ...]) -> None:
    open_side(record_tree)
    before = tree_bytes(record_tree)
    result = run_memento(record_tree, *verb)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {SEVERAL_OPEN}\n")
    assert tree_bytes(record_tree) == before


def test_the_baseline_of_a_named_span_is_read_while_several_spans_are_open(record_tree: Path) -> None:
    open_side(record_tree)
    result = run_memento(record_tree, "digest", "baseline", "--span", "2026-09-30")
    assert (result.exit_code, result.stdout.splitlines()[0]) == (0, "## Close — 2026-09-30T18:00")


def test_the_baseline_with_one_span_open_is_still_the_newest_close_block_across_every_session_record(record_tree: Path) -> None:
    result = run_memento(record_tree, "digest", "baseline")
    assert (result.exit_code, result.stdout.splitlines()[0]) == (0, "## Close — 2026-09-30T18:00")
    close_open_span(record_tree)
    assert run_memento(record_tree, "digest", "baseline").stdout == result.stdout


def test_a_span_naming_no_events_file_is_refused(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "span 2026-10-01-nope has no events file", "events", "add", "--span", "2026-10-01-nope", "self", "decision", "x", now=FIXED_NOW)


def test_a_write_to_a_closed_span_is_refused_and_its_tail_is_still_read(record_tree: Path) -> None:
    assert_refused_landing_nothing(record_tree, "span 2026-09-30 is closed", "events", "add", "--span", "2026-09-30", "self", "decision", "x", now=FIXED_NOW)
    result = run_memento(record_tree, "events", "tail", "--span", "2026-09-30")
    assert (result.exit_code, result.stdout) == (0, "2026-09-30T18:00 | self | span-event | close; HEAD abc1234 (clean); next: rest\n")


@pytest.mark.parametrize("span", ["foo", "2026-10-01-Side", "2026-10-01-", "10-01", "2026-10-01 side"])
def test_a_span_off_the_grammar_is_refused(record_tree: Path, span: str) -> None:
    assert_refused_landing_nothing(record_tree, f"span '{span}' {SPAN_SHAPE}", "events", "add", "--span", span, "self", "decision", "x", now=FIXED_NOW)


def test_events_add_lands_in_the_named_span_alone(record_tree: Path) -> None:
    open_side(record_tree)
    main_before = (record_tree / MAIN_EVENTS).read_bytes()
    result = run_memento(record_tree, "events", "add", "--span", SIDE, "owner", "decision", "side decided")
    line = "2026-10-01T10:30 | owner | decision | side decided"
    assert (result.exit_code, result.stdout) == (0, f"{SIDE_EVENTS}: {line}\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8") == SIDE_OPENED + "\n" + line + "\n"
    assert (record_tree / MAIN_EVENTS).read_bytes() == main_before


def test_events_add_names_the_main_span_when_both_are_open(record_tree: Path) -> None:
    open_side(record_tree)
    result = run_memento(record_tree, "events", "add", "--span", MAIN, "owner", "decision", "main decided")
    assert (result.exit_code, result.stdout) == (0, f"{MAIN_EVENTS}: 2026-10-01T10:30 | owner | decision | main decided\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8") == SIDE_OPENED + "\n"


def test_events_tail_prints_the_named_spans_tail(record_tree: Path) -> None:
    open_side(record_tree)
    run_memento(record_tree, "events", "add", "--span", SIDE, "owner", "decision", "side decided")
    result = run_memento(record_tree, "events", "tail", "--span", SIDE)
    assert (result.exit_code, result.stdout) == (0, SIDE_OPENED + "\n2026-10-01T10:30 | owner | decision | side decided\n")


def test_digest_add_lands_in_the_named_spans_session_record(record_tree: Path) -> None:
    open_side(record_tree)
    result = run_memento(record_tree, "digest", "add", "--span", SIDE, "The side span began.")
    assert (result.exit_code, result.stdout) == (0, f"{SIDE_SESSION}: # 2026-10-01\n{SIDE_SESSION}: The side span began.\n")
    assert (record_tree / SIDE_SESSION).read_text(encoding="utf-8") == "# 2026-10-01\n\nThe side span began.\n"
    assert not (record_tree / DAY / "session.md").exists()


def test_digest_close_closes_the_named_span_and_leaves_the_other_open(record_tree: Path) -> None:
    open_side(record_tree)
    assert run_memento(record_tree, "digest", "close", "--span", SIDE, "side stands", "none", "rest").exit_code == 0
    assert (record_tree / SIDE_SESSION).read_text(encoding="utf-8").startswith("# 2026-10-01\n\n## Close — 2026-10-01T10:30\n\nHEAD    ")
    assert (record_tree / SIDE_SESSION).read_text(encoding="utf-8").endswith("state   side stands\nopen    none\nnext    rest\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8").splitlines()[-1].startswith("2026-10-01T10:30 | self | span-event | close; HEAD ")
    assert not (record_tree / DAY / "session.md").exists()
    assert run_memento(record_tree, "events", "add", "--span", MAIN, "self", "decision", "main still open").exit_code == 0


def test_a_failure_on_a_suffixed_span_lands_in_its_own_failures_file_with_its_events_line(record_tree: Path) -> None:
    open_side(record_tree)
    result = run_memento(record_tree, "failure", "add", "--span", SIDE, *FAILURE_PARTS)
    line = "2026-10-01T10:30 | self | failure | A line written twice"
    entry = "## A line written twice\n\nWhat happened: the writer appended twice\n\nMechanism: nothing refused the second write\n\nCorrection: the writer refuses it\n"
    assert result.exit_code == 0
    assert (record_tree / SIDE_FAILURES).read_text(encoding="utf-8") == "# 2026-10-01 — failures\n\n" + entry
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8").endswith(line + "\n")
    assert not (record_tree / DAY / "failures.md").exists()
    assert check_findings(record_tree) == []


def test_the_baseline_of_a_named_span_is_the_newest_close_block_of_its_own_session_record(record_tree: Path) -> None:
    open_side(record_tree)
    run_memento(record_tree, "digest", "close", "--span", SIDE, "side stands", "none", "rest")
    named = run_memento(record_tree, "digest", "baseline", "--span", "2026-09-30")
    assert (named.exit_code, named.stdout.splitlines()[0]) == (0, "## Close — 2026-09-30T18:00")
    side = run_memento(record_tree, "digest", "baseline", "--span", SIDE)
    assert (side.exit_code, side.stdout.splitlines()[-1]) == (0, "next    rest")
    assert run_memento(record_tree, "digest", "baseline", "--span", MAIN).stdout == ""
    refused = run_memento(record_tree, "digest", "baseline", "--span", "2026-10-01-nope")
    assert (refused.exit_code, refused.stderr) == (1, "refused: span 2026-10-01-nope has no events file\n")


def test_a_suffixed_span_opened_while_open_fails_the_alternation_in_its_own_file_alone(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    (record_tree / SIDE_EVENTS).write_text(SIDE_OPENED + "\n2026-10-01T10:31 | self | span-event | open; again\n", encoding="utf-8")
    assert check_findings(record_tree) == [f"FAIL  {SIDE_EVENTS}:2  span-event 'open;' where 'close;' is due; spans open and close in turn"]


def test_a_suffixed_span_open_does_not_disturb_the_main_spans_alternation(record_tree: Path) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    open_side(record_tree)
    assert run_memento(record_tree, "digest", "close", "--span", MAIN, "s", "o", "n").exit_code == 0
    assert check_findings(record_tree) == []


@pytest.mark.parametrize("suffixed", [SIDE_EVENTS, SIDE_FAILURES])
def test_a_line_removed_from_a_suffixed_spans_file_after_the_recipes_landed_fails(record_tree: Path, suffixed: str) -> None:
    commit_recipes_at(record_tree, "2026-09-30T12:00:00")
    open_side(record_tree)
    run_memento(record_tree, "failure", "add", "--span", SIDE, *FAILURE_PARTS)
    commit_tree(record_tree, "write the side span")
    kept = (record_tree / suffixed).read_text(encoding="utf-8").splitlines(keepends=True)[:-1]
    (record_tree / suffixed).write_text("".join(kept), encoding="utf-8")
    sha = commit_tree(record_tree, "drop its last line")
    removals = [finding for finding in check_findings(record_tree) if "removes a written line" in finding]
    assert len(removals) == 1
    assert removals[0].startswith(f"FAIL  {suffixed}  commit {sha[:8]} removes a written line: ")


def test_a_failure_entry_off_the_shape_in_a_suffixed_failures_file_fails(record_tree: Path) -> None:
    open_side(record_tree)
    run_memento(record_tree, "failure", "add", "--span", SIDE, *FAILURE_PARTS)
    replace_once(record_tree, SIDE_FAILURES, "Correction: the writer refuses it", "Correction:")
    assert check_findings(record_tree) == [f"FAIL  {SIDE_FAILURES}:3  Correction is empty"]


def test_a_suffixed_failures_file_is_paired_with_its_own_events_file(record_tree: Path) -> None:
    open_side(record_tree)
    (record_tree / SIDE_FAILURES).write_text("# 2026-10-01 — failures\n\n" + "## A line written twice\n\nWhat happened: w\n\nMechanism: m\n\nCorrection: c\n", encoding="utf-8")
    run_memento(record_tree, "failure", "add", "--span", MAIN, "Main failure", "w", "m", "c")
    assert check_findings(record_tree) == [f"FAIL  {SIDE_FAILURES}  1 entries but 0 failure lines in {SIDE_EVENTS}"]


def test_a_suffixed_spans_close_with_no_close_block_in_its_own_session_record_fails(record_tree: Path) -> None:
    open_side(record_tree)
    assert run_memento(record_tree, "digest", "close", "--span", MAIN, "s", "o", "n").exit_code == 0
    with (record_tree / SIDE_EVENTS).open("a", encoding="utf-8") as events:
        events.write("2026-10-01T10:31 | self | span-event | close; HEAD abc1234 (clean); next: rest\n")
    assert check_findings(record_tree) == [f"FAIL  {SIDE_EVENTS}  1 span closes but 0 close blocks in {SIDE_SESSION}"]


@pytest.mark.parametrize("name", ["events-Side.md", "session-a_b.md", "failures-.md", "events.old.md"])
def test_a_journal_file_whose_name_is_no_span_name_fails(record_tree: Path, name: str) -> None:
    (record_tree / DAY / name).write_text("", encoding="utf-8")
    assert check_findings(record_tree) == [f"FAIL  {DAY}/{name}  not a span file: <name>[-<suffix>].md, the suffix lowercase letters and digits joined by single hyphens"]


def test_a_suffixed_failures_file_is_admitted_to_history(record_tree: Path) -> None:
    open_side(record_tree)
    run_memento(record_tree, "failure", "add", "--span", SIDE, *FAILURE_PARTS)
    commit_tree(record_tree, "write the side span")
    assert check_findings(record_tree) == []


def test_a_schema_whose_failure_home_has_no_suffix_slot_is_refused_by_the_verbs(record_tree: Path) -> None:
    rewrite_schema(record_tree, "failure", "home", f"{HISTORY}/{{date}}/failures.md")
    message = (
        f"schema declares failure home {HISTORY}/{{date}}/failures.md; memento knows homes ending '{{date}}/<name>*.md', "
        "the '*' standing for a span's suffix; amend the module with the schema"
    )
    assert_refused_landing_nothing(record_tree, message, "failure", "add", *FAILURE_PARTS, now=FIXED_NOW)


def write_session(root: Path, day: str, name: str, *blocks: tuple[str, str]) -> str:
    """A session record holding close blocks, each given as its minute and its next step; returns its path."""
    path = f"{HISTORY}/{day}/{name}"
    texts = [f"## Close — {minute}\n\nHEAD    abc1234 (clean)\nstate   s\nopen    none\nnext    {step}\n" for minute, step in blocks]
    (root / path).write_text(f"# {day}\n\n" + "\nnarrative\n\n".join(texts), encoding="utf-8")
    return path


def baseline_next(root: Path) -> str:
    """The next step of the close block `digest baseline` prints."""
    return run_memento(root, "digest", "baseline").stdout.splitlines()[-1]


def test_of_close_blocks_the_baseline_is_the_later_span_date_before_the_later_minute(record_tree: Path) -> None:
    write_session(record_tree, "2026-10-01", "session.md", ("2026-10-01T09:00", "later day"))
    assert baseline_next(record_tree) == "next    later day"


def test_of_close_blocks_on_one_date_the_baseline_is_the_later_minute_before_the_file_name(record_tree: Path) -> None:
    write_session(record_tree, "2026-09-30", "session-b.md", ("2026-09-30T18:01", "later minute"))
    assert baseline_next(record_tree) == "next    later minute"


def test_of_close_blocks_stamped_one_minute_the_baseline_is_the_later_file_name_before_the_later_line(record_tree: Path) -> None:
    write_session(record_tree, "2026-10-01", "session-b.md", ("2026-10-01T09:00", "b first"), ("2026-10-01T09:00", "b second, lower down"))
    write_session(record_tree, "2026-10-01", "session-c.md", ("2026-10-01T09:00", "c"))
    assert baseline_next(record_tree) == "next    c"


def test_of_close_blocks_stamped_one_minute_a_suffixed_record_sorts_below_the_unsuffixed_one(record_tree: Path) -> None:
    write_session(record_tree, "2026-09-30", "session-b.md", ("2026-09-30T18:00", "b first"), ("2026-09-30T18:00", "b second, lower down"))
    assert baseline_next(record_tree) == "next    rest"


def test_of_close_blocks_stamped_one_minute_in_one_file_the_baseline_is_the_one_lower_down(record_tree: Path) -> None:
    write_session(record_tree, "2026-09-30", "session.md", ("2026-09-30T18:00", "first"), ("2026-09-30T18:00", "second"))
    assert baseline_next(record_tree) == "next    second"


def test_patdown_reports_every_open_span_with_its_tail(record_tree: Path) -> None:
    open_side(record_tree)
    sections = run_memento(record_tree, "patdown").stdout.split("\n\n")
    assert [section for section in sections if section.startswith("tail")] == [
        f"tail {MAIN}\n2026-10-01T09:00 | self | span-event | open; the next span\n2026-10-01T09:10 | agent:J-b | receipt | verdict delivered",
        f"tail {SIDE}\n{SIDE_OPENED}",
    ]
    found = next(section for section in sections if section.startswith("found"))
    assert [line for line in found.splitlines() if line.startswith("span")] == [
        f"span    {MAIN} open since 2026-10-01T09:00",
        f"span    {SIDE} open since 2026-10-01T10:30",
    ]


def test_patdown_with_every_span_closed_reports_the_newest_span_closed(record_tree: Path) -> None:
    close_open_span(record_tree)
    stdout = run_memento(record_tree, "patdown").stdout
    assert f"tail {MAIN}\n2026-10-01T18:00 | self | span-event | close;" in stdout
    assert "span    closed\n" in stdout


def test_a_suffixed_span_is_written_from_a_linked_worktree_in_its_own_tree(record_tree: Path, linked: Path) -> None:
    root_before = tree_bytes(record_tree)
    steps = [
        ("events", "open", "--suffix", "side", "a", "side", "span"),
        ("events", "add", "--span", SIDE, "self", "decision", "x"),
        ("digest", "add", "--span", SIDE, "narrative"),
        ("failure", "add", "--span", SIDE, *FAILURE_PARTS),
        ("digest", "close", "--span", SIDE, "s", "o", "n"),
    ]
    for step in steps:
        result = run_memento(linked, *step)
        assert result.exit_code == 0, result.stderr
    assert git(linked, "status", "--porcelain", "--untracked-files=all").splitlines() == [f"?? {SIDE_EVENTS}", f"?? {SIDE_FAILURES}", f"?? {SIDE_SESSION}"]
    assert tree_bytes(record_tree) == root_before
    assert run_memento(linked, "check").stdout == "records: 0 FAIL, 0 WARN\n"


@pytest.mark.parametrize(
    "command",
    [
        ("events", "open", "x"),
        ("events", "add", "--span", MAIN, "self", "decision", "x"),
        ("events", "add", "self", "decision", "x"),
        ("digest", "add", "--span", MAIN, "p"),
        ("digest", "close", "--span", MAIN, "s", "o", "n"),
        ("failure", "add", "--span", MAIN, *FAILURE_PARTS),
    ],
)
def test_the_main_span_still_refuses_in_a_linked_worktree(linked: Path, command: tuple[str, ...]) -> None:
    before = tree_bytes(linked)
    result = run_memento(linked, *command)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {ROOT_ONLY.format(worktree=linked)}\n")
    assert tree_bytes(linked) == before


def test_a_linked_worktree_with_its_suffixed_span_and_the_main_one_open_needs_the_span_named(linked: Path) -> None:
    open_side_result = run_memento(linked, "events", "open", "--suffix", "side", "a", "side", "span")
    assert open_side_result.exit_code == 0
    result = run_memento(linked, "events", "add", "self", "decision", "x")
    assert (result.exit_code, result.stderr) == (1, f"refused: {SEVERAL_OPEN}\n")


def test_a_root_checkout_still_writes_the_main_span(record_tree: Path) -> None:
    open_side(record_tree)
    assert run_memento(record_tree, "events", "add", "--span", MAIN, "self", "decision", "x").exit_code == 0


UNTRACKED_FILE = "docs/orchestration_log/archive/follow-ups.md"
UNTRACKED_DIRECTORY = "delivered_dashboards"
TRACKED_SPEC = "docs/specs/notes.txt"


@pytest.fixture
def tree_citing_untracked_paths(record_tree: Path) -> Path:
    """The record tree, its schema retiring a file and its conventions grounding a rule on a directory, both untracked, and on a tracked spec."""
    schema = yaml.safe_load((record_tree / SCHEMA_PATH).read_text(encoding="utf-8"))
    schema["retired"] = [{"path": UNTRACKED_FILE, "superseded_by": "docs/capabilities.md"}]
    (record_tree / SCHEMA_PATH).write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")
    replace_once(record_tree, "docs/conventions.md", "ground  owner ruling 2026-08-04", f"ground  owner ruling 2026-08-04, {UNTRACKED_DIRECTORY}/, {TRACKED_SPEC}")
    (record_tree / TRACKED_SPEC).parent.mkdir(parents=True, exist_ok=True)
    (record_tree / TRACKED_SPEC).write_text("# notes\n", encoding="utf-8")
    commit_tree(record_tree, "cite paths")
    (record_tree / UNTRACKED_FILE).write_text("retired\n", encoding="utf-8")
    (record_tree / UNTRACKED_DIRECTORY).mkdir()
    (record_tree / UNTRACKED_DIRECTORY / "page.html").write_text("page\n", encoding="utf-8")
    return record_tree


def test_the_root_checkouts_check_passes_over_paths_git_does_not_track(tree_citing_untracked_paths: Path) -> None:
    assert check_findings(tree_citing_untracked_paths) == []


def test_a_linked_worktrees_check_resolves_a_path_git_does_not_track_against_the_root_checkout(tree_citing_untracked_paths: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(tree_citing_untracked_paths, "worktree", "add", "-q", "--detach", str(worktree))
    assert not (worktree / UNTRACKED_FILE).exists()
    assert not (worktree / UNTRACKED_DIRECTORY).exists()
    assert check_findings(worktree) == []


def test_a_linked_worktrees_check_never_resolves_a_tracked_path_against_the_root_checkout(tree_citing_untracked_paths: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(tree_citing_untracked_paths, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / TRACKED_SPEC).unlink()
    findings = check_findings(worktree)
    assert findings == [
        f"FAIL  docs/conventions.md:8  ground names '{TRACKED_SPEC}', which is not a committed path",
        f"FAIL  docs/conventions.md:8  points at '{TRACKED_SPEC}', which does not exist",
    ]
    assert check_findings(tree_citing_untracked_paths) == []


SPAN_VARIABLE = "MEMENTO_SPAN"
MAIN_SPAN_FILES = (MAIN_EVENTS, f"{DAY}/session.md", f"{DAY}/failures.md")


def span_files_bytes(root: Path, files: tuple[str, ...]) -> dict[str, bytes | None]:
    """The bytes of each named file, None for one that does not exist."""
    return {name: (root / name).read_bytes() if (root / name).exists() else None for name in files}


@pytest.mark.parametrize("verb", JOURNAL_VERBS.values(), ids=JOURNAL_VERBS.keys())
def test_every_journal_verb_takes_its_span_from_the_environment_while_several_are_open(
    record_tree: Path, monkeypatch: pytest.MonkeyPatch, verb: tuple[str, ...]
) -> None:
    open_side(record_tree)
    main_before = span_files_bytes(record_tree, MAIN_SPAN_FILES)
    monkeypatch.setenv(SPAN_VARIABLE, SIDE)
    result = run_memento(record_tree, *verb)
    assert (result.exit_code, result.stderr) == (0, "")
    assert span_files_bytes(record_tree, MAIN_SPAN_FILES) == main_before


def test_events_add_lands_in_the_span_the_environment_names(record_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_side(record_tree)
    monkeypatch.setenv(SPAN_VARIABLE, SIDE)
    result = run_memento(record_tree, "events", "add", "owner", "decision", "side decided")
    assert (result.exit_code, result.stdout) == (0, f"{SIDE_EVENTS}: 2026-10-01T10:30 | owner | decision | side decided\n")
    assert (record_tree / SIDE_EVENTS).read_text(encoding="utf-8").endswith("side decided\n")


def test_an_explicit_span_wins_over_the_environment(record_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_side(record_tree)
    side_before = (record_tree / SIDE_EVENTS).read_bytes()
    monkeypatch.setenv(SPAN_VARIABLE, SIDE)
    result = run_memento(record_tree, "events", "add", "--span", MAIN, "owner", "decision", "main decided")
    assert (result.exit_code, result.stdout) == (0, f"{MAIN_EVENTS}: 2026-10-01T10:30 | owner | decision | main decided\n")
    assert (record_tree / SIDE_EVENTS).read_bytes() == side_before


@pytest.mark.parametrize(
    ("named", "message"),
    [
        ("2026-09-30", "span 2026-09-30 is closed"),
        ("2026-10-01-nope", "span 2026-10-01-nope has no events file"),
        ("foo", f"span 'foo' {SPAN_SHAPE}"),
    ],
)
def test_a_span_the_environment_names_that_is_not_open_is_refused_as_the_option_would(
    record_tree: Path, monkeypatch: pytest.MonkeyPatch, named: str, message: str
) -> None:
    before = tree_bytes(record_tree)
    monkeypatch.setenv(SPAN_VARIABLE, named)
    result = run_memento(record_tree, "events", "add", "self", "decision", "x")
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert tree_bytes(record_tree) == before


@pytest.mark.parametrize("verb", JOURNAL_VERBS.values(), ids=JOURNAL_VERBS.keys())
def test_every_journal_verbs_help_names_the_span_variable(record_tree: Path, verb: tuple[str, ...]) -> None:
    result = run_memento(record_tree, *verb[:2], "--help")
    assert (result.exit_code, SPAN_VARIABLE in result.stdout) == (0, True)
