"""A change lands only if its candidate corpus adds no FAIL the current corpus lacks, identified by path and message."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from memento import Environment, Refusal
from memento.__main__ import CHECKS
from memento.candidate import refuse_new_failures
from memento.findings import Check, Corpus, Finding, warn
from memento.schema import load_corpus
from memento.git import git_exit_status
from memento.store import Store
from tests.conftest import FIXED_NOW, HISTORY, replace_once, tree_bytes

CAPABILITIES = "docs/capabilities.md"
CLEAN_CAPABILITIES = "# Capabilities\n\n## Scoring\n\nThe scorer slices its input.\n"
MISSING_POINTER = "The scorer slices its input, as docs/missing.md says.\n"


def environment_for(root: Path, checks: tuple[Check, ...] = CHECKS) -> Environment:
    """The environment a verb receives for a tree, carrying the given checks."""
    return Environment(store=Store(root=root, replace_file=os.replace), now=lambda: FIXED_NOW, checks=checks, run_git=git_exit_status)


def test_a_change_adding_a_failure_is_refused_naming_it(record_tree: Path) -> None:
    before = tree_bytes(record_tree)
    changes = {CAPABILITIES: CLEAN_CAPABILITIES.replace("The scorer slices its input.\n", MISSING_POINTER)}
    with pytest.raises(Refusal) as refusal:
        refuse_new_failures(environment_for(record_tree), changes)
    assert str(refusal.value) == (
        "the change adds 1 FAIL: docs/capabilities.md  points at 'docs/missing.md', which does not exist"
    )
    assert tree_bytes(record_tree) == before


def test_a_failure_already_present_does_not_block_a_change_that_moves_its_line(record_tree: Path) -> None:
    replace_once(record_tree, CAPABILITIES, "The scorer slices its input.\n", MISSING_POINTER)
    moved = CLEAN_CAPABILITIES.replace("## Scoring\n\n", "## Scoring\n\nA new opening line.\n\n").replace(
        "The scorer slices its input.\n", MISSING_POINTER
    )
    refuse_new_failures(environment_for(record_tree), {CAPABILITIES: moved})


def test_a_change_repairing_a_failure_lands(record_tree: Path) -> None:
    replace_once(record_tree, CAPABILITIES, "The scorer slices its input.\n", MISSING_POINTER)
    refuse_new_failures(environment_for(record_tree), {CAPABILITIES: CLEAN_CAPABILITIES})


def test_a_deletion_of_a_home_the_map_names_is_refused(record_tree: Path) -> None:
    with pytest.raises(Refusal) as refusal:
        refuse_new_failures(environment_for(record_tree), {"docs/label-data-defects.md": None})
    assert str(refusal.value) == (
        "the change adds 3 FAIL: .claude/memento-map.md  'docs/label-data-defects.md' does not exist; "
        ".claude/memento-map.md  points at 'docs/label-data-defects.md', which does not exist; "
        ".claude/memento.yaml  points at 'docs/label-data-defects.md', which does not exist"
    )


def test_a_new_events_file_off_the_events_shape_is_refused(record_tree: Path) -> None:
    changes = {f"{HISTORY}/2026-10-02/events.md": "2026-10-02T09:00 | someone | decision | x\n"}
    with pytest.raises(Refusal) as refusal:
        refuse_new_failures(environment_for(record_tree), changes)
    assert str(refusal.value) == (
        f"the change adds 1 FAIL: {HISTORY}/2026-10-02/events.md  not 'YYYY-MM-DDTHH:MM | actor | kind | what': "
        "actor 'someone' is not owner[:<identity>], self[:<model>], subagent:<name>, peer:<name>, agent:<name> or tool:<name>"
    )


def test_two_new_failures_are_both_named_in_check_order(record_tree: Path) -> None:
    changes = {
        CAPABILITIES: CLEAN_CAPABILITIES.replace("The scorer slices its input.\n", MISSING_POINTER),
        "docs/results.md": (record_tree / "docs/results.md").read_text(encoding="utf-8").replace("2026-10-01", "October"),
    }
    with pytest.raises(Refusal) as refusal:
        refuse_new_failures(environment_for(record_tree), changes)
    assert str(refusal.value) == (
        "the change adds 2 FAIL: docs/results.md  date cell 'October' is not YYYY-MM-DD; "
        "docs/capabilities.md  points at 'docs/missing.md', which does not exist"
    )


def test_the_candidate_runs_the_checks_its_environment_carries(record_tree: Path) -> None:
    changes = {CAPABILITIES: CLEAN_CAPABILITIES.replace("The scorer slices its input.\n", MISSING_POINTER)}
    refuse_new_failures(environment_for(record_tree, checks=()), changes)


def warn_on_missing_pointer(corpus: Corpus) -> Iterator[Finding]:
    """A check that only warns, for each capabilities line naming docs/missing.md."""
    for number, line in enumerate(corpus.text(CAPABILITIES).splitlines(), 1):
        if "docs/missing.md" in line:
            yield warn(f"{CAPABILITIES}:{number}", "names docs/missing.md")


def test_a_new_warning_does_not_block_a_change(record_tree: Path) -> None:
    changes = {CAPABILITIES: CLEAN_CAPABILITIES.replace("The scorer slices its input.\n", MISSING_POINTER)}
    refuse_new_failures(environment_for(record_tree, checks=(warn_on_missing_pointer,)), changes)


def test_one_new_failure_on_two_lines_is_named_once(record_tree: Path) -> None:
    changes = {CAPABILITIES: CLEAN_CAPABILITIES.replace("The scorer slices its input.\n", MISSING_POINTER + "\n" + MISSING_POINTER)}
    with pytest.raises(Refusal) as refusal:
        refuse_new_failures(environment_for(record_tree), changes)
    assert str(refusal.value) == (
        "the change adds 1 FAIL: docs/capabilities.md  points at 'docs/missing.md', which does not exist"
    )


def test_a_substituted_corpus_reads_changed_added_and_deleted_paths(record_tree: Path) -> None:
    added, deleted = f"{HISTORY}/2026-10-02/events.md", f"{HISTORY}/2026-09-30/events.md"
    unmatched = {f"{HISTORY}/2026-10-02/notes.md": "x\n", f"{HISTORY}/2026-10-02/events-x.md/inner.md": "x\n"}
    corpus = load_corpus(record_tree).substituted({CAPABILITIES: "changed\n", added: "added\n", deleted: None} | unmatched)
    assert (corpus.text(CAPABILITIES), corpus.raw(added)) == ("changed\n", b"added\n")
    assert (corpus.exists(added), corpus.is_file(added), corpus.exists(deleted), corpus.is_file(deleted)) == (True, True, False, False)
    assert corpus.glob(f"{HISTORY}/*/events*.md") == [f"{HISTORY}/2026-10-01/events.md", added]
    assert (added in corpus.tracked, deleted in corpus.tracked, CAPABILITIES in corpus.tracked) == (True, False, True)
    assert corpus.tracked == sorted(corpus.tracked)
    with pytest.raises(FileNotFoundError):
        corpus.text(deleted)
    assert load_corpus(record_tree).text(CAPABILITIES) == CLEAN_CAPABILITIES
