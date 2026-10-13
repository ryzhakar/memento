"""`memento directive/results/fact/string_recipes/spec` correct one living record in place, or refuse and land nothing."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from memento.living import DIRECTIVE_LABELS, DIRECTIVE_REQUIRED_LABELS, DIRECTIVE_WIDTH, RESULT_COLUMNS
from tests.conftest import FIXED_NOW, FIXTURES, build_record_tree, commit_all, git, replace_once, run_memento

RULE_YAML = "rule.yaml"
ABSENT = object()
ROW_YAML = "row.yaml"


def write_yaml(root: Path, name: str, data: dict) -> str:
    """Write a block-form YAML spec file under the tree and return its absolute path."""
    target = root / name
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(target)


def assert_refused(root: Path, message: str, *args: str) -> None:
    """Assert the command refuses with this message on stderr alone and leaves docs/ untouched."""
    before = (root / "docs/conventions.md").read_bytes()
    before_results = (root / "docs/results.md").read_bytes()
    result = run_memento(root, *args)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert (root / "docs/conventions.md").read_bytes() == before
    assert (root / "docs/results.md").read_bytes() == before_results


def assert_refused_beginning(root: Path, message_start: str, *args: str) -> None:
    """Assert the command refuses with a stderr message beginning this way and leaves docs/ untouched."""
    before = (root / "docs/conventions.md").read_bytes()
    before_results = (root / "docs/results.md").read_bytes()
    result = run_memento(root, *args)
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith(f"refused: {message_start}")
    assert (root / "docs/conventions.md").read_bytes() == before
    assert (root / "docs/results.md").read_bytes() == before_results


def test_directive_add_lands_a_new_block_at_the_end_of_its_heading_section(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {
            "id": "new-rule",
            "heading": "pipeline code",
            "when": "writing new code",
            "do": "follow the pattern",
            "never": None,
            "why": "consistency",
            "ground": "owner ruling 2026-08-04",
            "except": None,
        },
    )
    result = run_memento(record_tree, "directive", "add", rule)
    assert (result.exit_code, result.stdout) == (0, "docs/conventions.md: new-rule\n")
    text = (record_tree / "docs/conventions.md").read_text(encoding="utf-8")
    assert "## pipeline code\n\n### new-rule\nwhen    writing new code\ndo      follow the pattern\nwhy     consistency\nground  owner ruling 2026-08-04\n\n## measuring" in text
    assert run_memento(record_tree, "check").exit_code == 0


def test_directive_add_refuses_a_heading_the_schema_does_not_have(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {"id": "new-rule", "heading": "nonexistent heading", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"},
    )
    assert_refused(record_tree, "heading 'nonexistent heading' is not one of the schema's headings", "directive", "add", rule)


def test_directive_add_refuses_a_duplicate_id(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree, RULE_YAML, {"id": "measure-first", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"}
    )
    result = run_memento(record_tree, "directive", "add", rule)
    assert result.exit_code == 1
    assert "measure-first" in result.stderr
    assert "appears twice" in result.stderr


def test_directive_add_refuses_an_id_outside_the_pattern(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree, RULE_YAML, {"id": "Not Valid", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"}
    )
    result = run_memento(record_tree, "directive", "add", rule)
    assert result.exit_code == 1
    assert "Not Valid" in result.stderr
    assert "lowercase words joined by hyphens" in result.stderr


def test_directive_add_refuses_a_missing_required_label(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "x", "ground": "owner ruling 2026-08-04"})
    assert_refused(record_tree, "label 'do' is missing", "directive", "add", rule)


def test_directive_add_names_the_first_missing_label_in_the_schemas_order(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "do": "y"})
    assert_refused(record_tree, "label 'when' is missing", "directive", "add", rule)


def test_directive_add_refuses_a_blank_label_value_as_empty(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "   ", "do": "y", "ground": "owner ruling 2026-08-04"})
    assert_refused(record_tree, "label 'when' is empty", "directive", "add", rule)


def test_directive_add_refuses_an_empty_label_value_as_empty(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "", "do": "y", "ground": "owner ruling 2026-08-04"})
    assert_refused(record_tree, "label 'when' is empty", "directive", "add", rule)


def test_directive_add_refuses_a_label_value_spanning_two_lines(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "first\nsecond", "do": "y", "ground": "owner ruling 2026-08-04"}
    )
    assert_refused(record_tree, "label 'when' carries a line break", "directive", "add", rule)


def test_directive_add_names_the_label_not_the_field_when_refusing_an_empty_except(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04", "except": ""},
    )
    assert_refused(record_tree, "label 'except' is empty", "directive", "add", rule)


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("id", ABSENT, "is missing 'id'"),
        ("heading", ABSENT, "is missing 'heading'"),
        ("id", 5, "names a non-text id"),
        ("heading", 5, "names a non-text heading"),
    ],
)
def test_directive_add_refuses_an_id_or_heading_that_is_missing_or_not_text(record_tree: Path, field: str, value: object, problem: str) -> None:
    data = {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"}
    if value is ABSENT:
        del data[field]
    else:
        data[field] = value
    rule = write_yaml(record_tree, RULE_YAML, data)
    assert_refused(record_tree, f"'{rule}' {problem}", "directive", "add", rule)


def test_directive_add_help_names_the_file_and_identity_refusals(record_tree: Path) -> None:
    result = run_memento(record_tree, "directive", "add", "--help")
    help_text = " ".join(result.stdout.split())
    assert "a file that does not read, is not UTF-8, is not valid YAML or is not a mapping" in help_text
    assert "a key that is not text" in help_text
    assert "a missing or non-text id or heading" in help_text


def test_directive_add_refuses_a_label_the_rule_does_not_have(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04", "whn": "x"},
    )
    assert_refused(record_tree, "label 'whn' is not a rule label", "directive", "add", rule)


def test_directive_add_names_a_misspelt_label_before_the_label_it_leaves_missing(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "whn": "x", "do": "y", "ground": "owner ruling 2026-08-04"})
    assert_refused(record_tree, "label 'whn' is not a rule label", "directive", "add", rule)


@pytest.mark.parametrize(("label", "value"), [("when", 5), ("when", None), ("when", ["x"]), ("never", 5)])
def test_directive_add_refuses_a_label_value_that_is_not_text(record_tree: Path, label: str, value: object) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04", label: value},
    )
    assert_refused(record_tree, f"label '{label}' is not text", "directive", "add", rule)


def test_directive_add_help_names_the_label_value_refusal(record_tree: Path) -> None:
    result = run_memento(record_tree, "directive", "add", "--help")
    assert "a label value that is empty, blank or carries a line break" in " ".join(result.stdout.split())


def test_directive_add_help_names_the_unknown_label_and_non_text_refusals(record_tree: Path) -> None:
    result = run_memento(record_tree, "directive", "add", "--help")
    help_text = " ".join(result.stdout.split())
    assert "a label that is not a rule label" in help_text
    assert "a label value that is not text" in help_text


def test_directive_add_refuses_a_ground_naming_no_committed_path_or_ruling(record_tree: Path) -> None:
    rule = write_yaml(record_tree, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "nothing here"})
    result = run_memento(record_tree, "directive", "add", rule)
    assert result.exit_code == 1
    assert "FAIL" not in result.stdout
    assert "names no committed path" in result.stderr or "adds 1 FAIL" in result.stderr


def test_directive_replace_with_its_own_content_is_a_byte_no_op(record_tree: Path) -> None:
    before = (record_tree / "docs/conventions.md").read_bytes()
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {
            "id": "measure-first",
            "heading": "commit & merge",
            "when": "quoting a number",
            "do": "measure it first",
            "ground": "owner ruling 2026-08-04",
        },
    )
    result = run_memento(record_tree, "directive", "replace", rule)
    assert result.exit_code == 0
    assert (record_tree / "docs/conventions.md").read_bytes() == before


def test_directive_replace_moves_the_block_when_its_heading_changed(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree,
        RULE_YAML,
        {
            "id": "measure-first",
            "heading": "pipeline code",
            "when": "quoting a number",
            "do": "measure it first",
            "ground": "owner ruling 2026-08-04",
        },
    )
    result = run_memento(record_tree, "directive", "replace", rule)
    assert result.exit_code == 0
    text = (record_tree / "docs/conventions.md").read_text(encoding="utf-8")
    assert "## commit & merge\n\n## pipeline code\n\n### measure-first\n" in text
    assert run_memento(record_tree, "check").exit_code == 0


def test_directive_replace_refuses_an_absent_id(record_tree: Path) -> None:
    rule = write_yaml(
        record_tree, RULE_YAML, {"id": "no-such-rule", "heading": "commit & merge", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"}
    )
    assert_refused(record_tree, "rule 'no-such-rule' is not in docs/conventions.md", "directive", "replace", rule)


def test_directive_drop_deletes_the_block(record_tree: Path) -> None:
    result = run_memento(record_tree, "directive", "drop", "measure-first")
    assert (result.exit_code, result.stdout) == (0, "docs/conventions.md: measure-first\n")
    text = (record_tree / "docs/conventions.md").read_text(encoding="utf-8")
    assert "measure-first" not in text
    assert "## commit & merge\n\n## pipeline code" in text
    assert run_memento(record_tree, "check").exit_code == 0


def test_directive_drop_refuses_an_absent_id(record_tree: Path) -> None:
    assert_refused(record_tree, "rule 'no-such-rule' is not in docs/conventions.md", "directive", "drop", "no-such-rule")


def test_directive_drop_refuses_a_rule_still_cited(record_tree: Path) -> None:
    before = (record_tree / "docs/conventions.md").read_bytes()
    rule_citation = "conventions" + " rule " + "`measure-first`"
    (record_tree / "docs/capabilities.md").write_text(
        f"# Capabilities\n\n## Scoring\n\nThe scorer slices its input, per {rule_citation}.\n", encoding="utf-8"
    )
    git(record_tree, "add", "docs/capabilities.md")
    git(record_tree, "commit", "-q", "-m", "fixture cites the rule")
    result = run_memento(record_tree, "directive", "drop", "measure-first")
    assert result.exit_code == 1
    assert "measure-first" in result.stderr
    assert "does not exist" in result.stderr
    assert (record_tree / "docs/conventions.md").read_bytes() == before


def test_directive_labels_match_the_schemas(record_tree: Path) -> None:
    assert list(DIRECTIVE_LABELS) == ["when", "do", "never", "why", "ground", "except"]
    assert list(DIRECTIVE_REQUIRED_LABELS) == ["when", "do", "ground"]


def test_a_schema_dropping_a_directive_label_fails_the_structure_agreement(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "    - except\n", "")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "memento's DirectiveRule knows" in result.stdout
    assert "amend the module with the schema" in result.stdout


def test_a_rule_with_a_blank_label_value_fails_the_conventions(record_tree: Path) -> None:
    replace_once(record_tree, "docs/conventions.md", "when    quoting a number\n", "when        \n")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "FAIL  docs/conventions.md:5  measure-first: label 'when' is empty\nrecords: 1 FAIL, 0 WARN\n")


def test_a_rule_with_an_empty_label_value_fails_the_conventions(record_tree: Path) -> None:
    replace_once(record_tree, "docs/conventions.md", "when    quoting a number\n", "when    \n")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "FAIL  docs/conventions.md:5  measure-first: label 'when' is empty\nrecords: 1 FAIL, 0 WARN\n")


def test_the_sample_conventions_file_is_byte_equal_after_replacing_every_rule_with_itself(record_tree: Path) -> None:
    import os

    from memento import Environment
    from memento.git import git_exit_status
    from memento.living import block_span, directive_blocks, directive_replace, heading_of_block, labelled_lines
    from memento.store import Store

    real_text = (FIXTURES / "conventions.md").read_text(encoding="utf-8")
    (record_tree / "docs/conventions.md").write_text(real_text, encoding="utf-8")
    git(record_tree, "add", "docs/conventions.md")
    git(record_tree, "commit", "-q", "-m", "fixture sample conventions")
    environment = Environment(store=Store(root=record_tree, replace_file=os.replace), now=lambda: FIXED_NOW, checks=(), run_git=git_exit_status)
    _, blocks = directive_blocks(real_text)
    for _number, rule_id, rule_lines in blocks:
        body, problems = labelled_lines(rule_lines, DIRECTIVE_WIDTH)
        assert problems == []
        assert body is not None
        text = (record_tree / "docs/conventions.md").read_text(encoding="utf-8")
        start, _end = block_span(text, rule_id, "docs/conventions.md")
        heading = heading_of_block(text, start, "docs/conventions.md").strip().lower()
        rule_path = write_yaml(
            record_tree, RULE_YAML, {"id": rule_id, "heading": heading, **{label: value for label, (_line, value) in body.items()}}
        )
        directive_replace(environment, rule_path)
    assert (record_tree / "docs/conventions.md").read_text(encoding="utf-8") == real_text


def row_data(**overrides: object) -> dict:
    """A valid row.yaml mapping, overridden per test."""
    base = {
        "approach": "new approach",
        "world": "store.de × other.de",
        "labels": "verified",
        "folds": "3",
        "R@P95/fold": "10.0%",
        "P@R10/fold": "90.0%",
        "P@R30/fold": "80.0%",
        "P@R50/fold": "70.0%",
        "bar": "3 of 3",
        "run": "`run/id`",
        "verdict": "a new contender",
        "date": "2026-10-09",
    }
    return base | overrides


@pytest.mark.parametrize(
    ("group", "verb", "fields"),
    [
        ("directive", "add", {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"}),
        ("results", "add", row_data()),
        ("results", "replace", row_data(approach="contender", world="contender")),
    ],
)
def test_a_structured_input_file_holding_a_key_that_is_not_text_is_refused(record_tree: Path, group: str, verb: str, fields: dict) -> None:
    path = write_yaml(record_tree, "keyed.yaml", {1: "x", **fields})
    assert_refused(record_tree, f"'{path}' names a key that is not text: 1", group, verb, path)


def test_results_add_lands_a_new_row(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data())
    result = run_memento(record_tree, "results", "add", row)
    assert result.exit_code == 0
    text = (record_tree / "docs/results.md").read_text(encoding="utf-8")
    assert "| new approach | store.de × other.de |" in text
    assert run_memento(record_tree, "check").exit_code == 0


def test_results_add_refuses_an_existing_key(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(approach="contender", world="contender"))
    result = run_memento(record_tree, "results", "add", row)
    assert result.exit_code == 1
    assert "already appears" in result.stderr


def test_results_add_refuses_a_blank_cell_as_empty(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(verdict="   "))
    assert_refused(record_tree, "column 'verdict' is empty", "results", "add", row)


def test_results_add_refuses_an_empty_cell_as_empty(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(verdict=""))
    assert_refused(record_tree, "column 'verdict' is empty", "results", "add", row)


def test_results_add_refuses_a_cell_spanning_two_lines(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(verdict="first\nsecond"))
    assert_refused(record_tree, "column 'verdict' carries a line break", "results", "add", row)


def test_results_add_refuses_a_column_the_table_does_not_have(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(x="y"))
    assert_refused(record_tree, "column 'x' is not a results column", "results", "add", row)


def test_results_add_refuses_a_cell_that_is_not_text(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(folds=3))
    assert_refused(record_tree, "column 'folds' is not text", "results", "add", row)


def test_results_add_names_a_non_text_cell_by_its_schema_spelling(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(**{"R@P95/fold": 5}))
    assert_refused(record_tree, "column 'R@P95/fold' is not text", "results", "add", row)


def test_results_add_refuses_a_date_that_is_neither_text_nor_a_date(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(date=5))
    assert_refused(record_tree, "date cell '5' is not YYYY-MM-DD", "results", "add", row)


def test_results_add_refuses_a_missing_column_by_name(record_tree: Path) -> None:
    data = row_data()
    del data["verdict"]
    assert_refused(record_tree, "column 'verdict' is missing", "results", "add", write_yaml(record_tree, ROW_YAML, data))


def test_results_add_names_the_first_missing_column_by_its_schema_spelling(record_tree: Path) -> None:
    data = row_data()
    del data["P@R10/fold"], data["R@P95/fold"]
    assert_refused(record_tree, "column 'R@P95/fold' is missing", "results", "add", write_yaml(record_tree, ROW_YAML, data))


def test_results_add_names_the_column_not_the_field_when_refusing_an_empty_cell(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(**{"R@P95/fold": ""}))
    assert_refused(record_tree, "column 'R@P95/fold' is empty", "results", "add", row)


def test_results_add_refuses_a_file_that_is_not_utf8(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_bytes(b"approach: \xff\xfe\n")
    assert_refused(record_tree, f"'{row_path}' is not UTF-8", "results", "add", str(row_path))


def test_results_add_refuses_a_file_that_does_not_read(record_tree: Path) -> None:
    row_path = record_tree / "absent.yaml"
    assert_refused_beginning(record_tree, f"'{row_path}' does not read: ", "results", "add", str(row_path))


def test_results_add_refuses_a_file_that_is_not_valid_yaml(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text("approach: [unclosed\n", encoding="utf-8")
    assert_refused_beginning(record_tree, f"'{row_path}' is not valid YAML: ", "results", "add", str(row_path))


def test_results_add_refuses_a_file_that_is_not_a_mapping(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text("- approach\n- world\n", encoding="utf-8")
    assert_refused(record_tree, f"'{row_path}' is not a YAML mapping", "results", "add", str(row_path))


def test_results_add_help_names_the_file_and_column_refusals(record_tree: Path) -> None:
    result = run_memento(record_tree, "results", "add", "--help")
    help_text = " ".join(result.stdout.split())
    assert "a file that does not read, is not UTF-8, is not valid YAML or is not a mapping" in help_text
    assert "a key that is not text" in help_text
    assert "a column is missing" in help_text


def test_results_add_help_names_the_cell_refusals(record_tree: Path) -> None:
    result = run_memento(record_tree, "results", "add", "--help")
    assert "a cell that is empty, blank, holds '|' or carries a line break" in " ".join(result.stdout.split())


def test_results_add_help_names_the_unknown_column_and_non_text_refusals(record_tree: Path) -> None:
    result = run_memento(record_tree, "results", "add", "--help")
    help_text = " ".join(result.stdout.split())
    assert "a column that is not a results column" in help_text
    assert "a cell that is not text" in help_text


def test_results_add_refuses_a_cell_holding_a_pipe(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(verdict="bad | cell"))
    assert_refused(record_tree, "column 'verdict' holds '|', which breaks a table row", "results", "add", row)


def test_results_add_refuses_an_unreal_date(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text(yaml.safe_dump(row_data()).replace("2026-10-09", "2026-10-32"), encoding="utf-8")
    assert_refused(record_tree, "date cell '2026-10-32' is not a real date", "results", "add", str(row_path))


def test_results_add_lands_a_row_whose_date_is_an_unquoted_yaml_date(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text(yaml.safe_dump(row_data(date=date(2026, 10, 9))), encoding="utf-8")
    result = run_memento(record_tree, "results", "add", str(row_path))
    assert result.exit_code == 0
    assert (record_tree / "docs/results.md").read_text(encoding="utf-8").endswith("| a new contender | 2026-10-09 |\n")


def test_results_add_refuses_a_date_that_carries_a_time(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text(yaml.safe_dump(row_data(date=date(2026, 10, 9))).replace("2026-10-09", "2026-10-09 10:00:00"), encoding="utf-8")
    assert_refused(record_tree, "date cell '2026-10-09 10:00:00' is not YYYY-MM-DD", "results", "add", str(row_path))


def test_results_add_refuses_an_unquoted_unreal_date(record_tree: Path) -> None:
    row_path = record_tree / ROW_YAML
    row_path.write_text(yaml.safe_dump(row_data(date=date(2026, 10, 9))).replace("2026-10-09", "2026-10-32"), encoding="utf-8")
    assert_refused_beginning(record_tree, f"'{row_path}' is not valid YAML: '2026-10-32' is not a real date or time", "results", "add", str(row_path))


def test_results_replace_replaces_the_row_in_place(record_tree: Path) -> None:
    before = (record_tree / "docs/results.md").read_text(encoding="utf-8")
    row = write_yaml(record_tree, ROW_YAML, row_data(approach="contender", world="contender", verdict="an updated verdict"))
    result = run_memento(record_tree, "results", "replace", row)
    assert result.exit_code == 0
    after = (record_tree / "docs/results.md").read_text(encoding="utf-8")
    assert after != before
    assert after.count("\n| ") == before.count("\n| ")
    assert "an updated verdict" in after


def test_results_replace_refuses_an_absent_key(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data())
    assert_refused(record_tree, "no row in docs/results.md carries key ('new approach', 'store.de × other.de')", "results", "replace", row)


def test_results_drop_deletes_the_row(record_tree: Path) -> None:
    result = run_memento(record_tree, "results", "drop", "contender", "contender")
    assert result.exit_code == 0
    text = (record_tree / "docs/results.md").read_text(encoding="utf-8")
    assert "contender" not in text


def test_results_drop_refuses_no_such_row(record_tree: Path) -> None:
    assert_refused(record_tree, "no row in docs/results.md carries key ('nope', 'nowhere')", "results", "drop", "nope", "nowhere")


def test_a_schema_dropping_a_results_column_fails_the_structure_agreement(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "    - date\n", "")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "memento's ResultRow knows" in result.stdout
    assert "amend the module with the schema" in result.stdout


def test_two_rows_sharing_a_key_fail_the_results_table(record_tree: Path) -> None:
    text = (record_tree / "docs/results.md").read_text(encoding="utf-8")
    duplicated = text.rstrip("\n") + "\n" + text.splitlines()[-1] + "\n"
    (record_tree / "docs/results.md").write_text(duplicated, encoding="utf-8")
    git(record_tree, "add", "docs/results.md")
    git(record_tree, "commit", "-q", "-m", "fixture duplicate row")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "already appears" in result.stdout


def test_a_results_row_with_an_empty_cell_fails_the_results_table(record_tree: Path) -> None:
    replace_once(record_tree, "docs/results.md", "| contender | 2026-10-01 |", "|  | 2026-10-01 |")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "FAIL  docs/results.md:5  column 'verdict' is empty\nrecords: 1 FAIL, 0 WARN\n")


def test_a_results_row_with_an_unreal_date_fails_the_results_table(record_tree: Path) -> None:
    replace_once(record_tree, "docs/results.md", "2026-10-01", "2026-10-32")
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (1, "FAIL  docs/results.md:5  date cell '2026-10-32' is not a real date\nrecords: 1 FAIL, 0 WARN\n")


def test_results_columns_match_the_schema(record_tree: Path) -> None:
    assert list(RESULT_COLUMNS) == [
        "approach",
        "world",
        "labels",
        "folds",
        "R@P95/fold",
        "P@R10/fold",
        "P@R30/fold",
        "P@R50/fold",
        "bar",
        "run",
        "verdict",
        "date",
    ]


def test_the_sample_results_file_is_byte_equal_after_replacing_every_row_with_itself(record_tree: Path) -> None:
    import os

    from memento import Environment
    from memento.git import git_exit_status
    from memento.living import results_replace
    from memento.store import Store

    real_text = (FIXTURES / "results.md").read_text(encoding="utf-8")
    (record_tree / "docs/results.md").write_text(real_text, encoding="utf-8")
    git(record_tree, "add", "docs/results.md")
    git(record_tree, "commit", "-q", "-m", "fixture sample results")
    environment = Environment(store=Store(root=record_tree, replace_file=os.replace), now=lambda: FIXED_NOW, checks=(), run_git=git_exit_status)
    data_rows = [line for line in real_text.splitlines() if line.startswith("|")][2:]
    for line in data_rows:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        row_path = write_yaml(record_tree, ROW_YAML, dict(zip(RESULT_COLUMNS, cells, strict=True)))
        results_replace(environment, row_path)
    assert (record_tree / "docs/results.md").read_text(encoding="utf-8") == real_text


def test_fact_replace_lands_the_new_passage(record_tree: Path) -> None:
    result = run_memento(record_tree, "fact", "replace", "The scorer slices its input.", "The scorer slices its input in chunks.")
    assert (result.exit_code, result.stdout) == (0, "docs/capabilities.md\n")
    assert "in chunks" in (record_tree / "docs/capabilities.md").read_text(encoding="utf-8")


def test_fact_replace_refuses_zero_occurrences(record_tree: Path) -> None:
    assert_refused(
        record_tree,
        "'nothing like this' occurs 0 times in docs/capabilities.md; replace takes exactly one occurrence",
        "fact",
        "replace",
        "nothing like this",
        "x",
    )


def test_fact_replace_refuses_a_candidate_fail(record_tree: Path) -> None:
    result = run_memento(record_tree, "fact", "replace", "The scorer slices its input.", "The scorer slices its input, as docs/missing.md says.")
    assert result.exit_code == 1
    assert "does not exist" in result.stderr


def test_fact_replace_deleting_a_heading_another_record_points_at_refuses(record_tree: Path) -> None:
    (record_tree / "docs/conventions.md").write_text((FIXTURES / "conventions.md").read_text(encoding="utf-8"), encoding="utf-8")
    git(record_tree, "add", "docs/conventions.md")
    git(record_tree, "commit", "-q", "-m", "fixture sample conventions citing a capabilities heading")
    result = run_memento(record_tree, "fact", "replace", "## Scoring\n", "## Renamed Scoring\n")
    assert result.exit_code == 1
    assert "capabilities § Scoring" in result.stderr
    assert "names no heading" in result.stderr


def test_string_recipes_replace_lands_the_new_passage(record_tree: Path) -> None:
    result = run_memento(record_tree, "string_recipes", "replace", "# string_recipes\n", "# string_recipes\n\nMore text.\n")
    assert (result.exit_code, result.stdout) == (0, "docs/string_comparison_recipes.md\n")
    assert "More text." in (record_tree / "docs/string_comparison_recipes.md").read_text(encoding="utf-8")


def test_string_recipes_replace_refuses_several_occurrences(record_tree: Path) -> None:
    replace_once(record_tree, "docs/string_comparison_recipes.md", "# string_recipes\n", "# string_recipes\n# string_recipes\n")
    assert_refused(
        record_tree,
        "'# string_recipes\n' occurs 2 times in docs/string_comparison_recipes.md; replace takes exactly one occurrence",
        "string_recipes",
        "replace",
        "# string_recipes\n",
        "x",
    )


def test_spec_add_writes_the_drafts_bytes(record_tree: Path, tmp_path: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("# a new spec\n\nSome unbuilt design.\n", encoding="utf-8")
    result = run_memento(record_tree, "spec", "add", "a-new-spec", str(draft))
    assert (result.exit_code, result.stdout) == (0, "docs/specs/a-new-spec.md\n")
    assert (record_tree / "docs/specs/a-new-spec.md").read_text(encoding="utf-8") == draft.read_text(encoding="utf-8")
    assert run_memento(record_tree, "check").exit_code == 0


def test_spec_add_refuses_a_name_outside_the_pattern(record_tree: Path, tmp_path: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text("# x\n", encoding="utf-8")
    assert_refused(record_tree, "spec name 'Not Allowed' is not lowercase words joined by hyphens", "spec", "add", "Not Allowed", str(draft))


def test_spec_add_refuses_an_existing_name(record_tree: Path, tmp_path: Path) -> None:
    (record_tree / "docs/specs").mkdir(parents=True, exist_ok=True)
    (record_tree / "docs/specs/taken.md").write_text("# taken\n", encoding="utf-8")
    git(record_tree, "add", "docs/specs/taken.md")
    git(record_tree, "commit", "-q", "-m", "fixture spec")
    draft = tmp_path / "draft.md"
    draft.write_text("# x\n", encoding="utf-8")
    assert_refused(record_tree, "'docs/specs/taken.md' already exists", "spec", "add", "taken", str(draft))


def test_spec_replace_lands_the_new_passage(record_tree: Path) -> None:
    (record_tree / "docs/specs/one.md").write_text("# one\n\nOld text.\n", encoding="utf-8")
    git(record_tree, "add", "docs/specs/one.md")
    git(record_tree, "commit", "-q", "-m", "fixture spec")
    result = run_memento(record_tree, "spec", "replace", "one", "Old text.", "New text.")
    assert (result.exit_code, result.stdout) == (0, "docs/specs/one.md\n")
    assert "New text." in (record_tree / "docs/specs/one.md").read_text(encoding="utf-8")


def test_spec_replace_refuses_no_such_spec(record_tree: Path) -> None:
    assert_refused(record_tree, "'docs/specs/none.md' does not exist", "spec", "replace", "none", "x", "y")


def test_spec_archive_moves_the_file_whole(record_tree: Path) -> None:
    (record_tree / "docs/specs/one.md").write_text("# one\n\nSome text.\n", encoding="utf-8")
    git(record_tree, "add", "docs/specs/one.md")
    git(record_tree, "commit", "-q", "-m", "fixture spec")
    result = run_memento(record_tree, "spec", "archive", "one")
    archived = f"docs/orchestration_log/archive/spec-one-{FIXED_NOW.date().isoformat()}.md"
    assert (result.exit_code, result.stdout) == (0, f"docs/specs/one.md -> {archived}\n")
    assert not (record_tree / "docs/specs/one.md").exists()
    assert (record_tree / archived).read_text(encoding="utf-8") == "# one\n\nSome text.\n"
    commit_all(record_tree)
    assert run_memento(record_tree, "check").exit_code == 0


def test_spec_archive_refuses_no_such_spec(record_tree: Path) -> None:
    assert_refused(record_tree, "'docs/specs/none.md' does not exist", "spec", "archive", "none")


def test_spec_archive_refuses_an_existing_destination(record_tree: Path) -> None:
    (record_tree / "docs/specs/one.md").write_text("# one\n", encoding="utf-8")
    archived = f"docs/orchestration_log/archive/spec-one-{FIXED_NOW.date().isoformat()}.md"
    (record_tree / archived).parent.mkdir(parents=True, exist_ok=True)
    (record_tree / archived).write_text("# already there\n", encoding="utf-8")
    git(record_tree, "add", "docs/specs/one.md", archived)
    git(record_tree, "commit", "-q", "-m", "fixture spec and archive collision")
    assert_refused(record_tree, f"'{archived}' already exists", "spec", "archive", "one")


def test_check_map_refuses_a_path_that_only_starts_with_a_home(record_tree: Path) -> None:
    (record_tree / "docs/capabilities.md.bak").write_text("# stray backup\n", encoding="utf-8")
    git(record_tree, "add", "docs/capabilities.md.bak")
    git(record_tree, "commit", "-q", "-m", "fixture stray backup file")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "docs/capabilities.md.bak  not a record the map lists, a declared product, or a retired path" in result.stdout


RESULTS_FILE = "docs/results.md"
CONVENTIONS_FILE = "docs/conventions.md"
COLUMNS_NAMED = "approach, world, labels, folds, R@P95/fold, P@R10/fold, P@R30/fold, P@R50/fold, bar, run, verdict, date"
LABELS_NAMED = "when, do, never, why, ground, except"
CONTENDER_ROW = "| contender | contender | contender | contender | contender | contender | contender | contender | contender | contender | contender | 2026-10-01 |"


def sample_conventions(root: Path) -> str:
    """Commit the sample conventions file, three headings and five rules, into the tree and return its text."""
    text = (FIXTURES / "conventions.md").read_text(encoding="utf-8")
    (root / CONVENTIONS_FILE).write_text(text, encoding="utf-8")
    git(root, "add", CONVENTIONS_FILE)
    git(root, "commit", "-q", "-m", "fixture sample conventions")
    return text


@pytest.mark.parametrize(
    ("column", "value"),
    [("labels", "verified"), ("R@P95/fold", "0.21"), ("verdict", "leads"), ("date", "2026-10-09"), ("approach", "square adapter")],
)
def test_results_set_changes_one_column_of_the_row_in_place(record_tree: Path, column: str, value: str) -> None:
    before = (record_tree / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    result = run_memento(record_tree, "results", "set", "contender", "contender", column, value)
    cells = ["contender"] * 11 + ["2026-10-01"]
    cells[list(RESULT_COLUMNS).index(column)] = value
    key = (cells[0], cells[1])
    assert (result.exit_code, result.stdout) == (0, f"{RESULTS_FILE}: {key}\n")
    after = (record_tree / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    assert after[:-1] == before[:-1]
    assert after[-1] == "| " + " | ".join(cells) + " |"
    assert run_memento(record_tree, "check").exit_code == 0


@pytest.mark.parametrize(("column", "value"), [("verdict", "leads"), ("date", "2026-10-09"), ("labels", "verified")])
def test_results_set_writes_the_bytes_results_replace_writes_for_the_whole_row(record_tree: Path, tmp_path: Path, column: str, value: str) -> None:
    other = build_record_tree(tmp_path / "other")
    cells = ["contender"] * 11 + ["2026-10-01"]
    cells[list(RESULT_COLUMNS).index(column)] = value
    row = write_yaml(other, ROW_YAML, dict(zip(RESULT_COLUMNS, cells, strict=True)))
    assert run_memento(other, "results", "replace", row).exit_code == 0
    assert run_memento(record_tree, "results", "set", "contender", "contender", column, value).exit_code == 0
    assert (record_tree / RESULTS_FILE).read_bytes() == (other / RESULTS_FILE).read_bytes()


def test_setting_every_column_of_every_sample_row_to_its_own_value_leaves_the_file_byte_equal(record_tree: Path) -> None:
    real_text = (FIXTURES / "results.md").read_text(encoding="utf-8")
    (record_tree / RESULTS_FILE).write_text(real_text, encoding="utf-8")
    git(record_tree, "add", RESULTS_FILE)
    git(record_tree, "commit", "-q", "-m", "fixture sample results")
    for line in [line for line in real_text.splitlines() if line.startswith("|")][2:]:
        cells = dict(zip(RESULT_COLUMNS, [cell.strip() for cell in line.strip("|").split("|")], strict=True))
        for column, value in cells.items():
            result = run_memento(record_tree, "results", "set", cells["approach"], cells["world"], column, value)
            assert result.exit_code == 0, result.stderr
    assert (record_tree / RESULTS_FILE).read_text(encoding="utf-8") == real_text


def test_results_set_refuses_a_column_the_table_lacks_naming_the_columns(record_tree: Path) -> None:
    assert_refused(record_tree, f"'colour' is not a results column; the columns are: {COLUMNS_NAMED}", "results", "set", "contender", "contender", "colour", "x")


def test_results_set_refuses_a_key_no_row_holds(record_tree: Path) -> None:
    assert_refused(record_tree, "no row in docs/results.md carries key ('nope', 'nowhere')", "results", "set", "nope", "nowhere", "verdict", "x")


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("verdict", "", "column 'verdict' is empty"),
        ("verdict", "   ", "column 'verdict' is empty"),
        ("verdict", "two\nlines", "column 'verdict' carries a line break"),
        ("verdict", "bad | cell", "column 'verdict' holds '|', which breaks a table row"),
        ("date", "2026-10-32", "date cell '2026-10-32' is not a real date"),
        ("date", "yesterday", "date cell 'yesterday' is not YYYY-MM-DD"),
    ],
)
def test_results_set_refuses_a_value_its_column_refuses(record_tree: Path, column: str, value: str, message: str) -> None:
    assert_refused(record_tree, message, "results", "set", "contender", "contender", column, value)


def test_results_set_refuses_a_key_the_change_would_repeat(record_tree: Path) -> None:
    row = write_yaml(record_tree, ROW_YAML, row_data(approach="contender", world="store.de × other.de"))
    assert run_memento(record_tree, "results", "add", row).exit_code == 0
    before = (record_tree / RESULTS_FILE).read_bytes()
    result = run_memento(record_tree, "results", "set", "contender", "store.de × other.de", "world", "contender")
    assert result.exit_code == 1
    assert "refused: the change adds 1 FAIL" in result.stderr
    assert "already appears" in result.stderr
    assert (record_tree / RESULTS_FILE).read_bytes() == before


def test_results_set_refuses_a_row_whose_cells_do_not_match_the_columns(record_tree: Path) -> None:
    replace_once(record_tree, RESULTS_FILE, CONTENDER_ROW, "| contender | contender | 2026-10-01 |")
    assert_refused(record_tree, "the row in docs/results.md carrying key ('contender', 'contender') has 3 cells for 12 columns", "results", "set", "contender", "contender", "verdict", "x")


@pytest.mark.parametrize(
    ("label", "value", "expected_block"),
    [
        ("why", "a reason", "when    quoting a number\ndo      measure it first\nwhy     a reason\nground  owner ruling 2026-08-04"),
        ("do", "measure it twice", "when    quoting a number\ndo      measure it twice\nground  owner ruling 2026-08-04"),
        ("except", "a rehearsal", "when    quoting a number\ndo      measure it first\nground  owner ruling 2026-08-04\nexcept  a rehearsal"),
    ],
)
def test_directive_set_changes_one_label_of_the_rule_in_place_and_in_schema_order(record_tree: Path, label: str, value: str, expected_block: str) -> None:
    before = (record_tree / CONVENTIONS_FILE).read_text(encoding="utf-8")
    result = run_memento(record_tree, "directive", "set", "measure-first", label, value)
    assert (result.exit_code, result.stdout) == (0, f"{CONVENTIONS_FILE}: measure-first\n")
    after = (record_tree / CONVENTIONS_FILE).read_text(encoding="utf-8")
    old_block = "when    quoting a number\ndo      measure it first\nground  owner ruling 2026-08-04"
    assert after == before.replace(old_block, expected_block)
    assert run_memento(record_tree, "check").exit_code == 0


def test_directive_set_writes_the_bytes_directive_replace_writes_for_the_whole_rule(record_tree: Path, tmp_path: Path) -> None:
    sample_conventions(record_tree)
    other = build_record_tree(tmp_path / "other")
    sample_conventions(other)
    rule = write_yaml(
        other,
        RULE_YAML,
        {
            "id": "explicit-seeds",
            "heading": "pipeline code",
            "when": "drawing at random",
            "do": "take the seed from config",
            "never": "a module-level seed",
            "why": "a run must reproduce, whatever the machine",
            "ground": "capabilities § Scoring",
            "except": "a test that seeds by loop index",
        },
    )
    assert run_memento(other, "directive", "replace", rule).exit_code == 0
    result = run_memento(record_tree, "directive", "set", "explicit-seeds", "why", "a run must reproduce, whatever the machine")
    assert result.exit_code == 0
    assert (record_tree / CONVENTIONS_FILE).read_bytes() == (other / CONVENTIONS_FILE).read_bytes()


def test_setting_every_label_of_every_sample_rule_to_its_own_value_leaves_the_file_byte_equal(record_tree: Path) -> None:
    from memento.living import directive_blocks, labelled_lines

    real_text = sample_conventions(record_tree)
    _headings, blocks = directive_blocks(real_text)
    for _number, rule_id, rule_lines in blocks:
        body, problems = labelled_lines(rule_lines, DIRECTIVE_WIDTH)
        assert (body is None, problems) == (False, [])
        for label, (_line, value) in body.items():
            result = run_memento(record_tree, "directive", "set", rule_id, label, value)
            assert result.exit_code == 0, result.stderr
    assert (record_tree / CONVENTIONS_FILE).read_text(encoding="utf-8") == real_text


def test_directive_set_refuses_a_label_the_rule_model_lacks_naming_the_labels(record_tree: Path) -> None:
    assert_refused(record_tree, f"'colour' is not a directive label; the labels are: {LABELS_NAMED}", "directive", "set", "measure-first", "colour", "x")


def test_directive_set_refuses_an_absent_id(record_tree: Path) -> None:
    assert_refused(record_tree, "rule 'no-such-rule' is not in docs/conventions.md", "directive", "set", "no-such-rule", "do", "x")


@pytest.mark.parametrize(
    ("value", "message"),
    [("", "label 'do' is empty"), ("   ", "label 'do' is empty"), ("first\nsecond", "label 'do' carries a line break")],
)
def test_directive_set_refuses_a_value_the_label_refuses(record_tree: Path, value: str, message: str) -> None:
    assert_refused(record_tree, message, "directive", "set", "measure-first", "do", value)


def test_directive_set_refuses_a_ground_that_would_add_a_records_failure(record_tree: Path) -> None:
    before = (record_tree / CONVENTIONS_FILE).read_bytes()
    result = run_memento(record_tree, "directive", "set", "measure-first", "ground", "a hunch")
    assert (result.exit_code, result.stdout) == (1, "")
    assert "refused: the change adds 1 FAIL" in result.stderr
    assert (record_tree / CONVENTIONS_FILE).read_bytes() == before


def test_directive_set_refuses_a_rule_whose_block_does_not_parse(record_tree: Path) -> None:
    replace_once(record_tree, CONVENTIONS_FILE, "do      measure it first\n", "do measure it first\n")
    assert_refused(
        record_tree,
        "rule 'measure-first' does not parse: not a 'when/do/never/why/ground/except' line padded to 8; 'memento directive replace' writes it whole",
        "directive",
        "set",
        "measure-first",
        "why",
        "x",
    )
