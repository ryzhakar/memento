"""The change log: entries appended oldest first, and the fact record's pointers into it."""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from memento import Environment, Refusal, construct, refusals_reported
from memento.candidate import refuse_new_failures
from memento.findings import DATE, FAIL, Corpus, Finding, fail, split_entries
from memento.git import git_exit_status
from memento.pointers import home_of
from memento.project import (
    change_log_archive_path,
    change_log_mention,
    change_log_pointer,
    change_log_pointer_form,
    home_of_kind,
    project_of,
)
from memento.schema import AMEND_TOGETHER, SCHEMA_HOME, Schema, agreeing_schema, load_corpus

change_group = typer.Typer(no_args_is_help=True, help="The change log: add an entry, supersede one, repair one that fails the check, age the oldest out.")

ENTRY_HEADER = re.compile(rf"^## (?P<start>{DATE})(?: → (?P<end>{DATE}))? — (?P<title>\S.*)$")
FIELD_LABEL = re.compile(r"^(?P<label>[A-Z][A-Z →-]*[A-Z]):(?P<pad> *)")
NEVER_COMPRESSED_FIELD = "INVALIDATES"
KIND_FIELD = "KIND"
ENTRY_DATE = re.compile(rf"(?m)^## ({DATE})")
ENTRY_DATE_FIELD = re.compile(rf"(?m)^\w+: (?P<text>{DATE})$")

FIELD_LABELS: tuple[str, ...] = ("KIND", "FROM → TO", "WHY", "INVALIDATES", "EVIDENCE", "SUPERSEDED-BY")
FIELD_ATTRS: tuple[str, ...] = ("kind", "from_to", "why", "invalidates", "evidence", "superseded_by")
ATTR_FOR_LABEL: dict[str, str] = dict(zip(FIELD_LABELS, FIELD_ATTRS))
CANONICAL_LEADING_BLANK_LINES = 0
CANONICAL_TRAILING_BLANK_LINES = 1
CANONICAL_FINAL_TRAILING_BLANK_LINES = 0


class EntryShapeError(Refusal):
    """A change-log entry whose shape does not fit the schema, naming its body offset and what is wrong."""

    def __init__(self, offset: int, message: str) -> None:
        """Carry the body offset a shape problem was found at, and the message describing it."""
        super().__init__(message)
        self.offset = offset
        self.message = message


class ChangeEntry(BaseModel):
    """One `## ` entry of the change log: its header, its fields, and the blank lines framing it."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    start: date
    end: date | None
    title: str
    kind: str
    from_to: str
    why: str
    invalidates: str
    evidence: str | None
    superseded_by: str | None
    leading_blank_lines: int
    trailing_blank_lines: int

    def header_text(self) -> str:
        """The `## ...` header line this entry renders."""
        span = f"{self.start.isoformat()} → {self.end.isoformat()}" if self.end else self.start.isoformat()
        return f"## {span} — {self.title}"

    def present_fields(self) -> dict[str, str]:
        """This entry's field values, by schema label, omitting the ones it carries as None."""
        return {label: value for label in FIELD_LABELS if (value := getattr(self, ATTR_FOR_LABEL[label])) is not None}

    def render(self, *, width: int) -> str:
        """The exact text this entry occupies in the change log, header through its trailing blank lines."""
        lines = [self.header_text(), *([""] * self.leading_blank_lines)]
        for label, value in self.present_fields().items():
            value_lines = value.split("\n")
            lines.append(f"{label}:".ljust(width) + value_lines[0])
            lines += [line if not line else " " * width + line for line in value_lines[1:]]
        lines += [""] * self.trailing_blank_lines
        return "\n".join(lines) + "\n"


class NewEntryInput(BaseModel):
    """The structured fields `change add` reads from its entry.yaml file."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    start: date
    end: date | None
    title: str
    kind: str
    from_to: str
    why: str
    invalidates: list[str]
    evidence: str | None = None
    merged: str


def count_words(text: str) -> int:
    """The whitespace-separated word count of a text, its embedded line breaks folded to spaces."""
    return len(text.replace("\n", " ").split())


def fields_structure_disagreement(spec: Mapping[str, Any]) -> str | None:
    """The structure-agreement message when the schema's change fields differ from what ChangeEntry knows, or None."""
    declared = tuple(spec["fields"])
    if declared != FIELD_LABELS:
        return f"schema declares change fields {list(declared)}; memento knows {list(FIELD_LABELS)}; {AMEND_TOGETHER}"
    return None


