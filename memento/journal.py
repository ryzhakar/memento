"""The journal's verbs: open and add to a span's events, add to and close its narrative, record its failures."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import typer
from pydantic import TypeAdapter, ValidationError

from memento import Environment, Refusal, announce, construct, quiet_option, refusals_reported
from memento.actor import Actor, Self_, parse_actor
from memento.clock import minute
from memento.findings import split_entries
from memento.journal_git import found_head
from memento.journal_shapes import (
    CHANGE_EVENT,
    CLOSE_HEADER,
    ENTRY_HEADING,
    EVENT_FIELD_SEPARATOR,
    FAILURE_EVENT,
    HEAD_LABEL,
    LABEL_MARK,
    LONGEST_CLOSE_LABEL,
    MINUTE_WIDTH,
    NEWLINE,
    NEXT_LABEL,
    SPAN_EVENT,
    CloseBlock,
    EventLine,
    FailureEntry,
    Paragraph,
    close_label_width_shortfall,
    close_labels_disagreement,
    failure_labels_disagreement,
    state_overrun,
)
from memento.journal_spans import (
    SPAN_CLOSING,
    SPAN_MARK_END,
    SPAN_OPENING,
    Span,
    events_tail,
    lineage_head,
    named_span,
    span_or_file,
    newest_close_block,
    open_since,
    open_span_events,
    open_spans,
    read_span_events,
    several_open,
    span_events,
    span_files,
    span_path,
)
from memento.schema import DIGEST_KIND, EVENT_KINDS, EVENTS_KIND, FAILURE_KIND, EventKind, Schema, agreeing_schema, load_corpus
from memento.store import Act, Store, refuse_worktree


events_group = typer.Typer(no_args_is_help=True, help="Events: open a span, add a line to its events file, tail it.")
failure_group = typer.Typer(no_args_is_help=True, help="Failures: an entry together with its events line.")
digest_group = typer.Typer(no_args_is_help=True, help="The span's narrative: add a paragraph, close the span, print the newest close block.")

STDIN_FIELD_COUNT = 3
STDIN_LINE_SHAPE = "is not '<actor> | <kind> | <what>'"
NO_STDIN_EVENTS = "stdin holds no event line"
STDIN_BESIDE_WORDS = "--stdin takes the events from stdin; give no actor, kind or what"
WORDS_OR_STDIN = "give an actor, a kind and a what, or --stdin"
ROUTED_ELSEWHERE = {
    SPAN_EVENT: f"{SPAN_EVENT} lines are written by 'memento events open' or 'memento digest close'",
    FAILURE_EVENT: f"a {FAILURE_EVENT} is written by 'memento failure add', which writes its entry and its events line together",
    CHANGE_EVENT: f"{CHANGE_EVENT} goes to the change log: 'memento change add'",
}
CLOCK_TIME_START = len("YYYY-MM-DDT")
SESSION_TITLE = "# {day}\n"
FAILURES_TITLE = "# {day} — failures\n"
AFTER_CLOSE_HEADING = "## After the {time} close\n"
EVENT_KIND_ADAPTER = TypeAdapter(EventKind)
SPAN_VARIABLE = "MEMENTO_SPAN"
SPAN_HELP = "The span to act on: '<date>' or '<date>-<suffix>'. Omitted, the span MEMENTO_SPAN names, else the one a .memento-span file names, else the only open span; required while several are open."
BASELINE_SPAN_HELP = (
    "Read only this span's session record: '<date>' or '<date>-<suffix>'. Omitted, the span MEMENTO_SPAN names, else the one a .memento-span file names, else every session record; "
    "required while several spans are open."
)


def span_option(help_text: str = SPAN_HELP) -> str | None:
    """The `--span` option every journal verb takes, defaulting to the span the MEMENTO_SPAN variable names."""
    return typer.Option(None, "--span", envvar=SPAN_VARIABLE, callback=span_or_file, help=help_text)


def span_transaction(store: Store, schema: Schema, span: Span) -> AbstractContextManager[Act]:
    """The transaction a span's verb writes in: the main span's in the root checkout alone, a suffixed span's confined to its own files in the root checkout or a linked worktree."""
    if span.suffix is None:
        refuse_worktree(store.root)
        return store.transaction()
    return store.confined_transaction(span_files(schema, span))


def refuse_disagreement(disagreement: str | None) -> None:
    """Refuse a write while the schema and memento disagree on the record's structure."""
    if disagreement is not None:
        raise Refusal(disagreement)


