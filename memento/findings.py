"""What a record check reads, the corpus, and what it reports, findings."""

import fnmatch
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, NamedTuple

from memento import git

DATE = r"\d{4}-\d{2}-\d{2}"
MINUTE = rf"{DATE}T\d{{2}}:\d{{2}}"
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
FAIL = "FAIL"
WARN = "WARN"


class Finding(NamedTuple):
    """One check result: its level, the place it points at, and what it says."""

    level: str
    where: str
    message: str


def fail(where: str, message: str) -> Finding:
    """A finding that fails the check."""
    return Finding(FAIL, where, message)


def warn(where: str, message: str) -> Finding:
    """A finding that warns without failing the check."""
    return Finding(WARN, where, message)


def normalized(path: str | PurePath) -> str:
    """A project-relative path spelled as pathlib spells it."""
    return str(PurePosixPath(path))


@dataclass(frozen=True)
class Corpus:
    """The records a check reads: the schema, the tracked paths, and the working tree beneath root with changes over it; main_root names the main checkout when root is a linked worktree."""

    root: Path
    schema: dict[str, Any]
    tracked: list[str]
    changes: Mapping[str, str | None] = field(default_factory=dict)
    main_root: Path | None = None

    def substituted(self, changes: Mapping[str, str | None]) -> "Corpus":
        """This corpus with changed files' texts over the working tree and among the tracked paths, None meaning deleted."""
        merged = {**self.changes, **{normalized(path): text for path, text in changes.items()}}
        deleted = {path for path, text in merged.items() if text is None}
        tracked = sorted((set(self.tracked) - deleted) | (set(merged) - deleted))
        return replace(self, changes=merged, tracked=tracked)

    def locate(self, path: str) -> Path:
        """Where a project-relative path is read: beneath root, or beneath the main checkout for a path a linked worktree neither holds nor git tracks."""
        here = self.root / path
        if self.main_root is None or here.exists() or git.tracks(self.root, path):
            return here
        return self.main_root / path

    def text(self, path: str) -> str:
        """The text of a project-relative file."""
        if path in self.changes:
            changed = self.changes[path]
            if changed is None:
                raise FileNotFoundError(f"{path} is deleted in this corpus")
            return changed
        return self.locate(path).read_text(encoding="utf-8")

    def raw(self, path: str) -> bytes:
        """The bytes of a project-relative file."""
        if path in self.changes:
            return self.text(path).encode("utf-8")
        return self.locate(path).read_bytes()

    def exists(self, path: str) -> bool:
        """Whether a project-relative path exists in the working tree."""
        if path in self.changes:
            return self.changes[path] is not None
        return self.locate(path).exists()

    def is_file(self, path: str) -> bool:
        """Whether a project-relative path is a regular file in the working tree."""
        if path in self.changes:
            return self.changes[path] is not None
        return self.locate(path).is_file()

    def is_dir(self, path: str) -> bool:
        """Whether a project-relative path is a directory in the working tree."""
        return self.locate(path).is_dir()

    def glob(self, pattern: str) -> list[str]:
        """The project-relative paths a glob pattern matches, sorted as paths."""
        on_disk = {normalized(path.relative_to(self.root)) for path in self.root.glob(pattern)}
        deleted = {path for path, text in self.changes.items() if text is None}
        added = {path for path, text in self.changes.items() if text is not None and matches_glob(path, pattern)}
        return sorted((on_disk - deleted) | added, key=PurePosixPath)

    def holds_object(self, revision: str) -> bool:
        """Whether git holds the object a revision names."""
        return git.object_exists(self.root, revision)


type Check = Callable[[Corpus], Iterator[Finding]]


def matches_glob(path: str, pattern: str) -> bool:
    """Whether a project-relative path matches a glob pattern segment by segment, as a pathlib glob without `**` does."""
    path_parts, pattern_parts = PurePosixPath(path).parts, PurePosixPath(pattern).parts
    return len(path_parts) == len(pattern_parts) and all(map(fnmatch.fnmatchcase, path_parts, pattern_parts))


def run_checks(corpus: Corpus, checks: Iterable[Check]) -> list[Finding]:
    """Every finding of every check over a corpus, in the checks' order."""
    return [finding for check_records in checks for finding in check_records(corpus)]


def split_entries(text: str) -> Iterator[tuple[int, str, list[str]]]:
    """Each `## ` entry of a record: its line number, its header, and the lines beneath it."""
    header_line, header, body = 0, None, []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            if header is not None:
                yield header_line, header, body
            header_line, header, body = number, line, []
        elif header is not None:
            body.append(line)
    if header is not None:
        yield header_line, header, body
