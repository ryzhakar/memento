"""The git queries the records need, each run against an explicit root."""

import subprocess
from collections.abc import Callable
from pathlib import Path

type GitRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def git_exit_status(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run one git command against root, leaving its exit status to the caller."""
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def git_output(root: Path, *args: str) -> str:
    """The standard output of one git command against root, raising when git fails."""
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def tracked_files(root: Path) -> list[str]:
    """Every path git tracks under root, in `git ls-files` order."""
    return git_output(root, "ls-files").splitlines()


def untracked_files(root: Path) -> list[str]:
    """Every path under root that git neither tracks nor ignores."""
    return git_output(root, "ls-files", "--others", "--exclude-standard").splitlines()


def object_exists(root: Path, revision: str) -> bool:
    """Whether git holds the object a revision names."""
    return git_exit_status(root, "cat-file", "-e", revision).returncode == 0


def is_ancestor(root: Path, revision: str, descendant: str) -> bool:
    """Whether a revision is the descendant itself or one of its ancestors; a revision git holds no commit for is not."""
    return git_exit_status(root, "merge-base", "--is-ancestor", revision, descendant).returncode == 0


def git_directory(root: Path, flag: str) -> Path:
    """The directory `git rev-parse <flag>` names, made absolute against root."""
    return (root / git_output(root, "rev-parse", flag).strip()).resolve()


def is_worktree(root: Path) -> bool:
    """Whether root is a linked worktree rather than the repository's own checkout."""
    return git_directory(root, "--git-dir") != git_directory(root, "--git-common-dir")


def main_checkout(root: Path) -> Path:
    """The repository's own checkout: the first worktree git lists, whichever linked worktree root is."""
    first = git_output(root, "worktree", "list", "--porcelain").splitlines()[0]
    return Path(first.removeprefix("worktree ")).resolve()


def tracks(root: Path, path: str) -> bool:
    """Whether git tracks a file or any file beneath a directory, the path read literally."""
    return bool(git_output(root, "ls-files", "--", f":(literal){path}").strip())


def file_at_head(root: Path, path: str) -> bytes | None:
    """The bytes HEAD holds at a path, or None when HEAD holds none."""
    shown = git_exit_status(root, "show", f"HEAD:{path}")
    return shown.stdout if shown.returncode == 0 else None