def event_kind(text: str) -> EventKind:
    """The event kind a kind argument names, refusing the kinds other verbs write."""
    if text in ROUTED_ELSEWHERE:
        raise Refusal(ROUTED_ELSEWHERE[text])
    try:
        return EVENT_KIND_ADAPTER.validate_python(text)
    except ValidationError as unknown:
        raise Refusal(f"'{text}' is not an event kind ({', '.join(EVENT_KINDS)})") from unknown


def refuse_clock_behind(stamp: str, events_text: str) -> None:
    """Refuse a line stamped before the events file's last line."""
    lines = events_text.splitlines()
    if lines and lines[-1][:MINUTE_WIDTH] > stamp:
        raise Refusal(f"the clock reads {stamp}, before the events file's last line at {lines[-1][:MINUTE_WIDTH]}")


def refuse_repeated_line(rendered: Sequence[str], events_text: str) -> None:
    """Refuse a line identical to the line written just before it, a retried write landing twice."""
    previous = events_text.splitlines()[-1:]
    for line in rendered:
        if previous and previous[0] == line:
            raise Refusal(f"the line '{line}' repeats the line just before it; a retried write lands once")
        previous = [line]


def text_if_present(root: Path, path: str) -> str | None:
    """A record's text, or None when it does not exist."""
    return (root / path).read_text(encoding="utf-8") if (root / path).exists() else None


def written_lines(path: str, text: str) -> list[str]:
    """What a verb prints for text it wrote to a path: one '<path>: <line>' per line that is not blank."""
    return [f"{path}: {line}" for line in text.splitlines() if line.strip()]


class EventRequest(NamedTuple):
    """One event a caller asks for: its actor and kind parsed, its what still to be validated, and the stdin line it came from, if any."""

    actor: Actor
    kind: EventKind
    what: str
    stdin_line: int | None


def refusal_naming_line(refusal: Refusal, stdin_line: int | None) -> Refusal:
    """The refusal, prefixed with the stdin line it concerns when it concerns one."""
    return refusal if stdin_line is None else Refusal(f"stdin line {stdin_line}: {refusal}")


def event_request(actor: str, kind: str, what: str, stdin_line: int | None) -> EventRequest:
    """The request a caller's actor, kind and what state, refusing an actor or kind off the schema's, naming the stdin line."""
    try:
        return EventRequest(parse_actor(actor), event_kind(kind), what, stdin_line)
    except Refusal as refusal:
        raise refusal_naming_line(refusal, stdin_line) from refusal


def stdin_event_request(stdin_line: int, text: str) -> EventRequest:
    """The request one stdin line '<actor> | <kind> | <what>' states, its what keeping any further ' | '."""
    fields = text.split(EVENT_FIELD_SEPARATOR, STDIN_FIELD_COUNT - 1)
    if len(fields) != STDIN_FIELD_COUNT:
        raise Refusal(f"stdin line {stdin_line} {STDIN_LINE_SHAPE}")
    actor, kind, what = fields
    return event_request(actor, kind, what, stdin_line)


def stdin_event_requests(text: str) -> list[EventRequest]:
    """One request per line of a stdin text, refused whole on its first line off the shape."""
    requests = [stdin_event_request(number, line) for number, line in enumerate(text.splitlines(), 1)]
    if not requests:
        raise Refusal(NO_STDIN_EVENTS)
    return requests


def requested_events(actor: str | None, kind: str | None, what: list[str] | None, stdin: bool, read_stdin: Callable[[], str]) -> list[EventRequest]:
    """The events a verb call asks for: the one its words state, or those of every stdin line, refusing both or neither."""
    words_given = actor is not None or kind is not None or bool(what)
    if stdin and words_given:
        raise Refusal(STDIN_BESIDE_WORDS)
    if stdin:
        return stdin_event_requests(read_stdin())
    if actor is None or kind is None or not what:
        raise Refusal(WORDS_OR_STDIN)
    return [event_request(actor, kind, " ".join(what), None)]


def event_lines(at: datetime, requests: Sequence[EventRequest]) -> list[EventLine]:
    """One events line per request, all stamped at one time, refused whole on the first line whose what is invalid."""
    lines = []
    for request in requests:
        try:
            lines.append(construct(EventLine, at=at, actor=request.actor, kind=request.kind, what=request.what))
        except Refusal as refusal:
            raise refusal_naming_line(refusal, request.stdin_line) from refusal
    return lines


