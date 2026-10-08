"""What a record check reads, the corpus, and what it reports, findings."""

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
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
    """The records a check reads: the schema, the tracked paths, and the working tree beneath root."""

    root: Path
    schema: dict[str, Any]
    tracked: list[str]

    def text(self, path: str) -> str:
        """The text of a project-relative file."""
        return (self.root / path).read_text(encoding="utf-8")

    def raw(self, path: str) -> bytes:
        """The bytes of a project-relative file."""
        return (self.root / path).read_bytes()

    def exists(self, path: str) -> bool:
        """Whether a project-relative path exists in the working tree."""
        return (self.root / path).exists()

    def is_file(self, path: str) -> bool:
        """Whether a project-relative path is a regular file in the working tree."""
        return (self.root / path).is_file()

    def is_dir(self, path: str) -> bool:
        """Whether a project-relative path is a directory in the working tree."""
        return (self.root / path).is_dir()

    def glob(self, pattern: str) -> list[str]:
        """The project-relative paths a glob pattern matches, sorted as paths."""
        return [normalized(path.relative_to(self.root)) for path in sorted(self.root.glob(pattern))]

    def holds_object(self, revision: str) -> bool:
        """Whether git holds the object a revision names."""
        return git.object_exists(self.root, revision)


type Check = Callable[[Corpus], Iterator[Finding]]


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
