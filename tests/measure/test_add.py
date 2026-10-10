"""`memento measurement add` appends one validated entry to the measurement log and prints its id, or refuses and lands nothing."""

import json
from collections.abc import Iterator
from typing import Any

import yaml

from memento.__main__ import CHECKS
from memento.findings import Corpus, Finding, fail
from tests.memento.conftest import commit_all, git, replace_once, run_memento
from tests.memento.measure.conftest import (
    MEASUREMENT_LOG,
    REPORT_RAW,
    SPEC_DIRECTORY,
    TIME_RAW,
    MeasuredTree,
    add_entry,
    code,
    entry_spec,
    measurement_log,
    parsed_run,
    record_item,
    run_record,
    sha256_of,
    transcribed_run,
    write_yaml,
)

EARLY_START = "2026-10-01T09:00:00+02:00"
EARLY_END = "2026-10-01T09:00:58+02:00"


def test_an_entry_from_one_run_record_lands_as_one_document_and_prints_its_id(measured: MeasuredTree) -> None:
    record = run_record(measured.old, parsed_run(measured, EARLY_START, EARLY_END))
    result = add_entry(measured, entry_spec(measured, [record_item(measured, "run", record)]))
    assert (result.exit_code, result.stdout, result.stderr) == (0, f"{MEASUREMENT_LOG}: m-20261001-001\n", "")
    document = yaml.safe_load(measurement_log(measured).removeprefix("---\n"))
    assert (document["kind"], document["id"], document["by"]) == ("measurement", "m-20261001-001", "self")
    assert document["machine"] == record["machine"] and document["env"] == record["env"]
    assert document["items"][0]["readings"]["max_rss"] == {"bytes": 18791546880, "resolution_bytes": 1}
    assert "code" not in document["items"][0]
    assert run_memento(measured.root, "check").stdout == "records: 0 FAIL, 0 WARN\n"


LATE_START = "2026-10-01T09:05:00+02:00"
LATE_END = "2026-10-01T09:06:00+02:00"


def parsed_item(tree: MeasuredTree, sha: str, started: str = EARLY_START, ended: str = EARLY_END, name: str = "run", **fields: Any) -> dict[str, str]:
    """An entry-spec item naming a parsed run record at a sha."""
    return record_item(tree, name, run_record(sha, parsed_run(tree, started, ended, **fields)))


def transcribed_item(tree: MeasuredTree, started: str = EARLY_START, ended: str = EARLY_END, **fields: Any) -> dict[str, Any]:
    """An inline transcribed item at the old code."""
    return {"transcribed": run_record(tree.old, transcribed_run(tree, started, ended, **fields))}


def logged_document(tree: MeasuredTree, index: int = 0) -> dict[str, Any]:
    """One document of the measurement log, parsed."""
    return list(yaml.safe_load_all(measurement_log(tree)))[index]


def assert_refused(tree: MeasuredTree, spec: dict[str, Any], message: str) -> None:
    """Assert `measurement add` refuses with this message on stderr alone and leaves the log's bytes untouched."""
    before = (tree.root / MEASUREMENT_LOG).read_bytes()
    result = add_entry(tree, spec)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert (tree.root / MEASUREMENT_LOG).read_bytes() == before


def test_a_transcribed_reading_is_stored_in_base_units_at_the_resolution_of_its_last_printed_digit(measured: MeasuredTree) -> None:
    result = add_entry(measured, entry_spec(measured, [transcribed_item(measured)]))
    assert result.exit_code == 0, result.stderr
    readings = logged_document(measured)["items"][0]["readings"]
    assert readings == {
        "wall": {"ns": 75600000000, "resolution_ns": 10000000},
        "max_rss": {"bytes": 17270000000, "resolution_bytes": 10000000},
    }


def test_a_binary_unit_reading_rounds_to_whole_bytes_and_never_claims_a_finer_resolution(measured: MeasuredTree) -> None:
    raw = "peak 16.99 GiB\n"
    (measured.scratch / "report.md").write_text(raw, encoding="utf-8")
    item = transcribed_item(measured, raw=raw, readings={"max_rss": "16.99 GiB"})
    item["transcribed"]["item"]["provenance"] = {"transcribed": {"source": str(measured.scratch / "report.md"), "line": 1}}
    item["transcribed"]["item"]["evidence"]["sha256"] = sha256_of(measured.scratch / "report.md")
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0
    assert logged_document(measured)["items"][0]["readings"]["max_rss"] == {"bytes": 18242873590, "resolution_bytes": 10737419}


def test_a_memory_text_with_no_stated_unit_is_kept_as_its_text(measured: MeasuredTree) -> None:
    item = transcribed_item(measured, readings={"max_rss": {"unit_ambiguous": "17.27 GB"}})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0
    assert logged_document(measured)["items"][0]["readings"]["max_rss"] == {"unit_ambiguous": "17.27 GB"}


def test_each_entry_takes_the_id_one_past_the_days_highest(measured: MeasuredTree) -> None:
    for name in ("first.yaml", "second.yaml"):
        add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]), name=name)
    assert [document["id"] for document in yaml.safe_load_all(measurement_log(measured))] == ["m-20261001-001", "m-20261001-002"]


def test_an_old_against_new_entry_carries_one_level_per_code_state(measured: MeasuredTree) -> None:
    design = {"varies_code": {"levels": {"old": code(measured.old), "new": code(measured.new)}}}
    items = [
        parsed_item(measured, measured.old, name="old", level="old"),
        parsed_item(measured, measured.new, LATE_START, LATE_END, name="new", level="new"),
    ]
    assert add_entry(measured, entry_spec(measured, items, design=design)).exit_code == 0
    assert [item["level"] for item in logged_document(measured)["items"]] == ["old", "new"]


def report_lines(row_line: int, total: int) -> str:
    """A report holding the transcribed table row at one line among filler lines."""
    lines = [f"filler {number}" for number in range(1, total + 1)]
    lines[row_line - 1] = REPORT_RAW.rstrip("\n")
    return "\n".join(lines) + "\n"


