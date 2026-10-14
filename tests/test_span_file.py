"""The `.memento-span` file: a worktree names its span once, as `.python-version` names an interpreter."""

from pathlib import Path

import pytest

from memento.journal_spans import SPAN_FILE, span_file_text, span_or_file


def checkout(tmp_path: Path) -> Path:
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")
    return tmp_path


def test_the_file_beside_the_checkout_names_the_span_from_a_subdirectory(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    (root / SPAN_FILE).write_text("2026-10-14-u1\n")
    below = root / "docs" / "deep"
    below.mkdir(parents=True)
    assert span_file_text(below) == "2026-10-14-u1"


def test_no_file_names_no_span(tmp_path: Path) -> None:
    assert span_file_text(checkout(tmp_path)) is None


def test_the_search_stops_at_the_checkout(tmp_path: Path) -> None:
    (tmp_path / SPAN_FILE).write_text("2026-10-14-outer\n")
    inner = tmp_path / "repo"
    inner.mkdir()
    checkout(inner)
    assert span_file_text(inner) is None


def test_an_option_given_wins_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    (root / SPAN_FILE).write_text("2026-10-14-u1\n")
    monkeypatch.chdir(root)
    assert span_or_file("2026-10-14-u2") == "2026-10-14-u2"
    assert span_or_file(None) == "2026-10-14-u1"
