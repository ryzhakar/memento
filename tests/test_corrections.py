"""A project lists owner-ruled commits in its schema: the history check skips them, and the check holds each to its commit and its ruling."""

from pathlib import Path

import pytest
import yaml

from memento.journal_checks import holds_owner_decision_at
from tests.conftest import HISTORY, SCHEMA_PATH, commit_tree, git, replace_once, run_memento
from tests.test_journal import append_line, check_findings, commit_recipes_at

OPEN_EVENTS = f"{HISTORY}/2026-10-01/events.md"
SESSION = f"{HISTORY}/2026-09-30/session.md"
ABSENT_EVENTS = f"{HISTORY}/2026-10-01/events-absent.md"
SELF_DECISION_MINUTE = "2026-10-01T09:20"
OWNER_DISCOVERY_MINUTE = "2026-10-01T09:21"
OWNER_DECISION_MINUTE = "2026-10-01T09:22"
NO_LINE_MINUTE = "2026-10-01T09:23"
FIRST_CORRECTED_MINUTE = "2026-10-01T09:30"
SECOND_CORRECTED_MINUTE = "2026-10-01T09:31"
OWNER_RULING = f"{OPEN_EVENTS} {OWNER_DECISION_MINUTE}"
NOWHERE = "0123456789abcdef0123456789abcdef01234567"
WRITTEN_LINES = [
    f"{SELF_DECISION_MINUTE} | self | decision | a session decision",
    f"{OWNER_DISCOVERY_MINUTE} | owner | discovery | an owner discovery",
    f"{OWNER_DECISION_MINUTE} | owner | decision | the owner's ruling",
    f"{FIRST_CORRECTED_MINUTE} | self | commitment | the first one",
    f"{SECOND_CORRECTED_MINUTE} | self | commitment | the second one",
]
CORRECTED_WHATS = {
    FIRST_CORRECTED_MINUTE: "the first one",
    SECOND_CORRECTED_MINUTE: "the second one",
}
BLOCK_DOES_NOT_FIT = f"refused: {SCHEMA_PATH} 'project:' block does not fit: "


@pytest.fixture
def corrigible_tree(record_tree: Path) -> Path:
    """A record tree whose append-only history began before its open span's events gained an owner ruling and two lines to correct."""
    commit_recipes_at(record_tree, "2026-10-01T12:00:00")
    for line in WRITTEN_LINES:
        append_line(record_tree, OPEN_EVENTS, line)
    commit_tree(record_tree, "write the lines")
    return record_tree


def correct_kind_of_line_at(root: Path, stamp: str) -> str:
    """Commit the edit of the commitment line stamped at the minute into a delegation, returning the commit's sha."""
    replace_once(root, OPEN_EVENTS, f"{stamp} | self | commitment |", f"{stamp} | self | delegation |")
    return commit_tree(root, f"correct {stamp}")


def list_corrections(root: Path, *corrections: dict[str, str]) -> None:
    """Write the schema's `project.journal_corrections` as the items given, leaving the write uncommitted."""
    schema = yaml.safe_load((root / SCHEMA_PATH).read_text(encoding="utf-8"))
    schema["project"]["journal_corrections"] = list(corrections)
    (root / SCHEMA_PATH).write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")


def removal_finding(sha: str, stamp: str) -> str:
    """The finding the history check prints for a commit that edited the commitment line at the minute away."""
    return f"FAIL  {OPEN_EVENTS}  commit {sha[:8]} removes a written line: '{stamp} | self | commitment | {CORRECTED_WHATS[stamp]}'"


def test_a_commit_editing_a_written_line_in_place_fails_when_no_correction_lists_it(corrigible_tree: Path) -> None:
    sha = correct_kind_of_line_at(corrigible_tree, FIRST_CORRECTED_MINUTE)
    assert check_findings(corrigible_tree) == [removal_finding(sha, FIRST_CORRECTED_MINUTE)]


def test_a_commit_a_correction_lists_passes_the_history_check(corrigible_tree: Path) -> None:
    sha = correct_kind_of_line_at(corrigible_tree, FIRST_CORRECTED_MINUTE)
    list_corrections(corrigible_tree, {"commit": sha, "ruling": OWNER_RULING})
    assert check_findings(corrigible_tree) == []


@pytest.mark.parametrize("listed", [FIRST_CORRECTED_MINUTE, SECOND_CORRECTED_MINUTE])
def test_listing_one_commit_leaves_the_other_commit_checked(corrigible_tree: Path, listed: str) -> None:
    shas = {stamp: correct_kind_of_line_at(corrigible_tree, stamp) for stamp in CORRECTED_WHATS}
    list_corrections(corrigible_tree, {"commit": shas[listed], "ruling": OWNER_RULING})
    (other,) = CORRECTED_WHATS.keys() - {listed}
    assert check_findings(corrigible_tree) == [removal_finding(shas[other], other)]


