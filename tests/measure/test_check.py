"""The measurement log's check fails a document that does not parse, is not canonical, breaks id order or targets, or names code git lacks."""

from pathlib import Path

from tests.memento.conftest import git, replace_once, run_memento
from tests.memento.measure.conftest import MEASUREMENT_LOG, MeasuredTree, add_entry, code, entry_spec, measurement_log, parsed_run, record_item, run_record

FIRST_START = "2026-10-01T09:00:00+02:00"
FIRST_END = "2026-10-01T09:00:58+02:00"
ABSENT_SHA = "f" * 40


def logged_entry(tree: MeasuredTree, name: str = "entry.yaml", **fields: object) -> None:
    """Add one entry from a parsed run at the old code."""
    item = record_item(tree, name.removesuffix(".yaml"), run_record(tree.old, parsed_run(tree, FIRST_START, FIRST_END)))
    result = add_entry(tree, entry_spec(tree, [item], **fields), name=name)
    assert result.exit_code == 0, result.stderr


def findings(root: Path) -> list[str]:
    """The check's finding lines, its summary dropped."""
    return run_memento(root, "check").stdout.splitlines()[:-1]


def test_a_log_reformatted_by_hand_fails_the_canonical_check(measured: MeasuredTree) -> None:
    logged_entry(measured)
    replace_once(measured.root, MEASUREMENT_LOG, "paths:\n- matchtuner/\n", "paths: [matchtuner/]\n")
    assert findings(measured.root) == [
        f"FAIL  {MEASUREMENT_LOG}:1  is not the block form memento writes; the log is written by 'just memento measurement add' and 'void' only"
    ]


def test_a_document_that_does_not_parse_fails_naming_what_it_lacks(measured: MeasuredTree) -> None:
    (measured.root / MEASUREMENT_LOG).write_text("---\nkind: void\nid: m-20261001-001\n", encoding="utf-8")
    [finding] = findings(measured.root)
    assert finding.startswith(f"FAIL  {MEASUREMENT_LOG}:1  does not parse as a measurement or a void: ")
    assert all(f"void.{field}: " in finding for field in ("recorded", "by", "target", "reason", "evidence"))


def test_text_before_the_first_document_fails_as_a_document_that_does_not_parse(measured: MeasuredTree) -> None:
    (measured.root / MEASUREMENT_LOG).write_text("# measurements\n", encoding="utf-8")
    [finding] = findings(measured.root)
    assert finding.startswith(f"FAIL  {MEASUREMENT_LOG}:1  does not parse as a measurement or a void: ")


def test_a_repeated_or_decreasing_id_fails(measured: MeasuredTree) -> None:
    logged_entry(measured)
    logged_entry(measured, "second.yaml")
    second_line = measurement_log(measured).splitlines().index("id: m-20261001-002") - 1
    replace_once(measured.root, MEASUREMENT_LOG, "id: m-20261001-002", "id: m-20261001-001")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:{second_line}  id m-20261001-001 repeats an earlier document's id"]
    log = measured.root / MEASUREMENT_LOG
    log.write_text(measurement_log(measured).replace("id: m-20261001-001", "id: m-20261002-001", 1), encoding="utf-8")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:{second_line}  id m-20261001-001 is not after the earlier id m-20261002-001"]


def test_targets_that_are_not_earlier_measurements_fail(measured: MeasuredTree) -> None:
    logged_entry(measured)
    logged_entry(measured, "second.yaml")
    replace_once(measured.root, MEASUREMENT_LOG, "supersedes: []\n---\n", "supersedes:\n- m-20261001-002\n---\n")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:1  supersedes m-20261001-002, which is not a measurement earlier in the log"]


def test_a_void_target_after_it_or_beyond_its_entrys_items_fails(measured: MeasuredTree) -> None:
    logged_entry(measured)
    run_memento(measured.root, "measurement", "void", "m-20261001-001#1", "--reason", "slept", "--evidence", measured.old)
    void_line = measurement_log(measured).splitlines().index("kind: void")
    replace_once(measured.root, MEASUREMENT_LOG, "target: m-20261001-001#1", "target: m-20261001-001#2")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:{void_line}  void target m-20261001-001#2 names item 2, but m-20261001-001 holds 1 items"]
    replace_once(measured.root, MEASUREMENT_LOG, "target: m-20261001-001#2", "target: m-20261001-003")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:{void_line}  void target m-20261001-003 is not a measurement earlier in the log"]
    replace_once(measured.root, MEASUREMENT_LOG, f"evidence: {measured.old}", f"evidence: {ABSENT_SHA}")
    assert findings(measured.root)[-1] == f"FAIL  {MEASUREMENT_LOG}:{void_line}  '{ABSENT_SHA}' is not a commit in this repository"


def test_code_git_does_not_hold_fails(measured: MeasuredTree) -> None:
    logged_entry(measured)
    replace_once(measured.root, MEASUREMENT_LOG, "- matchtuner/\n", "- matchtuner/selection.py\n")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:1  path 'matchtuner/selection.py' does not exist at {measured.old}"]
    replace_once(measured.root, MEASUREMENT_LOG, "- matchtuner/selection.py\n", "- matchtuner/\n")
    replace_once(measured.root, MEASUREMENT_LOG, "command: select", "command: run-audit")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:1  stage 'run-audit' is not a matchtuner command at {measured.old} (build-store, score-all, select)"]
    replace_once(measured.root, MEASUREMENT_LOG, "command: run-audit", "command: select")
    replace_once(measured.root, MEASUREMENT_LOG, f"sha: {measured.old}", f"sha: {ABSENT_SHA}")
    assert findings(measured.root) == [f"FAIL  {MEASUREMENT_LOG}:1  '{ABSENT_SHA}' is not a commit in this repository"]


def test_a_code_state_declaring_no_matchtuner_command_fails(measured: MeasuredTree) -> None:
    logged_entry(measured, design={"fixed": {"code": code(measured.old)}}, paths=["docs/"])
    first_commit = git(measured.root, "rev-list", "--max-parents=0", "HEAD").strip()
    replace_once(measured.root, MEASUREMENT_LOG, f"sha: {measured.old}", f"sha: {first_commit}")
    assert findings(measured.root) == [
        f"FAIL  {MEASUREMENT_LOG}:1  matchtuner/__main__.py at {first_commit} declares no command, so no stage can be checked there"
    ]
