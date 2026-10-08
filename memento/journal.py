"""The journal, appended and never edited: traces, close blocks and failures."""

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, cast

import typer
from pydantic import AfterValidator, BaseModel, ConfigDict

from memento import Environment, Refusal, construct, refusals_reported
from memento.actor import Actor, parse_actor
from memento.clock import minute
from memento.findings import MINUTE, SHA, Corpus, Finding, fail, split_entries
from memento.schema import (
    DATE_PLACEHOLDER,
    SEGMENT_WILDCARD,
    TRACE_EVENT_KINDS,
    EventKind,
    Schema,
    agreeing_schema,
    kind_for,
    trace_home,
)

trace_group = typer.Typer(no_args_is_help=True, help="The trace: one line per event, appended to the open span's trace.")

ROUTED_ELSEWHERE = {
    "span-event": "span-event lines are written by 'just memento span open|close'",
    "failure": "a failure is written by 'just memento failure add', which writes its entry and its trace line together",
    "change": "change goes to the change log: 'just memento change add'",
}
NO_OPEN_SPAN = "no span is open; 'just memento span open <what>'"
SPAN_EVENT = "span-event"
SPAN_OPENING = "open"
SPAN_CLOSING = "close"
TRACE_FIELD_SEPARATOR = " | "
TRACE_FIELDS = 4
KIND_FIELD = 2
WHAT_FIELD = 3
MINUTE_WIDTH = len("YYYY-MM-DDTHH:MM")
VISIBLE_START = re.compile(r"\S")

RATIFIED_ON = "2026-09-23"
JOURNAL_FILE_PATTERNS = ["session*.md", "events*.md", "failures.md"]
HISTORY_GLOB = "docs/orchestration_log/history/*"
CLOSE_HEADING = "## Close"
CLOSE_HEADER = re.compile(rf"## Close — {MINUTE}")
SPAN_CLOSE_LINE = re.compile(r" \| span-event \| close")
FAILURE_LINE = " | failure | "
STATE_LABEL = "state"
HEAD_LABEL = "HEAD"
QUOTED_LINE_WIDTH = 60


def written_after_ratification(corpus: Corpus, pattern: str) -> list[str]:
    """Journal files the current formats govern; those standing at ratification keep the shape they had."""
    return [path for path in corpus.glob(f"{HISTORY_GLOB}/{pattern}") if PurePosixPath(path).parent.name > RATIFIED_ON]


def trace_line_shape(schema: Schema) -> re.Pattern[str]:
    """The shape every trace line takes: minute, actor, an event kind the schema declares, and what."""
    kinds = "|".join(re.escape(kind) for kind in schema["events"])
    return re.compile(rf"^(?P<minute>{MINUTE}) \| (owner|self|agent:[\w.-]+|tool:[\w.-]+) \| ({kinds}) \| \S")


def check_traces(corpus: Corpus) -> Iterator[Finding]:
    """Fail trace files misdated at their first line, lines off the trace shape, and time running backwards."""
    line_shape = trace_line_shape(corpus.schema)
    for path in written_after_ratification(corpus, "events*.md"):
        day = PurePosixPath(path).parent.name
        lines = corpus.text(path).splitlines()
        if lines and not lines[0].startswith(day):
            yield fail(f"{path}:1", f"first line is not dated {day}, the date naming the file")
        previous = ""
        for number, line in enumerate(lines, 1):
            shape = line_shape.match(line)
            if not shape:
                yield fail(f"{path}:{number}", "not 'YYYY-MM-DDTHH:MM | actor | kind | what'")
                continue
            if shape["minute"] < previous:
                yield fail(f"{path}:{number}", f"time {shape['minute']} goes back from {previous}")
            previous = max(previous, shape["minute"])