def record_events(environment: Environment, requests: Sequence[EventRequest], wanted: Span | None) -> tuple[str, list[str]]:
    """Append one line per request to the open span's events file in one act, returning the file's path and the lines."""
    root = environment.store.root
    schema = agreeing_schema(root)
    span = open_span_events(root, schema, wanted).span
    with span_transaction(environment.store, schema, span) as act:
        lines = event_lines(environment.now(), requests)
        found = open_span_events(root, schema, span)
        refuse_clock_behind(minute(lines[0].at), found.text)
        refuse_repeated_line([line.render() for line in lines], found.text)
        act.append(found.events_file, "".join(line.render() + NEWLINE for line in lines))
    return found.events_file, [line.render() for line in lines]


@events_group.command(
    "add",
    help=(
        "Append one line '<minute> | <actor> | <kind> | <what>' to a span's events file "
        "(the events home, <span date>/events.md or <span date>/events-<suffix>.md), stamped by the clock. With --stdin, append one such "
        "line per stdin line '<actor> | <kind> | <what>' (a what may hold further ' | '), all in one act and all stamped alike. "
        "Reads .claude/memento.yaml and the events files; prints '<events file>: <line>' per line written. Refuses: a kind other than "
        "decision, discovery, commitment, delegation, receipt or attenuation; an actor other than owner, self, agent:<name> or tool:<name>; "
        "a what that is not one line; --stdin beside an actor, kind or what, or neither of them; a stdin with no line, or a stdin line "
        "off '<actor> | <kind> | <what>' or holding an invalid part, which refuses the whole batch naming its line number; "
        "a --span or MEMENTO_SPAN off the '<date>[-<suffix>]' shape, naming no events file, or naming a closed span; "
        "no open span; several open spans and neither --span nor MEMENTO_SPAN; a clock behind the events file's last line; a line identical to the one just before it; "
        "the main span in a linked worktree (a suffixed span writes there); "
        "an events file whose working copy edits a line HEAD holds; a schema whose structure memento does not know."
    ),
)
def add(
    ctx: typer.Context,
    actor: str | None = typer.Argument(None, help="owner, self, agent:<name> or tool:<name>. Omitted with --stdin."),
    kind: str | None = typer.Argument(None, help="decision, discovery, commitment, delegation, receipt or attenuation. Omitted with --stdin."),
    what: list[str] | None = typer.Argument(None, help="What happened; several words are joined by single spaces. Omitted with --stdin."),
    stdin: bool = typer.Option(False, "--stdin", help="Read one event per stdin line, '<actor> | <kind> | <what>', and append them all in one act."),
    span: str | None = span_option(),
    quiet: bool = quiet_option(),
) -> None:
    """Append one event line, or one per stdin line, to a span's events file."""
    with refusals_reported():
        requests = requested_events(actor, kind, what, stdin, typer.get_text_stream("stdin").read)
        events_file, lines = record_events(ctx.obj, requests, named_span(span))
    announce(NEWLINE.join(f"{events_file}: {line}" for line in lines), quiet)


def refuse_open_span(span: Span, events_text: str) -> None:
    """Refuse opening a span while its lineage's newest one is open."""
    since = open_since(events_text)
    if since is not None:
        raise Refusal(f"span {span.name} is open since {since}; resume it or close it")


def open_span(environment: Environment, what: str, suffix: str | None) -> tuple[str, str]:
    """Append a span-opening line to today's events file of the main span, or of the suffixed one beside it, returning the file's path and the line."""
    root = environment.store.root
    schema = agreeing_schema(root)
    today = construct(Span, day=environment.now().date().isoformat(), suffix=suffix)
    with span_transaction(environment.store, schema, today) as act:
        line = construct(EventLine, at=environment.now(), actor=Self_(), kind=SPAN_EVENT, what=f"{SPAN_OPENING}{SPAN_MARK_END} {what}")
        newest = lineage_head(root, schema, suffix)
        if newest is not None:
            refuse_open_span(newest.span, newest.text)
            refuse_clock_behind(minute(line.at), newest.text)
        events_file = span_path(schema, EVENTS_KIND, construct(Span, day=line.at.date().isoformat(), suffix=suffix))
        act.append(events_file, line.render() + NEWLINE)
    return events_file, line.render()


