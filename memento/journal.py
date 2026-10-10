"""The journal's verbs: open and add to a span's events, add to and close its narrative, record its failures."""

from pathlib import Path

import typer
from pydantic import TypeAdapter, ValidationError

from memento import Environment, Refusal, construct, refusals_reported
from memento.actor import Self_, parse_actor
from memento.clock import minute
from memento.findings import split_entries
from memento.journal_git import found_head
from memento.journal_shapes import (
    CHANGE_EVENT,
    CLOSE_HEADER,
    ENTRY_HEADING,
    FAILURE_EVENT,
    HEAD_LABEL,
    LABEL_MARK,
    LONGEST_CLOSE_LABEL,
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
    events_file_for,
    events_tail,
    failures_file_for,
    newest_close_block,
    newest_events_file,
    open_since,
    open_span_events,
    session_file_for,
    span_day,
)
from memento.schema import EVENT_KINDS, EventKind, agreeing_schema, load_corpus, load_schema


events_group = typer.Typer(no_args_is_help=True, help="Events: open a span, add a line to its events file, tail it.")
failure_group = typer.Typer(no_args_is_help=True, help="Failures: an entry together with its events line.")
digest_group = typer.Typer(no_args_is_help=True, help="The span's narrative: add a paragraph, close the span, print the newest close block.")

ROUTED_ELSEWHERE = {
    SPAN_EVENT: f"{SPAN_EVENT} lines are written by 'memento events open' or 'memento digest close'",
    FAILURE_EVENT: f"a {FAILURE_EVENT} is written by 'memento failure add', which writes its entry and its events line together",
    CHANGE_EVENT: f"{CHANGE_EVENT} goes to the change log: 'memento change add'",
}
MINUTE_WIDTH = len("YYYY-MM-DDTHH:MM")
CLOCK_TIME_START = len("YYYY-MM-DDT")
SESSION_TITLE = "# {day}\n"
FAILURES_TITLE = "# {day} — failures\n"
AFTER_CLOSE_HEADING = "## After the {time} close\n"
EVENT_KIND_ADAPTER = TypeAdapter(EventKind)


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


def text_if_present(root: Path, path: str) -> str | None:
    """A record's text, or None when it does not exist."""
    return (root / path).read_text(encoding="utf-8") if (root / path).exists() else None


def written_lines(path: str, text: str) -> list[str]:
    """What a verb prints for text it wrote to a path: one '<path>: <line>' per line that is not blank."""
    return [f"{path}: {line}" for line in text.splitlines() if line.strip()]


def record_event(environment: Environment, actor: str, kind: str, what: str) -> tuple[str, str]:
    """Append one event line to the open span's events file, returning the file's path and the line."""
    root = environment.store.root
    schema = agreeing_schema(root)
    author, event = parse_actor(actor), event_kind(kind)
    with environment.store.transaction() as act:
        line = construct(EventLine, at=environment.now(), actor=author, kind=event, what=what)
        span = open_span_events(root, schema)
        refuse_clock_behind(minute(line.at), span.text)
        act.append(span.events_file, line.render() + NEWLINE)
    return span.events_file, line.render()


@events_group.command(
    "add",
    help=(
        "Append one line '<minute> | <actor> | <kind> | <what>' to the open span's events file "
        "(the events home, <span date>/events.md), stamped by the clock. Reads .claude/memento.yaml "
        "and the events file; prints '<events file>: <line>'. Refuses: a kind other than decision, discovery, commitment, "
        "delegation, receipt or attenuation; an actor other than owner, self, agent:<name> or tool:<name>; "
        "a what that is not one line; no open span; a clock behind the events file's last line; a worktree; "
        "an events file whose working copy edits a line HEAD holds; a schema whose structure memento does not know."
    ),
)
def add(
    ctx: typer.Context,
    actor: str = typer.Argument(..., help="owner, self, agent:<name> or tool:<name>."),
    kind: str = typer.Argument(..., help="decision, discovery, commitment, delegation, receipt or attenuation."),
    what: list[str] = typer.Argument(..., help="What happened; several words are joined by single spaces."),
) -> None:
    """Append one event line to the open span's events file."""
    with refusals_reported():
        events_file, line = record_event(ctx.obj, actor, kind, " ".join(what))
    typer.echo(f"{events_file}: {line}")


def refuse_open_span(events_file: str, events_text: str) -> None:
    """Refuse opening a span while the newest one is open."""
    since = open_since(events_text)
    if since is not None:
        raise Refusal(f"span {span_day(events_file)} is open since {since}; resume it or close it")