def transcribed_refusals(tree: MeasuredTree) -> list[tuple[dict[str, Any], str]]:
    """Inline transcribed items each breaking one transcription rule, with the refusal each earns."""
    return [
        (transcribed_item(tree, readings={"max_rss": "17.27"}), "item 1: max_rss reading '17.27' carries no unit; write '<number> <unit>' with one of B, kB, MB, GB, KiB, MiB, GiB (kB = 10^3 B, GB = 10^9 B, KiB = 2^10 B, GiB = 2^30 B)"),
        (transcribed_item(tree, readings={"max_rss": "17.27 GiB"}), "item 1: max_rss reading '17.27 GiB' declares GiB; its raw text does not contain 'GiB'"),
        (transcribed_item(tree, readings={"max_rss": "17.27 gigs"}), "item 1: max_rss reading '17.27 gigs' names unit 'gigs', not one of B, kB, MB, GB, KiB, MiB, GiB (kB = 10^3 B, GB = 10^9 B, KiB = 2^10 B, GiB = 2^30 B)"),
        (transcribed_item(tree, readings={"max_rss": "17.270 GB"}), "item 1: max_rss reading '17.270 GB' has a finer resolution than its raw text, which prints 17.27"),
        (transcribed_item(tree, readings={"max_rss": "17.28 GB"}), "item 1: max_rss reading '17.28 GB': its raw text prints no number equal to 17.28"),
        (transcribed_item(tree, readings={"max_rss": "about 17 GB"}), "item 1: max_rss reading 'about 17 GB' is not '<number> <unit>'"),
        (transcribed_item(tree, readings={"wall": "75.60 s", "max_rss": {"unit_ambiguous": "17.3 GB"}}), "item 1: max_rss reading '17.3 GB' is not in its raw text"),
        (transcribed_item(tree, readings={"wall": {"unit_ambiguous": "75.60 s"}}), "items.0.transcribed.item.run: metric 'wall' is duration, but its reading is UnitAmbiguous"),
        (transcribed_item(tree, readings={"wall": "75.60 ns"}, raw="| 1 | 75.60 ns |\n"), "item 1: wall reading '75.60 ns' is finer than one nanosecond"),
    ]


def test_each_transcription_rule_refuses_the_item_breaking_it(measured: MeasuredTree) -> None:
    for item, message in transcribed_refusals(measured):
        assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_transcribed_raw_text_far_from_its_cited_line_is_refused(measured: MeasuredTree) -> None:
    report = measured.scratch / "report.md"
    report.write_text(report_lines(5, 100), encoding="utf-8")
    item = transcribed_item(measured, provenance={"transcribed": {"source": str(report), "line": 46}})
    item["transcribed"]["item"]["evidence"]["sha256"] = sha256_of(report)
    message = f"item 1: raw line '{REPORT_RAW.strip()}' is not in {report} within 40 lines of line 46"
    assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_transcribed_raw_text_within_forty_lines_of_its_cited_line_lands(measured: MeasuredTree) -> None:
    report = measured.scratch / "report.md"
    report.write_text(report_lines(5, 100), encoding="utf-8")
    item = transcribed_item(measured, provenance={"transcribed": {"source": str(report), "line": 45}})
    item["transcribed"]["item"]["evidence"]["sha256"] = sha256_of(report)
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def run_rule_refusals(tree: MeasuredTree) -> list[tuple[dict[str, Any], str]]:
    """Run records each breaking one rule of a run, with the refusal each earns."""
    record = "item 1: item.run"
    return [
        (
            {"contention": {"declared_solo": {"how_checked": "ps", "neighbours": ["pytest"]}}},
            f"{record}.contention.declared_solo: declared_solo takes no neighbours, yet names ['pytest']; a run with a neighbour is declared_beside",
        ),
        (
            {"contention": {"declared_beside": {"neighbours": []}}},
            f"{record}.contention.declared_beside: declared_beside names no neighbour; a run beside nothing is declared_solo",
        ),
        (
            {"process": {"shared": {"position": 3}}},
            f"{record}: reads max_rss from a shared process (position 3); a process's high-water mark never decreases, so only a fresh process yields it",
        ),
        (
            {"argv": ["uv", "run", "matchtuner", "select"], "readings": {"footprint": {"bytes": 9438336, "resolution_bytes": 1}}},
            f"{record}: reads footprint, but argv[0] is 'uv', a wrapper; footprint describes the direct child only",
        ),
        (
            {"readings": {"peak_rss": {"bytes": 1, "resolution_bytes": 1}}},
            f"{record}: metric 'peak_rss' is not registered (wall, user_cpu, sys_cpu, max_rss, footprint, sampled_rss_max, swap_growth, "
            "scratch_peak, artifact_bytes:<name>, span_wall:<name>, hwm_increase:<name>, throughput:<name>)",
        ),
        (
            {"readings": {"wall": {"bytes": 1, "resolution_bytes": 1}}},
            f"{record}: metric 'wall' is duration, but its reading is Memory",
        ),
        (
            {"readings": {"swap_growth": {"bytes": 1, "resolution_bytes": 1}}},
            f"{record}: reads swap_growth, which only a run the run verb observed carries",
        ),
        (
            {"sleep": {"observed": {"slept_ns": 1000000001}}},
            f"{record}: the run slept 1000000001 ns, over 1000000000; a slept run is void, not valid",
        ),
        (
            {"started": EARLY_END, "ended": EARLY_START},
            f"{record}: the run ends 2026-10-01T09:00:00+02:00, before it starts 2026-10-01T09:00:58+02:00",
        ),
        (
            {"raw": "line\n" * 61},
            f"{record}.raw: raw holds 61 lines; it holds 1 to 60",
        ),
    ]


def test_each_run_rule_refuses_the_run_record_breaking_it(measured: MeasuredTree) -> None:
    for fields, message in run_rule_refusals(measured):
        assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, **fields)]), message)


def test_a_sleep_over_a_second_is_admitted_on_a_void_run(measured: MeasuredTree) -> None:
    item = parsed_item(measured, measured.old, sleep={"observed": {"slept_ns": 1000000001}}, validity={"void": {"reason": "slept"}})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_runs_out_of_execution_order_or_overlapping_are_refused(measured: MeasuredTree) -> None:
    overlapping = [parsed_item(measured, measured.old, name="a"), parsed_item(measured, measured.old, "2026-10-01T09:00:30+02:00", LATE_END, name="b")]
    message = "item 2 starts 2026-10-01T09:00:30+02:00, before the run before it ends 2026-10-01T09:00:58+02:00; runs are listed in execution order and do not overlap"
    assert_refused(measured, entry_spec(measured, overlapping), message)
    reversed_order = [parsed_item(measured, measured.old, LATE_START, LATE_END, name="a"), parsed_item(measured, measured.old, name="b")]
    message = "item 2 starts 2026-10-01T09:00:00+02:00, before the run before it ends 2026-10-01T09:06:00+02:00; runs are listed in execution order and do not overlap"
    assert_refused(measured, entry_spec(measured, reversed_order), message)


def test_items_measured_on_different_machines_or_environments_are_refused(measured: MeasuredTree) -> None:
    first = parsed_item(measured, measured.old, name="a")
    other_machine = {"stated": {"text": "a 15 GB sandbox", "source": "docs/sandbox.md:1"}}
    second = record_item(measured, "b", run_record(measured.old, parsed_run(measured, LATE_START, LATE_END), machine=other_machine))
    message = (
        'items 1 and 2 ran on different machines ({"stated": {"text": "M2 Max 32 GB", "source": "docs/orchestration_log/recon/2026-10-01/report.md:3"}} '
        'vs {"stated": {"text": "a 15 GB sandbox", "source": "docs/sandbox.md:1"}})'
    )
    assert_refused(measured, entry_spec(measured, [first, second]), message)
    other_env = {"stated": {"versions": {"polars": "1.40.0"}, "source": "docs/sandbox.md:1"}}
    second = record_item(measured, "b", run_record(measured.old, parsed_run(measured, LATE_START, LATE_END), env=other_env))
    message = (
        'items 1 and 2 differ in env ({"stated": {"versions": {"polars": "1.39.3"}, "source": "docs/orchestration_log/recon/2026-10-01/report.md:3"}} '
        'vs {"stated": {"versions": {"polars": "1.40.0"}, "source": "docs/sandbox.md:1"}}); an entry varies only its design\'s variable'
    )
    assert_refused(measured, entry_spec(measured, [first, second]), message)