def validate_entry_fields(
    *,
    header_words: int,
    fields_present: Mapping[str, str],
    order: Sequence[str],
    fields: Sequence[str],
    required_fields: Sequence[str],
    one_line_fields: Sequence[str],
    kind_values: Sequence[str],
    word_cap: int,
) -> None:
    """Raise for a missing required field, fields out of order, a bad KIND value, a one-line field spanning lines, or an over-cap entry."""
    missing = [name for name in required_fields if name not in fields_present]
    if missing:
        raise EntryShapeError(0, f"missing required field(s): {', '.join(missing)}")
    if list(order) != sorted(order, key=fields.index):
        raise EntryShapeError(0, f"fields out of order: {' / '.join(order)}")
    kind_value = fields_present.get(KIND_FIELD, "")
    if kind_value not in kind_values:
        raise EntryShapeError(0, f"KIND '{kind_value}' is not one of {list(kind_values)}")
    for name in one_line_fields:
        value = fields_present.get(name)
        if value is not None and "\n" in value:
            raise EntryShapeError(0, f"{name} spans {value.count(chr(10)) + 1} lines; it takes one")
    words = header_words + sum(count_words(value) for name, value in fields_present.items() if name != NEVER_COMPRESSED_FIELD)
    if words > word_cap:
        raise EntryShapeError(0, f"{words} words outside INVALIDATES, over {word_cap}")


def calendar_date(text: str) -> date:
    """The date a YYYY-MM-DD text names, refusing one that is not a calendar date."""
    try:
        return date.fromisoformat(text)
    except ValueError as invalid:
        raise EntryShapeError(0, f"{text} is not a calendar date") from invalid


def parse_entry_text(
    header: str,
    body: Sequence[str],
    *,
    width: int,
    fields: Sequence[str],
    required_fields: Sequence[str],
    one_line_fields: Sequence[str],
    kind_values: Sequence[str],
    word_cap: int,
) -> ChangeEntry:
    """Build a ChangeEntry from one `## ` entry's header and body lines, raising EntryShapeError on any shape violation."""
    shape = ENTRY_HEADER.match(header)
    if not shape:
        raise EntryShapeError(0, "header is not '## YYYY-MM-DD — Title' or '## YYYY-MM-DD → YYYY-MM-DD — Title'")
    current: str | None = None
    order: list[str] = []
    blocks: dict[str, list[str]] = {}
    pending_blanks = 0
    leading_blank_lines = 0
    for offset, line in enumerate(body, 1):
        if not line.strip():
            pending_blanks += 1
            continue
        label = FIELD_LABEL.match(line)
        if label and label["label"] in fields:
            name = label["label"]
            if len(name) + 1 + len(label["pad"]) != width:
                raise EntryShapeError(offset, f"{name} label is not padded to column {width}")
            if name in blocks:
                raise EntryShapeError(offset, f"{name} appears twice")
            if current is None:
                leading_blank_lines = pending_blanks
            elif pending_blanks:
                raise EntryShapeError(offset, f"a blank line separates {current} from {name}")
            pending_blanks = 0
            order.append(name)
            blocks[name] = [line[width:]]
            current = name
        elif label and label["pad"]:
            raise EntryShapeError(offset, f"unknown field {label['label']}")
        elif current is None:
            raise EntryShapeError(offset, "text before the first field")
        else:
            if not line.startswith(" " * width):
                raise EntryShapeError(offset, f"continuation not indented to column {width}")
            blocks[current] += [""] * pending_blanks + [line[width:]]
            pending_blanks = 0
    fields_present = {name: "\n".join(lines) for name, lines in blocks.items()}
    validate_entry_fields(
        header_words=len(header.split()),
        fields_present=fields_present,
        order=order,
        fields=fields,
        required_fields=required_fields,
        one_line_fields=one_line_fields,
        kind_values=kind_values,
        word_cap=word_cap,
    )
    return construct(
        ChangeEntry,
        start=calendar_date(shape["start"]),
        end=calendar_date(shape["end"]) if shape["end"] else None,
        title=shape["title"],
        kind=fields_present[KIND_FIELD],
        from_to=fields_present["FROM → TO"],
        why=fields_present["WHY"],
        invalidates=fields_present[NEVER_COMPRESSED_FIELD],
        evidence=fields_present.get("EVIDENCE"),
        superseded_by=fields_present.get("SUPERSEDED-BY"),
        leading_blank_lines=leading_blank_lines,
        trailing_blank_lines=pending_blanks,
    )


