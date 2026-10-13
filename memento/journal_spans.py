"""Which spans exist, where each one's journal records live, which are open, and which close block is the newest, read from the working tree."""

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel

from memento import Refusal, construct
from memento.findings import Corpus, normalized, split_entries
from memento.journal_shapes import (
    CLOSE_HEADER,
    FROZEN,
    MINUTE_FIELD,
    MINUTE_WIDTH,
    NEWLINE,
    SPAN_EVENT,
    WHAT_FIELD,
    entry_text,
    event_fields_of_kind,
)
from memento.schema import DATE_PLACEHOLDER, DIGEST_KIND, EVENTS_KIND, SEGMENT_WILDCARD, SPAN_KINDS, Schema, events_home


NO_OPEN_SPAN = "no span is open; 'memento events open <what>'"
SPAN_OPENING = "open"
SPAN_CLOSING = "close"
SPAN_MARK_END = ";"
JOURNAL_FILE_PATTERNS = ["session*.md", "events*.md", "failures*.md"]
DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
SUFFIX = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SPAN_NAME = re.compile(rf"(?P<day>{DAY})(?:-(?P<suffix>{SUFFIX}))?")
SPAN_SHAPE = "is not YYYY-MM-DD or YYYY-MM-DD-<suffix>, the suffix lowercase letters and digits joined by single hyphens"
SPAN_FILE_NAME = "not a span file: <name>[-<suffix>].md, the suffix lowercase letters and digits joined by single hyphens"


def a_day(text: str) -> str:
    """Admit a day spelled YYYY-MM-DD in ASCII digits."""
    if not re.fullmatch(DAY, text):
        raise ValueError(f"day '{text}' is not YYYY-MM-DD")
    return text


def a_suffix(text: str) -> str:
    """Admit a suffix of lowercase letters and digits joined by single hyphens."""
    if not re.fullmatch(SUFFIX, text):
        raise ValueError(f"suffix '{text}' is not lowercase letters and digits joined by single hyphens")
    return text


class Span(BaseModel):
    """One span: the day it opened on and, for a parallel span, the suffix naming it beside the main one."""

    model_config = FROZEN
    day: Annotated[str, AfterValidator(a_day)]
    suffix: Annotated[str, AfterValidator(a_suffix)] | None

    @property
    def name(self) -> str:
        """The span as `--span` and messages spell it: `<day>` or `<day>-<suffix>`."""
        return self.day if self.suffix is None else f"{self.day}-{self.suffix}"

    def order(self) -> tuple[str, str]:
        """How spans sort: by day, then by suffix, the main span first."""
        return self.day, self.suffix or ""


def parse_span(text: str) -> Span:
    """The span a `--span` argument names, refused when it is off the `<date>[-<suffix>]` shape."""
    named = SPAN_NAME.fullmatch(text)
    if named is None:
        raise Refusal(f"span '{text}' {SPAN_SHAPE}")
    return construct(Span, day=named["day"], suffix=named["suffix"])


def span_pattern(home: str) -> re.Pattern[str]:
    """The paths a span kind's home covers, its day and its optional suffix captured."""
    head, wildcard, tail = home.partition(SEGMENT_WILDCARD)
    dated = re.escape(head).replace(re.escape(DATE_PLACEHOLDER), f"(?P<day>{DAY})")
    suffix_slot = f"(?:-(?P<suffix>{SUFFIX}))?" if wildcard else ""
    return re.compile(dated + suffix_slot + re.escape(tail))


def span_of(schema: Schema, kind: str, path: str) -> Span | None:
    """The span a journal file of a kind belongs to, or None when its name carries no valid span."""
    named = span_pattern(schema["kinds"][kind]["home"]).fullmatch(path)
    if named is None:
        return None
    groups = named.groupdict()
    return construct(Span, day=groups.get("day", ""), suffix=groups.get("suffix"))


def span_path(schema: Schema, kind: str, span: Span) -> str:
    """The file a span's record of a kind lives in."""
    suffix = "" if span.suffix is None else f"-{span.suffix}"
    return schema["kinds"][kind]["home"].replace(DATE_PLACEHOLDER, span.day).replace(SEGMENT_WILDCARD, suffix)


def span_files(schema: Schema, span: Span) -> frozenset[str]:
    """Every journal file a span owns: its events, its session record and its failures."""
    return frozenset(span_path(schema, kind, span) for kind in SPAN_KINDS)


def events_glob(schema: Schema) -> str:
    """The glob matching every events file, whatever its day and suffix."""
    return events_home(schema).replace(DATE_PLACEHOLDER, SEGMENT_WILDCARD)


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
    """A span, its events file and the text it holds."""

    span: Span
    events_file: str
    text: str

    def recency(self) -> tuple[str, str, str]:
        """How recently the span was written: its day, then the minute of its last line, then its file."""
        lines = self.text.splitlines()
        return self.span.day, lines[-1][:MINUTE_WIDTH] if lines else "", self.events_file