def varies_code(tree: MeasuredTree) -> dict[str, Any]:
    """A design running the old and the new code."""
    return {"varies_code": {"levels": {"old": code(tree.old), "new": code(tree.new)}}}


def test_level_rules_refuse_items_missing_the_design(measured: MeasuredTree) -> None:
    old_only = [parsed_item(measured, measured.old, level="old")]
    assert_refused(measured, entry_spec(measured, old_only, design=varies_code(measured)), "level 'new' has no item")
    fixed_with_level = [parsed_item(measured, measured.old, level="old")]
    assert_refused(measured, entry_spec(measured, fixed_with_level), "item 1 carries level 'old', but the design is fixed")
    unknown = [parsed_item(measured, measured.old, level="older")]
    assert_refused(measured, entry_spec(measured, unknown, design=varies_code(measured)), "item 1 level 'older' is not one of the design's levels (old, new)")
    unlevelled = [parsed_item(measured, measured.old)]
    assert_refused(measured, entry_spec(measured, unlevelled, design=varies_code(measured)), "item 1 level None is not one of the design's levels (old, new)")


def test_an_item_measured_at_code_other_than_its_levels_is_refused(measured: MeasuredTree) -> None:
    items = [parsed_item(measured, measured.new, name="a", level="old"), parsed_item(measured, measured.new, LATE_START, LATE_END, name="b", level="new")]
    message = f"item 1 ran code {measured.new}, but level 'old' is {measured.old}"
    assert_refused(measured, entry_spec(measured, items, design=varies_code(measured)), message)
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.new)]), f"item 1 ran code {measured.new}, but level 'fixed' is {measured.old}")


def test_code_the_repository_cannot_vouch_for_is_refused(measured: MeasuredTree) -> None:
    absent = "f" * 40
    spec = entry_spec(measured, [parsed_item(measured, absent)], design={"fixed": {"code": code(absent)}})
    assert_refused(measured, spec, f"'{absent}' is not a commit in this repository")
    spec = entry_spec(measured, [parsed_item(measured, measured.old)], paths=["matchtuner/", "matchtuner/selection.py"])
    assert_refused(measured, spec, f"path 'matchtuner/selection.py' does not exist at {measured.old}")
    spec = entry_spec(measured, [parsed_item(measured, measured.old)], subject={"stage": {"command": "run-audit"}})
    assert_refused(measured, spec, f"stage 'run-audit' is not a matchtuner command at {measured.old} (build-store, score-all, select)")


def test_a_stage_the_newer_code_declares_lands_at_that_code(measured: MeasuredTree) -> None:
    spec = entry_spec(measured, [parsed_item(measured, measured.new)], subject={"stage": {"command": "run-audit"}}, design={"fixed": {"code": code(measured.new)}})
    assert add_entry(measured, spec).exit_code == 0


def test_a_code_state_whose_matchtuner_declares_no_command_is_refused(measured: MeasuredTree) -> None:
    first_commit = git(measured.root, "rev-list", "--max-parents=0", "HEAD").strip()
    spec = entry_spec(measured, [parsed_item(measured, first_commit)], paths=["docs/"], design={"fixed": {"code": code(first_commit)}})
    assert_refused(measured, spec, f"matchtuner/__main__.py at {first_commit} declares no command, so no stage can be checked there")


def test_entry_and_record_shape_rules_refuse_what_breaks_them(measured: MeasuredTree) -> None:
    transcribed_in_file = record_item(measured, "t", run_record(measured.old, transcribed_run(measured, EARLY_START, EARLY_END, readings={"wall": {"ns": 1, "resolution_ns": 1}})))
    assert_refused(measured, entry_spec(measured, [transcribed_in_file]), "item 1: a run record file holds a parsed or observed run; a transcription is written inline under 'transcribed'")
    parsed_inline = {"transcribed": run_record(measured.old, parsed_run(measured, EARLY_START, EARLY_END, readings={"wall": "57.78 s"}))}
    assert_refused(measured, entry_spec(measured, [parsed_inline]), "item 1: an inline run is transcribed; a parsed or observed run is a run record file named by 'run_record'")
    no_machine = record_item(measured, "m", {key: value for key, value in run_record(measured.old, parsed_run(measured, EARLY_START, EARLY_END)).items() if key != "machine"})
    assert_refused_from(measured, entry_spec(measured, [no_machine]), "item 1: machine:")
    no_code = record_item(measured, "c", {key: value for key, value in run_record(measured.old, parsed_run(measured, EARLY_START, EARLY_END)).items() if key != "code"})
    assert_refused_from(measured, entry_spec(measured, [no_code]), "item 1: code:")
    assert_refused(measured, entry_spec(measured, [{"run_record": "nowhere.yaml"}]), "item 1: nowhere.yaml is not a file")
    late_run = [parsed_item(measured, measured.old, "2026-10-01T10:31:00+02:00", "2026-10-01T10:32:00+02:00")]
    assert_refused(measured, entry_spec(measured, late_run), "item 1 ends 2026-10-01T10:32:00+02:00, after the entry is recorded 2026-10-01T10:30:42+02:00")
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old)], supersedes=["m-20260930-001"]), "supersedes m-20260930-001, which is not a measurement earlier in the log")
    two_lines = entry_spec(measured, [parsed_item(measured, measured.old)], question="what\nnow")
    assert_refused(measured, two_lines, "question: 'what\nnow' is not one line opening on a visible character")


def test_a_hash_its_file_no_longer_has_is_refused_while_the_file_exists(measured: MeasuredTree) -> None:
    evidence = measured.scratch / "evidence.txt"
    evidence.write_text(TIME_RAW, encoding="utf-8")
    item = parsed_item(measured, measured.old, evidence={"path": str(evidence), "sha256": sha256_of(evidence), "durability": "scratchpad"})
    log = measured.scratch / "select.log"
    for changed in (evidence, log):
        recorded = sha256_of(changed)
        changed.write_text("rewritten\n", encoding="utf-8")
        assert_refused(measured, entry_spec(measured, [item]), f"item 1: {changed} hashes {sha256_of(changed)}, not the recorded {recorded}")
        changed.unlink()
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_a_dirty_code_state_whose_diff_no_longer_hashes_as_recorded_is_refused(measured: MeasuredTree) -> None:
    diff = measured.scratch / "change.diff"
    diff.write_text("+one line\n", encoding="utf-8")
    dirty = {"dirty": {"base": measured.old, "diff_sha256": sha256_of(diff), "diff_path": str(diff)}}
    item = record_item(measured, "dirty", run_record(measured.old, parsed_run(measured, EARLY_START, EARLY_END), code=dirty))
    spec = entry_spec(measured, [item], design={"fixed": {"code": dirty}})
    recorded = sha256_of(diff)
    diff.write_text("+another line\n", encoding="utf-8")
    assert_refused(measured, spec, f"item 1: {diff} hashes {sha256_of(diff)}, not the recorded {recorded}")
    diff.write_text("+one line\n", encoding="utf-8")
    assert add_entry(measured, spec).exit_code == 0