def open_span(environment: Environment, what: str) -> tuple[str, str]:
    """Append a span-opening line to today's events file, returning the file's path and the line."""
    root = environment.store.root
    schema = agreeing_schema(root)
    with environment.store.transaction() as act:
        line = construct(EventLine, at=environment.now(), actor=Self_(), kind=SPAN_EVENT, what=f"{SPAN_OPENING}{SPAN_MARK_END} {what}")
        newest = newest_events_file(root, schema)
        if newest is not None:
            newest_text = (root / newest).read_text(encoding="utf-8")
            refuse_open_span(newest, newest_text)
            refuse_clock_behind(minute(line.at), newest_text)
        events_file = events_file_for(schema, line.at.date().isoformat())
        act.append(events_file, line.render() + NEWLINE)
    return events_file, line.render()


@events_group.command(
    "open",
    help=(
        "Open a span: append '<minute> | self | span-event | open; <what>' to today's events file "
        "(the events home, <today>/events.md), created with its directory when absent, stamped by the clock. "
        "Reads .claude/memento.yaml and the newest events file; prints '<events file>: <line>'. Refuses: the newest span "
        "still open; a what that is not one line; a clock behind the newest events file's last line; a worktree; "
        "an events file whose working copy edits a line HEAD holds; a schema whose structure memento does not know."
    ),
)
def open_(
    ctx: typer.Context,
    what: list[str] = typer.Argument(..., help="What the span opens on; several words are joined by single spaces."),
) -> None:
    """Open a span in today's events file."""
    with refusals_reported():
        events_file, line = open_span(ctx.obj, " ".join(what))
    typer.echo(f"{events_file}: {line}")


@events_group.command(
    "tail",
    help=(
        "Print the newest span's events file (the events home, <span date>/events.md), open or closed, "
        "from its last span-event line to its end. Reads .claude/memento.yaml and that file; prints nothing when no "
        "events file exists. Writes nothing and refuses nothing."
    ),
)
def tail(ctx: typer.Context) -> None:
    """Print the newest span's events from its last span-event line."""
    environment: Environment = ctx.obj
    root = environment.store.root
    events_file = newest_events_file(root, load_schema(root))
    typer.echo(events_tail((root / events_file).read_text(encoding="utf-8")) if events_file else "", nl=False)


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


def add_paragraphs(environment: Environment, texts: list[str]) -> tuple[str, str]:
    """Append each text as one paragraph to the open span's session record, returning the record's path and the text added."""
    root = environment.store.root
    schema = agreeing_schema(root)
    paragraphs = [construct(Paragraph, text=text) for text in texts]
    with environment.store.transaction() as act:
        events_file = open_span_events(root, schema).events_file
        session = session_file_for(events_file)
        added = session_lead(text_if_present(root, session), span_day(events_file))
        added += "".join(NEWLINE + paragraph.render() for paragraph in paragraphs)
        act.append(session, added)
    return session, added


@digest_group.command(
    "add",
    help=(
        "Append each argument as one paragraph to the open span's session record "
        "(the digest home, <span date>/session.md), created under a '# <span date>' title when absent; "
        "narrative after a close block opens under '## After the <HH:MM> close'. Reads .claude/memento.yaml, the open "
        "span's events file and its session record; prints '<session record>: <line>' per line written. Refuses: an "
        "argument holding a line break, empty or starting with a blank; an argument starting '## '; no open span; "
        "a worktree; a session record whose working copy edits a line HEAD holds."
    ),
)
def add_paragraph(
    ctx: typer.Context,
    paragraphs: list[str] = typer.Argument(..., help="One paragraph per argument, each one line."),
) -> None:
    """Append paragraphs to the open span's session record."""
    with refusals_reported():
        session, added = add_paragraphs(ctx.obj, paragraphs)
    typer.echo(NEWLINE.join(written_lines(session, added)))


def refuse_multiline_next(next_step: str) -> None:
    """Refuse a next step the span's close line cannot carry on one line."""
    if NEWLINE in next_step:
        raise Refusal("next is one line; the span's close line in the events file carries it")


def close_span(environment: Environment, state: str, open_items: str, next_step: str) -> list[tuple[str, str]]:
    """Append a close block to the open span's session record and its close line to its events file, in one act."""
    root = environment.store.root
    schema = agreeing_schema(root)
    spec = schema["kinds"]["digest"]
    refuse_disagreement(close_labels_disagreement(schema))
    refuse_disagreement(close_label_width_shortfall(schema))
    refuse_multiline_next(next_step)
    with environment.store.transaction() as act:
        events_file, events_text = open_span_events(root, schema)
        at = environment.now()
        head = found_head(environment.run_git, root)
        block = construct(CloseBlock, at=at, head=head, state=state, open_items=open_items, next_step=next_step)
        overrun = state_overrun(block, spec["state_max_lines"])
        if overrun is not None:
            raise Refusal(overrun)
        what = f"{SPAN_CLOSING}{SPAN_MARK_END} {HEAD_LABEL} {head.render()}{SPAN_MARK_END} {NEXT_LABEL}{LABEL_MARK} {next_step}"
        line = construct(EventLine, at=at, actor=Self_(), kind=SPAN_EVENT, what=what)
        refuse_clock_behind(minute(at), events_text)
        session = session_file_for(events_file)
        session_text = text_if_present(root, session)
        block_text = (SESSION_TITLE.format(day=span_day(events_file)) if session_text is None else "") + NEWLINE + block.render(spec["close_label_width"])
        act.append(session, block_text)
        act.append(events_file, line.render() + NEWLINE)
    return [(session, block_text), (events_file, line.render())]