def split_log(text: str) -> tuple[str, list[str]]:
    """A change log's text before its first entry, and each entry's raw text, cut where findings.split_entries cuts."""
    lines = text.splitlines(keepends=True)
    starts = [number - 1 for number, _header, _body in split_entries(text)]
    if not starts:
        return text, []
    bounds = [*starts, len(lines)]
    return "".join(lines[: starts[0]]), ["".join(lines[begin:end]) for begin, end in zip(bounds, bounds[1:])]


def header_line(raw: str) -> str:
    """The first line of a raw entry, its `## ` header as written."""
    return raw.splitlines()[0]


def entry_header(raw: str) -> re.Match[str] | None:
    """The shape match of a raw entry's header line, or None when that line is not a change-log header."""
    return ENTRY_HEADER.match(header_line(raw))


def parse_raw_entry(raw: str, spec: Mapping[str, Any]) -> ChangeEntry:
    """The ChangeEntry one raw entry holds, raising EntryShapeError on any shape violation."""
    header, *body = raw.splitlines()
    return parse_entry_text(
        header,
        body,
        width=spec["label_width"],
        fields=spec["fields"],
        required_fields=spec["required_fields"],
        one_line_fields=spec["one_line_fields"],
        kind_values=spec["kind_values"],
        word_cap=spec["word_cap"],
    )


def matching_index(raws: Sequence[str], target: date, title_prefix: str) -> int:
    """The index of the one raw entry dated target whose title starts with title_prefix, refusing on zero or several."""
    matches = [
        index
        for index, raw in enumerate(raws)
        if (shape := entry_header(raw)) and shape["start"] == target.isoformat() and shape["title"].startswith(title_prefix)
    ]
    if not matches:
        raise Refusal(f"no entry dated {target.isoformat()} with title starting '{title_prefix}'")
    if len(matches) > 1:
        raise Refusal(f"{len(matches)} entries dated {target.isoformat()} with title starting '{title_prefix}'; name one")
    return matches[0]


def estimate_tokens(text: str, tokens_per_word: float) -> int:
    """The schema's token estimate for a text: its word count times tokens-per-word, rounded."""
    return round(len(text.split()) * tokens_per_word)


def new_entry(data: NewEntryInput) -> ChangeEntry:
    """A canonically formatted ChangeEntry for a just-added record: no blank line before it, one after."""
    return construct(
        ChangeEntry,
        start=data.start,
        end=data.end,
        title=data.title,
        kind=data.kind,
        from_to=data.from_to,
        why=data.why,
        invalidates="\n".join(data.invalidates),
        evidence=data.evidence,
        superseded_by=None,
        leading_blank_lines=CANONICAL_LEADING_BLANK_LINES,
        trailing_blank_lines=CANONICAL_FINAL_TRAILING_BLANK_LINES,
    )


def refuse_entry_shape(entry: ChangeEntry, spec: Mapping[str, Any]) -> None:
    """Refuse an entry whose fields break the schema's change shape, as a CLI-facing Refusal."""
    fields_present = entry.present_fields()
    try:
        validate_entry_fields(
            header_words=len(entry.header_text().split()),
            fields_present=fields_present,
            order=list(fields_present),
            fields=spec["fields"],
            required_fields=spec["required_fields"],
            one_line_fields=spec["one_line_fields"],
            kind_values=spec["kind_values"],
            word_cap=spec["word_cap"],
        )
    except EntryShapeError as error:
        raise Refusal(error.message) from error


def refuse_future_date(label: str, value: date | None, today: date) -> None:
    """Refuse a date field standing after today."""
    if value is not None and value > today:
        raise Refusal(f"{label} {value.isoformat()} is after today ({today.isoformat()})")


