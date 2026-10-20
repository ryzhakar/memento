"""Every write verb takes --quiet and prints nothing on success; a refusal still prints on stderr and exits 1."""

from collections.abc import Callable
from pathlib import Path

import click
import pytest
import typer.main

from memento.__main__ import app
from tests.conftest import assert_quiet_write, fixture_schema, git, replace_once, run_memento
from tests.test_changelog import FROZEN_BASE_PREFIX, break_frozen_base_entry, head_sha, log_text, set_change_budget, write_entry_yaml
from tests.test_journal import FAILURE_PARTS
from tests.test_living import ROW_YAML, RULE_YAML, row_data, write_yaml

QUIET = "--quiet"
READ_VERBS = frozenset({("check",), ("patdown",), ("help",), ("events", "tail"), ("digest", "baseline")})
Invocation = tuple[tuple[str, ...], str | None]
Case = Callable[[Path, Path], Invocation]


def verb_paths() -> dict[tuple[str, ...], click.Command]:
    """Every verb the CLI registers, by its group and verb names."""
    verbs: dict[tuple[str, ...], click.Command] = {}
    for name, command in typer.main.get_command(app).commands.items():
        if isinstance(command, click.Group):
            verbs.update({(name, verb): inner for verb, inner in command.commands.items()})
        else:
            verbs[(name,)] = command
    return verbs


def test_every_write_verb_takes_quiet_and_no_read_verb_does() -> None:
    verbs = verb_paths()
    assert READ_VERBS <= verbs.keys()
    takers = {path for path, command in verbs.items() if any(QUIET in parameter.opts for parameter in command.params)}
    assert takers == verbs.keys() - READ_VERBS


def words(*arguments: str) -> Case:
    """A case that needs no setup: the verb and its arguments as given."""
    return lambda _root, _scratch: (arguments, None)


def change_entry(root: Path, scratch: Path) -> Invocation:
    """`change add` of a later entry merged at the tree's HEAD."""
    entry = write_entry_yaml(scratch, start="2026-09-30", title="A later change", merged=head_sha(root))
    return ("change", "add", str(entry)), None


def change_age(root: Path, scratch: Path) -> Invocation:
    """`change age` over a log of two entries whose budget holds only the second."""
    entry = write_entry_yaml(scratch, start="2026-09-30", title="A second entry", merged=head_sha(root))
    assert run_memento(root, "change", "add", str(entry)).exit_code == 0
    second_entry = log_text(root)[log_text(root).index("## 2026-09-30") :]
    remaining_words = len(("# Architecture Log\n\n" + second_entry).split())
    set_change_budget(root, round(remaining_words * fixture_schema()["token_estimate"]["tokens_per_word"]) + 1)
    return ("change", "age"), None


def change_repair(root: Path, scratch: Path) -> Invocation:
    """`change repair` of the fixture's first entry, which fails the check."""
    break_frozen_base_entry(root)
    entry = write_entry_yaml(scratch, start="2026-04-09", title="Repaired frozen base", merged=head_sha(root))
    return ("change", "repair", FROZEN_BASE_PREFIX, str(entry)), None


def directive_file(root: Path, _scratch: Path) -> str:
    """A rule file for `measure-first` with a changed `do`."""
    return write_yaml(root, RULE_YAML, {"id": "measure-first", "heading": "commit & merge", "when": "quoting a number", "do": "measure it twice", "ground": "owner ruling 2026-08-04"})


def directive_new(root: Path, scratch: Path) -> Invocation:
    """`directive add` of a new rule."""
    rule = write_yaml(root, RULE_YAML, {"id": "new-rule", "heading": "pipeline code", "when": "x", "do": "y", "ground": "owner ruling 2026-08-04"})
    return ("directive", "add", rule), None


def directive_replaced(root: Path, scratch: Path) -> Invocation:
    """`directive replace` of `measure-first`."""
    return ("directive", "replace", directive_file(root, scratch)), None


def results_new(root: Path, _scratch: Path) -> Invocation:
    """`results add` of a new row."""
    return ("results", "add", write_yaml(root, ROW_YAML, row_data())), None


def results_replaced(root: Path, _scratch: Path) -> Invocation:
    """`results replace` of the fixture's one row."""
    return ("results", "replace", write_yaml(root, ROW_YAML, row_data(approach="contender", world="contender", verdict="leads"))), None


def spec_committed(root: Path) -> None:
    """Commit a spec named `one` into the tree."""
    (root / "docs/specs/one.md").write_text("# one\n\nOld text.\n", encoding="utf-8")
    git(root, "add", "docs/specs/one.md")
    git(root, "commit", "-q", "-m", "fixture spec")


def spec_new(root: Path, scratch: Path) -> Invocation:
    """`spec add` of a draft."""
    draft = scratch / "draft.md"
    draft.write_text("# a new spec\n", encoding="utf-8")
    return ("spec", "add", "a-new-spec", str(draft)), None


