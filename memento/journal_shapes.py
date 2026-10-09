"""The journal's record shapes: events lines, HEAD fields, close blocks, failure entries and paragraphs, each parsed and rendered."""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from memento import Refusal, construct
from memento.actor import Actor, parse_actor
from memento.clock import MINUTE_FORMAT, minute
from memento.findings import MINUTE
from memento.schema import AMEND_TOGETHER, EventKind, Schema


RoutedKind = Literal["span-event", "failure", "change"]
SPAN_EVENT, FAILURE_EVENT, CHANGE_EVENT = get_args(RoutedKind)
EVENTS_SHAPE = "not 'YYYY-MM-DDTHH:MM | actor | kind | what'"
EVENT_FIELD_SEPARATOR = " | "
EVENT_FIELDS = 4
MINUTE_FIELD = 0
ACTOR_FIELD = 1
KIND_FIELD = 2
WHAT_FIELD = 3
VISIBLE_START = re.compile(r"\S")
NEWLINE = "\n"
ENTRY_HEADING = "## "
CLOSE_LABELS = ("HEAD", "state", "open", "next")
FAILURE_LABELS = ("What happened", "Mechanism", "Correction")
HEAD_LABEL, STATE_LABEL, OPEN_LABEL, NEXT_LABEL = CLOSE_LABELS
LONGEST_CLOSE_LABEL = max(CLOSE_LABELS, key=len)
WHAT_HAPPENED_LABEL, MECHANISM_LABEL, CORRECTION_LABEL = FAILURE_LABELS
LABEL_MARK = ":"
PARAGRAPH_STRIDE = 2
CLEAN_TREE = "clean"
DIRTY_TREE = "dirty: "
COMMIT_SHA = r"[0-9a-f]{7,40}"
HEAD_TEXT = re.compile(rf"(?P<sha>{COMMIT_SHA}) \((?:{CLEAN_TREE}|{DIRTY_TREE}(?P<dirty>.+))\)")
DIRTY_PATH_SEPARATOR = ", "
CLOSE_HEADING = "## Close"
CLOSE_STAMP_SEPARATOR = " — "
CLOSE_HEADER = re.compile(rf"{CLOSE_HEADING}{CLOSE_STAMP_SEPARATOR}(?P<minute>{MINUTE})", re.ASCII)
ASCII_MINUTE = re.compile(MINUTE, re.ASCII)
QUOTED_LINE_WIDTH = 60
FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


def visible_single_line(name: str) -> Callable[[str], str]:
    """A validator admitting one line that opens on a visible character, its refusals naming the part."""

    def admitted(text: str) -> str:
        """The text, when it is one line opening on a visible character."""
        if not text:
            raise ValueError(f"{name} is empty")
        if not VISIBLE_START.match(text):
            raise ValueError(f"{name} must start with a visible character")
        if text.splitlines() != [text]:
            raise ValueError(f"{name} is one line; it carries a line break")
        return text

    return admitted


def visible_lines(name: str) -> Callable[[str], str]:
    """A validator admitting newline-separated lines that each open on a visible character, its refusals naming the field."""

    def admitted(text: str) -> str:
        """The text, when every line of it opens on a visible character."""
        if not text:
            raise ValueError(f"{name} is empty")
        lines = text.split(NEWLINE)
        if text.splitlines() != lines:
            raise ValueError(f"{name} carries a line break other than a newline")
        if not all(VISIBLE_START.match(line) for line in lines):
            raise ValueError(f"{name} has a line not starting with a visible character")
        return text

    return admitted


def without_dirty_path_separator(text: str) -> str:
    """Admit a dirty path the HEAD field can list and read back as one path."""
    if DIRTY_PATH_SEPARATOR in text:
        raise ValueError(f"dirty path {text} holds '{DIRTY_PATH_SEPARATOR}', which separates one dirty path from the next")
    return text


def not_an_entry_heading(text: str) -> str:
    """Admit a paragraph that cannot be read as an entry's heading, a close block's above all."""
    if text.startswith(CLOSE_HEADING):
        raise ValueError(f"a paragraph starting '{CLOSE_HEADING}' would read as a close block")
    if text.startswith(ENTRY_HEADING):
        raise ValueError(f"a paragraph starting '{ENTRY_HEADING}' would read as an entry heading")
    return text


type What = Annotated[str, AfterValidator(visible_single_line("what"))]
type LineKind = Literal[EventKind, RoutedKind]
type CommitSha = Annotated[str, StringConstraints(pattern=rf"^{COMMIT_SHA}$")]
type DirtyPath = Annotated[str, AfterValidator(visible_single_line("dirty path")), AfterValidator(without_dirty_path_separator)]
type ParagraphText = Annotated[str, AfterValidator(visible_single_line("paragraph")), AfterValidator(not_an_entry_heading)]


class EventLine(BaseModel):
    """One event as the events file records it: when, who, which kind, and what."""

    model_config = FROZEN
    at: datetime
    actor: Actor
    kind: LineKind
    what: What

    def render(self) -> str:
        """The line as the events file holds it."""
        return EVENT_FIELD_SEPARATOR.join([minute(self.at), self.actor.render(), self.kind, self.what])


