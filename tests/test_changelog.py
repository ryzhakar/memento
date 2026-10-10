"""The change log's door: add an entry, supersede one, age the oldest out, and the check ChangeEntry replaces."""

import re
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from memento.changelog import EntryShapeError, parse_entry_text, parse_raw_entry, split_log
from tests.memento.conftest import PROJECT_ROOT, fixture_schema, git, replace_once, run_memento

REQUIRED_ENTRY = {
    "end": None,
    "kind": "computed",
    "from_to": "an old shape → a new shape",
    "why": "the old shape was weak",
    "invalidates": ["nothing"],
}
DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")


def spec() -> dict:
    """The change kind's schema spec, as the fixture tree declares it."""
    return fixture_schema()["kinds"]["change"]


def head_sha(root: Path) -> str:
    """The tree's one commit, an ancestor of its own `main` branch by construction."""
    return git(root, "rev-parse", "HEAD").strip()


def set_change_budget(root: Path, tokens: int) -> None:
    """Lower the fixture schema's change-kind budget_tokens to a given value."""
    replace_once(root, ".claude/memento.yaml", "    budget_tokens: 55000\n", f"    budget_tokens: {tokens}\n")


def log_text(root: Path) -> str:
    """The change log's current text."""
    return (root / "docs/architecture_log.md").read_text(encoding="utf-8")


def as_yaml_date(value: object) -> object:
    """A 'YYYY-MM-DD' string turned into a date, so the dump emits it as an unquoted YAML date; anything else, unchanged."""
    return date.fromisoformat(value) if isinstance(value, str) and DATE_SHAPE.fullmatch(value) else value


def write_entry_yaml(tmp_path: Path, **fields: object) -> Path:
    """A block-form YAML file of entry fields, REQUIRED_ENTRY's defaults filled in beneath the given ones."""
    data = {key: as_yaml_date(value) for key, value in {**REQUIRED_ENTRY, **fields}.items()}
    path = tmp_path / "entry.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_add_appends_a_new_entry_in_schema_field_order(record_tree: Path) -> None:
    entry_path = write_entry_yaml(
        record_tree.parent,
        start="2026-09-30",
        title="A later change",
        merged=head_sha(record_tree),
        evidence="merge abc1234",
    )
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    text = (record_tree / "docs/architecture_log.md").read_text(encoding="utf-8")
    assert text.endswith(
        "\n".join(
            [
                "## 2026-09-30 — A later change",
                "KIND:          computed",
                "FROM → TO:     an old shape → a new shape",
                "WHY:           the old shape was weak",
                "INVALIDATES:   nothing",
                "EVIDENCE:      merge abc1234",
                "",
            ]
        )
    )


def real_last_entry_text() -> str:
    """The real docs/architecture_log.md's current last entry, rendered exactly as it stands (trailing_blank_lines == 0, since nothing follows it)."""
    real_spec = yaml.safe_load((PROJECT_ROOT / ".claude/memento.yaml").read_text(encoding="utf-8"))["kinds"]["change"]
    text = (PROJECT_ROOT / "docs/architecture_log.md").read_text(encoding="utf-8")
    _prefix, raws = split_log(text)
    entry = parse_raw_entry(raws[-1], real_spec)
    assert entry.trailing_blank_lines == 0
    return entry.render(width=real_spec["label_width"])


def test_add_closes_the_seam_behind_the_real_logs_last_entry(record_tree: Path) -> None:
    last_entry_text = real_last_entry_text()
    (record_tree / "docs/architecture_log.md").write_text("# Architecture Log\n\n" + last_entry_text, encoding="utf-8")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-10-10", title="A later change", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path), now=datetime.fromisoformat("2026-10-10T09:00:00+02:00"))
    assert result.exit_code == 0
    text = (record_tree / "docs/architecture_log.md").read_text(encoding="utf-8")
    seam = text.removeprefix("# Architecture Log\n\n" + last_entry_text.removesuffix("\n"))
    assert seam.startswith("\n\n## 2026-10-10 — A later change\n")


def test_add_on_the_real_log_ends_it_with_one_newline(record_tree: Path) -> None:
    real_text = (PROJECT_ROOT / "docs/architecture_log.md").read_text(encoding="utf-8")
    (record_tree / "docs/architecture_log.md").write_text(real_text, encoding="utf-8")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-10-10", title="A later change", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path), now=datetime.fromisoformat("2026-10-10T09:00:00+02:00"))
    assert result.exit_code == 0
    text = log_text(record_tree)
    assert text.startswith(real_text + "\n## 2026-10-10 — A later change\n")
    assert text.endswith("INVALIDATES:   nothing\n")
    assert not text.endswith("\n\n")