def refuse_unmerged_entry(root: Path, schema: Schema, data: NewEntryInput) -> None:
    """Refuse an entry whose `merged` sha git does not know as an ancestor of the project's main branch."""
    main_branch = project_of(schema).main_branch
    if git_exit_status(root, "merge-base", "--is-ancestor", data.merged, main_branch).returncode != 0:
        raise Refusal(f"{data.merged} is not on {main_branch}; an entry is written after its change is merged")


def change_spec(environment: Environment) -> tuple[Path, Schema, Mapping[str, Any]]:
    """The root, the schema, and the change kind's own spec, refusing when the schema's change fields disagree with ChangeEntry's."""
    root = environment.store.root
    schema = agreeing_schema(root)
    spec = schema["kinds"]["change"]
    disagreement = fields_structure_disagreement(spec)
    if disagreement:
        raise Refusal(disagreement)
    return root, schema, spec


def with_closing_seam(entry: ChangeEntry) -> ChangeEntry:
    """The entry with the house's one-blank-line seam, now that it is no longer the log's last."""
    if entry.trailing_blank_lines:
        return entry
    return entry.model_copy(update={"trailing_blank_lines": CANONICAL_TRAILING_BLANK_LINES})


def read_entry_yaml(entry_path: Path) -> dict[str, Any]:
    """The mapping an entry file holds, refusing a file that does not read, decode, parse, or hold a mapping with text keys."""
    try:
        text = entry_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as invalid:
        raise Refusal(f"{entry_path.name} is not UTF-8 text") from invalid
    except OSError as invalid:
        raise Refusal(f"{entry_path.name} does not read: {invalid.strerror}") from invalid
    try:
        raw = yaml.safe_load(text)
    except ValueError as invalid:
        for field in ENTRY_DATE_FIELD.finditer(text):
            calendar_date(field["text"])
        raise Refusal(f"{entry_path.name} does not parse: {invalid}") from invalid
    except yaml.YAMLError as invalid:
        raise Refusal(f"{entry_path.name} is not valid YAML: {invalid}") from invalid
    except RecursionError as invalid:
        raise Refusal(f"{entry_path.name} is nested too deeply to parse") from invalid
    if not isinstance(raw, dict):
        raise Refusal(f"{entry_path.name} is not a mapping")
    if not all(isinstance(key, str) for key in raw):
        raise Refusal(f"{entry_path.name} has a key that is not text")
    return raw


def refuse_quoted_date(label: str, value: object) -> None:
    """Refuse a start or end the YAML carries as text, after checking that the text names a calendar date."""
    if isinstance(value, str):
        calendar_date(value)
        raise Refusal(f"{label} {value} is quoted; write it unquoted as a YAML date")


def replaced(raws: Sequence[str], index: int, text: str) -> str:
    """The raw entries with the one at index replaced by text, joined back into a log body."""
    return "".join([*raws[:index], text, *raws[index + 1 :]])


def entry_input(raw: Mapping[str, Any]) -> NewEntryInput:
    """The NewEntryInput a mapping holds, refusing a key or value the entry does not take, naming it."""
    try:
        return NewEntryInput.model_validate(raw)
    except ValidationError as invalid:
        first = invalid.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise Refusal(f"{location}: {first['msg']}" if location else first["msg"]) from invalid


def append_entry(environment: Environment, entry_path: Path) -> str:
    """Validate and append one new entry built from entry_path's YAML to the change log, returning its path."""
    root, schema, spec = change_spec(environment)
    raw = read_entry_yaml(entry_path)
    refuse_quoted_date("start", raw.get("start"))
    refuse_quoted_date("end", raw.get("end"))
    data = entry_input(raw)
    entry = new_entry(data)
    refuse_entry_shape(entry, spec)
    path, width = spec["home"], spec["label_width"]
    current_text = (root / path).read_text(encoding="utf-8")
    prefix, raws = split_log(current_text)
    last = parse_raw_entry(raws[-1], spec) if raws else None
    if last is not None and entry.start < last.start:
        raise Refusal(f"start {entry.start.isoformat()} is before the last entry's start {last.start.isoformat()}; entries run oldest first")
    today = environment.now().date()
    refuse_future_date("start", entry.start, today)
    refuse_future_date("end", entry.end, today)
    refuse_unmerged_entry(root, schema, data)
    seamed = "" if last is None else with_closing_seam(last).render(width=width)
    new_text = prefix + "".join(raws[:-1]) + seamed + entry.render(width=width)
    with environment.store.transaction() as act:
        refuse_new_failures(environment, {path: new_text})
        act.replace(path, new_text)
    return path