def test_an_operation_whose_harness_is_missing_is_refused(measured: MeasuredTree) -> None:
    subject = {"operation": {"callable": "matchtuner.selection.draw_selection_pairs", "harness": str(measured.scratch / "measure_panel.py")}}
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old)], subject=subject), f"harness {measured.scratch / 'measure_panel.py'} is not a file")
    (measured.scratch / "measure_panel.py").write_text("print()\n", encoding="utf-8")
    assert add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)], subject=subject)).exit_code == 0


def test_a_worktree_is_refused_and_its_log_left_alone(measured: MeasuredTree) -> None:
    worktree = measured.scratch / "worktree"
    git(measured.root, "worktree", "add", "-q", str(worktree))
    spec = write_yaml(worktree / SPEC_DIRECTORY / "entry.yaml", entry_spec(measured, [parsed_item(measured, measured.old)]))
    result = run_memento(worktree, "measurement", "add", str(spec))
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: records are written in the root checkout only; this is worktree {worktree}\n")
    assert (worktree / MEASUREMENT_LOG).read_text(encoding="utf-8") == ""


def test_a_hand_edit_of_a_committed_document_fails_the_check_and_refuses_the_next_append(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    commit_all(measured.root)
    edited_line = measurement_log(measured).splitlines().index("paths:") + 1
    replace_once(measured.root, MEASUREMENT_LOG, "paths:\n- matchtuner/\n", "paths: [matchtuner/]\n")
    canonical = f"FAIL  {MEASUREMENT_LOG}:1  is not the block form memento writes; the log is written by 'just memento measurement add' and 'void' only"
    assert run_memento(measured.root, "check").stdout.splitlines()[0] == canonical
    message = f"{MEASUREMENT_LOG} is not an extension of HEAD's copy (first difference at line {edited_line}); written lines are never edited"
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old)]), message)


def test_an_entry_whose_landing_would_add_a_records_failure_is_refused(measured: MeasuredTree) -> None:
    def check_empty_log(corpus: Corpus) -> Iterator[Finding]:
        """Fail any measurement log holding a document."""
        if corpus.text(MEASUREMENT_LOG):
            yield fail(MEASUREMENT_LOG, "the log holds a document")

    spec_path = write_yaml(measured.root / SPEC_DIRECTORY / "entry.yaml", entry_spec(measured, [parsed_item(measured, measured.old)]))
    result = run_memento(measured.root, "measurement", "add", str(spec_path), checks=(*CHECKS, check_empty_log))
    assert (result.exit_code, result.stderr) == (1, f"refused: the change adds 1 FAIL: {MEASUREMENT_LOG}  the log holds a document\n")
    assert measurement_log(measured) == ""


def test_a_day_holding_its_last_id_refuses_another_entry(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    replace_once(measured.root, MEASUREMENT_LOG, "id: m-20261001-001", "id: m-20261001-999")
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old)]), "the log holds 999 documents for m-20261001-NNN; no id is left for that day")


def test_a_log_document_that_does_not_parse_refuses_the_next_append(measured: MeasuredTree) -> None:
    (measured.root / MEASUREMENT_LOG).write_text("---\nkind: measurement\n", encoding="utf-8")
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith("refused: the measurement log's document at line 1 does not parse (measurement.id:")


EVIDENCE = "docs/orchestration_log/recon/2026-10-01/memento-in/entry.yaml:3"


def void(tree: MeasuredTree, target: str, reason: str = "overlapped an evaluate", evidence: str = EVIDENCE) -> Any:
    """Run `measurement void` on the tree."""
    return run_memento(tree.root, "measurement", "void", target, "--reason", reason, "--evidence", evidence)


def assert_refused_from(tree: MeasuredTree, spec: dict[str, Any], prefix: str) -> None:
    """Assert `measurement add` refuses with a message starting with this memento-owned prefix and leaves the log's bytes untouched."""
    before = (tree.root / MEASUREMENT_LOG).read_bytes()
    result = add_entry(tree, spec)
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith(f"refused: {prefix}")
    assert (tree.root / MEASUREMENT_LOG).read_bytes() == before


def assert_void_refused_from(tree: MeasuredTree, prefix: str, target: str, **options: str) -> None:
    """Assert `measurement void` refuses with a message starting with this memento-owned prefix and leaves the log's bytes untouched."""
    before = (tree.root / MEASUREMENT_LOG).read_bytes()
    result = void(tree, target, **options)
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr.startswith(f"refused: {prefix}")
    assert (tree.root / MEASUREMENT_LOG).read_bytes() == before


def assert_void_refused(tree: MeasuredTree, message: str, target: str, **options: str) -> None:
    """Assert `measurement void` refuses with this message and leaves the log's bytes untouched."""
    before = (tree.root / MEASUREMENT_LOG).read_bytes()
    result = void(tree, target, **options)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
    assert (tree.root / MEASUREMENT_LOG).read_bytes() == before


def test_a_void_of_an_earlier_run_lands_after_it_and_prints_its_id(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    result = void(measured, "m-20261001-001#1")
    assert (result.exit_code, result.stdout, result.stderr) == (0, f"{MEASUREMENT_LOG}: m-20261001-002\n", "")
    assert measurement_log(measured).endswith(
        "---\nkind: void\nid: m-20261001-002\nrecorded: '2026-10-01T10:30:42+02:00'\nby: self\ntarget: m-20261001-001#1\n"
        f"reason: overlapped an evaluate\nevidence: {EVIDENCE}\n"
    )
    assert run_memento(measured.root, "check").stdout == "records: 0 FAIL, 0 WARN\n"


def test_a_void_whose_target_or_evidence_does_not_hold_is_refused(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    assert_void_refused(measured, "void target m-20261001-007 is not a measurement earlier in the log", "m-20261001-007")
    assert_void_refused(measured, "void target m-20261001-001#2 names item 2, but m-20261001-001 holds 1 items", "m-20261001-001#2")
    assert_void_refused_from(measured, "target:", "m-1#1")
    absent = "f" * 40
    assert_void_refused(measured, f"'{absent}' is not a commit in this repository", "m-20261001-001", evidence=absent)
    assert_void_refused(measured, "evidence docs/missing.md is not a file", "m-20261001-001", evidence="docs/missing.md:1")
    evidence_file = EVIDENCE.rpartition(":")[0]
    lines = len((measured.root / evidence_file).read_text(encoding="utf-8").splitlines())
    past_end = f"{evidence_file}:{lines + 1}"
    assert_void_refused(measured, f"evidence {past_end} points past the end of {evidence_file}, which holds {lines} lines", "m-20261001-001", evidence=past_end)
    assert_void_refused(measured, "reason: ' late' is not one line opening on a visible character", "m-20261001-001", reason=" late")
    void(measured, "m-20261001-001")
    assert_void_refused(measured, "void target m-20261001-002 is not a measurement earlier in the log", "m-20261001-002")


def test_a_void_cited_by_commit_lands(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))
    assert void(measured, "m-20261001-001", evidence=measured.new).exit_code == 0


def test_a_question_over_its_character_limit_is_refused(measured: MeasuredTree) -> None:
    spec = entry_spec(measured, [parsed_item(measured, measured.old)], question="q" * 161)
    assert_refused(measured, spec, "question: question runs 161 characters, over 160")
    assert add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)], question="q" * 160)).exit_code == 0


