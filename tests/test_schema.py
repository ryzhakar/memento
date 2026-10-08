"""`kind_for` resolves a path to the kind whose home covers it, by the schema's home grammar."""

import pytest

from memento.schema import kind_for
from tests.memento.conftest import fixture_schema

HISTORY = "docs/orchestration_log/history"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("docs/capabilities.md", "fact"),
        ("CLAUDE.md", "charter"),
        (".claude/memento.yaml", "schema"),
        (f"{HISTORY}/2026-10-08/events.md", "trace"),
        (f"{HISTORY}/2026-10-08/events-parallel.md", "trace"),
        (f"{HISTORY}/2026-10-08/session.md", "digest"),
        (f"{HISTORY}/2026-10-08/failures.md", "failure"),
        (f"{HISTORY}/2026-09-06/frontier-questions-and-verdicts.md", "report"),
        (f"{HISTORY}/2026-09-06/nested/notes.md", "report"),
        ("docs/orchestration_log/recon/2026-10-08/specs/memento-module.md", "scratch"),
        ("docs/reference/collection-item-schema.md", "reference"),
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
        "matchtuner/score.py",
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