def supersede_entry(environment: Environment, target_date_text: str, title_prefix: str, by: str) -> str:
    """Add SUPERSEDED-BY to the one matching entry, or append `by` as its new line, returning the change log's path."""
    root, _schema, spec = change_spec(environment)
    path, width = spec["home"], spec["label_width"]
    target = calendar_date(target_date_text)
    current_text = (root / path).read_text(encoding="utf-8")
    prefix, raws = split_log(current_text)
    index = matching_index(raws, target, title_prefix)
    entry = parse_raw_entry(raws[index], spec)
    superseded_by = by if entry.superseded_by is None else f"{entry.superseded_by}\n{by}"
    updated = entry.model_copy(update={"superseded_by": superseded_by})
    refuse_entry_shape(updated, spec)
    new_text = prefix + replaced(raws, index, updated.render(width=width))
    with environment.store.transaction() as act:
        refuse_new_failures(environment, {path: new_text})
        act.replace(path, new_text)
    return path


def retired_entry_line(schema: Schema, archive_path: str) -> str:
    """The schema `retired` list entry for an aged-out archive file, for `schema amend` to add."""
    return f"- path: {archive_path}\n  superseded_by: {home_of_kind(schema, 'change')} (current entries)\n"


def age_out(environment: Environment) -> tuple[str, str]:
    """Move the oldest whole entries out of the change log until it is under budget, returning the archive path and its retired line."""
    root, schema, spec = change_spec(environment)
    path = spec["home"]
    per_word, budget = schema["token_estimate"]["tokens_per_word"], spec["budget_tokens"]
    current_text = (root / path).read_text(encoding="utf-8")
    if estimate_tokens(current_text, per_word) <= budget:
        raise Refusal(f"the change log is under its budget of {budget} tokens")
    prefix, raws = split_log(current_text)
    remaining = list(raws)
    moved: list[str] = []
    while remaining and estimate_tokens(prefix + "".join(remaining), per_word) > budget:
        moved.append(remaining.pop(0))
    if not moved:
        raise Refusal(f"no whole entry can move out of the change log to fit its budget of {budget} tokens")
    moved_entries = [parse_raw_entry(raw, spec) for raw in moved]
    archive_path = change_log_archive_path(schema, moved_entries[0].start.isoformat(), moved_entries[-1].start.isoformat())
    if (root / archive_path).exists():
        raise Refusal(f"{archive_path} already exists")
    archive_text = "".join(moved)
    new_log_text = prefix + "".join(remaining)
    with environment.store.transaction() as act:
        refuse_new_failures(environment, {path: new_log_text, archive_path: archive_text})
        act.replace(path, new_log_text)
        act.replace(archive_path, archive_text)
    return archive_path, retired_entry_line(schema, archive_path)


def header_index(raws: Sequence[str], header_prefix: str) -> int:
    """The index of the one raw entry whose header line, after `## `, starts with header_prefix, refusing on zero or several."""
    matches = [index for index, raw in enumerate(raws) if header_line(raw).removeprefix("## ").startswith(header_prefix)]
    if not matches:
        raise Refusal(f"no entry whose header starts with '{header_prefix}'")
    if len(matches) > 1:
        raise Refusal(f"{len(matches)} entries whose header starts with '{header_prefix}'; name one")
    return matches[0]


def first_line_of(prefix: str, raws: Sequence[str], index: int) -> int:
    """The 1-based line at which entry index begins in a log made of prefix and raws."""
    return len((prefix + "".join(raws[:index])).splitlines(keepends=True)) + 1


