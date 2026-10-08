"""`memento check` reports each record defect the ported checks find, one tree per defect."""

import ast
from functools import partial
from pathlib import Path

from memento.__main__ import CHECKS
from memento.findings import Check
from tests.memento.conftest import HISTORY, PROJECT_ROOT, fixture_schema, git, replace_once, run_memento

CLEAN_SUMMARY = "records: 0 FAIL, 0 WARN\n"
CLOSED_SPAN = f"{HISTORY}/2026-09-30"
OPEN_SPAN = f"{HISTORY}/2026-10-01"


def assert_single_failure(root: Path, finding: str) -> None:
    """Assert the check reports exactly one finding, this one, and exits 1."""
    result = run_memento(root, "check")
    assert (result.exit_code, result.stdout) == (1, f"{finding}\nrecords: 1 FAIL, 0 WARN\n")


def test_a_valid_tree_reports_no_finding_and_exits_zero(record_tree: Path) -> None:
    result = run_memento(record_tree, "check")
    assert (result.exit_code, result.stdout) == (0, CLEAN_SUMMARY)


def test_a_goal_missing_a_field_fails_the_charter(record_tree: Path) -> None:
    replace_once(record_tree, "CLAUDE.md", "- constraints: the constraints of the goal\n", "")
    assert_single_failure(record_tree, "FAIL  CLAUDE.md:13  goal lacks constraints")


def test_a_map_row_disagreeing_on_load_fails_the_map(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento-map.md", "| fact | need |", "| fact | start |")
    assert_single_failure(record_tree, "FAIL  .claude/memento-map.md:11  load 'start' disagrees with the schema's 'need' for fact")


def test_a_mispadded_label_fails_the_change_log(record_tree: Path) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "WHY:           ", "WHY:          ")
    assert_single_failure(record_tree, "FAIL  docs/architecture_log.md:6  WHY label is not padded to column 15")


def test_an_events_line_going_back_in_time_fails_the_events_file(record_tree: Path) -> None:
    replace_once(record_tree, f"{OPEN_SPAN}/events.md", "2026-10-01T09:10", "2026-10-01T08:59")
    assert_single_failure(
        record_tree, f"FAIL  {OPEN_SPAN}/events.md:2  time 2026-10-01T08:59 goes back from 2026-10-01T09:00"
    )


def test_a_state_over_its_line_limit_fails_the_close_block(record_tree: Path) -> None:
    continuation = "        and more\n"
    replace_once(record_tree, f"{CLOSED_SPAN}/session.md", "state   the fixture stands\n", "state   the fixture stands\n" + continuation * 3)
    assert_single_failure(record_tree, f"FAIL  {CLOSED_SPAN}/session.md:5  state runs 4 lines, over 3")


def test_a_rule_without_its_do_line_fails_the_conventions(record_tree: Path) -> None:
    replace_once(record_tree, "docs/conventions.md", "do      measure it first\n", "")
    assert_single_failure(record_tree, "FAIL  docs/conventions.md:5  measure-first: missing do")


def test_a_bare_change_log_citation_fails_the_fact_record(record_tree: Path) -> None:
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input; see docs/architecture_log.md.")
    assert_single_failure(
        record_tree,
        "FAIL  docs/capabilities.md:5  cites the change log other than as 'decided YYYY-MM-DD, see docs/architecture_log.md'",
    )


def test_hard_wrapped_prose_fails_the_frame(record_tree: Path) -> None:
    replace_once(record_tree, "docs/ground-truth.md", "# frame\n", "# frame\n\nOne sentence\ncontinues here.\n")
    assert_single_failure(
        record_tree, "FAIL  docs/ground-truth.md:4  prose continues a line above; authored prose carries no hard wrap"
    )


def test_an_undated_results_row_fails_the_results_table(record_tree: Path) -> None:
    replace_once(record_tree, "docs/results.md", "2026-10-01", "October")
    assert_single_failure(record_tree, "FAIL  docs/results.md:5  date cell 'October' is not YYYY-MM-DD")


def test_a_failure_entry_missing_its_mechanism_fails_the_failure_record(record_tree: Path) -> None:
    replace_once(record_tree, f"{CLOSED_SPAN}/failures.md", "Mechanism:", "Mechanics:")
    assert_single_failure(
        record_tree,
        f"FAIL  {CLOSED_SPAN}/failures.md:3  paragraphs open ['What happened', 'Correction'], "
        "not ['What happened', 'Mechanism', 'Correction']",
    )


