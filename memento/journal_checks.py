"""The journal's record checks: each record's shape, the files that must agree, and the history that is only ever appended."""

from collections.abc import Iterator
from pathlib import PurePosixPath

from memento import Refusal
from memento.actor import Owner, parse_actor
from memento.clock import minute
from memento.findings import Corpus, Finding, fail, split_entries
from memento.git import is_ancestor
from memento.journal_git import HistoryStart, history_start, lines_removed_since
from memento.journal_shapes import (
    ROUTED_EVENT_KINDS,
    ACTOR_FIELD,
    CLOSE_HEADING,
    FAILURE_EVENT,
    MINUTE_FIELD,
    QUOTED_LINE_WIDTH,
    close_label_width_shortfall,
    close_labels_disagreement,
    event_fields_of_kind,
    failure_labels_disagreement,
    parse_close_block,
    parse_event_line,
    parse_failure_entry,
    state_overrun,
)
from memento.journal_spans import (
    SPAN_CLOSING,
    SPAN_FILE_NAME,
    SPAN_MARK_END,
    SPAN_OPENING,
    span_marks,
    span_of,
    span_path,
)
from memento.project import JournalCorrection, history_glob, project_of
from memento.schema import DIGEST_KIND, EVENTS_KIND, FAILURE_KIND, SCHEMA_HOME, event_kinds_of, kind_for


SHORT_SHA_WIDTH = 8
DECISION_EVENT = "decision"
SPAN_FILE_PATTERNS = ((EVENTS_KIND, "events*.md"), (DIGEST_KIND, "session*.md"), (FAILURE_KIND, "failures*.md"))


def written_after_ratification(corpus: Corpus, pattern: str) -> list[str]:
    """Journal files the current formats govern; those standing at ratification keep the shape they had."""
    ratified_on = project_of(corpus.schema).ratified_on
    return [path for path in corpus.glob(f"{history_glob(corpus.schema)}/{pattern}") if PurePosixPath(path).parent.name > ratified_on]


def check_event_lines(corpus: Corpus) -> Iterator[Finding]:
    """Fail events files misdated at their first line, lines off the events shape, and time running backwards."""
    declared_kinds = {*event_kinds_of(corpus.schema), *ROUTED_EVENT_KINDS}
    for path in written_after_ratification(corpus, "events*.md"):
        day = PurePosixPath(path).parent.name
        lines = corpus.text(path).splitlines()
        if lines and not lines[0].startswith(day):
            yield fail(f"{path}:1", f"first line is not dated {day}, the date naming the file")
        previous = ""
        for number, line in enumerate(lines, 1):
            try:
                event = parse_event_line(line)
            except Refusal as unreadable:
                yield fail(f"{path}:{number}", str(unreadable))
                continue
            if event.kind not in declared_kinds:
                yield fail(f"{path}:{number}", f"'{event.kind}' is not an event kind this schema declares ({', '.join(sorted(declared_kinds))})")
            stamp = minute(event.at)
            if stamp < previous:
                yield fail(f"{path}:{number}", f"time {stamp} goes back from {previous}")
            previous = max(previous, stamp)


def close_block_findings(corpus: Corpus, path: str) -> Iterator[Finding]:
    """Fail each close block of a session record that breaks the close shape or runs its state too long."""
    spec = corpus.schema["kinds"]["digest"]
    for line_number, header, body in split_entries(corpus.text(path)):
        if not header.startswith(CLOSE_HEADING):
            continue
        where = f"{path}:{line_number}"
        try:
            block = parse_close_block(header, body, spec["close_label_width"])
        except Refusal as unreadable:
            yield fail(where, str(unreadable))
            continue
        overrun = state_overrun(block, spec["state_max_lines"])
        if overrun is not None:
            yield fail(where, overrun)


def check_close_blocks(corpus: Corpus) -> Iterator[Finding]:
    """Fail a schema whose close labels differ from CloseBlock's or whose close label width cannot hold them, and close blocks breaking the close shape in every session record written after ratification."""
    for disagreement in (close_labels_disagreement(corpus.schema), close_label_width_shortfall(corpus.schema)):
        if disagreement is not None:
            yield fail(SCHEMA_HOME, disagreement)
    for path in written_after_ratification(corpus, "session*.md"):
        yield from close_block_findings(corpus, path)


def check_failures(corpus: Corpus) -> Iterator[Finding]:
    """Fail a schema whose failure labels differ from FailureEntry's, entries off the failure shape, and failure files outnumbering their events file's failure lines."""
    disagreement = failure_labels_disagreement(corpus.schema)
    if disagreement is not None:
        yield fail(SCHEMA_HOME, disagreement)
    for path in written_after_ratification(corpus, "failures*.md"):
        entries = list(split_entries(corpus.text(path)))
        for line_number, header, body in entries:
            try:
                parse_failure_entry(header, body)
            except Refusal as unreadable:
                yield fail(f"{path}:{line_number}", str(unreadable))
        span = span_of(corpus.schema, FAILURE_KIND, path)
        if span is None:
            continue
        events_file = span_path(corpus.schema, EVENTS_KIND, span)
        events_lines = corpus.text(events_file).splitlines() if corpus.exists(events_file) else []
        pointers = sum(event_fields_of_kind(line, FAILURE_EVENT) is not None for line in events_lines)
        if pointers < len(entries):
            yield fail(path, f"{len(entries)} entries but {pointers} failure lines in {events_file}")