@digest_group.command(
    "close",
    help=(
        "Close the open span: append a close block '## Close — <minute>' with HEAD, state, open and next to its session "
        "record (the digest home, <span date>/session.md, created under a '# <span date>' title when absent) "
        "and '<minute> | self | span-event | close; HEAD <head>; next: <next>' to its events file, in one act. HEAD is "
        "'<short sha> (clean)' or '<short sha> (dirty: <paths>)' from git status. A field's lines are separated by "
        "newlines. Reads .claude/memento.yaml, git, the events file and the session record; prints '<path>: <line>' per "
        "line written. Refuses: no open span; a HEAD naming no commit; a state over the schema's state_max_lines (3); an empty field; a field line "
        "starting with a blank; a next of more than one line; a changed path whose name holds ', '; a clock behind the "
        "events file's last line; a worktree; "
        "a file whose working copy edits a line HEAD holds; a schema whose structure or close labels memento does not know; "
        f"a schema close_label_width under {len(LONGEST_CLOSE_LABEL)}, the length of the longest close label '{LONGEST_CLOSE_LABEL}'."
    ),
)
def close(
    ctx: typer.Context,
    state: str = typer.Argument(..., help="Where the work stands, at most three lines."),
    open: str = typer.Argument(..., help="Waits, tripwires and delegations left open, or 'none'."),
    next: str = typer.Argument(..., help="The first step of the next span, one line."),
) -> None:
    """Close the open span with its close block and close line."""
    with refusals_reported():
        written = close_span(ctx.obj, state, open, next)
    typer.echo(NEWLINE.join(line for path, text in written for line in written_lines(path, text)))


@digest_group.command(
    "baseline",
    help=(
        "Print the newest close block across every session record under the digest home, as the record "
        "holds it: the one under the latest span date, then with the latest minute, then the later in its record. "
        "Prints nothing when no close block exists. Writes nothing and refuses nothing."
    ),
)
def baseline(ctx: typer.Context) -> None:
    """Print the newest close block."""
    environment: Environment = ctx.obj
    closed = newest_close_block(load_corpus(environment.store.root))
    typer.echo(closed.text if closed else "", nl=False)


def refuse_repeated_title(failures_file: str, failures_text: str | None, title: str) -> None:
    """Refuse a failure whose title an entry in the failures file already carries."""
    headers = [header for _line_number, header, _body in split_entries(failures_text or "")]
    if f"{ENTRY_HEADING}{title}" in headers:
        raise Refusal(f"failure '{title}' is already in {failures_file}")


def add_failure(environment: Environment, title: str, what_happened: str, mechanism: str, correction: str) -> list[tuple[str, str]]:
    """Append a failure entry to the open span's failures file and its failure line to its events file, in one act."""
    root = environment.store.root
    schema = agreeing_schema(root)
    refuse_disagreement(failure_labels_disagreement(schema))
    entry = construct(FailureEntry, title=title, what_happened=what_happened, mechanism=mechanism, correction=correction)
    with environment.store.transaction() as act:
        events_file, events_text = open_span_events(root, schema)
        day = span_day(events_file)
        failures_file = failures_file_for(schema, day)
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
        "the open span's failures file (the failure home, <span date>/failures.md, created under a "
        "'# <span date> — failures' title when absent) and '<minute> | self | failure | <title>' to its events file, in "
        "one act. Reads .claude/memento.yaml, the events file and the failures file; prints '<path>: <line>' per line "
        "written. Refuses: a part that is empty, more than one line, or starting with a blank; a title already in the "
        "failures file; no open span; a clock behind the events file's last line; a worktree; a file whose working copy "
        "edits a line HEAD holds; a schema whose structure or failure labels memento does not know."
    ),
)
def add_failure_entry(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="The failure's title, one line."),
    what_happened: str = typer.Argument(..., help="What happened, one line."),
    mechanism: str = typer.Argument(..., help="The mechanism behind it, one line."),
    correction: str = typer.Argument(..., help="The correction, one line."),
) -> None:
    """Append a failure entry and its events line."""
    with refusals_reported():
        written = add_failure(ctx.obj, title, what_happened, mechanism, correction)
    typer.echo(NEWLINE.join(line for path, text in written for line in written_lines(path, text)))