def test_a_sleep_of_exactly_a_second_leaves_a_run_valid(measured: MeasuredTree) -> None:
    item = parsed_item(measured, measured.old, sleep={"observed": {"slept_ns": 1000000000}})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_a_transcribed_raw_text_up_to_forty_lines_after_its_cited_line_lands_and_beyond_is_refused(measured: MeasuredTree) -> None:
    report = measured.scratch / "report.md"
    report.write_text(report_lines(50, 100), encoding="utf-8")
    for cited, lands in ((10, True), (9, False)):
        item = transcribed_item(measured, provenance={"transcribed": {"source": str(report), "line": cited}})
        item["transcribed"]["item"]["evidence"]["sha256"] = sha256_of(report)
        result = add_entry(measured, entry_spec(measured, [item]))
        assert (result.exit_code == 0) is lands, result.stderr


def test_a_transcription_whose_source_is_gone_lands_unverified(measured: MeasuredTree) -> None:
    item = transcribed_item(measured, provenance={"transcribed": {"source": str(measured.scratch / "gone.md"), "line": 5}})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_a_summary_lands_inline_and_is_refused_from_a_run_record_file(measured: MeasuredTree) -> None:
    report = measured.scratch / "report.md"
    summary = {
        "item": "summary",
        "statistic": "median",
        "n": 5,
        "readings": {"max_rss": "17.27 GB"},
        "raw": REPORT_RAW,
        "evidence": {"path": str(report), "sha256": sha256_of(report), "durability": "scratchpad"},
        "provenance": {"transcribed": {"source": str(report), "line": 5}},
    }
    in_file = record_item(measured, "summary", run_record(measured.old, summary | {"readings": {"max_rss": {"bytes": 1, "resolution_bytes": 1}}}))
    assert_refused(measured, entry_spec(measured, [in_file]), "item 1: a run record file holds a parsed or observed run; a transcription is written inline under 'transcribed'")
    assert add_entry(measured, entry_spec(measured, [{"transcribed": run_record(measured.old, summary)}])).exit_code == 0
    assert logged_document(measured)["items"][0]["readings"] == {"max_rss": {"bytes": 17270000000, "resolution_bytes": 10000000}}


def test_named_metrics_land_and_a_transcribed_rate_counts_per_its_time_unit(measured: MeasuredTree) -> None:
    raw = "draw_wall_s=12.04 rows 1,500.5/s\n"
    report = measured.scratch / "report.md"
    report.write_text(raw, encoding="utf-8")
    item = transcribed_item(measured, raw=raw, readings={"span_wall:draw": "12.04 s", "throughput:rows": "1,500.5/s"})
    item["transcribed"]["item"]["provenance"] = {"transcribed": {"source": str(report), "line": 1}}
    item["transcribed"]["item"]["evidence"]["sha256"] = sha256_of(report)
    result = add_entry(measured, entry_spec(measured, [item]))
    assert result.exit_code == 0, result.stderr
    assert logged_document(measured)["items"][0]["readings"] == {
        "span_wall:draw": {"ns": 12040000000, "resolution_ns": 10000000},
        "throughput:rows": {"count": 15005, "per_ns": 10000000000, "resolution_count": 1},
    }


def test_metrics_admitted_only_by_a_fresh_or_observed_run_are_refused_elsewhere(measured: MeasuredTree) -> None:
    shared = parsed_item(measured, measured.old, process={"shared": {"position": 2}}, readings={"hwm_increase:audit": {"bytes": 1, "resolution_bytes": 1}})
    message = "item 1: item.run: reads hwm_increase:audit from a shared process (position 2); a process's high-water mark never decreases, so only a fresh process yields it"
    assert_refused(measured, entry_spec(measured, [shared]), message)
    parsed = parsed_item(measured, measured.old, readings={"scratch_peak": {"bytes": 1, "resolution_bytes": 1}})
    assert_refused(measured, entry_spec(measured, [parsed]), "item 1: item.run: reads scratch_peak, which only a run the run verb observed carries")


def test_a_void_whose_landing_would_add_a_records_failure_is_refused(measured: MeasuredTree) -> None:
    add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old)]))

    def check_no_void(corpus: Corpus) -> Iterator[Finding]:
        """Fail any measurement log holding a void."""
        if "kind: void" in corpus.text(MEASUREMENT_LOG):
            yield fail(MEASUREMENT_LOG, "the log holds a void")

    before = measurement_log(measured)
    result = run_memento(measured.root, "measurement", "void", "m-20261001-001", "--reason", "r", "--evidence", EVIDENCE, checks=(*CHECKS, check_no_void))
    assert (result.exit_code, result.stderr) == (1, f"refused: the change adds 1 FAIL: {MEASUREMENT_LOG}  the log holds a void\n")
    assert measurement_log(measured) == before


def test_a_run_record_reading_its_raw_text_never_prints_is_refused(measured: MeasuredTree) -> None:
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 10000000}, "max_rss": {"bytes": 5, "resolution_bytes": 1}}
    message = 'item 1: max_rss reading {"bytes": 5, "resolution_bytes": 1} disagrees with its raw text, which prints 18791546880 B on line 2'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, readings=readings)]), message)


def test_a_run_record_reading_finer_than_its_raw_texts_resolution_is_refused(measured: MeasuredTree) -> None:
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 1}, "max_rss": {"bytes": 18791546880, "resolution_bytes": 1}}
    message = 'item 1: wall reading {"ns": 57780000000, "resolution_ns": 1} has a finer resolution than its raw text, which prints 57.78 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, readings=readings)]), message)


def test_a_run_record_reading_with_text_its_raw_text_lacks_is_refused(measured: MeasuredTree) -> None:
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 10000000}, "max_rss": {"unit_ambiguous": "17.3 GB"}}
    message = "item 1: max_rss reading '17.3 GB' is not in its raw text"
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, readings=readings)]), message)


