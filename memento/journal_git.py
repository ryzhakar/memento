"""The git reads the journal needs: HEAD with the paths changed beside it, and the journal's history since the memento recipes landed."""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from memento import Refusal, construct
from memento.clock import MINUTE_FORMAT
from memento.git import GitRunner, git_output
from memento.journal_shapes import Head
from memento.schema import DATE_PLACEHOLDER, SEGMENT_WILDCARD, Schema


NO_HEAD_COMMIT = "HEAD names no commit yet; git status has nothing to measure against"
PORCELAIN_PATH_START = len("XY ")
JOURNAL_KINDS = ("events", "digest", "failure", "measurement")
FIRST_RECIPES_FILE = "memento.just"
GLOB_PATHSPEC = ":(glob)"
COMMIT_MARK = "\x01"
DIFF_FILE_HEADER = "diff --git "
DIFF_NEW_PATH = " b/"
HUNK_HEADER = "@@"
REMOVED_LINE = "-"
PARENTS_OF = "^@"
PLAIN_PATCH_LOG = ("log", "-p", "--no-renames", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/")


class HistoryStart(NamedTuple):
    """The commit that first added the memento recipes, from which the journal's history is held append-only."""

    sha: str
    minute: str


def history_start(root: Path) -> HistoryStart | None:
    """The oldest commit adding the memento recipes, or None when the history holds none."""
    added = git_output(root, "log", "--diff-filter=A", "--format=%H %ad", f"--date=format:{MINUTE_FORMAT}", "--", FIRST_RECIPES_FILE)
    oldest = added.splitlines()[-1:]
    if not oldest:
        return None
    sha, stamp = oldest[0].split()
    return HistoryStart(sha, stamp)


def journal_pathspecs(schema: Schema) -> list[str]:
    """Git pathspecs covering every journal home and the measurement log."""
    homes = (schema["kinds"][kind]["home"].replace(DATE_PLACEHOLDER, SEGMENT_WILDCARD) for kind in JOURNAL_KINDS)
    return [f"{GLOB_PATHSPEC}{home}" for home in homes]


class RemovedLine(NamedTuple):
    """A line a commit removed from a journal file."""

    sha: str
    path: str
    line: str


def removed_lines(log: str) -> Iterator[RemovedLine]:
    """Every line a `git log -p` output, its commits marked by COMMIT_MARK, removes inside a hunk."""
    sha, path, in_hunk = "", "", False
    for line in log.splitlines():
        if line.startswith(COMMIT_MARK):
            sha, in_hunk = line.removeprefix(COMMIT_MARK), False
        elif line.startswith(DIFF_FILE_HEADER):
            path, in_hunk = line.rpartition(DIFF_NEW_PATH)[2], False
        elif line.startswith(HUNK_HEADER):
            in_hunk = True
        elif in_hunk and line.startswith(REMOVED_LINE):
            yield RemovedLine(sha, path, line.removeprefix(REMOVED_LINE))


def lines_removed_since(root: Path, schema: Schema, start: HistoryStart) -> Iterator[RemovedLine]:
    """Every line a commit since the start removed from an events, session, failures or measurement file."""
    log = git_output(root, *PLAIN_PATCH_LOG, f"--format={COMMIT_MARK}%H", "HEAD", "--not", f"{start.sha}{PARENTS_OF}", "--", *journal_pathspecs(schema))
    return removed_lines(log)


def found_head(run_git: GitRunner, root: Path) -> Head:
    """HEAD's short sha and the paths git reports changed in the working tree, refused when HEAD names no commit."""
    head = run_git(root, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        raise Refusal(NO_HEAD_COMMIT)
    status = run_git(root, "status", "--porcelain").stdout.decode("utf-8")
    dirty = tuple(entry[PORCELAIN_PATH_START:] for entry in status.splitlines())
    return construct(Head, sha=head.stdout.decode("utf-8").strip(), dirty=dirty)