@events_group.command(
    "open",
    help=(
        "Open a span: append '<minute> | self | span-event | open; <what>' to today's events file "
        "(the events home, <today>/events.md; with --suffix <s>, <today>/events-<s>.md, a parallel span beside the main one), "
        "created with its directory when absent, stamped by the clock. "
        "Reads .claude/memento.yaml and the newest events file of that suffix; prints '<events file>: <line>'. Refuses: the newest span "
        "of that suffix still open; a suffix that is not lowercase letters and digits joined by single hyphens; a what that is not one line; "
        "a clock behind that events file's last line; the main span in a linked worktree (a suffixed span opens there); "
        "an events file whose working copy edits a line HEAD holds; a schema whose structure memento does not know."
    ),
)
def open_(
    ctx: typer.Context,
    what: list[str] = typer.Argument(..., help="What the span opens on; several words are joined by single spaces."),
    suffix: str | None = typer.Option(None, "--suffix", help="Open a parallel span named by this suffix, beside the main one."),
    quiet: bool = quiet_option(),
) -> None:
    """Open a span in today's events file."""
    with refusals_reported():
        events_file, line = open_span(ctx.obj, " ".join(what), suffix)
    announce(f"{events_file}: {line}", quiet)


@events_group.command(
    "tail",
    help=(
        "Print a span's events file (the events home, <span date>/events.md or <span date>/events-<suffix>.md), open or closed, "
        "from its last span-event line to its end: the --span or MEMENTO_SPAN named, else the only open span, else the most recently written. "
        "Reads .claude/memento.yaml and the events files; prints nothing when no events file exists. Writes nothing. "
        "Refuses: a --span or MEMENTO_SPAN off the '<date>[-<suffix>]' shape or naming no events file; several open spans and neither --span nor MEMENTO_SPAN; "
        "a schema whose structure memento does not know."
    ),
)
def tail(ctx: typer.Context, span: str | None = span_option()) -> None:
    """Print a span's events from its last span-event line."""
    environment: Environment = ctx.obj
    root = environment.store.root
    with refusals_reported():
        found = read_span_events(root, agreeing_schema(root), named_span(span))
    typer.echo(events_tail(found.text) if found else "", nl=False)


def last_close_minute(session_text: str) -> str | None:
    """The minute of the close block a session record ends in, or None when its last entry is no close block."""
    headers = [header for _line_number, header, _body in split_entries(session_text)]
    stamp = CLOSE_HEADER.fullmatch(headers[-1]) if headers else None
    return stamp["minute"] if stamp else None


def session_lead(session_text: str | None, day: str) -> str:
    """What precedes new narrative in a session record: its title when absent, a heading when it ends in a close block."""
    if session_text is None:
        return SESSION_TITLE.format(day=day)
    closed_at = last_close_minute(session_text)
    return NEWLINE + AFTER_CLOSE_HEADING.format(time=closed_at[CLOCK_TIME_START:]) if closed_at else ""


def add_paragraphs(environment: Environment, texts: list[str], wanted: Span | None) -> tuple[str, str]:
    """Append each text as one paragraph to the open span's session record, returning the record's path and the text added."""
    root = environment.store.root
    schema = agreeing_schema(root)
    paragraphs = [construct(Paragraph, text=text) for text in texts]
    span = open_span_events(root, schema, wanted).span
    with span_transaction(environment.store, schema, span) as act:
        open_span_events(root, schema, span)
        session = span_path(schema, DIGEST_KIND, span)
        added = session_lead(text_if_present(root, session), span.day)
        added += "".join(NEWLINE + paragraph.render() for paragraph in paragraphs)
        act.append(session, added)
    return session, added


@digest_group.command(
    "add",
    help=(
        "Append each argument as one paragraph to a span's session record "
        "(the digest home, <span date>/session.md or <span date>/session-<suffix>.md), created under a '# <span date>' title when absent; "
        "narrative after a close block opens under '## After the <HH:MM> close'. Reads .claude/memento.yaml, the "
        "span's events file and its session record; prints '<session record>: <line>' per line written. Refuses: an "
        "argument holding a line break, empty or starting with a blank; an argument starting '## '; a --span or MEMENTO_SPAN off the "
        "'<date>[-<suffix>]' shape, naming no events file, or naming a closed span; no open span; several open spans and neither --span nor MEMENTO_SPAN; "
        "the main span in a linked worktree (a suffixed span writes there); a session record whose working copy edits a line HEAD holds."
    ),
)
def add_paragraph(
    ctx: typer.Context,
    paragraphs: list[str] = typer.Argument(..., help="One paragraph per argument, each one line."),
    span: str | None = span_option(),
    quiet: bool = quiet_option(),
) -> None:
    """Append paragraphs to a span's session record."""
    with refusals_reported():
        session, added = add_paragraphs(ctx.obj, paragraphs, named_span(span))
    announce(NEWLINE.join(written_lines(session, added)), quiet)