def test_a_run_record_reading_in_a_unit_its_raw_text_never_names_lands_at_no_finer_a_resolution_than_it_prints(measured: MeasuredTree) -> None:
    raw = "Elapsed (wall clock) time (h:mm:ss or m:ss): 0:57.78\nMaximum resident set size (kbytes): 18350\n"
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 100000000}, "max_rss": {"bytes": 18790400, "resolution_bytes": 1024}}
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=readings)]))
    assert result.exit_code == 0, result.stderr
    assert logged_document(measured)["items"][0]["readings"] == readings


THROUGHPUT_RAW = "draw rows 1,500.5/s\n"
THROUGHPUT_READING = {"count": 15005, "per_ns": 10000000000, "resolution_count": 1}


def test_a_run_record_throughput_its_raw_text_prints_lands(measured: MeasuredTree) -> None:
    item = parsed_item(measured, measured.old, raw=THROUGHPUT_RAW, readings={"throughput:rows": THROUGHPUT_READING})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_a_run_record_throughput_its_raw_text_never_prints_is_refused(measured: MeasuredTree) -> None:
    item = parsed_item(measured, measured.old, raw="draw rows 1,400.5/s\n", readings={"throughput:rows": THROUGHPUT_READING})
    message = 'item 1: throughput:rows reading {"count": 15005, "per_ns": 10000000000, "resolution_count": 1} disagrees with its raw text, which prints 1,400.5/s on line 1'
    assert_refused(measured, entry_spec(measured, [item]), message)


SAMPLED_READING = {"sampled_rss_max": {"bytes": 17270000000, "resolution_bytes": 10000000}}


def test_a_sampled_rss_reading_whose_raw_text_states_no_interval_is_refused(measured: MeasuredTree) -> None:
    message = "item 1: item.run: reads sampled_rss_max, but its raw text states no sampling interval"
    for raw in ("peak sampled rss 17.27 GB\n", "the interval is unknown\npeak sampled rss 17.27 GB\n"):
        item = parsed_item(measured, measured.old, raw=raw, readings=SAMPLED_READING)
        assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_sampled_rss_reading_lands_when_its_raw_text_states_the_interval(measured: MeasuredTree) -> None:
    for stated in ("interval_ms=500", "Interval: 5 s", "sampled at interval 5s", "sample_interval=5"):
        item = parsed_item(measured, measured.old, raw=f"{stated}\npeak sampled rss 17.27 GB\n", readings=SAMPLED_READING)
        result = add_entry(measured, entry_spec(measured, [item]))
        assert result.exit_code == 0, (stated, result.stderr)


SPAN_READING = {"span_wall:draw": {"ns": 12040000000, "resolution_ns": 10000000}}


def test_a_span_wall_reading_whose_raw_text_has_no_line_for_the_span_is_refused(measured: MeasuredTree) -> None:
    message = "item 1: item.run: reads span_wall:draw, but its raw text has no line naming 'draw' with a number"
    for raw in ("withdraw_wall_s=12.04\n", "draw took a while\n12.04\n"):
        assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=SPAN_READING)]), message)


def test_a_span_wall_reading_lands_when_its_raw_text_has_the_spans_line(measured: MeasuredTree) -> None:
    for raw in ("draw_wall=12.04 s\n", "span draw: 12.04s\n"):
        result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=SPAN_READING)]))
        assert result.exit_code == 0, (raw, result.stderr)


def test_a_throughput_reading_whose_raw_text_has_no_line_for_the_quantity_is_refused(measured: MeasuredTree) -> None:
    message = "item 1: item.run: reads throughput:rows, but its raw text has no line naming 'rows' with a number"
    for raw in ("arrows 1,500.5/s\n", "rowsets 1,500.5/s\n", "rows per second were fast\n1,500.5/s\n"):
        item = parsed_item(measured, measured.old, raw=raw, readings={"throughput:rows": THROUGHPUT_READING})
        assert_refused(measured, entry_spec(measured, [item]), message)


def inline_summary(tree: MeasuredTree, raw: str, readings: dict[str, str]) -> dict[str, Any]:
    """An inline transcribed median over five runs, its raw text the one line of the scratch report."""
    report = tree.scratch / "report.md"
    report.write_text(raw, encoding="utf-8")
    summary = {
        "item": "summary",
        "statistic": "median",
        "n": 5,
        "readings": readings,
        "raw": raw,
        "evidence": {"path": str(report), "sha256": sha256_of(report), "durability": "scratchpad"},
        "provenance": {"transcribed": {"source": str(report), "line": 1}},
    }
    return {"transcribed": run_record(tree.old, summary)}


def test_a_summary_reading_whose_raw_text_does_not_meet_its_metrics_admission_is_refused(measured: MeasuredTree) -> None:
    unnamed = inline_summary(measured, "| 1 | 1,500.5/s |\n", {"throughput:rows": "1,500.5/s"})
    message = "items.0.transcribed.item.summary: reads throughput:rows, but its raw text has no line naming 'rows' with a number"
    assert_refused(measured, entry_spec(measured, [unnamed]), message)
    assert add_entry(measured, entry_spec(measured, [inline_summary(measured, "| rows | 1,500.5/s |\n", {"throughput:rows": "1,500.5/s"})])).exit_code == 0


def test_a_run_record_reading_lands_at_the_finest_resolution_any_matching_number_in_its_raw_text_is_printed_at(measured: MeasuredTree) -> None:
    raw = "       57.78 real\n       57.780 real\n         18791546880  maximum resident set size\n"
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 1000000}, "max_rss": {"bytes": 18791546880, "resolution_bytes": 1}}
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=readings)]))
    assert result.exit_code == 0, result.stderr


def test_a_run_record_throughput_finer_than_its_raw_texts_resolution_is_refused(measured: MeasuredTree) -> None:
    finer = {"count": 1500500, "per_ns": 1000000000000, "resolution_count": 1}
    item = parsed_item(measured, measured.old, raw=THROUGHPUT_RAW, readings={"throughput:rows": finer})
    message = (
        'item 1: throughput:rows reading {"count": 1500500, "per_ns": 1000000000000, "resolution_count": 1} '
        "has a finer resolution than its raw text, which prints 1,500.5/s on line 1"
    )
    assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_run_record_reading_coarser_than_its_raw_text_prints_it_lands(measured: MeasuredTree) -> None:
    readings = {"wall": {"ns": 57780000000, "resolution_ns": 100000000}, "max_rss": {"bytes": 18791546880, "resolution_bytes": 4096}}
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, readings=readings)]))
    assert result.exit_code == 0, result.stderr
    sampled = {"sampled_rss_max": {"bytes": 17270000000, "resolution_bytes": 100000000}}
    item = parsed_item(measured, measured.old, raw="interval_ms=500\npeak 17.27 GB\n", readings=sampled)
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_a_run_record_memory_reading_finer_than_its_raw_texts_resolution_is_refused(measured: MeasuredTree) -> None:
    readings = {"sampled_rss_max": {"bytes": 17270000000, "resolution_bytes": 1}}
    message = 'item 1: sampled_rss_max reading {"bytes": 17270000000, "resolution_bytes": 1} has a finer resolution than its raw text, which prints 17.27 GB on line 2'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw="interval_ms=500\npeak 17.27 GB\n", readings=readings)]), message)