def check_spans_closed_in_digest(corpus: Corpus) -> Iterator[Finding]:
    """Fail events files recording more span closes than their session record holds close blocks."""
    for events_file in written_after_ratification(corpus, "events*.md"):
        span = span_of(corpus.schema, EVENTS_KIND, events_file)
        if span is None:
            continue
        closes = sum(mark.mark == SPAN_CLOSING for mark in span_marks(corpus.text(events_file)))
        digest = span_path(corpus.schema, DIGEST_KIND, span)
        blocks = sum(line.startswith(CLOSE_HEADING) for line in corpus.text(digest).splitlines()) if corpus.exists(digest) else 0
        if closes > blocks:
            yield fail(events_file, f"{closes} span closes but {blocks} close blocks in {digest}")


def check_span_file_names(corpus: Corpus) -> Iterator[Finding]:
    """Fail events, session and failures files written after ratification whose names carry no valid span."""
    for kind, pattern in SPAN_FILE_PATTERNS:
        for path in written_after_ratification(corpus, pattern):
            if span_of(corpus.schema, kind, path) is None:
                yield fail(path, SPAN_FILE_NAME)


def check_journal_history(corpus: Corpus) -> Iterator[Finding]:
    """Fail what the journal's history since the memento recipes first landed holds: a removed written line, then a span opened or closed out of turn."""
    start = history_start(corpus.root, project_of(corpus.schema).history_start_file)
    if start is None:
        return
    yield from removed_line_findings(corpus, start)
    yield from alternation_findings(corpus, start)


def removed_line_findings(corpus: Corpus, start: HistoryStart) -> Iterator[Finding]:
    """Fail every line a commit since the start removed from an events, session, failures or measurement file, except in a commit the project lists as an owner-ruled journal correction."""
    corrected = {correction.commit for correction in project_of(corpus.schema).journal_corrections}
    for removed in lines_removed_since(corpus.root, corpus.schema, start):
        if removed.sha not in corrected:
            yield fail(removed.path, f"commit {removed.sha[:SHORT_SHA_WIDTH]} removes a written line: {removed.line[:QUOTED_LINE_WIDTH]!r}")


def alternation_findings(corpus: Corpus, start: HistoryStart) -> Iterator[Finding]:
    """Fail a span-event opening an open span or closing a closed one, written since the start."""
    lineages: dict[str, str] = {}
    for events_file in written_after_ratification(corpus, "events*.md"):
        lineage = PurePosixPath(events_file).name
        for mark in span_marks(corpus.text(events_file)):
            due = SPAN_CLOSING if lineages.get(lineage) == SPAN_OPENING else SPAN_OPENING
            if mark.mark != due and mark.minute >= start.minute:
                yield fail(f"{events_file}:{mark.line_number}", f"span-event '{mark.mark}{SPAN_MARK_END}' where '{due}{SPAN_MARK_END}' is due; spans open and close in turn")
            lineages[lineage] = mark.mark


def is_owner(actor_field: str) -> bool:
    """Whether an events line's actor field spells the owner, bare or stamped with an identity."""
    try:
        return isinstance(parse_actor(actor_field), Owner)
    except Refusal:
        return False


def holds_owner_decision_at(events_text: str, stamp: str) -> bool:
    """Whether an events file holds an owner decision line stamped at the minute."""
    lines = (event_fields_of_kind(line, DECISION_EVENT) for line in events_text.splitlines())
    return any(fields is not None and fields[MINUTE_FIELD] == stamp and is_owner(fields[ACTOR_FIELD]) for fields in lines)


def ruling_problem(corpus: Corpus, correction: JournalCorrection) -> str | None:
    """Why a correction's ruling resolves to no owner decision line, or None when it resolves."""
    path, stamp = correction.ruling_path, correction.ruling_minute
    if kind_for(corpus.schema, path) != EVENTS_KIND:
        return f"ruling {path} is not an events file"
    if not corpus.exists(path):
        return f"ruling {path} does not exist"
    if not holds_owner_decision_at(corpus.text(path), stamp):
        return f"ruling {path} holds no 'owner | decision' line at {stamp}"
    return None


def check_journal_corrections(corpus: Corpus) -> Iterator[Finding]:
    """Fail a listed journal correction whose commit is not in HEAD's history, and one whose ruling resolves to no owner decision line at its minute."""
    for correction in project_of(corpus.schema).journal_corrections:
        if not is_ancestor(corpus.root, correction.commit, "HEAD"):
            yield fail(SCHEMA_HOME, f"journal correction commit {correction.commit} is not in HEAD's history")
        problem = ruling_problem(corpus, correction)
        if problem is not None:
            yield fail(SCHEMA_HOME, f"journal correction {correction.commit}: {problem}")