def refuse_multiline_next(next_step: str) -> None:
    """Refuse a next step the span's close line cannot carry on one line."""
    if NEWLINE in next_step:
        raise Refusal("next is one line; the span's close line in the events file carries it")


def close_span(environment: Environment, state: str, open_items: str, next_step: str, wanted: Span | None) -> list[tuple[str, str]]:
    """Append a close block to the open span's session record and its close line to its events file, in one act."""
    root = environment.store.root
    schema = agreeing_schema(root)
    spec = schema["kinds"]["digest"]
    refuse_disagreement(close_labels_disagreement(schema))
    refuse_disagreement(close_label_width_shortfall(schema))
    refuse_multiline_next(next_step)
    span = open_span_events(root, schema, wanted).span
    with span_transaction(environment.store, schema, span) as act:
        _span, events_file, events_text = open_span_events(root, schema, span)
        at = environment.now()
        head = found_head(environment.run_git, root)
        block = construct(CloseBlock, at=at, head=head, state=state, open_items=open_items, next_step=next_step)
        overrun = state_overrun(block, spec["state_max_lines"])
        if overrun is not None:
            raise Refusal(overrun)
        what = f"{SPAN_CLOSING}{SPAN_MARK_END} {HEAD_LABEL} {head.render()}{SPAN_MARK_END} {NEXT_LABEL}{LABEL_MARK} {next_step}"
        line = construct(EventLine, at=at, actor=Self_(), kind=SPAN_EVENT, what=what)
        refuse_clock_behind(minute(at), events_text)
        session = span_path(schema, DIGEST_KIND, span)
        session_text = text_if_present(root, session)
        block_text = (SESSION_TITLE.format(day=span.day) if session_text is None else "") + NEWLINE + block.render(spec["close_label_width"])
        act.append(session, block_text)
        act.append(events_file, line.render() + NEWLINE)
    return [(session, block_text), (events_file, line.render())]


@digest_group.command(
    "close",
    help=(
        "Close a span: append a close block '## Close — <minute>' with HEAD, state, open and next to its session "
        "record (the digest home, <span date>/session.md or <span date>/session-<suffix>.md, created under a '# <span date>' title when absent) "
        "and '<minute> | self | span-event | close; HEAD <head>; next: <next>' to its events file, in one act. HEAD is "
        "'<short sha> (clean)' or '<short sha> (dirty: <paths>)' from git status. A field's lines are separated by "
        "newlines. Reads .claude/memento.yaml, git, the events file and the session record; prints '<path>: <line>' per "
        "line written. Refuses: a --span or MEMENTO_SPAN off the '<date>[-<suffix>]' shape, naming no events file, or naming a closed span; no open span; "
        "several open spans and neither --span nor MEMENTO_SPAN; a HEAD naming no commit; a state over the schema's state_max_lines (3); an empty field; a field line "
        "starting with a blank; a next of more than one line; a changed path whose name holds ', '; a clock behind the "
        "events file's last line; the main span in a linked worktree (a suffixed span closes there); "
        "a file whose working copy edits a line HEAD holds; a schema whose structure or close labels memento does not know; "
        f"a schema close_label_width under {len(LONGEST_CLOSE_LABEL)}, the length of the longest close label '{LONGEST_CLOSE_LABEL}'."
    ),
)
def close(
    ctx: typer.Context,
    state: str = typer.Argument(..., help="Where the work stands, at most three lines."),
    open: str = typer.Argument(..., help="Waits, tripwires and delegations left open, or 'none'."),
    next: str = typer.Argument(..., help="The first step of the next span, one line."),
    span: str | None = span_option(),
    quiet: bool = quiet_option(),
) -> None:
    """Close a span with its close block and close line."""
    with refusals_reported():
        written = close_span(ctx.obj, state, open, next, named_span(span))
    announce(NEWLINE.join(line for path, text in written for line in written_lines(path, text)), quiet)