def test_a_span_close_without_its_close_block_fails_the_events_file(record_tree: Path) -> None:
    with (record_tree / CLOSED_SPAN / "events.md").open("a", encoding="utf-8") as events_file:
        events_file.write("2026-09-30T18:10 | self | span-event | close; again\n")
    assert_single_failure(
        record_tree, f"FAIL  {CLOSED_SPAN}/events.md  2 span closes but 1 close blocks in {CLOSED_SPAN}/session.md"
    )


def test_a_pointer_at_a_missing_file_fails_the_pointing_record(record_tree: Path) -> None:
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input, as docs/missing.md says.")
    assert_single_failure(record_tree, "FAIL  docs/capabilities.md:5  points at 'docs/missing.md', which does not exist")


def test_a_record_over_its_budget_fails_the_budget(record_tree: Path) -> None:
    schema = fixture_schema()
    budget, per_word = schema["kinds"]["frame"]["budget_tokens"], schema["token_estimate"]["tokens_per_word"]
    body_words = round(budget / per_word)
    replace_once(record_tree, "docs/ground-truth.md", "# frame\n", "# frame\n\n" + " ".join(["word"] * body_words) + "\n")
    estimate = round((body_words + 2) * per_word)
    assert_single_failure(record_tree, f"FAIL  docs/ground-truth.md  ~{estimate} tokens, over the frame budget of {budget}")


def test_a_design_file_in_history_fails_history_admission(record_tree: Path) -> None:
    (record_tree / OPEN_SPAN / "design.md").write_text("# design\n", encoding="utf-8")
    git(record_tree, "add", f"{OPEN_SPAN}/design.md")
    assert_single_failure(
        record_tree,
        f"FAIL  {OPEN_SPAN}/design.md  history/ admits only session*, events* and failures.md after 2026-09-23; "
        "other artifacts go to recon/",
    )


def test_a_nul_byte_in_a_record_fails_the_record(record_tree: Path) -> None:
    replace_once(record_tree, "docs/label-data-defects.md", "# label_defects\n", "# label_defects\x00\n")
    assert_single_failure(record_tree, "FAIL  docs/label-data-defects.md  contains NUL bytes")


def test_a_schema_dropping_an_event_kind_fails_the_structure_agreement(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "  attenuation: events\n", "")
    declared = {
        "decision": "events",
        "discovery": "events",
        "commitment": "events",
        "delegation": "events",
        "receipt": "events",
        "span-event": "events",
        "failure": "failure",
        "change": "change",
    }
    known = {
        "decision": "events",
        "discovery": "events",
        "commitment": "events",
        "delegation": "events",
        "receipt": "events",
        "attenuation": "events",
        "span-event": "events",
        "failure": "failure",
        "change": "change",
    }
    assert_single_failure(
        record_tree,
        f"FAIL  .claude/memento.yaml  schema declares events {declared}; memento knows {known}; "
        "amend the module with the schema",
    )


def test_a_group_registered_for_an_owner_only_kind_fails_the_structure_agreement(record_tree: Path) -> None:
    replace_once(
        record_tree,
        ".claude/memento.yaml",
        "home: docs/capabilities.md\n    tier: dossier\n    load: need\n    verification: measured\n",
        "home: docs/capabilities.md\n    tier: dossier\n    load: need\n    verification: owner-stated\n",
    )
    assert_single_failure(
        record_tree,
        "FAIL  .claude/memento.yaml  schema marks fact owner-stated; memento registers a 'fact' group; "
        "amend the module with the schema",
    )


def test_the_check_command_runs_the_checks_its_environment_carries(record_tree: Path) -> None:
    replace_once(record_tree, "docs/results.md", "2026-10-01", "October")
    result = run_memento(record_tree, "check", checks=())
    assert (result.exit_code, result.stdout) == (0, CLEAN_SUMMARY)


def registered_name(check: Check) -> str:
    """The module-qualified name of a registered check, looking through a partial binding."""
    function = check.func if isinstance(check, partial) else check
    return f"{function.__module__}.{function.__name__}"


def defined_check_names() -> set[str]:
    """Every top-level `check_*` function defined in a module under memento/."""
    names = set()
    for path in sorted((PROJECT_ROOT / "memento").rglob("*.py")):
        module = ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts).removesuffix(".__init__")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {f"{module}.{node.name}" for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("check_")}
    return names


def test_every_check_defined_under_memento_is_in_the_check_tuple() -> None:
    assert defined_check_names() == {registered_name(check) for check in CHECKS}
