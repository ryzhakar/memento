"""Standing proof that the check catches what it claims to: one violation planted in a valid tree, one expected finding."""

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import commit_all, replace_once, run_memento

OPEN_DAY = "docs/orchestration_log/history/2026-10-01/events.md"
CLOSED_DAY = "docs/orchestration_log/history/2026-09-30/"
CAPABILITIES = "docs/capabilities.md"


def append_to(root: Path, path: str, text: str) -> None:
    """Append text to a tree file."""
    target = root / path
    target.write_text(target.read_text(encoding="utf-8") + text, encoding="utf-8")


PLANTS: dict[str, tuple[Callable[[Path], None], str]] = {
    "a hard-wrapped paragraph": (lambda root: replace_once(root, CAPABILITIES, "slices its input.", "slices its\ninput."), "authored prose carries no hard wrap"),
    "a NUL byte": (lambda root: (root / CAPABILITIES).write_bytes((root / CAPABILITIES).read_bytes() + b"\x00"), "contains NUL bytes"),
    "a record no map row lists": (lambda root: (root / "docs/stray.md").write_text("x\n", encoding="utf-8"), "not a record the map lists, a declared product, or a retired path"),
    "a pointer at a missing file": (lambda root: replace_once(root, CAPABILITIES, "slices its input.", "slices its input (see docs/nowhere.md)."), "points at 'docs/nowhere.md', which does not exist"),
    "a map row of an undeclared kind": (lambda root: replace_once(root, ".claude/memento-map.md", "| fact |", "| factoid |"), "kind 'factoid' is not declared in the schema"),
    "an events line whose time runs backwards": (lambda root: append_to(root, OPEN_DAY, "2026-10-01T00:01 | self | decision | early\n"), "goes back from"),
    "an events line off the actor forms": (lambda root: append_to(root, OPEN_DAY, "2026-10-01T23:59 | nobody | decision | x\n"), "actor 'nobody' is not owner"),
    "a charter section out of its names": (lambda root: replace_once(root, "CLAUDE.md", "## Pointers", "## Elsewhere"), "do not follow the schema's"),
    "a rule with no ground": (lambda root: replace_once(root, "docs/conventions.md", "ground  owner ruling 2026-08-04\n", ""), "missing ground"),
    "a close block label misspelt": (lambda root: replace_once(root, f"{CLOSED_DAY}session.md", "next", "nxt"), "line outside the close fields"),
    "a failure entry missing its mechanism": (lambda root: replace_once(root, f"{CLOSED_DAY}failures.md", "Mechanism:", "Cause:"), "paragraphs open"),
}


def test_the_unplanted_tree_passes_the_check(record_tree: Path) -> None:
    assert run_memento(record_tree, "check").exit_code == 0


@pytest.mark.parametrize("name", PLANTS)
def test_a_planted_violation_is_caught(record_tree: Path, name: str) -> None:
    plant, expected = PLANTS[name]
    plant(record_tree)
    commit_all(record_tree)
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert any(line.startswith("FAIL") and expected in line for line in result.stdout.splitlines()), result.stdout


def test_a_record_written_and_never_added_warns_without_failing_the_check(record_tree: Path) -> None:
    (record_tree / "docs/note.md").write_text("x\n", encoding="utf-8")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 0
    assert "WARN  docs/note.md  untracked: git holds no copy of this record yet; commit it or ignore it" in result.stdout.splitlines()
