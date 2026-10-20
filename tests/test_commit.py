"""`memento commit` commits named record paths after a green check, or refuses and commits nothing."""

from pathlib import Path

import pytest

from tests.conftest import HISTORY, git, replace_once, run_memento
from tests.test_journal import FAILURE_PARTS

OPEN_EVENTS_FILE = f"{HISTORY}/2026-10-01/events.md"
CAPABILITIES = "docs/capabilities.md"
MAIN_SPAN = "2026-10-01"
SIDE_SPAN = "2026-10-01-side"
SIDE_FILES = [f"{HISTORY}/{MAIN_SPAN}/{kind}-side.md" for kind in ("events", "session", "failures")]
SIDE_FILES_LISTED = ", ".join(sorted(SIDE_FILES))
MAIN_FILES_LISTED = ", ".join(f"{HISTORY}/{MAIN_SPAN}/{kind}.md" for kind in ("events", "failures", "session"))


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


def test_a_non_journal_record_commits_from_a_linked_worktree_on_named_paths(committing_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(committing_tree, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / CAPABILITIES).write_text("# Capabilities\n\nChanged.\n", encoding="utf-8")
    result = run_memento(worktree, "commit", "subject", CAPABILITIES)
    assert result.exit_code == 0, result.stderr
    assert git(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == [CAPABILITIES]


def test_a_journal_file_still_refuses_to_commit_from_a_linked_worktree_without_a_suffixed_span(committing_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "linked"
    git(committing_tree, "worktree", "add", "-q", "--detach", str(worktree))
    message = f"records are written in the root checkout only; this is worktree {worktree}"
    assert_commit_refused(worktree, message, "subject", OPEN_EVENTS_FILE)


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


@pytest.fixture
def side_worktree(committing_tree: Path, tmp_path: Path) -> Path:
    """A linked worktree of the committing tree holding the side span's events, session and failures files, written there and uncommitted."""
    worktree = tmp_path / "linked"
    git(committing_tree, "worktree", "add", "-q", "--detach", str(worktree))
    for step in (("events", "open", "--suffix", "side", "a", "side", "span"), ("digest", "add", "--span", SIDE_SPAN, "narrative"), ("failure", "add", "--span", SIDE_SPAN, *FAILURE_PARTS)):
        assert run_memento(worktree, *step).exit_code == 0
    return worktree


def test_a_suffixed_span_commits_its_own_files_from_a_linked_worktree_in_that_worktree(committing_tree: Path, side_worktree: Path) -> None:
    root_before = head_state(committing_tree)
    result = run_memento(side_worktree, "commit", "--span", SIDE_SPAN, "record the side span", *SIDE_FILES)
    sha = git(side_worktree, "rev-parse", "--short", "HEAD").strip()
    assert (result.exit_code, result.stdout) == (0, "\n".join([f"{sha} docs: record the side span", *SIDE_FILES]) + "\n")
    assert git(side_worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == sorted(SIDE_FILES)
    assert git(side_worktree, "status", "--porcelain") == ""
    assert head_state(committing_tree) == root_before


def test_a_suffixed_span_commits_only_the_files_it_names_from_a_linked_worktree(committing_tree: Path, side_worktree: Path) -> None:
    named, left = SIDE_FILES[0], SIDE_FILES[1:]
    result = run_memento(side_worktree, "commit", "--span", SIDE_SPAN, "record the side events", named)
    assert result.exit_code == 0
    assert git(side_worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == [named]
    assert git(side_worktree, "status", "--porcelain", "--untracked-files=all").splitlines() == [f"?? {path}" for path in sorted(left)]


def test_the_unsuffixed_span_still_refuses_in_a_linked_worktree(side_worktree: Path) -> None:
    message = f"records are written in the root checkout only; this is worktree {side_worktree}"
    assert_commit_refused(side_worktree, message, "--span", MAIN_SPAN, "subject", OPEN_EVENTS_FILE)


@pytest.mark.parametrize("outside", [CAPABILITIES, OPEN_EVENTS_FILE, f"{HISTORY}/2026-10-01/events-other.md"])
def test_a_path_outside_the_suffixed_spans_files_is_refused_in_a_linked_worktree(side_worktree: Path, outside: str) -> None:
    replace_once(side_worktree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    message = f"{outside} is outside span {SIDE_SPAN}'s own files: {SIDE_FILES_LISTED}"
    assert_commit_refused(side_worktree, message, "--span", SIDE_SPAN, "subject", SIDE_FILES[0], outside)


def test_a_malformed_span_is_refused(side_worktree: Path) -> None:
    message = "span '2026-10-01-Side' is not YYYY-MM-DD or YYYY-MM-DD-<suffix>, the suffix lowercase letters and digits joined by single hyphens"
    assert_commit_refused(side_worktree, message, "--span", "2026-10-01-Side", "subject", SIDE_FILES[0])


def test_a_failing_check_refuses_a_span_commit_in_a_linked_worktree(side_worktree: Path) -> None:
    replace_once(side_worktree, "docs/results.md", "2026-10-01", "October")
    assert_commit_refused(side_worktree, "the records hold 1 FAIL; 'memento check' lists them", "--span", SIDE_SPAN, "subject", SIDE_FILES[0])


def test_a_named_span_confines_a_commit_in_the_root_checkout_too(committing_tree: Path) -> None:
    replace_once(committing_tree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    message = f"{CAPABILITIES} is outside span {MAIN_SPAN}'s own files: {MAIN_FILES_LISTED}"
    assert_commit_refused(committing_tree, message, "--span", MAIN_SPAN, "subject", OPEN_EVENTS_FILE, CAPABILITIES)


def test_a_named_span_commits_its_own_files_in_the_root_checkout(committing_tree: Path) -> None:
    replace_once(committing_tree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    result = run_memento(committing_tree, "commit", "--span", MAIN_SPAN, "record the decision", OPEN_EVENTS_FILE)
    assert result.exit_code == 0
    assert git(committing_tree, "show", "--name-only", "--format=", "HEAD").splitlines() == [OPEN_EVENTS_FILE]
    assert git(committing_tree, "status", "--porcelain") == f" M {CAPABILITIES}\n"


def test_the_span_variable_neither_confines_a_root_commit_nor_frees_a_worktree_commit(committing_tree: Path, side_worktree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMENTO_SPAN", SIDE_SPAN)
    message = f"records are written in the root checkout only; this is worktree {side_worktree}"
    assert_commit_refused(side_worktree, message, "subject", SIDE_FILES[0])
    replace_once(committing_tree, CAPABILITIES, "slices its input.", "slices its input in pieces.")
    assert run_memento(committing_tree, "commit", "record the capabilities", CAPABILITIES).exit_code == 0


def test_the_commit_help_names_the_span_option_and_the_worktree_it_opens(record_tree: Path) -> None:
    result = run_memento(record_tree, "commit", "--help")
    text = " ".join(result.stdout.replace("│", " ").split())
    assert result.exit_code == 0
    for phrase in ("--span", "a linked worktree included", "a journal file in a worktree without --span naming a suffixed span", "that is not that span's events, session or failures file", "does not read MEMENTO_SPAN"):
        assert phrase in text


def test_fold_amends_an_unpushed_head_that_changed_only_the_named_paths(record_tree: Path) -> None:
    git(record_tree, "config", "user.name", "fixture")
    git(record_tree, "config", "user.email", "fixture@example.com")
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input in pieces.")
    first = run_memento(record_tree, "commit", "first edit", "docs/capabilities.md")
    assert first.exit_code == 0, first.output
    before = git(record_tree, "rev-list", "--count", "HEAD").strip()
    replace_once(record_tree, "docs/capabilities.md", "in pieces.", "in small pieces.")
    folded = run_memento(record_tree, "commit", "--fold", "both edits", "docs/capabilities.md")
    assert folded.exit_code == 0, folded.output
    assert git(record_tree, "rev-list", "--count", "HEAD").strip() == before
    assert "both edits" in git(record_tree, "log", "-1", "--format=%s")


def test_fold_refuses_a_head_that_changed_another_path(record_tree: Path) -> None:
    git(record_tree, "config", "user.name", "fixture")
    git(record_tree, "config", "user.email", "fixture@example.com")
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input in pieces.")
    (record_tree / "other.txt").write_text("other\n")
    git(record_tree, "add", "-A")
    git(record_tree, "commit", "-q", "-m", "docs: unrelated work")
    replace_once(record_tree, "docs/capabilities.md", "in pieces.", "in small pieces.")
    refused = run_memento(record_tree, "commit", "--fold", "x", "docs/capabilities.md")
    assert refused.exit_code != 0 and "also changed" in refused.output


def test_fold_refuses_a_root_commit_that_changed_another_path(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "root")
    from memento.commit import refuse_unfoldable_head
    from memento import Refusal

    with pytest.raises(Refusal):
        refuse_unfoldable_head(tmp_path, ["docs/capabilities.md"])