def spec_replaced(root: Path, _scratch: Path) -> Invocation:
    """`spec replace` in the committed spec."""
    spec_committed(root)
    return ("spec", "replace", "one", "Old text.", "New text."), None


def spec_archived(root: Path, _scratch: Path) -> Invocation:
    """`spec archive` of the committed spec."""
    spec_committed(root)
    return ("spec", "archive", "one"), None


def commit_records(root: Path, _scratch: Path) -> Invocation:
    """`commit` of the capabilities record after an edit, in a repository that has a committer."""
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.com")
    replace_once(root, "docs/capabilities.md", "slices its input.", "slices its input in pieces.")
    return ("commit", "record the edit", "docs/capabilities.md"), None


STDIN_EVENTS = "owner | decision | the first\nself | decision | the second\n"
WRITE_CASES: dict[str, Case] = {
    "events add": words("events", "add", "self", "decision", "x"),
    "events add --stdin": lambda _root, _scratch: (("events", "add", "--stdin"), STDIN_EVENTS),
    "events open": words("events", "open", "--suffix", "quiet", "a", "span"),
    "digest add": words("digest", "add", "a paragraph"),
    "digest close": words("digest", "close", "s", "o", "n"),
    "failure add": words("failure", "add", *FAILURE_PARTS),
    "change add": change_entry,
    "change supersede": words("change", "supersede", "2026-04-09", "The frozen base", "2026-09-30 — replaced"),
    "change age": change_age,
    "change repair": change_repair,
    "change set": words("change", "set", FROZEN_BASE_PREFIX, "EVIDENCE", "merge abc1234"),
    "directive add": directive_new,
    "directive replace": directive_replaced,
    "directive set": words("directive", "set", "measure-first", "why", "a reason"),
    "directive drop": words("directive", "drop", "measure-first"),
    "results add": results_new,
    "results replace": results_replaced,
    "results set": words("results", "set", "contender", "contender", "verdict", "leads"),
    "results drop": words("results", "drop", "contender", "contender"),
    "fact replace": words("fact", "replace", "The scorer slices its input.", "The scorer slices its input in chunks."),
    "string_recipes replace": words("string_recipes", "replace", "# string_recipes\n", "# string_recipes\n\nMore text.\n"),
    "spec add": spec_new,
    "spec replace": spec_replaced,
    "spec archive": spec_archived,
    "commit": commit_records,
}


def test_the_write_cases_cover_every_write_verb_but_the_measurement_log() -> None:
    covered = {tuple(name.split()[:2]) if name != "commit" else ("commit",) for name in WRITE_CASES}
    assert covered == verb_paths().keys() - READ_VERBS - {("measurement", "add"), ("measurement", "void"), ("init",), ("baseline", "set")}  # init writes only into an empty repository: tests/test_scaffold.py; baseline set needs a baseline kind: tests/test_baseline.py


@pytest.mark.parametrize("case", WRITE_CASES.values(), ids=WRITE_CASES.keys())
def test_a_write_verb_under_quiet_lands_its_write_and_prints_nothing(record_tree: Path, tmp_path: Path, case: Case) -> None:
    arguments, stdin = case(record_tree, tmp_path)
    assert_quiet_write(record_tree, *arguments, stdin=stdin)


@pytest.mark.parametrize("case", WRITE_CASES.values(), ids=WRITE_CASES.keys())
def test_the_same_write_verb_without_quiet_prints_its_result(record_tree: Path, tmp_path: Path, case: Case) -> None:
    arguments, stdin = case(record_tree, tmp_path)
    result = run_memento(record_tree, *arguments, stdin=stdin)
    assert (result.exit_code, result.stdout != "", result.stderr) == (0, True, "")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("events", "add", "self", "decided", "x"), "'decided' is not an event kind (decision, discovery, commitment, delegation, receipt, attenuation)"),
        (("change", "set", FROZEN_BASE_PREFIX, "NOTE", "x"), "'NOTE' is not a change field; the fields are: KIND, FROM → TO, WHY, INVALIDATES, EVIDENCE, SUPERSEDED-BY, merged"),
        (("results", "drop", "nope", "nowhere"), "no row in docs/results.md carries key ('nope', 'nowhere')"),
        (("directive", "drop", "no-such-rule"), "rule 'no-such-rule' is not in docs/conventions.md"),
        (("spec", "archive", "none"), "'docs/specs/none.md' does not exist"),
        (("commit", "subject", "notes.txt"), "notes.txt is not a record; commit it with its code"),
    ],
)
def test_a_refusal_under_quiet_still_prints_on_stderr_and_exits_one(record_tree: Path, arguments: tuple[str, ...], message: str) -> None:
    (record_tree / "notes.txt").write_text("x\n", encoding="utf-8")
    result = run_memento(record_tree, *arguments, QUIET)
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", f"refused: {message}\n")