def break_frozen_base_entry(root: Path) -> None:
    """Give the fixture log's first entry a KIND no schema allows, so that entry alone fails its shape."""
    replace_once(root, "docs/architecture_log.md", "## 2026-04-09 — The frozen base changes\nKIND:          computed", "## 2026-04-09 — The frozen base changes\nKIND:          sideways")


def test_add_accepts_a_log_with_a_malformed_older_entry(record_tree: Path) -> None:
    first = run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-09-30", title="Second", merged=head_sha(record_tree))))
    assert first.exit_code == 0
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-10-10", title="Third", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path), now=datetime.fromisoformat("2026-10-10T09:00:00+02:00"))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    check = run_memento(record_tree, "check")
    assert check.exit_code == 1
    assert "KIND 'sideways' is not one of" in check.stdout


def test_supersede_leaves_a_malformed_older_entry_alone(record_tree: Path) -> None:
    first = run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-09-30", title="Second", merged=head_sha(record_tree))))
    assert first.exit_code == 0
    break_frozen_base_entry(record_tree)
    result = run_memento(record_tree, "change", "supersede", "2026-09-30", "Second", "2026-10-01 — replaced by a later one")
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert "SUPERSEDED-BY: 2026-10-01 — replaced by a later one\n" in log_text(record_tree)


def test_age_leaves_a_malformed_entry_it_does_not_move_alone(record_tree: Path) -> None:
    add_second_entry(record_tree)
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-09-30 — Second\nKIND:          computed", "## 2026-09-30 — Second\nKIND:          sideways")
    text = log_text(record_tree)
    remaining = entries_prefix_of(text) + text[text.index("## 2026-09-30"):]
    set_change_budget(record_tree, round(len(remaining.split()) * fixture_schema()["token_estimate"]["tokens_per_word"]))
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 0
    assert "docs/orchestration_log/archive/architecture_log-2026-04-09--2026-04-09.md" in result.stdout
    assert log_text(record_tree) == remaining


def test_age_refuses_a_log_with_no_whole_entry_to_move(record_tree: Path) -> None:
    (record_tree / "docs/architecture_log.md").write_text("# Architecture Log\n\n", encoding="utf-8")
    set_change_budget(record_tree, 1)
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 1
    assert "refused: no whole entry can move out of the change log to fit its budget of 1 tokens" in result.stderr