BSD_BLOCK = "        0.15 real         0.14 user         0.03 sys\n   334823424  maximum resident set size\n"
BSD_READINGS = {"wall": {"ns": 150000000, "resolution_ns": 10000000}, "max_rss": {"bytes": 334823424, "resolution_bytes": 1}}


def test_a_run_record_wall_in_another_unit_than_its_time_l_line_prints_is_refused(measured: MeasuredTree) -> None:
    readings = BSD_READINGS | {"wall": {"ns": 150000, "resolution_ns": 10000}}
    message = 'item 1: wall reading {"ns": 150000, "resolution_ns": 10000} disagrees with its raw text, which prints 0.15 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=BSD_BLOCK, readings=readings)]), message)


def test_a_run_record_max_rss_at_a_factor_of_its_time_l_line_is_refused(measured: MeasuredTree) -> None:
    readings = BSD_READINGS | {"max_rss": {"bytes": 342858162176, "resolution_bytes": 1024}}
    message = 'item 1: max_rss reading {"bytes": 342858162176, "resolution_bytes": 1024} disagrees with its raw text, which prints 334823424 B on line 2'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=BSD_BLOCK, readings=readings)]), message)


def test_a_run_record_wall_equal_to_the_printed_user_time_is_refused(measured: MeasuredTree) -> None:
    readings = BSD_READINGS | {"wall": {"ns": 140000000, "resolution_ns": 10000000}}
    message = 'item 1: wall reading {"ns": 140000000, "resolution_ns": 10000000} disagrees with its raw text, which prints 0.15 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=BSD_BLOCK, readings=readings)]), message)


def test_a_run_record_user_and_system_time_land_from_their_own_labels(measured: MeasuredTree) -> None:
    readings = BSD_READINGS | {"user_cpu": {"ns": 140000000, "resolution_ns": 10000000}, "sys_cpu": {"ns": 30000000, "resolution_ns": 10000000}}
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=BSD_BLOCK, readings=readings)]))
    assert result.exit_code == 0, result.stderr
    swapped = BSD_READINGS | {"user_cpu": {"ns": 30000000, "resolution_ns": 10000000}, "sys_cpu": {"ns": 140000000, "resolution_ns": 10000000}}
    message = 'item 1: user_cpu reading {"ns": 30000000, "resolution_ns": 10000000} disagrees with its raw text, which prints 0.14 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=BSD_BLOCK, readings=swapped)]), message)


GNU_BLOCK = (
    "\tUser time (seconds): 0.14\n\tSystem time (seconds): 0.03\n"
    "\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:02:03.45\n\tMaximum resident set size (kbytes): 18350\n"
)
GNU_READINGS = {
    "wall": {"ns": 3723450000000, "resolution_ns": 10000000},
    "user_cpu": {"ns": 140000000, "resolution_ns": 10000000},
    "sys_cpu": {"ns": 30000000, "resolution_ns": 10000000},
    "max_rss": {"bytes": 18790400, "resolution_bytes": 1024},
}


def test_a_run_record_from_a_time_v_block_lands_with_elapsed_read_as_its_printed_clock_and_rss_in_kib(measured: MeasuredTree) -> None:
    result = add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=GNU_BLOCK, readings=GNU_READINGS)]))
    assert result.exit_code == 0, result.stderr
    assert logged_document(measured)["items"][0]["readings"] == GNU_READINGS


def test_a_run_record_time_v_reading_in_the_wrong_unit_or_clock_is_refused(measured: MeasuredTree) -> None:
    for name, wrong, printed in (
        ("max_rss", {"bytes": 18350, "resolution_bytes": 1}, "18350 KiB on line 4"),
        ("wall", {"ns": 62030000000, "resolution_ns": 10000000}, "1:02:03.45 on line 3"),
    ):
        message = f"item 1: {name} reading {json.dumps(wrong)} disagrees with its raw text, which prints {printed}"
        item = parsed_item(measured, measured.old, raw=GNU_BLOCK, readings=GNU_READINGS | {name: wrong})
        assert_refused(measured, entry_spec(measured, [item]), message)


SAMPLED_RAW = "interval_ms=500\npeak 17.27 GB\n"


def test_a_run_record_reading_in_a_unit_other_than_the_one_printed_beside_its_number_is_refused(measured: MeasuredTree) -> None:
    readings = {"sampled_rss_max": {"bytes": 17270000, "resolution_bytes": 10000}}
    message = 'item 1: sampled_rss_max reading {"bytes": 17270000, "resolution_bytes": 10000} disagrees with its raw text, which prints 17.27 GB on line 2'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=SAMPLED_RAW, readings=readings)]), message)


def test_a_run_record_duration_in_another_unit_than_the_one_printed_beside_its_number_is_refused(measured: MeasuredTree) -> None:
    readings = {"span_wall:draw": {"ns": 3466800000000, "resolution_ns": 600000000}}
    message = 'item 1: span_wall:draw reading {"ns": 3466800000000, "resolution_ns": 600000000} disagrees with its raw text, which prints 57.78 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw="draw 57.78 s\n", readings=readings)]), message)


def test_a_run_record_throughput_per_another_unit_than_the_one_printed_is_refused(measured: MeasuredTree) -> None:
    per_minute = {"count": 15005, "per_ns": 600000000000, "resolution_count": 1}
    message = 'item 1: throughput:rows reading {"count": 15005, "per_ns": 600000000000, "resolution_count": 1} disagrees with its raw text, which prints 1,500.5/s on line 1'
    item = parsed_item(measured, measured.old, raw=THROUGHPUT_RAW, readings={"throughput:rows": per_minute})
    assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_run_record_reading_whose_number_prints_no_unit_beside_it_is_refused(measured: MeasuredTree) -> None:
    sampled = parsed_item(measured, measured.old, raw="interval_ms=500\n      18791546880  peak sampled rss\n", readings={"sampled_rss_max": {"bytes": 18791546880, "resolution_bytes": 1}})
    assert_refused(measured, entry_spec(measured, [sampled]), 'item 1: sampled_rss_max reading {"bytes": 18791546880, "resolution_bytes": 1} disagrees with its raw text, which prints no number with its unit beside it')
    message = "item 1: span_wall:draw reading " + json.dumps(SPAN_READING["span_wall:draw"]) + " disagrees with its raw text, which prints no number with its unit beside it on a line naming 'draw'"
    for raw in ("draw_wall_s=12.04\n", "draw 12.04 seconds\n"):
        assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=SPAN_READING)]), message)


