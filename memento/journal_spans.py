"""Where a span's journal records live, and which span is the newest and whether it is open, read from the working tree."""

from pathlib import Path, PurePosixPath
from typing import NamedTuple

from memento import Refusal
from memento.findings import Corpus, split_entries
from memento.journal_shapes import (
    CLOSE_HEADER,
    MINUTE_FIELD,
    NEWLINE,
    SPAN_EVENT,
    WHAT_FIELD,
    entry_text,
    event_fields_of_kind,
)
from memento.schema import DATE_PLACEHOLDER, EVENTS_KIND, SEGMENT_WILDCARD, Schema, events_home, kind_for


NO_OPEN_SPAN = "no span is open; 'just memento events open <what>'"
SPAN_OPENING = "open"
SPAN_CLOSING = "close"
SPAN_MARK_END = ";"
RATIFIED_ON = "2026-09-23"
JOURNAL_FILE_PATTERNS = ["session*.md", "events*.md", "failures.md"]
HISTORY_GLOB = "docs/orchestration_log/history/*"
SESSION_STEM = "session"
EVENTS_STEM = "events"


def events_file_for(schema: Schema, day: str) -> str:
    """The unsuffixed events file of the span opened on a day."""
    return events_home(schema).replace(SEGMENT_WILDCARD, "").replace(DATE_PLACEHOLDER, day)


def session_file_for(events_file: str) -> str:
    """The session record beside a span's events file, carrying the same suffix."""
    path = PurePosixPath(events_file)
    return str(path.with_name(path.name.replace(EVENTS_STEM, SESSION_STEM, 1)))


def failures_file_for(schema: Schema, day: str) -> str:
    """The failures file of a span's day."""
    return schema["kinds"]["failure"]["home"].replace(DATE_PLACEHOLDER, day)


def span_day(events_file: str) -> str:
    """The date a span opened on, which names its directory."""
    return PurePosixPath(events_file).parent.name


def newest_events_file(root: Path, schema: Schema) -> str | None:
    """The newest span's events file, the unsuffixed one under the latest date, or None when no span has one."""
    found = (str(path.relative_to(root)) for path in root.glob(events_file_for(schema, SEGMENT_WILDCARD)))
    return max((path for path in found if kind_for(schema, path) == EVENTS_KIND), default=None)


class SpanMark(NamedTuple):
    """A line of an events file that opens or closes a span."""

    line_number: int
    minute: str
    mark: str


def span_marks(events_text: str) -> list[SpanMark]:
    """Every line of an events file whose span-event opens or closes a span, in file order."""
    marks = []
    for number, line in enumerate(events_text.splitlines(), 1):
        fields = event_fields_of_kind(line, SPAN_EVENT)
        if fields is not None:
            mark, separator, _rest = fields[WHAT_FIELD].partition(SPAN_MARK_END)
            if separator and mark in (SPAN_OPENING, SPAN_CLOSING):
                marks.append(SpanMark(number, fields[MINUTE_FIELD], mark))
    return marks


def open_since(events_text: str) -> str | None:
    """The minute the events file's span opened, or None when its last mark closes it or it holds none."""
    marks = span_marks(events_text)
    return marks[-1].minute if marks and marks[-1].mark == SPAN_OPENING else None


class SpanEvents(NamedTuple):
    """A span's events file and the text it holds."""

    events_file: str
    text: str


def open_span_events(root: Path, schema: Schema) -> SpanEvents:
    """The open span's events file and its text, refusing when the newest span is closed or none exists."""
    events_file = newest_events_file(root, schema)
    if events_file is None:
        raise Refusal(NO_OPEN_SPAN)
    text = (root / events_file).read_text(encoding="utf-8")
    if open_since(text) is None:
        raise Refusal(NO_OPEN_SPAN)
    return SpanEvents(events_file, text)


def events_tail(events_text: str) -> str:
    """An events file's text from its last span-event line to its end."""
    lines = events_text.splitlines(keepends=True)
    starts = [number for number, line in enumerate(lines) if event_fields_of_kind(line.rstrip(NEWLINE), SPAN_EVENT) is not None]
    return "".join(lines[starts[-1] if starts else 0 :])


class ClosedSpan(NamedTuple):
    """A close block found in a session record: its span's date, its minute, the line it opens on, and its text."""

    day: str
    minute: str
    line_number: int
    text: str

    def recency(self) -> tuple[str, str, int]:
        """How new the close is: by its span's date, then its minute, then how far down its record it sits."""
        return self.day, self.minute, self.line_number


def newest_close_block(corpus: Corpus) -> ClosedSpan | None:
    """The newest close block across every session record, by span date, then minute, then place in its record; None when none holds one."""
    found = [
        ClosedSpan(span_day(path), stamp["minute"], line_number, entry_text(header, body))
        for path in corpus.glob(f"{HISTORY_GLOB}/{SESSION_STEM}*.md")
        for line_number, header, body in split_entries(corpus.text(path))
        if (stamp := CLOSE_HEADER.fullmatch(header))
    ]
    return max(found, key=ClosedSpan.recency, default=None)
