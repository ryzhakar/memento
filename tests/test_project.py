"""The schema's `project:` block is where memento learns a project's specifics: absent or misshapen it is refused, and each key steers what it names."""

from pathlib import Path
from typing import Any

import yaml

from memento.project import (
    change_log_archive_path,
    change_log_mention,
    change_log_pointer,
    change_log_pointer_form,
    config_space,
    history_glob,
    journal_relative_prefixes,
    journal_space,
    record_stem,
    spec_archive_path,
    spec_path,
)
from tests.conftest import HISTORY, SCHEMA_PATH, commit_tree, run_memento

OTHER_HOMES: dict[str, Any] = {
    "kinds": {
        "report": {"home": "journal/past/{date}/"},
        "scratch": {"home": "journal/bench/{date}/"},
        "archived": {"home": "journal/old/"},
        "change": {"home": "notes/changes.md"},
        "spec": {"home": "plans/"},
        "directive": {"home": "rules.md"},
    }
}

CLOSED_DAY_EVENTS = f"{HISTORY}/2026-09-30/events.md"


def rewrite_project(root: Path, **changes: Any) -> None:
    """Set keys of the tree's project block, a value of None dropping the key, and commit the schema."""
    schema = yaml.safe_load((root / SCHEMA_PATH).read_text(encoding="utf-8"))
    schema["project"] = {key: value for key, value in {**schema["project"], **changes}.items() if value is not None}
    (root / SCHEMA_PATH).write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")
    commit_tree(root, "rewrite the project block")


def check_lines(root: Path) -> list[str]:
    """The finding lines `memento check` prints for a tree, its summary left out."""
    return run_memento(root, "check").stdout.splitlines()[:-1]


def test_a_schema_without_a_project_block_is_refused(record_tree: Path) -> None:
    schema = yaml.safe_load((record_tree / SCHEMA_PATH).read_text(encoding="utf-8"))
    del schema["project"]
    (record_tree / SCHEMA_PATH).write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "refused: .claude/memento.yaml declares no 'project:' block\n")


def test_a_project_key_memento_does_not_know_is_refused(record_tree: Path) -> None:
    rewrite_project(record_tree, unexpected="x")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == "refused: .claude/memento.yaml 'project:' block does not fit: unexpected: Extra inputs are not permitted\n"


def test_a_missing_project_key_is_refused_by_name(record_tree: Path) -> None:
    rewrite_project(record_tree, main_branch=None)
    result = run_memento(record_tree, "check")
    assert result.stderr == "refused: .claude/memento.yaml 'project:' block does not fit: main_branch: Field required\n"


def test_a_ratification_date_that_is_no_date_is_refused(record_tree: Path) -> None:
    rewrite_project(record_tree, ratified_on="the autumn")
    assert run_memento(record_tree, "check").stderr.startswith("refused: .claude/memento.yaml 'project:' block does not fit: ratified_on:")


def test_the_ratification_date_sets_which_journal_files_the_current_shapes_govern(record_tree: Path) -> None:
    (record_tree / CLOSED_DAY_EVENTS).write_text("garbage\n", encoding="utf-8")
    commit_tree(record_tree, "break the closed span's first line")
    assert any(line.startswith(f"FAIL  {CLOSED_DAY_EVENTS}:1") for line in check_lines(record_tree))
    rewrite_project(record_tree, ratified_on="2026-09-30")
    assert [line for line in check_lines(record_tree) if CLOSED_DAY_EVENTS in line] == []


def test_the_record_space_sets_where_an_uncovered_file_is_a_finding(record_tree: Path) -> None:
    (record_tree / "docs/stray.md").write_text("# stray\n", encoding="utf-8")
    commit_tree(record_tree, "a stray file")
    assert check_lines(record_tree) == ["FAIL  docs/stray.md  not a record the map lists, a declared product, or a retired path"]
    rewrite_project(record_tree, record_space="notes/")
    assert check_lines(record_tree) == []


def test_the_code_roots_set_where_a_rule_citation_is_checked(record_tree: Path) -> None:
    (record_tree / "src").mkdir()
    (record_tree / "src/cite.py").write_text('RULE = "conventions rule `no-such-rule`"\n', encoding="utf-8")
    commit_tree(record_tree, "a citing module")
    assert check_lines(record_tree) == []
    rewrite_project(record_tree, code_roots=["src/"])
    assert check_lines(record_tree) == ["FAIL  src/cite.py:1  cites conventions rule 'no-such-rule', which does not exist"]


def test_the_code_roots_set_which_directories_a_record_may_point_into(record_tree: Path) -> None:
    (record_tree / "docs/capabilities.md").write_text("# Capabilities\n\nThe scorer lives in `src/score.py`.\n", encoding="utf-8")
    commit_tree(record_tree, "a pointer into src")
    assert check_lines(record_tree) == []
    rewrite_project(record_tree, code_roots=["src/"])
    assert check_lines(record_tree) == ["FAIL  docs/capabilities.md:3  points at 'src/score.py', which does not exist"]


def test_the_ratification_date_sets_when_history_stops_admitting_other_artifacts(record_tree: Path) -> None:
    notes = f"{HISTORY}/2026-09-30/notes.md"
    (record_tree / notes).write_text("# notes\n", encoding="utf-8")
    commit_tree(record_tree, "a report after ratification")
    assert check_lines(record_tree) == [f"FAIL  {notes}  history/ admits only session*, events* and failures.md after 2026-09-23; other artifacts go to recon/"]
    rewrite_project(record_tree, ratified_on="2026-09-30")
    assert check_lines(record_tree) == []


def test_paths_the_homes_imply_follow_the_homes_a_schema_declares() -> None:
    assert (history_glob(OTHER_HOMES), journal_space(OTHER_HOMES), journal_relative_prefixes(OTHER_HOMES)) == ("journal/past/*", "journal/", ("past/", "bench/"))
    assert config_space(OTHER_HOMES) == ".claude/"
    assert change_log_archive_path(OTHER_HOMES, "2026-01-01", "2026-02-02") == "journal/old/changes-2026-01-01--2026-02-02.md"
    assert (spec_path(OTHER_HOMES, "scoring"), spec_archive_path(OTHER_HOMES, "scoring", "2026-03-03")) == ("plans/scoring.md", "journal/old/spec-scoring-2026-03-03.md")
    assert (record_stem(OTHER_HOMES, "directive"), change_log_mention(OTHER_HOMES)) == ("rules", "changes.md")
    assert change_log_pointer_form(OTHER_HOMES) == "decided YYYY-MM-DD, see notes/changes.md"
    assert change_log_pointer(OTHER_HOMES).findall("decided 2026-04-04, see notes/changes.md; decided 2026-05-05, see docs/architecture_log.md") == ["2026-04-04"]
