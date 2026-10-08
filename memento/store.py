"""Landing record writes: in the root checkout only, under the record lock, all of an act or none of it."""

import fcntl
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from memento import Refusal
from memento.git import file_at_head, is_worktree

LOCK_PATH = ".git/memento.lock"
TEMPORARY_SUFFIX = ".memento"
NEW_RECORD_MODE = 0o644


class RestoreFailed(Exception):
    """A failed landing whose rollback left a file differing from its bytes before the act."""


@dataclass(frozen=True)
class Staged:
    """One file's bytes before an act and after it, None meaning the file is absent."""

    before: bytes | None
    after: bytes | None


@dataclass
class Act:
    """The writes one verb stages, landed together when its transaction ends."""

    root: Path
    staged: dict[str, Staged] = field(default_factory=dict)

    def append(self, path: str, text: str) -> None:
        """Stage text appended to a file, refusing when its working copy edits a line HEAD holds."""
        if path in self.staged:
            base = self.staged[path].after or b""
        else:
            base = read_if_present(self.root / path) or b""
            refuse_edited_head_lines(path, base, file_at_head(self.root, path) or b"")
        self.stage(path, base + text.encode("utf-8"))

    def replace(self, path: str, text: str) -> None:
        """Stage new whole content for a file, created when absent."""
        self.stage(path, text.encode("utf-8"))

    def remove(self, path: str) -> None:
        """Stage a file's deletion."""
        self.stage(path, None)

    def stage(self, path: str, after: bytes | None) -> None:
        """Record what a file holds once the act lands, beside its bytes before the act."""
        self.staged[path] = Staged(before=read_if_present(self.root / path), after=after)


@dataclass(frozen=True)
class Store:
    """The records beneath one root, and the file replacement every landing goes through."""

    root: Path
    replace_file: Callable[[Path, Path], None]

    @contextmanager
    def transaction(self) -> Iterator[Act]:
        """Refuse outside the root checkout, hold the record lock, and land everything staged, or nothing."""
        refuse_worktree(self.root)
        with record_lock(self.root):
            act = Act(self.root)
            yield act
            land(self.root, act.staged, self.replace_file)


def refuse_worktree(root: Path) -> None:
    """Refuse a write anywhere but the repository's own checkout."""
    if is_worktree(root):
        raise Refusal(f"records are written in the root checkout only; this is worktree {root}")


@contextmanager
def record_lock(root: Path) -> Iterator[None]:
    """Hold the exclusive record lock under root's git directory, waiting for any other holder."""
    with (root / LOCK_PATH).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_if_present(path: Path) -> bytes | None:
    """A file's bytes, or None when it does not exist."""
    return path.read_bytes() if path.exists() else None


def first_edited_line(head: bytes, working: bytes) -> int | None:
    """The first line number at which a working copy stops extending HEAD's copy, or None when it extends it."""
    head_lines, working_lines = head.splitlines(keepends=True), working.splitlines(keepends=True)
    for number, (held, current) in enumerate(zip(head_lines, working_lines), 1):
        if held != current:
            return number
    return len(working_lines) + 1 if len(working_lines) < len(head_lines) else None


def refuse_edited_head_lines(path: str, working: bytes, head: bytes) -> None:
    """Refuse appending to a file whose working copy is not an extension of HEAD's copy."""
    edited = first_edited_line(head, working)
    if edited is not None:
        raise Refusal(f"{path} is not an extension of HEAD's copy (first difference at line {edited}); written lines are never edited")


def mode_for(target: Path) -> int:
    """The permission bits a landed file keeps: its own when it exists, a record's default otherwise."""
    return stat.S_IMODE(target.stat().st_mode) if target.exists() else NEW_RECORD_MODE


def write_temporary(target: Path, content: bytes) -> Path:
    """Write content durably to a new temporary file beside target, carrying target's permission bits."""
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=TEMPORARY_SUFFIX)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode_for(target))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def discard(paths: Iterable[Path]) -> None:
    """Remove temporary files that will not land."""
    for path in paths:
        path.unlink(missing_ok=True)


def write_temporaries(root: Path, staged: Mapping[str, Staged]) -> dict[str, Path]:
    """One temporary file per staged write that leaves a file in place, or none of them when any fails."""
    temporaries: dict[str, Path] = {}
    try:
        for path, write in staged.items():
            if write.after is not None:
                temporaries[path] = write_temporary(root / path, write.after)
    except BaseException:
        discard(temporaries.values())
        raise
    return temporaries


def restore(root: Path, befores: Mapping[str, bytes | None]) -> None:
    """Put each landed file back to its bytes before the act, and verify every one byte for byte."""
    for path, before in befores.items():
        if before is None:
            (root / path).unlink(missing_ok=True)
        else:
            (root / path).write_bytes(before)
    differing = [path for path, before in befores.items() if read_if_present(root / path) != before]
    if differing:
        raise RestoreFailed(f"rollback left {', '.join(differing)} differing from the bytes before the act")


def land(root: Path, staged: Mapping[str, Staged], replace_file: Callable[[Path, Path], None]) -> None:
    """Replace or delete every staged file, or restore every one already touched and re-raise."""
    temporaries = write_temporaries(root, staged)
    landed: list[str] = []
    try:
        for path in staged:
            if path in temporaries:
                replace_file(temporaries[path], root / path)
            else:
                (root / path).unlink(missing_ok=True)
            landed.append(path)
    except BaseException:
        discard(temporary for path, temporary in temporaries.items() if path not in landed)
        restore(root, {path: staged[path].before for path in landed})
        raise