def close_block_findings(corpus: Corpus, path: str) -> Iterator[Finding]:
    """Fail each close block of a session record that breaks the schema's close shape."""
    spec = corpus.schema["kinds"]["digest"]
    labels, width = spec["close_labels"], spec["close_label_width"]
    for line_number, header, body in split_entries(corpus.text(path)):
        if not header.startswith(CLOSE_HEADING):
            continue
        where = f"{path}:{line_number}"
        if not CLOSE_HEADER.fullmatch(header):
            yield fail(where, "close header is not '## Close — YYYY-MM-DDTHH:MM'")
        found: list[tuple[str, int]] = []
        for line in (line for line in body if line.strip()):
            label = line[:width].strip()
            if label in labels and line[:width] == label.ljust(width):
                found.append((label, 1))
            elif line.startswith(" " * width) and found:
                found[-1] = (found[-1][0], found[-1][1] + 1)
            else:
                yield fail(where, f"line outside the close fields: {line[:QUOTED_LINE_WIDTH]!r}")
        if [label for label, _ in found] != labels:
            yield fail(where, f"close fields {[label for label, _ in found]} are not {labels}")
        for label, count in found:
            if label == STATE_LABEL and count > spec["state_max_lines"]:
                yield fail(where, f"state runs {count} lines, over {spec['state_max_lines']}")
        head_line = next((line for line in body if line.startswith(HEAD_LABEL)), "")
        if not SHA.match(head_line[width:]):
            yield fail(where, "HEAD does not start with a commit SHA")


def check_close_blocks(corpus: Corpus) -> Iterator[Finding]:
    """Fail close blocks breaking the close shape in every session record written after ratification."""
    for path in written_after_ratification(corpus, "session*.md"):
        yield from close_block_findings(corpus, path)


def check_failures(corpus: Corpus) -> Iterator[Finding]:
    """Fail failure entries missing their labelled paragraphs, and failure files outnumbering their trace's failure lines."""
    labels = corpus.schema["kinds"]["failure"]["entry_labels"]
    for path in written_after_ratification(corpus, "failures.md"):
        entries = list(split_entries(corpus.text(path)))
        for line_number, _header, body in entries:
            openings = [label for line in body for label in labels if line.startswith(f"{label}:")]
            if openings != labels:
                yield fail(f"{path}:{line_number}", f"paragraphs open {openings}, not {labels}")
        trace = str(PurePosixPath(path).parent / "events.md")
        pointers = sum(FAILURE_LINE in line for line in corpus.text(trace).splitlines()) if corpus.exists(trace) else 0
        if pointers < len(entries):
            yield fail(path, f"{len(entries)} entries but {pointers} failure lines in {trace}")


def check_spans_closed_in_digest(corpus: Corpus) -> Iterator[Finding]:
    """Fail traces recording more span closes than their session record holds close blocks."""
    for trace in written_after_ratification(corpus, "events*.md"):
        closes = sum(SPAN_CLOSE_LINE.search(line) is not None for line in corpus.text(trace).splitlines())
        trace_path = PurePosixPath(trace)
        digest = str(trace_path.parent / trace_path.name.replace("events", "session"))
        blocks = sum(line.startswith(CLOSE_HEADING) for line in corpus.text(digest).splitlines()) if corpus.exists(digest) else 0
        if closes > blocks:
            yield fail(trace, f"{closes} span closes but {blocks} close blocks in {digest}")


def visible_single_line(text: str) -> str:
    """Admit an event's what as one line opening on a visible character."""
    if not VISIBLE_START.match(text):
        raise ValueError("what must start with a visible character")
    if text.splitlines() != [text]:
        raise ValueError("what is one line; it carries a line break")
    return text


type What = Annotated[str, AfterValidator(visible_single_line)]


