"""A store transaction lands every staged write or none, under the record lock."""

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import pytest

from memento import Refusal
from memento.store import RestoreFailed, Store
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


def test_a_replacement_lands_new_whole_content_over_an_edited_working_copy(repository: Path) -> None:
    (repository / "kept.md").write_text("edited line\n", encoding="utf-8")
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.replace("kept.md", "rewritten\nwhole\n")
    assert (repository / "kept.md").read_bytes() == b"rewritten\nwhole\n"


def test_a_replacement_creates_a_file_that_did_not_exist(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.replace("created.md", "created\n")
    assert (repository / "created.md").read_bytes() == b"created\n"


def test_a_removal_deletes_the_file(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.remove("kept.md")
    assert not (repository / "kept.md").exists()


def test_a_failed_replacement_after_a_removal_restores_both_files(repository: Path) -> None:
    (repository / "other.md").write_bytes(b"other before\n")
    store = Store(root=repository, replace_file=failing_on_call(1))
    with pytest.raises(OSError, match="replacement of other.md failed"):
        with store.transaction() as act:
            act.remove("kept.md")
            act.replace("other.md", "other after\n")
    assert (repository / "kept.md").read_bytes() == b"kept line\n"
    assert (repository / "other.md").read_bytes() == b"other before\n"
    assert leftover_temporaries(repository) == []


def test_a_failed_landing_recreates_a_removed_file_with_its_permission_bits(repository: Path) -> None:
    (repository / "kept.md").chmod(0o640)
    (repository / "other.md").write_bytes(b"other before\n")
    store = Store(root=repository, replace_file=failing_on_call(1))
    with pytest.raises(OSError, match="replacement of other.md failed"):
        with store.transaction() as act:
            act.remove("kept.md")
            act.replace("other.md", "other after\n")
    assert (repository / "kept.md").read_bytes() == b"kept line\n"
    assert (repository / "kept.md").stat().st_mode & 0o777 == 0o640


def test_an_append_after_a_replacement_extends_the_replacement(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.replace("kept.md", "fresh\n")
        act.append("kept.md", "appended\n")
    assert (repository / "kept.md").read_bytes() == b"fresh\nappended\n"


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

    writers = [threading.Thread(target=first, daemon=True), threading.Thread(target=second, daemon=True)]
    for writer in writers:
        writer.start()
    try:
        first_holds.wait()
        assert not second_entered.wait(LOCK_WAIT_SECONDS)
    finally:
        release_first.set()
        for writer in writers:
            writer.join()
    assert (repository / "kept.md").read_text(encoding="utf-8") == "kept line\nfirst writer\nsecond writer\n"


def test_a_write_under_missing_directories_lands_with_them(repository: Path) -> None:
    with Store(root=repository, replace_file=os.replace).transaction() as act:
        act.append("history/2026-10-02/events.md", "first line\n")
    assert (repository / "history/2026-10-02/events.md").read_text(encoding="utf-8") == "first line\n"


def test_a_failed_landing_removes_the_directories_it_created(repository: Path) -> None:
    (repository / "history").mkdir()
    with pytest.raises(OSError, match="replacement of events.md failed"):
        with Store(root=repository, replace_file=failing_on_call(2)).transaction() as act:
            act.append("kept.md", "second line\n")
            act.append("history/2026-10-02/deep/events.md", "first line\n")
    assert sorted(path.name for path in (repository / "history").iterdir()) == []
    assert (repository / "kept.md").read_text(encoding="utf-8") == "kept line\n"


def test_a_created_directory_another_writer_filled_during_a_failed_landing_is_reported(repository: Path) -> None:
    def fill_then_fail(source: Path, target: Path) -> None:
        """Drop a stray file beside the target, then fail the replacement."""
        (target.parent / "stray.md").write_text("stray\n", encoding="utf-8")
        raise OSError("replacement failed")

    with pytest.raises(RestoreFailed, match="rollback left directory .*/history/2026-10-02 it created"):
        with Store(root=repository, replace_file=fill_then_fail).transaction() as act:
            act.append("history/2026-10-02/events.md", "first line\n")


def open_descriptor_count() -> int:
    """How many file descriptors this process holds open."""
    return len(os.listdir("/dev/fd"))


def test_a_temporary_that_cannot_be_opened_for_writing_leaves_no_descriptor_or_file(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opening_path = Path.open

    def refusing_writes(path: Path, mode: str = "r", buffering: int = -1, encoding: str | None = None) -> IO[Any]:
        """Open a path as pathlib does, except for writing, which fails."""
        if "w" in mode:
            raise OSError("opening for writing failed")
        return opening_path(path, mode, buffering, encoding)

    def refusing_descriptor(*args: object, **kwargs: object) -> object:
        """Fail as os.fdopen would when it cannot wrap a descriptor."""
        raise OSError("opening for writing failed")

    monkeypatch.setattr(Path, "open", refusing_writes)
    monkeypatch.setattr(os, "fdopen", refusing_descriptor)
    before = open_descriptor_count()
    with pytest.raises(OSError, match="opening for writing failed"):
        with Store(root=repository, replace_file=os.replace).transaction() as act:
            act.append("kept.md", "more\n")
    assert (open_descriptor_count(), sorted(path.name for path in repository.iterdir())) == (before, [".git", "kept.md"])


def test_a_rollback_that_cannot_restore_one_file_restores_the_others_and_names_it(repository: Path) -> None:
    calls: list[Path] = []

    def lock_first_then_fail_third(source: Path, target: Path) -> None:
        """Replace the first file and leave it read-only, replace the second, and fail the third."""
        calls.append(target)
        if len(calls) == 3:
            raise OSError("replacement of third.md failed")
        os.replace(source, target)
        if len(calls) == 1:
            target.chmod(0o444)

    with pytest.raises(RestoreFailed, match=r"^rollback left kept\.md differing from its bytes or permission bits before the act$"):
        with Store(root=repository, replace_file=lock_first_then_fail_third).transaction() as act:
            act.append("kept.md", "second line\n")
            act.append("second.md", "first line\n")
            act.append("third.md", "first line\n")
    assert (sorted(path.name for path in repository.iterdir()), leftover_temporaries(repository)) == ([".git", "kept.md"], [])