def fails_within(findings: Iterable[Finding], path: str, first: int, count: int) -> list[Finding]:
    """The FAILs among findings that point at the count lines from first in the file at path."""
    return [
        finding
        for finding in findings
        if finding.level == FAIL and finding.where.rpartition(":")[0] == path and first <= int(finding.where.rpartition(":")[2]) < first + count
    ]


def repair_entry(environment: Environment, header_prefix: str, entry_path: Path) -> str:
    """Replace the one entry whose header starts with header_prefix, when it holds a change-log FAIL and its replacement holds none, returning the change log's path."""
    root, schema, spec = change_spec(environment)
    path, width = spec["home"], spec["label_width"]
    corpus = load_corpus(root)
    prefix, raws = split_log(corpus.text(path))
    index = header_index(raws, header_prefix)
    first, count = first_line_of(prefix, raws, index), len(raws[index].splitlines())
    if not fails_within(check_change_log(corpus), path, first, count):
        raise Refusal(
            f"the entry whose header starts with '{header_prefix}' holds no FAIL of the change-log check; "
            "repair is for an entry that fails the check"
        )
    raw = read_entry_yaml(entry_path)
    refuse_quoted_date("start", raw.get("start"))
    refuse_quoted_date("end", raw.get("end"))
    data = entry_input(raw)
    entry = new_entry(data)
    refuse_unmerged_entry(root, schema, data)
    trailing = CANONICAL_FINAL_TRAILING_BLANK_LINES if index == len(raws) - 1 else CANONICAL_TRAILING_BLANK_LINES
    replacement = entry.model_copy(update={"trailing_blank_lines": trailing}).render(width=width)
    new_text = prefix + replaced(raws, index, replacement)
    holding = fails_within(check_change_log(corpus.substituted({path: new_text})), path, first, len(replacement.splitlines()))
    if holding:
        raise Refusal(f"the replacement holds a FAIL of the change-log check: {holding[0].message}")
    with environment.store.transaction() as act:
        refuse_new_failures(environment, {path: new_text})
        act.replace(path, new_text)
    return path


def check_change_log(corpus: Corpus) -> Iterator[Finding]:
    """Fail change-log entries ChangeEntry cannot parse, entries dated out of order, and a schema field list it disagrees with."""
    spec = corpus.schema["kinds"]["change"]
    path = home_of(corpus, "change")
    width = spec["label_width"]
    disagreement = fields_structure_disagreement(spec)
    if disagreement:
        yield fail(SCHEMA_HOME, disagreement)
    previous: ChangeEntry | None = None
    for line_number, header, body in split_entries(corpus.text(path)):
        try:
            entry = parse_entry_text(
                header,
                body,
                width=width,
                fields=spec["fields"],
                required_fields=spec["required_fields"],
                one_line_fields=spec["one_line_fields"],
                kind_values=spec["kind_values"],
                word_cap=spec["word_cap"],
            )
        except EntryShapeError as error:
            yield fail(f"{path}:{line_number + error.offset}", error.message)
            continue
        if previous is not None and entry.start < previous.start:
            yield fail(
                f"{path}:{line_number}",
                f"dated {entry.start.isoformat()} after an entry dated {previous.start.isoformat()}; entries run oldest first",
            )
        previous = entry


def check_change_log_pointers(corpus: Corpus) -> Iterator[Finding]:
    """Fail the fact record reaching the change log other than as 'decided YYYY-MM-DD, see <change log home>'."""
    entry_dates = set(ENTRY_DATE.findall(corpus.text(home_of(corpus, "change"))))
    fact = home_of(corpus, "fact")
    mention, pointer = change_log_mention(corpus.schema), change_log_pointer(corpus.schema)
    for number, line in enumerate(corpus.text(fact).splitlines(), 1):
        where = f"{fact}:{number}"
        mentions = line.count(mention)
        pointers = pointer.findall(line)
        if mentions > len(pointers):
            yield fail(where, f"cites the change log other than as '{change_log_pointer_form(corpus.schema)}'")
        for date_text in pointers:
            if date_text not in entry_dates:
                yield fail(where, f"points at a change-log entry dated {date_text}; none exists")