class TraceLine(BaseModel):
    """One event as the trace records it: when, who, which kind, and what."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    at: datetime
    actor: Actor
    kind: EventKind
    what: What

    def render(self) -> str:
        """The line as the trace file holds it."""
        return TRACE_FIELD_SEPARATOR.join([minute(self.at), self.actor.render(), self.kind, self.what])


def event_kind(text: str) -> EventKind:
    """The trace event kind a kind argument names, refusing the kinds other verbs write."""
    if text in ROUTED_ELSEWHERE:
        raise Refusal(ROUTED_ELSEWHERE[text])
    if text not in TRACE_EVENT_KINDS:
        raise Refusal(f"'{text}' is not an event kind ({', '.join(TRACE_EVENT_KINDS)})")
    return cast(EventKind, text)


def newest_trace(root: Path, schema: Schema) -> str:
    """The newest span's trace, the unsuffixed one under the latest date, refusing when no span has one."""
    unsuffixed = trace_home(schema).replace(SEGMENT_WILDCARD, "")
    found = (str(path.relative_to(root)) for path in root.glob(unsuffixed.replace(DATE_PLACEHOLDER, SEGMENT_WILDCARD)))
    traces = sorted(path for path in found if kind_for(schema, path) == "trace")
    if not traces:
        raise Refusal(NO_OPEN_SPAN)
    return traces[-1]


def kinds_and_whats(trace_text: str) -> list[tuple[str, str]]:
    """The kind and the what of every line of a trace that has all four fields."""
    split = (line.split(TRACE_FIELD_SEPARATOR, TRACE_FIELDS - 1) for line in trace_text.splitlines())
    return [(fields[KIND_FIELD], fields[WHAT_FIELD]) for fields in split if len(fields) == TRACE_FIELDS]


def refuse_closed_span(trace_text: str) -> None:
    """Refuse when the trace's last span-event that opens or closes a span closes it."""
    markers = [what for kind, what in kinds_and_whats(trace_text) if kind == SPAN_EVENT and what.startswith((SPAN_OPENING, SPAN_CLOSING))]
    if not markers or not markers[-1].startswith(SPAN_OPENING):
        raise Refusal(NO_OPEN_SPAN)


def refuse_clock_behind(stamp: str, trace_text: str) -> None:
    """Refuse a line stamped before the trace's last line."""
    lines = trace_text.splitlines()
    if lines and lines[-1][:MINUTE_WIDTH] > stamp:
        raise Refusal(f"the clock reads {stamp}, before the trace's last line at {lines[-1][:MINUTE_WIDTH]}")


def record_event(environment: Environment, actor: str, kind: str, what: str) -> tuple[str, str]:
    """Append one event line to the open span's trace, returning the trace's path and the line."""
    root = environment.store.root
    schema = agreeing_schema(root)
    line = construct(TraceLine, at=environment.now(), actor=parse_actor(actor), kind=event_kind(kind), what=what)
    with environment.store.transaction() as act:
        trace = newest_trace(root, schema)
        trace_text = (root / trace).read_text(encoding="utf-8")
        refuse_closed_span(trace_text)
        refuse_clock_behind(minute(line.at), trace_text)
        act.append(trace, line.render() + "\n")
    return trace, line.render()


@trace_group.command(
    "add",
    help=(
        "Append one line '<minute> | <actor> | <kind> | <what>' to the open span's trace "
        "(docs/orchestration_log/history/<span date>/events.md), stamped by the clock. Reads .claude/memento.yaml "
        "and the trace; prints '<trace>: <line>'. Refuses: a kind other than decision, discovery, commitment, "
        "delegation, receipt or attenuation; an actor other than owner, self, agent:<name> or tool:<name>; "
        "a what that is not one line; no open span; a clock behind the trace's last line; a worktree; "
        "a trace whose working copy edits a line HEAD holds; a schema whose structure memento does not know."
    ),
)
def add(
    ctx: typer.Context,
    actor: str = typer.Argument(..., help="owner, self, agent:<name> or tool:<name>."),
    kind: str = typer.Argument(..., help="decision, discovery, commitment, delegation, receipt or attenuation."),
    what: list[str] = typer.Argument(..., help="What happened; several words are joined by single spaces."),
) -> None:
    """Append one event line to the open span's trace."""
    with refusals_reported():
        trace, line = record_event(ctx.obj, actor, kind, " ".join(what))
    typer.echo(f"{trace}: {line}")
