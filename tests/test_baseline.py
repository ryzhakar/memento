"""The baseline kind: a table of things tracked, checked strictly and written one row at a time."""

from pathlib import Path

from tests.conftest import commit_tree, run_memento

BASELINE = """# Baseline — the state this project is expected to be in

| Thing tracked | Expected state |
|---|---|
| Branches | `main` pushed. |
| Review | Open. |

Depth reached: branches measured against git.

— verified by Claude, 2026-09-22
"""


def with_baseline(root: Path, text: str = BASELINE) -> Path:
    schema = root / ".claude/memento.yaml"
    body = schema.read_text(encoding="utf-8")
    schema.write_text(body.replace("kinds:\n", "kinds:\n  baseline:\n    home: docs/working_set.md\n    tier: dossier\n    load: need\n    verification: measured\n", 1), encoding="utf-8")
    (root / "docs/working_set.md").write_text(text, encoding="utf-8")
    map_file = root / ".claude/memento-map.md"
    map_file.write_text(map_file.read_text(encoding="utf-8") + "| docs/working_set.md | baseline | need | the expected state |\n", encoding="utf-8")
    commit_tree(root, "baseline")
    return root


def test_a_well_formed_baseline_passes_the_check(record_tree: Path) -> None:
    result = run_memento(with_baseline(record_tree), "check")
    assert result.exit_code == 0, result.stdout


def test_a_baseline_with_a_repeated_thing_a_short_row_and_no_mark_fails(record_tree: Path) -> None:
    broken = BASELINE.replace("| Review | Open. |", "| Branches | Open. |\n| Lonely |").replace("— verified by Claude, 2026-09-22\n", "")
    result = run_memento(with_baseline(record_tree, broken), "check")
    assert result.exit_code == 1
    assert "the thing 'Branches' has more than one row" in result.stdout
    assert "a row holds exactly 2 non-empty cells" in result.stdout
    assert "at least one mark line" in result.stdout


def test_set_replaces_a_row_and_appends_a_mark(record_tree: Path) -> None:
    root = with_baseline(record_tree)
    result = run_memento(root, "baseline", "set", "Review", "Closed.", "--by", "Claude")
    assert (result.exit_code, result.stdout) == (0, "docs/working_set.md: Review\n")
    text = (root / "docs/working_set.md").read_text(encoding="utf-8")
    assert "| Review | Closed. |" in text
    assert text.endswith("— Review restated, measured by Claude, 2026-10-01\n")


def test_set_adds_a_row_after_the_last_one(record_tree: Path) -> None:
    root = with_baseline(record_tree)
    run_memento(root, "baseline", "set", "Gate", "Green.", "--by", "Claude")
    lines = (root / "docs/working_set.md").read_text(encoding="utf-8").splitlines()
    assert lines[lines.index("| Review | Open. |") + 1] == "| Gate | Green. |"


def test_set_refuses_a_state_holding_a_pipe(record_tree: Path) -> None:
    result = run_memento(with_baseline(record_tree), "baseline", "set", "Review", "a | b", "--by", "Claude")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == "refused: state must be one non-empty line without '|'\n"


def test_set_refuses_a_schema_without_the_kind(record_tree: Path) -> None:
    result = run_memento(record_tree, "baseline", "set", "Review", "Closed.", "--by", "Claude")
    assert result.stderr == "refused: the schema declares no 'baseline' kind\n"


def test_set_under_quiet_lands_its_write_and_prints_nothing(record_tree: Path) -> None:
    root = with_baseline(record_tree)
    result = run_memento(root, "baseline", "set", "Review", "Closed.", "--by", "Claude", "--quiet")
    assert (result.exit_code, result.stdout) == (0, "")
    assert "| Review | Closed. |" in (root / "docs/working_set.md").read_text(encoding="utf-8")