class Head(BaseModel):
    """The commit a span closed at and the paths its working tree held changed."""

    model_config = FROZEN
    sha: CommitSha
    dirty: tuple[DirtyPath, ...]

    def render(self) -> str:
        """The HEAD field as a close block and its close line spell it."""
        return f"{self.sha} ({DIRTY_TREE}{DIRTY_PATH_SEPARATOR.join(self.dirty)})" if self.dirty else f"{self.sha} ({CLEAN_TREE})"


class CloseBlock(BaseModel):
    """A span's close block: when it closed, HEAD, the state, what stays open, and the next step."""

    model_config = FROZEN
    at: datetime
    head: Head
    state: Annotated[str, AfterValidator(visible_lines(STATE_LABEL))]
    open_items: Annotated[str, AfterValidator(visible_lines(OPEN_LABEL))]
    next_step: Annotated[str, AfterValidator(visible_lines(NEXT_LABEL))]

    def state_line_count(self) -> int:
        """How many lines the state field runs."""
        return len(self.state.split(NEWLINE))

    def render(self, label_width: int) -> str:
        """The block as a session record holds it, its labels padded to label_width."""
        fields = zip(CLOSE_LABELS, (self.head.render(), self.state, self.open_items, self.next_step), strict=True)
        lines = [(label if number == 0 else "").ljust(label_width) + line for label, text in fields for number, line in enumerate(text.split(NEWLINE))]
        return f"{CLOSE_HEADING}{CLOSE_STAMP_SEPARATOR}{minute(self.at)}\n\n" + NEWLINE.join(lines) + NEWLINE


class FailureEntry(BaseModel):
    """One diagnosed failure: its title and its three labelled paragraphs."""

    model_config = FROZEN
    title: Annotated[str, AfterValidator(visible_single_line("title"))]
    what_happened: Annotated[str, AfterValidator(visible_single_line(WHAT_HAPPENED_LABEL))]
    mechanism: Annotated[str, AfterValidator(visible_single_line(MECHANISM_LABEL))]
    correction: Annotated[str, AfterValidator(visible_single_line(CORRECTION_LABEL))]

    def render(self) -> str:
        """The entry as a failures file holds it."""
        paragraphs = zip(FAILURE_LABELS, (self.what_happened, self.mechanism, self.correction), strict=True)
        return f"{ENTRY_HEADING}{self.title}\n" + "".join(f"\n{label}{LABEL_MARK} {text}\n" for label, text in paragraphs)


class Paragraph(BaseModel):
    """One paragraph of a span's narrative."""

    model_config = FROZEN
    text: ParagraphText

    def render(self) -> str:
        """The paragraph as a session record holds it."""
        return self.text + NEWLINE


def parse_event_line(line: str) -> EventLine:
    """The event an events file line records, refused when the line is off the events shape."""
    fields = line.split(EVENT_FIELD_SEPARATOR, EVENT_FIELDS - 1)
    if len(fields) != EVENT_FIELDS or not ASCII_MINUTE.fullmatch(fields[MINUTE_FIELD]):
        raise Refusal(EVENTS_SHAPE)
    try:
        return construct(
            EventLine,
            at=datetime.strptime(fields[MINUTE_FIELD], MINUTE_FORMAT),
            actor=parse_actor(fields[ACTOR_FIELD]),
            kind=fields[KIND_FIELD],
            what=fields[WHAT_FIELD],
        )
    except (Refusal, ValueError) as unreadable:
        raise Refusal(f"{EVENTS_SHAPE}: {unreadable}") from unreadable


def parse_head(text: str) -> Head:
    """The HEAD a close block's HEAD field spells, refused when it is off the HEAD shape."""
    shape = HEAD_TEXT.fullmatch(text)
    if shape is None:
        raise Refusal("HEAD is not '<sha> (clean)' or '<sha> (dirty: <paths>)'")
    dirty = tuple(shape["dirty"].split(DIRTY_PATH_SEPARATOR)) if shape["dirty"] else ()
    return construct(Head, sha=shape["sha"], dirty=dirty)


def entry_lines(body: list[str]) -> list[str]:
    """An entry's lines beneath its header, without the blank lines separating it from the next."""
    lines = list(body)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def close_field_lines(body: list[str]) -> list[str]:
    """A close block's field lines, refusing a body that is not one blank line and then fields with no blank line among them."""
    lines = entry_lines(body)
    if lines[:1] != [""] or not all(line.strip() for line in lines[1:]):
        raise Refusal("a close block is its header, one blank line, then its fields with no blank line among them")
    return lines[1:]


