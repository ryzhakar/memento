"""The baseline table: what a project tracks and the state it is expected in, checked row by row and written one row at a time."""

import re
from collections.abc import Iterator

import typer

from memento import Environment, Refusal, announce, quiet_option, refusals_reported
from memento.findings import DATE, Corpus, Finding, fail
from memento.living import land_text
from memento.pointers import home_of
from memento.schema import load_corpus

baseline_group = typer.Typer(no_args_is_help=True, help="Baseline: set one tracked thing's expected state, with a mark.")

KIND = "baseline"
HEADING_START = "# Baseline"
HEADER_ROW = "| Thing tracked | Expected state |"
SEPARATOR_ROW = "|---|---|"
DEPTH_PREFIX = "Depth reached:"
MARK = re.compile(rf"^— .+, {DATE}$")
ROW_CELLS = 2


def table_rows(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Each data row of the baseline table: its line number and its two cells."""
    rows = []
    for number, line in enumerate(lines, 1):
        if line.startswith("|") and line not in (HEADER_ROW, SEPARATOR_ROW):
            rows.append((number, [cell.strip() for cell in line.strip().strip("|").split("|")]))
    return rows


def check_baseline(corpus: Corpus) -> Iterator[Finding]:
    """Fail a baseline record whose heading, table, depth line or marks break the shape."""
    if KIND not in corpus.schema["kinds"]:
        return
    path = home_of(corpus, KIND)
    if not corpus.exists(path):
        yield fail(path, "the baseline record a declared kind needs is absent")
        return
    lines = corpus.text(path).splitlines()
    if not lines or not lines[0].startswith(HEADING_START):
        yield fail(f"{path}:1", f"the first line starts '{HEADING_START}'")
    if HEADER_ROW not in lines or SEPARATOR_ROW not in lines:
        yield fail(path, f"the table opens '{HEADER_ROW}' then '{SEPARATOR_ROW}'")
    seen: set[str] = set()
    for number, cells in table_rows(lines):
        where = f"{path}:{number}"
        if len(cells) != ROW_CELLS or not all(cells):
            yield fail(where, f"a row holds exactly {ROW_CELLS} non-empty cells")
        elif cells[0] in seen:
            yield fail(where, f"the thing '{cells[0]}' has more than one row")
        else:
            seen.add(cells[0])
    if sum(line.startswith(DEPTH_PREFIX) for line in lines) != 1:
        yield fail(path, f"exactly one line starts '{DEPTH_PREFIX}'")
    if not any(MARK.fullmatch(line) for line in lines):
        yield fail(path, "at least one mark line '— <what and by whom>, YYYY-MM-DD' ends the record")


def baseline_set(environment: Environment, thing: str, state: str, by: str) -> tuple[str, str]:
    """Set one thing's expected state, adding its row when absent, and append a mark; returns the baseline's path and the thing."""
    for name, value in (("thing", thing), ("state", state), ("by", by)):
        if not value.strip() or "|" in value or "\n" in value:
            raise Refusal(f"{name} must be one non-empty line without '|'")
    corpus = load_corpus(environment.store.root)
    if KIND not in corpus.schema["kinds"]:
        raise Refusal(f"the schema declares no '{KIND}' kind")
    path = home_of(corpus, KIND)
    lines = corpus.text(path).splitlines()
    row = f"| {thing.strip()} | {state.strip()} |"
    rows = table_rows(lines)
    existing = [number for number, cells in rows if cells[0] == thing.strip()]
    if existing:
        lines[existing[0] - 1] = row
    elif rows:
        lines.insert(rows[-1][0], row)
    else:
        raise Refusal(f"{path} holds no table to add a row to")
    today = environment.now().date().isoformat()
    lines.append(f"— {thing.strip()} restated, measured by {by.strip()}, {today}")
    land_text(environment, path, "\n".join(lines) + "\n")
    return path, thing.strip()


@baseline_group.command(
    "set",
    help=(
        "Set the expected state of one tracked thing: its row is replaced, or added after the last row, and a mark "
        "'— <thing> restated, measured by <by>, <today>' ends the record. Reads .claude/memento.yaml and the baseline "
        "record. Prints '<baseline home>: <thing>'. Refuses: a schema declaring no baseline kind; a thing, state or "
        "author that is empty, holds '|' or carries a line break; a record with no table; any candidate FAIL; a worktree."
    ),
)
def baseline_set_command(
    ctx: typer.Context,
    thing: str = typer.Argument(..., help="The thing tracked: the row's first cell."),
    state: str = typer.Argument(..., help="Its expected state, one line."),
    by: str = typer.Option(..., "--by", help="Who measured it, for the mark."),
    quiet: bool = quiet_option(),
) -> None:
    """Set one baseline row and mark it."""
    with refusals_reported():
        path, name = baseline_set(ctx.obj, thing, state, by)
    announce(f"{path}: {name}", quiet)