def test_age_refuses_a_moved_entry_that_does_not_parse(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    set_change_budget(record_tree, 1)
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 1
    assert "refused: KIND 'sideways' is not one of" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_a_start_before_the_last_entrys(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-01-01", title="Too early", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: start 2026-01-01 is before the last entry's start 2026-04-09" in result.stderr


def test_add_refuses_a_start_after_today(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-10-02", title="Too late", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: start 2026-10-02 is after today (2026-10-01)" in result.stderr


def test_add_refuses_an_end_after_today(record_tree: Path) -> None:
    entry_path = write_entry_yaml(
        record_tree.parent, start="2026-09-30", end="2026-10-05", title="Spans past today", merged=head_sha(record_tree)
    )
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: end 2026-10-05 is after today (2026-10-01)" in result.stderr


def test_add_refuses_a_valid_end_date_written_quoted(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", end="2026-09-30", title="Quoted end", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("end: 2026-09-30", 'end: "2026-09-30"'), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: end 2026-09-30 is quoted; write it unquoted as a YAML date" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_impossible_date_written_bare(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Impossible", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("start: 2026-09-30", "start: 2026-02-30"), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: 2026-02-30 is not a calendar date" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_a_valid_date_written_quoted(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Quoted", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("start: 2026-09-30", 'start: "2026-09-30"'), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: start 2026-09-30 is quoted; write it unquoted as a YAML date" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_impossible_date_written_quoted(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Impossible", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("start: 2026-09-30", 'start: "2026-02-30"'), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: 2026-02-30 is not a calendar date" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_file_that_does_not_read(record_tree: Path) -> None:
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(record_tree.parent / "missing.yaml"))
    assert result.exit_code == 1
    assert "refused: missing.yaml does not read: " in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_path_that_is_a_directory(record_tree: Path) -> None:
    directory = record_tree.parent / "entries"
    directory.mkdir()
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(directory))
    assert result.exit_code == 1
    assert "refused: entries does not read: " in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_file_that_is_not_utf8(record_tree: Path) -> None:
    entry_path = record_tree.parent / "entry.yaml"
    entry_path.write_bytes(b"start: \xff\xfe\n")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: entry.yaml is not UTF-8 text" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_file_that_is_not_valid_yaml(record_tree: Path) -> None:
    entry_path = record_tree.parent / "entry.yaml"
    entry_path.write_text("start: [2026-09-30\n", encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: entry.yaml is not valid YAML:" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_file_that_is_not_a_mapping(record_tree: Path) -> None:
    entry_path = record_tree.parent / "entry.yaml"
    entry_path.write_text("- start\n- end\n", encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: entry.yaml is not a mapping" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_empty_entry_file(record_tree: Path) -> None:
    entry_path = record_tree.parent / "entry.yaml"
    entry_path.write_text("", encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: entry.yaml is not a mapping" in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_file_with_a_key_that_is_not_text(record_tree: Path) -> None:
    entry_path = record_tree.parent / "entry.yaml"
    entry_path.write_text("1: one\n", encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: entry.yaml has a key that is not text" in result.stderr
    assert log_text(record_tree) == before


@pytest.mark.parametrize(
    ("verb", "refusal"),
    [
        ("add", "does not read"),
        ("supersede", "zero or several matching entries"),
        ("age", "already exist"),
        ("repair", "holds no FAIL of the change-log check"),
    ],
)
def test_each_change_verb_help_names_its_refusals(record_tree: Path, verb: str, refusal: str) -> None:
    result = run_memento(record_tree, "change", verb, "--help")
    assert result.exit_code == 0
    assert "Refuses:" in result.stdout
    assert refusal in " ".join(result.stdout.split())


def test_add_refuses_a_sha_not_on_main(record_tree: Path) -> None:
    entry_path = write_entry_yaml(
        record_tree.parent, start="2026-09-30", title="Unmerged", merged="0" * 40
    )
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert f"refused: {'0' * 40} is not on main; an entry is written after its change is merged" in result.stderr


def test_add_refuses_evidence_over_one_line(record_tree: Path) -> None:
    entry_path = write_entry_yaml(
        record_tree.parent, start="2026-09-30", title="Multi-line evidence", merged=head_sha(record_tree), evidence="line one\nline two"
    )
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: EVIDENCE spans 2 lines; it takes one" in result.stderr


def test_add_refuses_over_word_cap(record_tree: Path) -> None:
    long_why = " ".join(f"word{i}" for i in range(400))
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Over cap", merged=head_sha(record_tree), why=long_why)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: " in result.stderr
    assert "words outside INVALIDATES, over 300" in result.stderr


def test_add_refuses_a_candidate_fail(record_tree: Path) -> None:
    log_path = record_tree / "docs/architecture_log.md"
    baseline_words = len(log_path.read_text(encoding="utf-8").split())
    per_word = fixture_schema()["token_estimate"]["tokens_per_word"]
    tight_budget = round(baseline_words * per_word) + 1
    set_change_budget(record_tree, tight_budget)
    entry_path = write_entry_yaml(
        record_tree.parent,
        start="2026-09-30",
        title="Pushes the log over budget",
        merged=head_sha(record_tree),
        invalidates=[" ".join(f"filler{i}" for i in range(20))],
    )
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "over the change budget of" in result.stderr


def test_add_refuses_in_a_worktree(record_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git(record_tree, "worktree", "add", "-q", str(worktree))
    entry_path = write_entry_yaml(tmp_path, start="2026-09-30", title="From a worktree", merged=head_sha(record_tree))
    result = run_memento(worktree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "records are written in the root checkout only" in result.stderr


def test_add_refuses_an_impossible_date_in_a_log_header(record_tree: Path) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base", "## 2026-02-30 — The frozen base")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="A later change", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: 2026-02-30 is not a calendar date" in result.stderr
    assert log_text(record_tree) == before


def test_check_fails_an_impossible_date_in_a_log_header(record_tree: Path) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base", "## 2026-02-30 — The frozen base")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "FAIL  docs/architecture_log.md:" in result.stdout
    assert "2026-02-30 is not a calendar date" in result.stdout


def test_check_fails_an_impossible_end_date_in_a_log_header(record_tree: Path) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base", "## 2026-04-09 → 2026-02-30 — The frozen base")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "2026-02-30 is not a calendar date" in result.stdout


def test_add_refuses_an_impossible_date_in_another_field_of_the_entry_file(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Placeholder", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("title: Placeholder", "title: 2026-02-30"), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: 2026-02-30 is not a calendar date" in result.stderr
    assert log_text(record_tree) == before


def test_supersede_refuses_an_impossible_target_date(record_tree: Path) -> None:
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "supersede", "2026-02-30", "The frozen base", "2026-09-30 — replaced outright")
    assert result.exit_code == 1
    assert "refused: 2026-02-30 is not a calendar date" in result.stderr
    assert log_text(record_tree) == before


def test_supersede_refuses_zero_matches(record_tree: Path) -> None:
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "Unrelated title", "2026-09-30 — replaced")
    assert result.exit_code == 1
    assert "refused: no entry dated 2026-04-09 with title starting 'Unrelated title'" in result.stderr


def test_supersede_adds_superseded_by_to_an_entry_with_none(record_tree: Path) -> None:
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — replaced outright")
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    text = (record_tree / "docs/architecture_log.md").read_text(encoding="utf-8")
    assert "SUPERSEDED-BY: 2026-09-30 — replaced outright\n" in text


def test_supersede_appends_a_new_line_to_an_existing_superseded_by(record_tree: Path) -> None:
    run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — first supersession")
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-31 — second supersession")
    assert result.exit_code == 0
    text = (record_tree / "docs/architecture_log.md").read_text(encoding="utf-8")
    assert "SUPERSEDED-BY: 2026-09-30 — first supersession\n               2026-09-31 — second supersession\n" in text


def test_supersede_refuses_the_cap_broken_after_the_addition(record_tree: Path) -> None:
    long_by = " ".join(f"word{i}" for i in range(400))
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", long_by)
    assert result.exit_code == 1
    assert result.stderr == "refused: 417 words outside INVALIDATES, over 300\n"


def test_supersede_refuses_several_matches(record_tree: Path) -> None:
    run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-04-09", title="The frozen second one", merged=head_sha(record_tree))))
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen", "2026-09-30 — replaced")
    assert result.exit_code == 1
    assert "refused: 2 entries dated 2026-04-09 with title starting 'The frozen'; name one" in result.stderr


def test_supersede_refuses_in_a_worktree(record_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git(record_tree, "worktree", "add", "-q", str(worktree))
    result = run_memento(worktree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — replaced")
    assert result.exit_code == 1
    assert "records are written in the root checkout only" in result.stderr


def test_supersede_refuses_a_candidate_fail(record_tree: Path) -> None:
    set_change_budget(record_tree, round(len(log_text(record_tree).split()) * fixture_schema()["token_estimate"]["tokens_per_word"]) + 1)
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — replaced, which pushes the log over its now-tight budget")
    assert result.exit_code == 1
    assert "over the change budget of" in result.stderr


def test_age_moves_the_oldest_entries_and_conserves_every_byte(record_tree: Path) -> None:
    log_path = record_tree / "docs/architecture_log.md"
    run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-09-30", title="A second entry", merged=head_sha(record_tree))))
    text_with_both_entries = log_path.read_text(encoding="utf-8")
    second_entry_start = text_with_both_entries.index("## 2026-09-30")
    prefix_end = len("# Architecture Log\n\n")
    first_entry_block = text_with_both_entries[prefix_end:second_entry_start]
    second_entry_block = text_with_both_entries[second_entry_start:]
    per_word = fixture_schema()["token_estimate"]["tokens_per_word"]
    remaining_words = len(("# Architecture Log\n\n" + second_entry_block).split())
    set_change_budget(record_tree, round(remaining_words * per_word) + 1)
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 0
    archive_path = result.stdout.splitlines()[0]
    assert archive_path == "docs/orchestration_log/archive/architecture_log-2026-04-09--2026-04-09.md"
    archived_text = (record_tree / archive_path).read_text(encoding="utf-8")
    new_log_text = log_path.read_text(encoding="utf-8")
    assert archived_text == first_entry_block
    assert archived_text + new_log_text.removeprefix("# Architecture Log\n\n") == text_with_both_entries.removeprefix("# Architecture Log\n\n")
    assert "- path: docs/orchestration_log/archive/architecture_log-2026-04-09--2026-04-09.md" in result.stdout


def test_age_refuses_when_already_under_budget(record_tree: Path) -> None:
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 1
    assert "the change log is under its budget of" in result.stderr


def test_age_refuses_when_the_archive_file_exists(record_tree: Path) -> None:
    archive_dir = record_tree / "docs/orchestration_log/archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "architecture_log-2026-04-09--2026-04-09.md").write_text("already here\n", encoding="utf-8")
    set_change_budget(record_tree, 1)
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_age_refuses_in_a_worktree(record_tree: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git(record_tree, "worktree", "add", "-q", str(worktree))
    set_change_budget(worktree, 1)
    result = run_memento(worktree, "change", "age")
    assert result.exit_code == 1
    assert "records are written in the root checkout only" in result.stderr


def test_age_refuses_a_candidate_fail_when_no_rung_fits_the_budget(record_tree: Path) -> None:
    set_change_budget(record_tree, 1)
    result = run_memento(record_tree, "change", "age")
    assert result.exit_code == 1
    assert "over the change budget of 1" in result.stderr


def test_a_change_verb_refuses_a_schema_declaring_a_change_field_memento_does_not_know(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "    - SUPERSEDED-BY\n", "    - SUPERSEDED-BY\n    - NOTE\n")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — replaced outright")
    assert result.exit_code == 1
    assert "refused: schema declares change fields" in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_an_entry_that_passes_the_check(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: the entry whose header starts with '2026-04-09 — The frozen base changes' holds no FAIL of the change-log check; repair is for an entry that fails the check" in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_a_replacement_that_fails_the_check(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", kind="sideways", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: the replacement holds a FAIL of the change-log check: KIND 'sideways' is not one of" in result.stderr
    assert log_text(record_tree) == before


def test_repair_replaces_a_malformed_entry_whole(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    text = log_text(record_tree)
    assert text.startswith("# Architecture Log\n\n## 2026-04-09 — Repaired frozen base\nKIND:          computed\n")
    assert "sideways" not in text
    assert run_memento(record_tree, "check").exit_code == 0


def entries_prefix_of(text: str) -> str:
    """The change log's text before its first `## ` entry."""
    return text[: text.index("\n## ") + 1]


def add_second_entry(record_tree: Path) -> None:
    """Append a valid entry dated 2026-09-30 titled Second, after the fixture's 2026-04-09 entry."""
    result = run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-09-30", title="Second", merged=head_sha(record_tree))))
    assert result.exit_code == 0


def test_repair_refuses_a_quoted_start(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("start: 2026-04-09", 'start: "2026-04-09"'), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: start 2026-04-09 is quoted; write it unquoted as a YAML date" in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_a_quoted_end(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", end="2026-04-10", title="Repaired frozen base", merged=head_sha(record_tree))
    entry_path.write_text(entry_path.read_text(encoding="utf-8").replace("end: 2026-04-10", 'end: "2026-04-10"'), encoding="utf-8")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: end 2026-04-10 is quoted; write it unquoted as a YAML date" in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_a_replacement_that_puts_the_entry_below_it_out_of_order(record_tree: Path) -> None:
    add_second_entry(record_tree)
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-10-01", title="Repaired frozen base", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: the change adds 1 FAIL: docs/architecture_log.md  dated 2026-09-30 after an entry dated 2026-10-01" in result.stderr
    assert log_text(record_tree) == before


def test_repair_replaces_an_entry_dated_before_its_predecessor(record_tree: Path) -> None:
    add_second_entry(record_tree)
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-09-30 — Second", "## 2026-03-01 — Second")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-05-01", title="Repaired second", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-03-01 — Second", str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert "## 2026-05-01 — Repaired second\n" in log_text(record_tree)
    assert run_memento(record_tree, "check").exit_code == 0


def test_repair_keeps_the_seam_behind_a_repaired_entry_that_is_not_last(record_tree: Path) -> None:
    add_second_entry(record_tree)
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 0
    assert "\n\n## 2026-09-30 — Second\n" in log_text(record_tree)
    assert run_memento(record_tree, "check").exit_code == 0


def entry_text(body_lines: list[str], *, header: str = "## 2026-04-09 — Title") -> tuple[str, list[str]]:
    """One synthetic entry's header and body, for direct parse_entry_text tests."""
    return header, body_lines


def parse_kwargs() -> dict:
    """The schema-derived keyword arguments parse_entry_text takes, from the fixture change spec."""
    change_spec = spec()
    return {
        "width": change_spec["label_width"],
        "fields": change_spec["fields"],
        "required_fields": change_spec["required_fields"],
        "one_line_fields": change_spec["one_line_fields"],
        "kind_values": change_spec["kind_values"],
        "word_cap": change_spec["word_cap"],
    }


VALID_BODY = [
    "KIND:          computed",
    "FROM → TO:     old → new",
    "WHY:           reasons",
    "INVALIDATES:   nothing",
]


def test_parse_entry_text_round_trips_a_clean_entry() -> None:
    header, body = entry_text(VALID_BODY)
    entry = parse_entry_text(header, body, **parse_kwargs())
    assert entry.render(width=parse_kwargs()["width"]) == header + "\n" + "\n".join(body) + "\n"


def test_parse_entry_text_fails_a_bad_header() -> None:
    with pytest.raises(EntryShapeError, match="header is not"):
        parse_entry_text("## not a header", VALID_BODY, **parse_kwargs())


def test_parse_entry_text_fails_a_duplicate_field() -> None:
    body = [*VALID_BODY, "KIND:          runnable"]
    with pytest.raises(EntryShapeError, match="KIND appears twice"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_an_unknown_field() -> None:
    body = ["KIND:          computed", "BOGUS:         text", "FROM → TO:     old → new", "WHY:           reasons", "INVALIDATES:   nothing"]
    with pytest.raises(EntryShapeError, match="unknown field BOGUS"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_text_before_the_first_field() -> None:
    body = ["stray text", *VALID_BODY]
    with pytest.raises(EntryShapeError, match="text before the first field"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_an_under_indented_continuation() -> None:
    body = ["INVALIDATES:   a long line", "          under-indented"]
    with pytest.raises(EntryShapeError, match="continuation not indented to column 15"):
        parse_entry_text("## 2026-04-09 — Title", body, **parse_kwargs())


def test_parse_entry_text_fails_a_blank_line_between_fields() -> None:
    body = ["KIND:          computed", "", "FROM → TO:     old → new", "WHY:           reasons", "INVALIDATES:   nothing"]
    with pytest.raises(EntryShapeError, match="a blank line separates KIND from FROM → TO"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_a_missing_required_field() -> None:
    body = ["KIND:          computed", "FROM → TO:     old → new", "WHY:           reasons"]
    with pytest.raises(EntryShapeError, match="missing required field\\(s\\): INVALIDATES"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_fields_out_of_order() -> None:
    body = ["WHY:           reasons", "KIND:          computed", "FROM → TO:     old → new", "INVALIDATES:   nothing"]
    with pytest.raises(EntryShapeError, match="fields out of order"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_fails_a_bad_kind_value() -> None:
    body = ["KIND:          sideways", "FROM → TO:     old → new", "WHY:           reasons", "INVALIDATES:   nothing"]
    with pytest.raises(EntryShapeError, match="KIND 'sideways' is not one of"):
        parse_entry_text(*entry_text(body), **parse_kwargs())


def test_parse_entry_text_keeps_a_blank_paragraph_break_inside_invalidates() -> None:
    body = [
        "KIND:          computed",
        "FROM → TO:     old → new",
        "WHY:           reasons",
        "INVALIDATES:   first paragraph",
        "",
        "               second paragraph",
    ]
    entry = parse_entry_text(*entry_text(body), **parse_kwargs())
    assert entry.invalidates == "first paragraph\n\nsecond paragraph"
    assert entry.render(width=15) == "## 2026-04-09 — Title\n" + "\n".join(body) + "\n"


def test_parse_entry_text_keeps_a_leading_blank_line_after_the_header() -> None:
    header, body = entry_text(["", *VALID_BODY])
    entry = parse_entry_text(header, body, **parse_kwargs())
    assert entry.leading_blank_lines == 1
    assert entry.render(width=15) == header + "\n" + "\n".join(body) + "\n"


def test_parse_entry_text_keeps_several_trailing_blank_lines() -> None:
    header, body = entry_text([*VALID_BODY, "", ""])
    entry = parse_entry_text(header, body, **parse_kwargs())
    assert entry.trailing_blank_lines == 2
    assert entry.render(width=15) == header + "\n" + "\n".join(body) + "\n"


def test_the_real_architecture_log_round_trips_through_changeentry() -> None:
    text = (PROJECT_ROOT / "docs/architecture_log.md").read_text(encoding="utf-8")
    real_spec = yaml.safe_load((PROJECT_ROOT / ".claude/memento.yaml").read_text(encoding="utf-8"))["kinds"]["change"]
    prefix, raws = split_log(text)
    entries = [parse_raw_entry(raw, real_spec) for raw in raws]
    assert prefix + "".join(entry.render(width=real_spec["label_width"]) for entry in entries) == text


def test_a_schema_dropping_a_change_field_fails_the_structure_agreement(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "    - SUPERSEDED-BY\n", "")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "schema declares change fields" in result.stdout
    assert "amend the module with the schema" in result.stdout


def test_an_entry_dated_before_its_predecessor_fails_the_check(record_tree: Path) -> None:
    run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start="2026-09-30", title="Later", merged=head_sha(record_tree))))
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-09-30", "## 2026-01-01")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "dated 2026-01-01 after an entry dated 2026-04-09; entries run oldest first" in result.stdout


def test_a_fact_pointer_to_a_change_entry_that_does_not_exist_fails_the_check(record_tree: Path) -> None:
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input; decided 2026-05-01, see docs/architecture_log.md.")
    result = run_memento(record_tree, "check")
    assert result.exit_code == 1
    assert "FAIL  docs/capabilities.md:5  points at a change-log entry dated 2026-05-01; none exists" in result.stdout


def add_entry(record_tree: Path, start: str, title: str) -> None:
    """Append a valid entry dated start and titled title, asserting that the add lands."""
    result = run_memento(record_tree, "change", "add", str(write_entry_yaml(record_tree.parent, start=start, title=title, merged=head_sha(record_tree))))
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "header",
    ["## 2026-02-30 — The frozen base changes", "## 2026-04-09 - The frozen base changes", "## 2026-4-9 — The frozen base changes", "## 2026-04-09 —"],
)
def test_repair_selects_an_entry_whose_header_is_malformed(record_tree: Path, header: str) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base changes", header)
    assert run_memento(record_tree, "check").exit_code == 1
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", header.removeprefix("## "), str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert run_memento(record_tree, "check").exit_code == 0


def test_repair_refuses_a_header_prefix_that_matches_no_entry(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-05-01", title="Nothing", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-05-01", str(entry_path))
    assert result.exit_code == 1
    assert "refused: no entry whose header starts with '2026-05-01'" in result.stderr


def test_repair_refuses_a_header_prefix_that_matches_several_entries(record_tree: Path) -> None:
    add_entry(record_tree, "2026-09-30", "Second")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Nothing", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-", str(entry_path))
    assert result.exit_code == 1
    assert "refused: 2 entries whose header starts with '2026-'; name one" in result.stderr


def test_repair_refuses_an_entry_the_check_passes_though_a_malformed_entry_sits_above_it(record_tree: Path) -> None:
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base changes", "## 2026-04-01 — The frozen base changes")
    add_entry(record_tree, "2026-05-01", "P entry")
    add_entry(record_tree, "2026-06-01", "E entry")
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-05-01 — P entry\nKIND:          computed", "## 2026-05-01 — P entry\nKIND:          sideways")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-06-01", title="Repaired E", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-06-01 — E entry", str(entry_path))
    assert result.exit_code == 1
    assert "refused: the entry whose header starts with '2026-06-01 — E entry' holds no FAIL of the change-log check" in result.stderr
    assert log_text(record_tree) == before


def nested_entry_path(record_tree: Path) -> Path:
    """An entry file whose YAML nests 3000 lists deep, past what the YAML parser recurses through."""
    entry_path = record_tree.parent / "nested.yaml"
    entry_path.write_text("a: " + "[" * 3000 + "]" * 3000 + "\n", encoding="utf-8")
    return entry_path


def test_add_refuses_an_entry_file_nested_too_deeply_to_parse(record_tree: Path) -> None:
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(nested_entry_path(record_tree)))
    assert result.exit_code == 1
    assert "refused: nested.yaml is nested too deeply to parse" in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_an_entry_file_nested_too_deeply_to_parse(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(nested_entry_path(record_tree)))
    assert result.exit_code == 1
    assert "refused: nested.yaml is nested too deeply to parse" in result.stderr
    assert log_text(record_tree) == before


def test_repair_repairs_an_entry_the_check_fails_by_order_behind_a_malformed_header(record_tree: Path) -> None:
    add_entry(record_tree, "2026-05-01", "E entry")
    add_entry(record_tree, "2026-07-01", "P entry")
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-04-09 — The frozen base changes", "## 2026-06-01 — The frozen base changes")
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-07-01 — P entry", "## 2026-07-01 - P entry bad dash")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-06-15", title="Repaired E", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-05-01 — E entry", str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert "## 2026-06-15 — Repaired E\n" in log_text(record_tree)


def test_repair_refuses_a_replacement_that_reproduces_the_finding_it_repairs(record_tree: Path) -> None:
    add_entry(record_tree, "2026-09-30", "Second")
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-09-30 — Second", "## 2026-03-01 — Second")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-03-01", title="Repaired second", merged=head_sha(record_tree))
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-03-01 — Second", str(entry_path))
    assert result.exit_code == 1
    assert "refused: the replacement holds a FAIL of the change-log check: dated 2026-03-01 after an entry dated 2026-04-09; entries run oldest first" in result.stderr
    assert log_text(record_tree) == before


def test_repair_lands_a_replacement_that_clears_the_finding(record_tree: Path) -> None:
    add_entry(record_tree, "2026-09-30", "Second")
    replace_once(record_tree, "docs/architecture_log.md", "## 2026-09-30 — Second", "## 2026-03-01 — Second")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-05-01", title="Repaired second", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "repair", "2026-03-01 — Second", str(entry_path))
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert run_memento(record_tree, "check").exit_code == 0


@pytest.mark.parametrize("separator", [" ", "\x0c"])
def test_the_verbs_and_check_cut_a_log_at_the_same_line_breaks(record_tree: Path, separator: str) -> None:
    add_entry(record_tree, "2026-09-30", "Second")
    replace_once(record_tree, "docs/architecture_log.md", "\n## 2026-09-30 — Second", f"{separator}## 2026-09-30 — Second")
    assert run_memento(record_tree, "check").exit_code == 0
    result = run_memento(record_tree, "change", "supersede", "2026-09-30", "Second", "2026-10-01 — replaced by a later one")
    assert (result.exit_code, result.stdout) == (0, "docs/architecture_log.md\n")
    assert run_memento(record_tree, "check").exit_code == 0


def test_add_refuses_an_entry_key_the_entry_does_not_take(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Keyed", merged=head_sha(record_tree), model="gpt")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: model: " in result.stderr
    assert log_text(record_tree) == before


def test_add_refuses_an_entry_value_the_entry_does_not_take(record_tree: Path) -> None:
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Valued", merged=head_sha(record_tree), invalidates="nothing")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert "refused: invalidates: " in result.stderr
    assert log_text(record_tree) == before


def test_repair_refuses_an_entry_key_the_entry_does_not_take(record_tree: Path) -> None:
    break_frozen_base_entry(record_tree)
    entry_path = write_entry_yaml(record_tree.parent, start="2026-04-09", title="Repaired frozen base", merged=head_sha(record_tree), model="gpt")
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "repair", "2026-04-09 — The frozen base changes", str(entry_path))
    assert result.exit_code == 1
    assert "refused: model: " in result.stderr
    assert log_text(record_tree) == before


def test_supersede_refuses_a_title_that_matches_an_entry_of_another_date(record_tree: Path) -> None:
    before = log_text(record_tree)
    result = run_memento(record_tree, "change", "supersede", "2026-05-01", "The frozen base", "2026-09-30 — replaced outright")
    assert result.exit_code == 1
    assert "refused: no entry dated 2026-05-01 with title starting 'The frozen base'" in result.stderr
    assert log_text(record_tree) == before


def test_add_names_the_main_branch_the_project_block_declares(record_tree: Path) -> None:
    replace_once(record_tree, ".claude/memento.yaml", "main_branch: main\n", "main_branch: trunk\n")
    entry_path = write_entry_yaml(record_tree.parent, start="2026-09-30", title="Unmerged", merged=head_sha(record_tree))
    result = run_memento(record_tree, "change", "add", str(entry_path))
    assert result.exit_code == 1
    assert f"refused: {head_sha(record_tree)} is not on trunk; an entry is written after its change is merged" in result.stderr
