"""The log's documents hold one block form, carry no floats, and keep raw instrument text verbatim."""

import yaml

from tests.memento.conftest import run_memento
from tests.memento.measure.conftest import MEASUREMENT_LOG, MeasuredTree, add_entry, entry_spec, measurement_log, parsed_run, record_item, run_record

SPEC_EXAMPLE_VOID = """---
kind: void
id: m-20261009-003
recorded: '2026-10-09T13:14:00+03:00'
by: self
target: m-20261009-001#1
reason: overlapped G2's evaluate (PID 75636)
evidence: docs/orchestration_log/recon/2026-10-07/reports/implementer-panel-scorer-budget.md:80
"""
START = "2026-10-01T09:00:00+02:00"
END = "2026-10-01T09:00:58+02:00"


def test_the_specs_example_void_is_already_in_the_one_block_form(measured: MeasuredTree) -> None:
    (measured.root / MEASUREMENT_LOG).write_text(SPEC_EXAMPLE_VOID, encoding="utf-8")
    assert run_memento(measured.root, "check").stdout.splitlines() == [
        f"FAIL  {MEASUREMENT_LOG}:1  void target m-20261009-001#1 is not a measurement earlier in the log",
        "records: 1 FAIL, 0 WARN",
    ]


def test_raw_text_with_indented_and_trailing_spaces_survives_the_block_form_verbatim(measured: MeasuredTree) -> None:
    raw = "       57.78 real       201.61 user  \n\t18791546880  maximum resident set size\n"
    record = run_record(measured.old, parsed_run(measured, START, END, raw=raw))
    assert add_entry(measured, entry_spec(measured, [record_item(measured, "run", record)])).exit_code == 0
    assert run_memento(measured.root, "check").stdout == "records: 0 FAIL, 0 WARN\n"
    assert raw in first_raw_text(measured)


def first_raw_text(tree: MeasuredTree) -> str:
    """The raw text of the log's first item, as YAML reads it back."""
    return yaml.safe_load(measurement_log(tree).removeprefix("---\n"))["items"][0]["raw"]


def test_a_float_or_an_offsetless_time_in_a_run_record_is_refused(measured: MeasuredTree) -> None:
    for fields, message in [
        ({"readings": {"wall": {"ns": 57.78, "resolution_ns": 1}}}, "item 1: item.run.readings.wall.duration.ns:"),
        ({"started": "2026-10-01T09:00:00"}, "item 1: item.run.started:"),
    ]:
        record = run_record(measured.old, parsed_run(measured, START, END) | fields)
        result = add_entry(measured, entry_spec(measured, [record_item(measured, "run", record)]))
        assert result.exit_code == 1
        assert result.stderr.startswith(f"refused: {message}")


def test_a_run_whose_log_records_no_exit_status_is_stored_with_its_exit_unknown_and_the_reason(measured: MeasuredTree) -> None:
    unrecorded = {"unknown": {"why": "a time -l block prints no exit status"}}
    record = run_record(measured.old, parsed_run(measured, START, END, exit=unrecorded))
    assert add_entry(measured, entry_spec(measured, [record_item(measured, "run", record)])).exit_code == 0
    assert yaml.safe_load(measurement_log(measured).removeprefix("---\n"))["items"][0]["exit"] == unrecorded
    assert run_memento(measured.root, "check").stdout == "records: 0 FAIL, 0 WARN\n"
