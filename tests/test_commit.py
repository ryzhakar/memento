"""`memento commit` commits named record paths after a green check, or refuses and commits nothing."""

from pathlib import Path

import pytest

from tests.conftest import HISTORY, git, replace_once, run_memento

OPEN_EVENTS_FILE = f"{HISTORY}/2026-10-01/events.md"
CAPABILITIES = "docs/capabilities.md"


@pytest.fixture
def committing_tree(record_tree: Path) -> Path:
    """A record tree whose repository carries a committer identity and an added decision in its open span."""
    git(record_tree, "config", "user.name", "fixture")
    git(record_tree, "config", "user.email", "fixture@example.com")
    run_memento(record_tree, "events", "add", "self", "decision", "commit the records")
    return record_tree


def head_state(root: Path) -> tuple[str, str]:
    """HEAD's sha and git's short status, which a refused commit leaves as they were."""
    return git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain")


def test_the_named_record_paths_land_in_one_docs_commit(committing_tree: Path) -> None:
    replace_once(committing_tree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    result = run_memento(committing_tree, "commit", "record the decision", OPEN_EVENTS_FILE)
    sha = git(committing_tree, "rev-parse", "--short", "HEAD").strip()
    assert (result.exit_code, result.stdout) == (0, f"{sha} docs: record the decision\n{OPEN_EVENTS_FILE}\n")
    assert git(committing_tree, "log", "-1", "--format=%B") == "docs: record the decision\n\n"
    assert git(committing_tree, "show", "--name-only", "--format=", "HEAD") == f"{OPEN_EVENTS_FILE}\n"
    assert git(committing_tree, "status", "--porcelain") == f" M {CAPABILITIES}\n"


def assert_commit_refused(root: Path, message: str, *args: str) -> None:
    """Assert `commit` refuses with this message on stderr alone and leaves HEAD and the working tree's status as they were."""
    before = head_state(root)
    result = run_memento(root, "commit", *args)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert head_state(root) == before


@pytest.mark.parametrize("path", ["notes.txt", "docs/orchestration_log/recon/2026-10-01/draft.md", "README.md"])
def test_a_path_that_is_no_record_is_refused(committing_tree: Path, path: str) -> None:
    (committing_tree / path).parent.mkdir(parents=True, exist_ok=True)
    (committing_tree / path).write_text("x\n", encoding="utf-8")
    assert_commit_refused(committing_tree, f"{path} is not a record; commit it with its code", "subject", OPEN_EVENTS_FILE, path)


def test_a_record_of_a_kind_no_verb_writes_is_refused(committing_tree: Path) -> None:
    replace_once(committing_tree, "docs/label-data-defects.md", "# label_defects\n", "# label_defects, edited\n")
    message = "docs/label-data-defects.md is a label_defects record, which no memento verb writes"
    assert_commit_refused(committing_tree, message, "subject", "docs/label-data-defects.md")


def test_a_path_with_no_change_is_refused(committing_tree: Path) -> None:
    assert_commit_refused(committing_tree, f"{CAPABILITIES} has no change to commit", "subject", OPEN_EVENTS_FILE, CAPABILITIES)


def test_a_subject_over_nine_words_is_refused(committing_tree: Path) -> None:
    subject = "one two three four five six seven eight nine ten"
    assert_commit_refused(committing_tree, "subject runs 10 words, over 9", subject, OPEN_EVENTS_FILE)


def test_a_subject_of_two_lines_is_refused(committing_tree: Path) -> None:
    assert_commit_refused(committing_tree, "subject is one line; it carries a line break", "one\ntwo", OPEN_EVENTS_FILE)


def test_records_holding_a_failing_finding_are_refused(committing_tree: Path) -> None:
    replace_once(committing_tree, "docs/results.md", "2026-10-01", "October")
    assert_commit_refused(committing_tree, "the records hold 1 FAIL; 'memento check' lists them", "subject", OPEN_EVENTS_FILE)


def test_a_commit_from_a_linked_worktree_is_refused(committing_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(committing_tree, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / CAPABILITIES).write_text("# Capabilities\n\nChanged.\n", encoding="utf-8")
    message = f"records are written in the root checkout only; this is worktree {worktree}"
    assert_commit_refused(worktree, message, "subject", CAPABILITIES)


def test_a_path_staged_before_the_commit_stays_out_of_it(committing_tree: Path) -> None:
    replace_once(committing_tree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    git(committing_tree, "add", CAPABILITIES)
    result = run_memento(committing_tree, "commit", "record the decision", OPEN_EVENTS_FILE)
    assert result.exit_code == 0
    assert git(committing_tree, "show", "--name-only", "--format=", "HEAD") == f"{OPEN_EVENTS_FILE}\n"
    assert git(committing_tree, "status", "--porcelain") == f"M  {CAPABILITIES}\n"


def test_a_commit_git_rejects_is_refused_and_leaves_nothing_staged(committing_tree: Path) -> None:
    hook = committing_tree / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho 'the hook rejects this commit' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    message = "git did not commit, so the records are unstaged: the hook rejects this commit"
    assert_commit_refused(committing_tree, message, "subject", OPEN_EVENTS_FILE)


def test_the_commit_type_and_the_subject_word_cap_come_from_the_project_block(committing_tree: Path) -> None:
    replace_once(committing_tree, ".claude/memento.yaml", "commit_type: docs\n", "commit_type: chore\n")
    replace_once(committing_tree, ".claude/memento.yaml", "commit_subject_words: 9\n", "commit_subject_words: 2\n")
    committed = run_memento(committing_tree, "commit", "two words", OPEN_EVENTS_FILE, ".claude/memento.yaml")
    sha = git(committing_tree, "rev-parse", "--short", "HEAD").strip()
    assert (committed.exit_code, committed.stdout) == (0, f"{sha} chore: two words\n{OPEN_EVENTS_FILE}\n.claude/memento.yaml\n")
    assert git(committing_tree, "log", "-1", "--format=%B") == "chore: two words\n\n"
    run_memento(committing_tree, "events", "add", "self", "decision", "again")
    assert_commit_refused(committing_tree, "subject runs 3 words, over 2", "three whole words", OPEN_EVENTS_FILE)