def test_a_run_record_named_reading_equal_to_a_number_on_another_line_is_refused(measured: MeasuredTree) -> None:
    raw = "draw 12.04 s\nsetup 99.9 s\n"
    other = {"span_wall:draw": {"ns": 99900000000, "resolution_ns": 100000000}}
    message = 'item 1: span_wall:draw reading {"ns": 99900000000, "resolution_ns": 100000000} disagrees with its raw text, which prints 12.04 s on line 1'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=other)]), message)
    assert add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=SPAN_READING)])).exit_code == 0


def test_a_run_record_max_rss_and_wall_over_prose_that_prints_a_larger_unit_are_refused(measured: MeasuredTree) -> None:
    raw = "peak 17.27 GB in 57.78 s\n"
    megabytes = {"bytes": 17270000, "resolution_bytes": 10000}
    minutes = {"ns": 3466800000000, "resolution_ns": 600000000}
    for readings, key, instrument in (
        ({"max_rss": megabytes}, "max_rss", "`time -l` maximum resident set size in bytes or `time -v` in KiB, a max over reaped descendants"),
        ({"wall": minutes}, "wall", "`time -l` real or `time -v` elapsed, wall clock including sleep"),
    ):
        message = f"item 1: {key} reading {json.dumps(readings[key])} disagrees with its raw text, which prints no line for it ({instrument})"
        assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=readings)]), message)


def test_a_run_record_footprint_lands_in_bytes_from_its_time_l_line_and_is_refused_at_a_factor_of_it(measured: MeasuredTree) -> None:
    raw = BSD_BLOCK + "   328058560  peak memory footprint\n"
    footprint = {"bytes": 328058560, "resolution_bytes": 1}
    assert add_entry(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=BSD_READINGS | {"footprint": footprint})])).exit_code == 0
    kibibytes = {"bytes": 335931965440, "resolution_bytes": 1024}
    message = f'item 1: footprint reading {json.dumps(kibibytes)} disagrees with its raw text, which prints 328058560 B on line 3'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=BSD_READINGS | {"footprint": kibibytes})]), message)


def inline_item(tree: MeasuredTree, raw: str, readings: dict[str, str]) -> dict[str, Any]:
    """An inline transcribed run whose raw text is the scratch report's whole text."""
    report = tree.scratch / "report.md"
    report.write_text(raw, encoding="utf-8")
    evidence = {"path": str(report), "sha256": sha256_of(report), "durability": "scratchpad"}
    return transcribed_item(tree, raw=raw, readings=readings, provenance={"transcribed": {"source": str(report), "line": 1}}, evidence=evidence)


def test_an_inline_reading_whose_number_the_raw_text_prints_beside_another_unit_is_refused(measured: MeasuredTree) -> None:
    item = inline_item(measured, "peak 17.27 GB, limit 3 MB\n", {"max_rss": "17.27 MB"})
    message = "item 1: max_rss reading '17.27 MB': its raw text prints 17.27 beside GB, never beside MB"
    assert_refused(measured, entry_spec(measured, [item]), message)


def test_inline_readings_over_other_units_the_raw_text_prints_beside_their_numbers_are_refused(measured: MeasuredTree) -> None:
    for raw, key, reading, printed_unit, declared in (
        ("peak 17.27 MB, limit 3 GB\n", "max_rss", "17.27 GB", "MB", "GB"),
        ("wall 57.78 s (about 1 min)\n", "wall", "57.78 min", "s", "min"),
    ):
        message = f"item 1: {key} reading '{reading}': its raw text prints {reading.split()[0]} beside {printed_unit}, never beside {declared}"
        assert_refused(measured, entry_spec(measured, [inline_item(measured, raw, {key: reading})]), message)


def test_inline_readings_land_when_their_number_is_printed_beside_the_declared_unit_or_beside_no_unit(measured: MeasuredTree) -> None:
    for raw, key, reading in (
        ("peak 17.27 GB, 17.27 MB of it shared\n", "max_rss", "17.27 MB"),
        ("| peak | 17.27 | GB |\n", "max_rss", "17.27 GB"),
        ("| wall | 57.78 | s |\n", "wall", "57.78 s"),
        ("wall 57.78 s (about 1 min)\n", "wall", "57.78 s"),
    ):
        result = add_entry(measured, entry_spec(measured, [inline_item(measured, raw, {key: reading})]))
        assert result.exit_code == 0, (raw, result.stderr)


def test_an_inline_reading_finer_than_the_print_beside_its_own_unit_is_refused_though_another_unit_prints_it_finer(measured: MeasuredTree) -> None:
    item = inline_item(measured, "peak 17.27 GB (17.270 MB of it shared)\n", {"max_rss": "17.270 GB"})
    message = "item 1: max_rss reading '17.270 GB' has a finer resolution than its raw text, which prints 17.27"
    assert_refused(measured, entry_spec(measured, [item]), message)


def test_a_run_record_wall_over_a_line_that_only_begins_with_the_real_label_is_refused(measured: MeasuredTree) -> None:
    raw = "        0.15 realloc         0.14 user         0.03 sys\n"
    readings = {"wall": {"ns": 150000000, "resolution_ns": 10000000}}
    message = 'item 1: wall reading {"ns": 150000000, "resolution_ns": 10000000} disagrees with its raw text, which prints no line for it (`time -l` real or `time -v` elapsed, wall clock including sleep)'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw=raw, readings=readings)]), message)


def test_a_run_record_reading_over_the_tail_of_a_longer_numeral_is_refused(measured: MeasuredTree) -> None:
    readings = {"sampled_rss_max": {"bytes": 27000000000, "resolution_bytes": 1000000000}}
    message = 'item 1: sampled_rss_max reading {"bytes": 27000000000, "resolution_bytes": 1000000000} disagrees with its raw text, which prints no number with its unit beside it'
    assert_refused(measured, entry_spec(measured, [parsed_item(measured, measured.old, raw="interval_ms=500\npeak .27 GB\n", readings=readings)]), message)


def test_a_run_record_throughput_at_a_coarser_digit_than_the_print_over_a_larger_span_lands(measured: MeasuredTree) -> None:
    coarser = {"count": 150050, "per_ns": 100000000000, "resolution_count": 10}
    item = parsed_item(measured, measured.old, raw=THROUGHPUT_RAW, readings={"throughput:rows": coarser})
    assert add_entry(measured, entry_spec(measured, [item])).exit_code == 0


def test_an_inline_summary_and_an_inline_rate_pair_their_number_with_the_unit_beside_it(measured: MeasuredTree) -> None:
    summary = inline_summary(measured, "median peak 17.27 GB, limit 3 MB\n", {"max_rss": "17.27 MB"})
    message = "item 1: max_rss reading '17.27 MB': its raw text prints 17.27 beside GB, never beside MB"
    assert_refused(measured, entry_spec(measured, [summary]), message)
    rate = inline_item(measured, "draw rows 1,500.5/s over 3 min\n", {"throughput:rows": "1,500.5/min"})
    message = "item 1: throughput:rows reading '1,500.5/min': its raw text prints 1,500.5 beside s, never beside min"
    assert_refused(measured, entry_spec(measured, [rate]), message)