@digest_group.command(
    "baseline",
    help=(
        "Print the newest close block across every session record under the digest home, as the record "
        "holds it: the one under the latest span date, then with the latest minute, then in the record whose file name sorts last, "
        "then the later in its record. With --span or MEMENTO_SPAN, only that span's session record is read. "
        "Prints nothing when no close block exists. Reads .claude/memento.yaml and the session records; writes nothing. "
        "Refuses: a --span or MEMENTO_SPAN off the '<date>[-<suffix>]' shape or naming no events file; several open spans and neither --span nor MEMENTO_SPAN; "
        "a schema whose structure memento does not know."
    ),
)
def baseline(ctx: typer.Context, span: str | None = span_option(BASELINE_SPAN_HELP)) -> None:
    """Print the newest close block."""
    environment: Environment = ctx.obj
    root = environment.store.root
    with refusals_reported():
        schema = agreeing_schema(root)
        named = named_span(span)
        if named is not None:
            span_events(root, schema, named)
        opened = open_spans(root, schema)
        if named is None and len(opened) > 1:
            raise Refusal(several_open(opened))
        closed = newest_close_block(load_corpus(root), named)
    typer.echo(closed.text if closed else "", nl=False)


def refuse_repeated_title(failures_file: str, failures_text: str | None, title: str) -> None:
    """Refuse a failure whose title an entry in the failures file already carries."""
    headers = [header for _line_number, header, _body in split_entries(failures_text or "")]
    if f"{ENTRY_HEADING}{title}" in headers:
        raise Refusal(f"failure '{title}' is already in {failures_file}")


def add_failure(environment: Environment, title: str, what_happened: str, mechanism: str, correction: str, wanted: Span | None) -> list[tuple[str, str]]:
    """Append a failure entry to the open span's failures file and its failure line to its events file, in one act."""
    root = environment.store.root
    schema = agreeing_schema(root)
    refuse_disagreement(failure_labels_disagreement(schema))
    entry = construct(FailureEntry, title=title, what_happened=what_happened, mechanism=mechanism, correction=correction)
    span = open_span_events(root, schema, wanted).span
    with span_transaction(environment.store, schema, span) as act:
        _span, events_file, events_text = open_span_events(root, schema, span)
        day = span.day
        failures_file = span_path(schema, FAILURE_KIND, span)
        failures_text = text_if_present(root, failures_file)
        refuse_repeated_title(failures_file, failures_text, title)
        line = construct(EventLine, at=environment.now(), actor=Self_(), kind=FAILURE_EVENT, what=title)
        refuse_clock_behind(minute(line.at), events_text)
        entry_added = (FAILURES_TITLE.format(day=day) if failures_text is None else "") + NEWLINE + entry.render()
        act.append(failures_file, entry_added)
        act.append(events_file, line.render() + NEWLINE)
    return [(failures_file, entry_added), (events_file, line.render())]


@failure_group.command(
    "add",
    help=(
        "Append a failure entry '## <title>' with its 'What happened:', 'Mechanism:' and 'Correction:' paragraphs to "
        "the open span's failures file (the failure home, <span date>/failures.md or <span date>/failures-<suffix>.md, created under a "
        "'# <span date> — failures' title when absent) and '<minute> | self | failure | <title>' to its events file, in "
        "one act. Reads .claude/memento.yaml, the events file and the failures file; prints '<path>: <line>' per line "
        "written. Refuses: a part that is empty, more than one line, or starting with a blank; a title already in the "
        "failures file; a --span or MEMENTO_SPAN off the '<date>[-<suffix>]' shape, naming no events file, or naming a closed span; no open span; "
        "several open spans and neither --span nor MEMENTO_SPAN; a clock behind the events file's last line; the main span in a linked worktree "
        "(a suffixed span writes there); a file whose working copy edits a line HEAD holds; a schema whose structure or "
        "failure labels memento does not know."
    ),
)
def add_failure_entry(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="The failure's title, one line."),
    what_happened: str = typer.Argument(..., help="What happened, one line."),
    mechanism: str = typer.Argument(..., help="The mechanism behind it, one line."),
    correction: str = typer.Argument(..., help="The correction, one line."),
    span: str | None = span_option(),
    quiet: bool = quiet_option(),
) -> None:
    """Append a failure entry and its events line."""
    with refusals_reported():
        written = add_failure(ctx.obj, title, what_happened, mechanism, correction, named_span(span))
    announce(NEWLINE.join(line for path, text in written for line in written_lines(path, text)), quiet)