def test_a_correction_lists_a_commit_by_its_full_sha_alone(corrigible_tree: Path) -> None:
    sha = correct_kind_of_line_at(corrigible_tree, FIRST_CORRECTED_MINUTE)
    list_corrections(corrigible_tree, {"commit": sha[:8], "ruling": OWNER_RULING})
    result = run_memento(corrigible_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith(f"{BLOCK_DOES_NOT_FIT}journal_corrections.0.commit:")


@pytest.mark.parametrize(
    ("item", "field"),
    [
        ({"commit": NOWHERE}, "journal_corrections.0.ruling"),
        ({"ruling": OWNER_RULING}, "journal_corrections.0.commit"),
        ({"commit": NOWHERE, "ruling": OWNER_RULING, "why": "because"}, "journal_corrections.0.why"),
        ({"commit": NOWHERE, "ruling": OPEN_EVENTS}, "journal_corrections.0.ruling"),
        ({"commit": NOWHERE, "ruling": f"{OPEN_EVENTS} 2026-10-01"}, "journal_corrections.0.ruling"),
        ({"commit": NOWHERE, "ruling": f"{OPEN_EVENTS} 2026-10-01T٠٩:٢٢"}, "journal_corrections.0.ruling"),
    ],
)
def test_a_correction_off_its_two_field_shape_is_refused_naming_the_field(corrigible_tree: Path, item: dict[str, str], field: str) -> None:
    list_corrections(corrigible_tree, item)
    result = run_memento(corrigible_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith(f"{BLOCK_DOES_NOT_FIT}{field}:")


def test_a_commit_on_another_branch_is_no_commit_in_heads_history(corrigible_tree: Path) -> None:
    main = git(corrigible_tree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(corrigible_tree, "checkout", "-q", "-b", "elsewhere")
    elsewhere = correct_kind_of_line_at(corrigible_tree, FIRST_CORRECTED_MINUTE)
    git(corrigible_tree, "checkout", "-q", main)
    assert git(corrigible_tree, "cat-file", "-t", elsewhere).strip() == "commit"
    list_corrections(corrigible_tree, {"commit": elsewhere, "ruling": OWNER_RULING})
    assert check_findings(corrigible_tree) == [f"FAIL  {SCHEMA_PATH}  journal correction commit {elsewhere} is not in HEAD's history"]


def test_a_commit_git_holds_nowhere_is_no_commit_in_heads_history(corrigible_tree: Path) -> None:
    list_corrections(corrigible_tree, {"commit": NOWHERE, "ruling": OWNER_RULING})
    assert check_findings(corrigible_tree) == [f"FAIL  {SCHEMA_PATH}  journal correction commit {NOWHERE} is not in HEAD's history"]


@pytest.mark.parametrize("revision", ["HEAD", "HEAD~1"])
def test_heads_own_commit_and_an_earlier_one_are_in_heads_history(corrigible_tree: Path, revision: str) -> None:
    sha = git(corrigible_tree, "rev-parse", revision).strip()
    list_corrections(corrigible_tree, {"commit": sha, "ruling": OWNER_RULING})
    assert check_findings(corrigible_tree) == []


@pytest.mark.parametrize(
    ("ruling", "problem"),
    [
        (f"{OPEN_EVENTS} {SELF_DECISION_MINUTE}", f"ruling {OPEN_EVENTS} holds no 'owner | decision' line at {SELF_DECISION_MINUTE}"),
        (f"{OPEN_EVENTS} {OWNER_DISCOVERY_MINUTE}", f"ruling {OPEN_EVENTS} holds no 'owner | decision' line at {OWNER_DISCOVERY_MINUTE}"),
        (f"{OPEN_EVENTS} {NO_LINE_MINUTE}", f"ruling {OPEN_EVENTS} holds no 'owner | decision' line at {NO_LINE_MINUTE}"),
        (f"{SESSION} {OWNER_DECISION_MINUTE}", f"ruling {SESSION} is not an events file"),
    ],
)
def test_a_ruling_that_resolves_to_no_owner_decision_line_fails_naming_why(corrigible_tree: Path, ruling: str, problem: str) -> None:
    sha = git(corrigible_tree, "rev-parse", "HEAD").strip()
    list_corrections(corrigible_tree, {"commit": sha, "ruling": ruling})
    assert check_findings(corrigible_tree) == [f"FAIL  {SCHEMA_PATH}  journal correction {sha}: {problem}"]


def test_a_ruling_in_an_earlier_spans_events_file_resolves(corrigible_tree: Path) -> None:
    sha = git(corrigible_tree, "rev-parse", "HEAD").strip()
    list_corrections(corrigible_tree, {"commit": sha, "ruling": f"{HISTORY}/2026-09-30/events.md 2026-09-30T09:05"})
    assert check_findings(corrigible_tree) == []


def test_a_ruling_in_a_file_that_does_not_exist_fails_beside_the_pointer_check(corrigible_tree: Path) -> None:
    sha = git(corrigible_tree, "rev-parse", "HEAD").strip()
    list_corrections(corrigible_tree, {"commit": sha, "ruling": f"{ABSENT_EVENTS} {OWNER_DECISION_MINUTE}"})
    findings = check_findings(corrigible_tree)
    assert findings[0] == f"FAIL  {SCHEMA_PATH}  journal correction {sha}: ruling {ABSENT_EVENTS} does not exist"
    assert [finding.endswith(f"points at '{ABSENT_EVENTS}', which does not exist") for finding in findings[1:]] == [True]


@pytest.mark.parametrize("actor", ["owner", "owner:Test Owner"])
def test_a_ruling_resolves_for_the_owner_bare_or_stamped_with_an_identity(actor: str) -> None:
    text = f"{OWNER_DECISION_MINUTE} | {actor} | decision | the owner's ruling"
    assert holds_owner_decision_at(text, OWNER_DECISION_MINUTE)


def test_a_ruling_does_not_resolve_for_another_actor() -> None:
    text = f"{OWNER_DECISION_MINUTE} | self:claude-sonnet-5-5 | decision | not the owner"
    assert not holds_owner_decision_at(text, OWNER_DECISION_MINUTE)
