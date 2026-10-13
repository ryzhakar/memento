"""Reading the records back at a session's start: the pat-down, which writes nothing."""

import typer

from memento import Environment, Refusal, refusals_reported
from memento.findings import FAIL, Corpus, run_checks, split_entries
from memento.git import git_output
from memento.journal_git import found_head
from memento.journal_shapes import HEAD_LABEL, NEWLINE, Head, parse_close_block
from memento.journal_spans import ClosedSpan, SpanEvents, events_tail, newest_close_block, open_since, open_spans, reported_spans, span_path
from memento.schema import DIGEST_KIND, Schema, load_corpus

FIELD_WIDTH = len("commit  ")
COMMIT_ROW = "commit"
DIRTY_ROW = "dirty"
SPAN_ROW = "span"
CHECK_ROW = "check"
CLOSE_ROW = "close"
ADDED_MARK = "+"
REMOVED_MARK = "-"
NO_BASELINE = "none"
CLOSED_SPAN = "closed"
SECTION_SEPARATOR = "\n\n"
PATDOWN_HELP = (
    "Pat down the records: print the baseline (the newest close block), one events tail per open span (the newest span "
    "when none is open) from its last span-event line, the found state (HEAD with the paths git reports changed, and "
    "every open span with the minute it opened), and the anomalies against the baseline: each commit since the "
    "baseline's HEAD, each changed path added or gone since it, each open span the baseline closed (or every open span "
    "when there is no baseline), and the count of FAIL findings 'memento check' reports. Reads "
    ".claude/memento.yaml, every record, and git. Writes nothing. Refuses only a repository whose HEAD names no commit."
)


def labelled(label: str, text: str) -> str:
    """One report line: a label padded to the report's field width, then its text."""
    return label.ljust(FIELD_WIDTH) + text


def baseline_head(closed: ClosedSpan, label_width: int) -> Head:
    """The HEAD the baseline closed at, refused when its close block is off the close shape."""
    _line_number, header, body = next(split_entries(closed.text))
    return parse_close_block(header, body, label_width).head


def commits_since(corpus: Corpus, head: Head) -> list[str]:
    """One anomaly line per commit since the baseline's HEAD, newest first."""
    if not corpus.holds_object(head.sha):
        return [labelled(COMMIT_ROW, f"baseline HEAD {head.sha} is not in this repository")]
    return [labelled(COMMIT_ROW, line) for line in git_output(corpus.root, "log", "--format=%h %s", f"{head.sha}..HEAD").splitlines()]


def dirty_moves(baseline: Head | None, found: Head) -> list[str]:
    """One anomaly line per changed path that appeared or went away since the baseline."""
    before = set(baseline.dirty) if baseline else set()
    appeared = [labelled(DIRTY_ROW, f"{ADDED_MARK} {path}") for path in found.dirty if path not in before]
    gone = [labelled(DIRTY_ROW, f"{REMOVED_MARK} {path}") for path in sorted(before - set(found.dirty))]
    return appeared + gone


def baseline_anomalies(corpus: Corpus, closed: ClosedSpan | None, found: Head) -> list[str]:
    """The commits and the changed paths that moved since the baseline, or why the baseline cannot be read."""
    if closed is None:
        return dirty_moves(None, found)
    try:
        head = baseline_head(closed, corpus.schema["kinds"]["digest"]["close_label_width"])
    except Refusal as unreadable:
        return [labelled(CLOSE_ROW, f"the baseline close block is unreadable: {unreadable}"), *dirty_moves(None, found)]
    return commits_since(corpus, head) + dirty_moves(head, found)


def span_anomalies(schema: Schema, closed: ClosedSpan | None, opened: list[SpanEvents]) -> list[str]:
    """A line for each open span the baseline does not account for, or that it closed."""
    lines = []
    for found in opened:
        since = f"{found.span.name} open since {open_since(found.text)}"
        if closed is None:
            lines.append(labelled(SPAN_ROW, since))
        elif span_path(schema, DIGEST_KIND, found.span) == closed.file:
            lines.append(labelled(SPAN_ROW, f"{since}; the baseline closed it at {closed.minute}"))
    return lines


def tail_sections(reported: list[SpanEvents]) -> list[list[str]]:
    """One tail section per reported span, or an empty one when no span has an events file."""
    return [[f"tail {found.span.name}", events_tail(found.text).rstrip(NEWLINE)] for found in reported] or [["tail", ""]]


def span_rows(opened: list[SpanEvents]) -> list[str]:
    """One found-state row per open span, or one saying every span is closed."""
    return [labelled(SPAN_ROW, f"{found.span.name} open since {open_since(found.text)}") for found in opened] or [labelled(SPAN_ROW, CLOSED_SPAN)]


def patdown_report(environment: Environment) -> str:
    """The baseline, the tail, the found state and the anomalies against the baseline, as the pat-down prints them."""
    corpus = load_corpus(environment.store.root)
    closed = newest_close_block(corpus)
    found = found_head(environment.run_git, corpus.root)
    opened = open_spans(corpus.root, corpus.schema)
    failures = sum(finding.level == FAIL for finding in run_checks(corpus, environment.checks))
    anomalies = [*baseline_anomalies(corpus, closed, found), *span_anomalies(corpus.schema, closed, opened), labelled(CHECK_ROW, f"{failures} {FAIL}")]
    sections = [
        ["baseline", closed.text.rstrip(NEWLINE) if closed else NO_BASELINE],
        *tail_sections(reported_spans(corpus.root, corpus.schema)),
        ["found", labelled(HEAD_LABEL, found.render()), *span_rows(opened)],
        ["anomalies", *anomalies],
    ]
    return SECTION_SEPARATOR.join(NEWLINE.join(section) for section in sections) + NEWLINE


def patdown(ctx: typer.Context) -> None:
    """Print the pat-down of the records."""
    with refusals_reported():
        report = patdown_report(ctx.obj)
    typer.echo(report, nl=False)