@change_group.command(
    "add",
    help=(
        "Append one entry to the change log (the change kind's home), built from <entry-yaml>'s start, end, title, kind, from_to, "
        "why, invalidates, evidence and merged fields, fields rendered in schema order. Reads .claude/memento.yaml, "
        "the last entry of the change log and <entry-yaml>; prints the path written. Refuses: an entry-yaml that does "
        "not read, is not UTF-8, is not valid YAML or is not a mapping with text keys; a start or end that is not a "
        "calendar date or is quoted; over word_cap words outside INVALIDATES; a start before the last entry's; a last "
        "entry that does not parse; a start or end after today; a merged sha that git does not know as an ancestor of "
        "the project's main branch; EVIDENCE spanning more than one line; a schema whose change fields differ from memento's; a candidate "
        "FAIL; a worktree."
    ),
)
def add(
    ctx: typer.Context,
    entry_path: Path = typer.Argument(..., help="A block-form YAML file with the entry's start, end, title, kind, from_to, why, invalidates, evidence and merged fields."),
) -> None:
    """Append one new entry to the change log."""
    with refusals_reported():
        path = append_entry(ctx.obj, entry_path)
    typer.echo(path)


@change_group.command(
    "supersede",
    help=(
        "Add SUPERSEDED-BY '<by>' to the one entry dated <date> whose title starts with <title-prefix>, or append "
        "<by> as a further line of its existing SUPERSEDED-BY. Reads .claude/memento.yaml and the matching entry; "
        "prints the path written. Refuses: a <date> that is not a calendar date; zero or several matching entries; a "
        "matching entry that does not parse; the word cap broken by the addition; a schema whose change fields differ "
        "from memento's; a candidate FAIL; a worktree."
    ),
)
def supersede(
    ctx: typer.Context,
    date_text: str = typer.Argument(..., help="The matching entry's start date, YYYY-MM-DD."),
    title_prefix: str = typer.Argument(..., help="A prefix of the matching entry's title."),
    by: str = typer.Argument(..., help="The SUPERSEDED-BY text to add or append."),
) -> None:
    """Add or extend one entry's SUPERSEDED-BY field."""
    with refusals_reported():
        path = supersede_entry(ctx.obj, date_text, title_prefix, by)
    typer.echo(path)


@change_group.command(
    "age",
    help=(
        "Move the change log's oldest whole entries, unedited, into "
        "<archived home>/<change log name>-<first>--<last>.md until the log is under its budget_tokens, "
        "in one act. Reads .claude/memento.yaml and the entries it moves; prints the archive path and the schema "
        "`retired` line for the owner to add. Refuses: the log already under budget; an archive file that already "
        "exists; no whole entry whose move brings the log under budget; a moved entry that does not parse; a schema whose "
        "change fields differ from memento's; a candidate "
        "FAIL; a worktree."
    ),
)
def age(ctx: typer.Context) -> None:
    """Move the oldest whole entries out of the change log until it is under budget."""
    with refusals_reported():
        archive_path, retired_line = age_out(ctx.obj)
    typer.echo(archive_path)
    typer.echo(retired_line)


@change_group.command(
    "repair",
    help=(
        "Replace the one entry whose header line starts with <header-prefix> (the text after '## '), whole, with the entry "
        "in <entry-yaml>, with the seam its position gives. Reads .claude/memento.yaml, the change log and <entry-yaml>; "
        "prints the path written. Refuses: zero or several entries whose header line starts with <header-prefix>; an entry "
        "that holds no FAIL of the change-log check (repair is for an entry that fails the check); a replacement that holds "
        "a FAIL of the check, named; an entry-yaml that does not read, is not UTF-8, is not valid YAML, is not a mapping, "
        "holds a key or value the entry does not take, or holds a date that is not a calendar date; a merged sha that git "
        "does not know as an ancestor of the project's main branch; a candidate FAIL elsewhere; a worktree."
    ),
)
def repair(
    ctx: typer.Context,
    header_prefix: str = typer.Argument(..., help="The start of the entry's header line, the text after '## '."),
    entry_path: Path = typer.Argument(..., help="A block-form YAML file with the replacement's start, end, title, kind, from_to, why, invalidates, evidence and merged fields."),
) -> None:
    """Replace one entry that fails the check with the entry in a YAML file."""
    with refusals_reported():
        path = repair_entry(ctx.obj, header_prefix, entry_path)
    typer.echo(path)