def span_events(root: Path, schema: Schema, span: Span) -> SpanEvents:
    """A span's events file and its text, refused when the span has none."""
    events_file = span_path(schema, EVENTS_KIND, span)
    if not (root / events_file).is_file():
        raise Refusal(f"span {span.name} has no events file")
    return SpanEvents(span, events_file, (root / events_file).read_text(encoding="utf-8"))


def newest_per_lineage(root: Path, schema: Schema) -> dict[str | None, Span]:
    """The newest span of each lineage, the main span's and each suffix's, found by the days of the events files."""
    newest: dict[str | None, Span] = {}
    for path in root.glob(events_glob(schema)):
        span = span_of(schema, EVENTS_KIND, normalized(path.relative_to(root)))
        if span is not None and (span.suffix not in newest or newest[span.suffix].day < span.day):
            newest[span.suffix] = span
    return newest


def lineage_head(root: Path, schema: Schema, suffix: str | None) -> SpanEvents | None:
    """The newest span of one lineage with its events text, or None when the lineage has no events file."""
    newest = newest_per_lineage(root, schema).get(suffix)
    return None if newest is None else span_events(root, schema, newest)


def open_spans(root: Path, schema: Schema) -> list[SpanEvents]:
    """Every lineage's newest span whose events file ends open, ordered by span."""
    heads = (span_events(root, schema, span) for span in sorted(newest_per_lineage(root, schema).values(), key=Span.order))
    return [head for head in heads if open_since(head.text) is not None]


def newest_span(root: Path, schema: Schema) -> SpanEvents | None:
    """The most recently written lineage head, open or closed, or None when no span has an events file."""
    heads = (span_events(root, schema, span) for span in newest_per_lineage(root, schema).values())
    return max(heads, key=SpanEvents.recency, default=None)


def several_open(opened: list[SpanEvents]) -> str:
    """The refusal for an omitted span while several are open, naming each."""
    return f"several spans are open: {', '.join(found.span.name for found in opened)}; name one with --span <date>[-<suffix>]"


def open_span_events(root: Path, schema: Schema, named: Span | None) -> SpanEvents:
    """The span a write goes to: the named one when it is open, else the only open one, refused when the named one is closed or absent, when none is open, and when several are open and none is named."""
    if named is not None:
        found = span_events(root, schema, named)
        if open_since(found.text) is None:
            raise Refusal(f"span {named.name} is closed")
        return found
    opened = open_spans(root, schema)
    if not opened:
        raise Refusal(NO_OPEN_SPAN)
    if len(opened) > 1:
        raise Refusal(several_open(opened))
    return opened[0]


def read_span_events(root: Path, schema: Schema, named: Span | None) -> SpanEvents | None:
    """The span a read prints: the named one, open or closed, else the only open one, else the newest, or None when no span exists; several open and none named is refused."""
    if named is not None:
        return span_events(root, schema, named)
    opened = open_spans(root, schema)
    if len(opened) > 1:
        raise Refusal(several_open(opened))
    return opened[0] if opened else newest_span(root, schema)


def reported_spans(root: Path, schema: Schema) -> list[SpanEvents]:
    """The spans the pat-down reports: every open one, or the newest when none is open."""
    opened = open_spans(root, schema)
    newest = newest_span(root, schema)
    return opened or ([newest] if newest is not None else [])


def events_tail(events_text: str) -> str:
    """An events file's text from its last span-event line to its end."""
    lines = events_text.splitlines(keepends=True)
    starts = [number for number, line in enumerate(lines) if event_fields_of_kind(line.rstrip(NEWLINE), SPAN_EVENT) is not None]
    return "".join(lines[starts[-1] if starts else 0 :])


class ClosedSpan(NamedTuple):
    """A close block found in a session record: its span's date, its minute, the record it sits in, the line it opens on, and its text."""

    day: str
    minute: str
    file: str
    line_number: int
    text: str

    def recency(self) -> tuple[str, str, str, int]:
        """How new the close is: by its span's date, then its minute, then its record's file name, then how far down its record it sits."""
        return self.day, self.minute, PurePosixPath(self.file).name, self.line_number


def close_blocks_in(corpus: Corpus, path: str) -> list[ClosedSpan]:
    """Every close block a session record holds."""
    return [
        ClosedSpan(PurePosixPath(path).parent.name, stamp["minute"], path, line_number, entry_text(header, body))
        for line_number, header, body in split_entries(corpus.text(path))
        if (stamp := CLOSE_HEADER.fullmatch(header))
    ]


def newest_close_block(corpus: Corpus, named: Span | None = None) -> ClosedSpan | None:
    """The newest close block across every session record, or in the named span's alone; None when there is none."""
    if named is None:
        paths = corpus.glob(corpus.schema["kinds"][DIGEST_KIND]["home"].replace(DATE_PLACEHOLDER, SEGMENT_WILDCARD))
    else:
        paths = [path for path in [span_path(corpus.schema, DIGEST_KIND, named)] if corpus.exists(path)]
    return max((block for path in paths for block in close_blocks_in(corpus, path)), key=ClosedSpan.recency, default=None)
