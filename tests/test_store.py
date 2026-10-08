"""A store transaction lands every staged write or none, under the record lock."""

import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from memento import Refusal
from memento.store import Store
from tests.memento.conftest import git

LOCK_WAIT_SECONDS = 0.3


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A repository whose HEAD holds one file, kept.md."""
    root = tmp_path / "repository"
    root.mkdir()
    (root / "kept.md").write_text("kept line\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "kept.md")
    git(root, "commit", "-q", "-m", "kept")
    return root


def failing_on_call(failing_call: int) -> Callable[[Path, Path], None]:
    """A file replacement that replaces as os.replace does until its numbered call, which raises."""
    calls = []

    def replace_file(source: Path, target: Path) -> None:
        """Replace target with source, or raise on the failing call."""
        calls.append(target)
        if len(calls) == failing_call:
            raise OSError(f"replacement of {target.name} failed")
        os.replace(source, target)

    return replace_file


def leftover_temporaries(root: Path) -> list[str]:
    """The temporary files a landing left beside the records."""
    return sorted(path.name for path in root.iterdir() if path.name.endswith(".memento"))


def test_two_staged_appends_land_together(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.append("kept.md", "second line\n")
        act.append("new.md", "first line\n")
    assert (repository / "kept.md").read_text(encoding="utf-8") == "kept line\nsecond line\n"
    assert (repository / "new.md").read_text(encoding="utf-8") == "first line\n"


def test_appends_to_one_file_accumulate_in_order(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.append("kept.md", "second\n")
        act.append("kept.md", "third\n")
    assert (repository / "kept.md").read_text(encoding="utf-8") == "kept line\nsecond\nthird\n"


def test_a_failed_second_replacement_restores_the_first_file_and_reraises(repository: Path) -> None:
    store = Store(root=repository, replace_file=failing_on_call(2))
    with pytest.raises(OSError, match="replacement of new.md failed"):
        with store.transaction() as act:
            act.append("kept.md", "second line\n")
            act.append("new.md", "first line\n")
    assert (repository / "kept.md").read_bytes() == b"kept line\n"
    assert not (repository / "new.md").exists()
    assert leftover_temporaries(repository) == []


def test_a_failed_replacement_removes_a_file_the_act_created(repository: Path) -> None:
    store = Store(root=repository, replace_file=failing_on_call(2))
    with pytest.raises(OSError, match="replacement of kept.md failed"):
        with store.transaction() as act:
            act.append("new.md", "first line\n")
            act.append("kept.md", "second line\n")
    assert not (repository / "new.md").exists()
    assert (repository / "kept.md").read_bytes() == b"kept line\n"
    assert leftover_temporaries(repository) == []


def test_a_refusal_inside_the_transaction_lands_nothing(repository: Path) -> None:
    with pytest.raises(Refusal):
        with Store(root=repository, replace_file=os.replace).transaction() as act:
            act.append("kept.md", "second line\n")
            raise Refusal("declined after staging")
    assert (repository / "kept.md").read_bytes() == b"kept line\n"


def test_a_landed_file_keeps_its_permission_bits(repository: Path) -> None:
    (repository / "kept.md").chmod(0o640)
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.append("kept.md", "second line\n")
    assert (repository / "kept.md").stat().st_mode & 0o777 == 0o640


def test_a_second_transaction_waits_for_the_first_to_land(repository: Path) -> None:
    store = Store(root=repository, replace_file=os.replace)
    first_holds, release_first, second_entered = threading.Event(), threading.Event(), threading.Event()

    def first() -> None:
        """Stage a write and hold the transaction until released."""
        with store.transaction() as act:
            act.append("kept.md", "first writer\n")
            first_holds.set()
            release_first.wait()

    def second() -> None:
        """Enter a transaction once the first holds its own."""
        first_holds.wait()
        with store.transaction() as act:
            second_entered.set()
            act.append("kept.md", "second writer\n")

    writers = [threading.Thread(target=first), threading.Thread(target=second)]
    for writer in writers:
        writer.start()
    first_holds.wait()
    assert not second_entered.wait(LOCK_WAIT_SECONDS)
    release_first.set()
    for writer in writers:
        writer.join()
    assert (repository / "kept.md").read_text(encoding="utf-8") == "kept line\nfirst writer\nsecond writer\n"