def close_fields(lines: list[str], label_width: int) -> dict[str, list[str]]:
    """Each close label's lines, continuations included, refusing a repeated field and a line that belongs to no field."""
    fields: dict[str, list[str]] = {}
    for line in lines:
        label = line[:label_width].strip()
        if label in CLOSE_LABELS and line[:label_width] == label.ljust(label_width):
            if label in fields:
                raise Refusal(f"close field '{label}' repeats")
            fields[label] = [line[label_width:]]
        elif line.startswith(" " * label_width) and fields:
            fields[list(fields)[-1]].append(line[label_width:])
        else:
            raise Refusal(f"line outside the close fields: {line[:QUOTED_LINE_WIDTH]!r}")
    if list(fields) != list(CLOSE_LABELS):
        raise Refusal(f"close fields {list(fields)} are not {list(CLOSE_LABELS)}")
    return fields


def parse_close_block(header: str, body: list[str], label_width: int) -> CloseBlock:
    """The close block a session record entry holds, refused when it is off the close shape."""
    stamp = CLOSE_HEADER.fullmatch(header)
    if stamp is None:
        raise Refusal("close header is not '## Close — YYYY-MM-DDTHH:MM'")
    fields = {label: NEWLINE.join(lines) for label, lines in close_fields(close_field_lines(body), label_width).items()}
    return construct(
        CloseBlock,
        at=datetime.strptime(stamp["minute"], MINUTE_FORMAT),
        head=parse_head(fields[HEAD_LABEL]),
        state=fields[STATE_LABEL],
        open_items=fields[OPEN_LABEL],
        next_step=fields[NEXT_LABEL],
    )


def failure_paragraphs(body: list[str]) -> list[str]:
    """A failure entry's paragraphs, refusing a body that is not a blank line before each one-line paragraph."""
    lines = entry_lines(body)
    if any(lines[0::PARAGRAPH_STRIDE]):
        raise Refusal("a failure entry is its title, then a blank line before each one-line paragraph")
    return lines[1::PARAGRAPH_STRIDE]


def labelled_text(label: str, paragraph: str) -> str:
    """The text a paragraph carries after its label, refused when a space does not separate them."""
    after_label = paragraph.removeprefix(f"{label}{LABEL_MARK}")
    if after_label and not after_label.startswith(" "):
        raise Refusal(f"'{label}{LABEL_MARK}' is not followed by a space")
    return after_label.removeprefix(" ")


def parse_failure_entry(header: str, body: list[str]) -> FailureEntry:
    """The failure a failures file entry records, refused unless it is exactly three paragraphs opening with the labels in order."""
    paragraphs = failure_paragraphs(body)
    openings = [label for paragraph in paragraphs for label in FAILURE_LABELS if paragraph.startswith(f"{label}{LABEL_MARK}")]
    if openings != list(FAILURE_LABELS):
        raise Refusal(f"paragraphs open {openings}, not {list(FAILURE_LABELS)}")
    if len(paragraphs) != len(FAILURE_LABELS):
        raise Refusal(f"{len(paragraphs)} paragraphs, not {len(FAILURE_LABELS)}")
    what_happened, mechanism, correction = (labelled_text(label, paragraph) for label, paragraph in zip(FAILURE_LABELS, paragraphs, strict=True))
    return construct(
        FailureEntry,
        title=header.removeprefix(ENTRY_HEADING),
        what_happened=what_happened,
        mechanism=mechanism,
        correction=correction,
    )


def entry_text(header: str, body: list[str]) -> str:
    """An entry as its record holds it, without the blank lines separating it from the next."""
    return NEWLINE.join([header, *entry_lines(body)]) + NEWLINE


def state_overrun(block: CloseBlock, state_max_lines: int) -> str | None:
    """Why a close block's state runs too long, or None when it fits."""
    count = block.state_line_count()
    return f"state runs {count} lines, over {state_max_lines}" if count > state_max_lines else None


def close_labels_disagreement(schema: Schema) -> str | None:
    """The structure-agreement message when the schema's close labels differ from CloseBlock's, or None."""
    declared = list(schema["kinds"]["digest"]["close_labels"])
    if declared != list(CLOSE_LABELS):
        return f"schema declares digest close_labels {declared}; memento knows {list(CLOSE_LABELS)}; {AMEND_TOGETHER}"
    return None


def close_label_width_shortfall(schema: Schema) -> str | None:
    """Why the schema's close label width cannot hold every close label, or None when it can."""
    width = schema["kinds"]["digest"]["close_label_width"]
    if width < len(LONGEST_CLOSE_LABEL):
        return f"schema declares digest close_label_width {width}, narrower than the close label '{LONGEST_CLOSE_LABEL}'"
    return None


def failure_labels_disagreement(schema: Schema) -> str | None:
    """The structure-agreement message when the schema's failure entry labels differ from FailureEntry's, or None."""
    declared = list(schema["kinds"]["failure"]["entry_labels"])
    if declared != list(FAILURE_LABELS):
        return f"schema declares failure entry_labels {declared}; memento knows {list(FAILURE_LABELS)}; {AMEND_TOGETHER}"
    return None


def event_fields_of_kind(line: str, kind: str) -> list[str] | None:
    """The four fields of an events line recording an event of one kind, or None for any other line."""
    fields = line.split(EVENT_FIELD_SEPARATOR, EVENT_FIELDS - 1)
    return fields if len(fields) == EVENT_FIELDS and fields[KIND_FIELD] == kind else None
