"""`kind_for` resolves a path to its kind by the schema's home grammar, and the command offers a group only for a kind memento writes."""

from pathlib import Path

import pytest

from memento.schema import kind_for
from tests.conftest import fixture_schema, run_memento

USAGE_ERROR_EXIT_CODE = 2

HISTORY = "docs/orchestration_log/history"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("docs/capabilities.md", "fact"),
        ("CLAUDE.md", "charter"),
        (".claude/memento.yaml", "schema"),
        (f"{HISTORY}/2026-10-08/events.md", "events"),
        (f"{HISTORY}/2026-10-08/events-parallel.md", "events"),
        (f"{HISTORY}/2026-10-08/session.md", "digest"),
        (f"{HISTORY}/2026-10-08/failures.md", "failure"),
        (f"{HISTORY}/2026-09-06/frontier-questions-and-verdicts.md", "report"),
        (f"{HISTORY}/2026-09-06/nested/notes.md", "report"),
        ("docs/orchestration_log/recon/2026-10-08/specs/memento-module.md", "scratch"),
        ("docs/reference/collection-item-schema.md", "reference"),
        ("docs/specs/memento.md", "spec"),
        ("docs/orchestration_log/archive/DECISIONS.md", "archived"),
    ],
)
def test_a_path_resolves_to_the_kind_whose_home_covers_it(path: str, kind: str) -> None:
    assert kind_for(fixture_schema(), path) == kind


@pytest.mark.parametrize(
    "path",
    [
        f"{HISTORY}/october/events.md",
        f"{HISTORY}/2026-10-08",
        "docs/capabilities.md.bak",
        "tool/score.py",
        "docs/orchestration_log/recon/notes.md",
        "docs/reference",
    ],
)
def test_a_path_no_home_covers_resolves_to_no_kind(path: str) -> None:
    assert kind_for(fixture_schema(), path) is None


def test_an_exact_home_outranks_a_wildcard_and_a_wildcard_outranks_a_directory() -> None:
    schema = {
        "kinds": {
            "directory": {"home": "docs/"},
            "wildcard": {"home": "docs/*.md"},
            "exact": {"home": "docs/x.md"},
        }
    }
    assert [kind_for(schema, path) for path in ("docs/x.md", "docs/y.md", "docs/sub/y.md")] == ["exact", "wildcard", "directory"]


@pytest.mark.parametrize("group", ["span", "deferral", "schema", "baseline", "charter", "frame", "reference"])
def test_no_command_exists_for_a_group_memento_does_not_have(record_tree: Path, group: str) -> None:
    result = run_memento(record_tree, group, "add")
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert f"No such command '{group}'" in result.stderr


@pytest.mark.parametrize("group", ["fact", "string_recipes", "map"])
def test_a_group_registered_before_its_verbs_answers_no_such_command(record_tree: Path, group: str) -> None:
    result = run_memento(record_tree, group, "add")
    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "No such command 'add'" in result.stderr


def test_the_measurement_group_help_lists_no_status_verb(record_tree: Path) -> None:
    result = run_memento(record_tree, "measurement", "--help")
    assert "run, log, add, void, find, compare, quote" in " ".join(result.stdout.split())
    assert "status" not in result.stdout
