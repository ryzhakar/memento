"""Living records, corrected in place: charter, map, directives, results, facts, recipes and specs."""

import re
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Self

import typer
import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo

from memento import Environment, Refusal, announce, construct, known_name, quiet_option, refusals_reported
from memento.candidate import refuse_new_failures
from memento.findings import DATE, SHA, Corpus, Finding, fail
from memento.pointers import ground_findings, home_of
from memento.project import config_space, journal_space, project_of, spec_archive_path, spec_path
from memento.schema import AMEND_TOGETHER, SCHEMA_HOME, Schema, home_pattern, load_corpus

directive_group = typer.Typer(no_args_is_help=True, help="Directives: add a rule, replace one, set one of its labels or drop one.")
results_group = typer.Typer(no_args_is_help=True, help="Results: add a contender's row, replace one, set one of its columns or drop one.")
fact_group = typer.Typer(no_args_is_help=True, help="Capabilities: replace one exact passage.")
string_recipes_group = typer.Typer(no_args_is_help=True, help="String-comparison recipes: replace one exact passage.")
spec_group = typer.Typer(no_args_is_help=True, help="Specifications: add one, replace one exact passage, archive one.")
map_group = typer.Typer(no_args_is_help=True, help="The map of the project's records: no verb writes it yet; 'memento check' checks it.")

MAP_COLUMNS = 4
MAP_HEADER_CELLS = ("path", "---")
GOAL_HEADING = re.compile(r"^### Goal \d+ · .+ · (?P<horizon>finite|standing) · \S+$")
GOAL_FIELD = re.compile(r"- (\w+):")
RULE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
GOALS_SECTION = "Goals"
DIRECTIVES_SECTION = "Prime directives"
GROUND_MARK = "Ground:"
COMPLETION_FIELD = "evidence"
FINITE = "finite"
STANDING = "standing"
DIRECTIVE_WIDTH = 8
TEMPLATE_HOME_MARKS = "{*"
DIRECTORY_MARK = "/"


def in_record_space(schema: Schema, path: str) -> bool:
    """Whether a tracked path sits where only records, products and retired paths may live."""
    in_docs = path.startswith(project_of(schema).record_space) and not path.startswith(journal_space(schema))
    at_root = DIRECTORY_MARK not in path and path.endswith(".md")
    in_config = path.startswith(config_space(schema)) and path.count(DIRECTORY_MARK) == 1
    return in_docs or at_root or in_config


def map_rows(corpus: Corpus, map_path: str) -> list[tuple[int, str, str, str, str]]:
    """Each data row of the map: its line number and its four cells."""
    rows = []
    for number, line in enumerate(corpus.text(map_path).splitlines(), 1):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == MAP_COLUMNS and cells[0] not in MAP_HEADER_CELLS:
            rows.append((number, cells[0], cells[1], cells[2], cells[3]))
    return rows


def check_map(corpus: Corpus) -> Iterator[Finding]:
    """Fail map rows disagreeing with the schema, kinds without exactly one row, and tracked records the map omits."""
    kinds = corpus.schema["kinds"]
    map_path = home_of(corpus, "map")
    rows_per_kind: dict[str, int] = {}
    for number, path, kind, load, _purpose in map_rows(corpus, map_path):
        where = f"{map_path}:{number}"
        if kind not in kinds:
            yield fail(where, f"kind '{kind}' is not declared in the schema")
            continue
        rows_per_kind[kind] = rows_per_kind.get(kind, 0) + 1
        if path != kinds[kind]["home"]:
            yield fail(where, f"path '{path}' is not the home the schema gives {kind}: '{kinds[kind]['home']}'")
        if load.split()[0] != kinds[kind]["load"]:
            yield fail(where, f"load '{load}' disagrees with the schema's '{kinds[kind]['load']}' for {kind}")
        if kinds[kind]["load"] not in corpus.schema["load_levels"]:
            yield fail(SCHEMA_HOME, f"{kind} declares load '{kinds[kind]['load']}', not one of the schema's load_levels")
        if not any(mark in path for mark in TEMPLATE_HOME_MARKS) and not corpus.exists(path):
            yield fail(where, f"'{path}' does not exist")
    for kind in kinds:
        if rows_per_kind.get(kind, 0) != 1:
            yield fail(map_path, f"kind {kind} has {rows_per_kind.get(kind, 0)} rows; the map carries exactly one")
    homes = [kinds[kind]["home"] for kind in kinds]
    products = [entry["path"] for entry in corpus.schema.get("products", [])]
    retired = [entry["path"] for entry in corpus.schema.get("retired", [])]
    for path in corpus.tracked:
        if not in_record_space(corpus.schema, path):
            continue
        covered = any(home_pattern(home).fullmatch(path) for home in homes)
        covered = covered or any(path == product or (product.endswith(DIRECTORY_MARK) and path.startswith(product)) for product in products)
        if not covered and path not in retired:
            yield fail(path, "not a record the map lists, a declared product, or a retired path")


def check_charter(corpus: Corpus) -> Iterator[Finding]:
    """Fail charter sections, goals and prime directives that break the schema's charter shape."""
    spec = corpus.schema["kinds"]["charter"]
    path = home_of(corpus, "charter")
    sections: list[str] = []
    goals: list[tuple[str, str, set[str]]] = []
    for number, line in enumerate(corpus.text(path).splitlines(), 1):
        where = f"{path}:{number}"
        if line.startswith("@") and not corpus.exists(line[1:].strip()):
            yield fail(where, f"autoload '{line[1:].strip()}' does not exist")
        elif line.startswith("## "):
            sections.append(line[3:])
        elif line.startswith("### ") and sections and sections[-1].startswith(GOALS_SECTION):
            shape = GOAL_HEADING.match(line)
            if not shape:
                yield fail(where, "goal heading is not '### Goal <n> · <name> · <finite|standing> · <status>'")
                continue
            goals.append((where, shape["horizon"], set()))
        elif line.startswith("- ") and sections and sections[-1].startswith(GOALS_SECTION) and goals:
            field = GOAL_FIELD.match(line)
            if field:
                goals[-1][2].add(field.group(1))
        elif line.startswith("- ") and sections and sections[-1].startswith(DIRECTIVES_SECTION):
            ground = line.rpartition(GROUND_MARK)[2]
            if GROUND_MARK not in line or not (re.search(DATE, ground) or SHA.search(ground)):
                yield fail(where, "prime directive does not end in 'Ground:' with a dated owner statement or a commit")
    names = spec["sections"]
    if len(sections) != len(names) or not all(title.startswith(name) for title, name in zip(sections, names)):
        yield fail(path, f"sections {sections} do not follow the schema's {names}")
    for where, horizon, fields in goals:
        wanted = [field for field in spec["goal_fields"] if field != COMPLETION_FIELD or horizon == FINITE]
        missing = [field for field in wanted if field not in fields]
        if missing:
            yield fail(where, f"goal lacks {', '.join(missing)}")
        if horizon == STANDING and COMPLETION_FIELD in fields:
            yield fail(where, "a standing goal carries no completion evidence")


def directive_blocks(text: str) -> tuple[list[str], list[tuple[int, str, list[tuple[int, str]]]]]:
    """The directive record's section headings, lowercased, and each rule block with its numbered non-blank lines."""
    headings: list[str] = []
    blocks: list[tuple[int, str, list[tuple[int, str]]]] = []
    block = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            headings.append(line[3:].strip().lower())
            block = None
        elif line.startswith("### "):
            block = (number, line[4:].strip(), [])
            blocks.append(block)
        elif line.strip() and block is not None:
            block[2].append((number, line))
    return headings, blocks


def one_line(text: str) -> str:
    """Admit a value that is not blank and carries no line break."""
    if not text.strip():
        raise ValueError("is empty")
    if text.splitlines() != [text]:
        raise ValueError("carries a line break")
    return text


def table_cell(text: str) -> str:
    """Admit a value fit for one table cell: not blank, one line, holding no '|'."""
    one_line(text)
    if "|" in text:
        raise ValueError("holds '|', which breaks a table row")
    return text


def labelled_fields(model: type[BaseModel]) -> dict[str, FieldInfo]:
    """The model's fields by the label or column each is spelled with in the schema and in input, in declaration order."""
    return {(info.alias or name): info for name, info in model.model_fields.items()}


def holds_text(info: FieldInfo) -> bool:
    """Whether the field holds text, which an optional field may leave null."""
    return info.annotation in (str, str | None)


def fits_text_field(info: FieldInfo, value: object) -> bool:
    """Whether value is text, or null where the field is optional."""
    return isinstance(value, str) or (value is None and not info.is_required())


def refuse_unknown_labels(kind: str, owner: str, fields: Mapping[str, FieldInfo], given: Mapping[str, object]) -> None:
    """Refuse the first label or column in given that the model does not have."""
    for label in given:
        if label not in fields:
            raise ValueError(f"{kind} '{label}' is not a {owner} {kind}")


def refuse_missing_labels(kind: str, fields: Mapping[str, FieldInfo], given: Mapping[str, object]) -> None:
    """Refuse the first required label or column of the model that given leaves out."""
    for label, info in fields.items():
        if info.is_required() and label not in given:
            raise ValueError(f"{kind} '{label}' is missing")


def refuse_non_text_values(kind: str, fields: Mapping[str, FieldInfo], given: Mapping[str, object]) -> None:
    """Refuse the first text label or column of the model that given fills with a value that is not text."""
    for label, info in fields.items():
        if holds_text(info) and label in given and not fits_text_field(info, given[label]):
            raise ValueError(f"{kind} '{label}' is not text")


def refuse_misshapen_input(model: type[BaseModel], kind: str, owner: str, given: Mapping[str, object]) -> Mapping[str, object]:
    """The given mapping, or the refusal naming its first unknown, missing or non-text label or column."""
    fields = labelled_fields(model)
    refuse_unknown_labels(kind, owner, fields, given)
    refuse_missing_labels(kind, fields, given)
    refuse_non_text_values(kind, fields, given)
    return given


def refuse_unfit_value(kind: str, name: str, text: str, admit: Callable[[str], str]) -> None:
    """Refuse text, naming the label or column it sits under, when admit finds it unfit."""
    try:
        admit(text)
    except ValueError as unfit:
        raise ValueError(f"{kind} '{name}' {unfit}") from unfit



class DirectiveRule(BaseModel):
    """One directive rule's labelled body, in the schema's label order."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    when: str
    do: str
    never: str | None = None
    why: str | None = None
    ground: str
    except_: str | None = Field(default=None, alias="except")

    @model_validator(mode="before")
    @classmethod
    def input_fits_the_rules_labels(cls, given: Mapping[str, object]) -> Mapping[str, object]:
        """Refuse input naming a label the rule lacks, leaving a required one out, or giving a value that is not text."""
        return refuse_misshapen_input(cls, "label", "rule", given)

    @model_validator(mode="after")
    def every_label_value_fits_one_line(self) -> Self:
        """Refuse a label whose value is empty or runs over a line, naming the label."""
        for label, value in self.body().items():
            refuse_unfit_value("label", label, value, one_line)
        return self

    def body(self) -> dict[str, str]:
        """This rule's labelled values, by label, carrying only the labels this rule holds one for."""
        return {label: value for label, value in self.model_dump(by_alias=True).items() if value is not None}

    def render(self, width: int = DIRECTIVE_WIDTH) -> str:
        """The rule's body as its block holds it: one label line per label it carries, padded to width."""
        return "\n".join(label.ljust(width) + value for label, value in self.body().items())


DIRECTIVE_LABELS = tuple(labelled_fields(DirectiveRule))
DIRECTIVE_REQUIRED_LABELS = tuple(label for label, info in labelled_fields(DirectiveRule).items() if info.is_required())


def labelled_lines(rule_lines: list[tuple[int, str]], width: int) -> tuple[dict[str, tuple[int, str]] | None, list[tuple[int, str]]]:
    """Each label's line number and value a block's lines hold, or the line-numbered shape problems that keep it from parsing."""
    problems: list[tuple[int, str]] = []
    body: dict[str, tuple[int, str]] = {}
    order: list[str] = []
    for line_number, line in rule_lines:
        label = line[:width].strip()
        if label not in DIRECTIVE_LABELS or line[:width] != label.ljust(width):
            problems.append((line_number, f"not a '{'/'.join(DIRECTIVE_LABELS)}' line padded to {width}"))
            continue
        order.append(label)
        body[label] = (line_number, line[width:])
    if problems:
        return None, problems
    if order != sorted(order, key=DIRECTIVE_LABELS.index) or len(order) != len(set(order)):
        where = rule_lines[0][0] if rule_lines else 0
        return None, [(where, f"labels {order} not in the schema's order, or repeated")]
    return body, []


def check_directives(corpus: Corpus) -> Iterator[Finding]:
    """Fail directive headings, rule ids, label lines, label values and grounds that break the schema's directive shape."""
    spec = corpus.schema["kinds"]["directive"]
    path = home_of(corpus, "directive")
    width = spec["label_width"]
    headings, blocks = directive_blocks(corpus.text(path))
    ids: set[str] = set()
    if headings != spec["headings"]:
        yield fail(path, f"headings {headings} are not the schema's {spec['headings']}")
    if list(spec["labels"]) != list(DIRECTIVE_LABELS) or list(spec["required_labels"]) != list(DIRECTIVE_REQUIRED_LABELS):
        yield fail(
            SCHEMA_HOME,
            f"schema declares directive labels {spec['labels']} (required {spec['required_labels']}); "
            f"memento's DirectiveRule knows {list(DIRECTIVE_LABELS)} (required {list(DIRECTIVE_REQUIRED_LABELS)}); {AMEND_TOGETHER}",
        )
    for number, rule_id, rule_lines in blocks:
        where = f"{path}:{number}"
        if not RULE_ID.fullmatch(rule_id):
            yield fail(where, f"rule id '{rule_id}' is not lowercase words joined by hyphens")
        if rule_id in ids:
            yield fail(where, f"rule id '{rule_id}' appears twice")
        ids.add(rule_id)
        body, problems = labelled_lines(rule_lines, width)
        for line_number, message in problems:
            yield fail(f"{path}:{line_number}", message)
        if body is None:
            continue
        missing = [label for label in DIRECTIVE_REQUIRED_LABELS if label not in body]
        if missing:
            yield fail(where, f"{rule_id}: missing {', '.join(missing)}")
        else:
            try:
                construct(DirectiveRule, **{label: value for label, (_line, value) in body.items()})
            except Refusal as refusal:
                yield fail(where, f"{rule_id}: {refusal}")
        if "ground" in body:
            ground_line, ground_value = body["ground"]
            yield from ground_findings(corpus, f"{path}:{ground_line}", ground_value)


def parsed_date(value: object) -> object:
    """A date object, from a `datetime.date` without a time or a real 'YYYY-MM-DD' string; anything else is refused."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(DATE, value):
        raise ValueError(f"date cell '{value}' is not YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as unreal:
        raise ValueError(f"date cell '{value}' is not a real date") from unreal


ResultDate = Annotated[date, BeforeValidator(parsed_date)]


class ResultRow(BaseModel):
    """One contender's row, in the schema's column order."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    approach: str
    world: str
    labels: str
    folds: str
    r_at_p95_per_fold: str = Field(alias="R@P95/fold")
    p_at_r10_per_fold: str = Field(alias="P@R10/fold")
    p_at_r30_per_fold: str = Field(alias="P@R30/fold")
    p_at_r50_per_fold: str = Field(alias="P@R50/fold")
    bar: str
    run: str
    verdict: str
    date: ResultDate

    @model_validator(mode="before")
    @classmethod
    def input_fits_the_results_columns(cls, given: Mapping[str, object]) -> Mapping[str, object]:
        """Refuse input naming a column the table lacks, leaving one out, or giving a text cell a value that is not text."""
        return refuse_misshapen_input(cls, "column", "results", given)

    @model_validator(mode="after")
    def every_cell_fits_one_table_cell(self) -> Self:
        """Refuse a column whose cell is empty, runs over a line or holds '|', naming the column."""
        for column, cell in zip(self.columns(), self.cells(), strict=True):
            refuse_unfit_value("column", column, cell, table_cell)
        return self

    def key(self) -> tuple[str, str]:
        """This row's (approach, world) identity."""
        return (self.approach, self.world)

    @classmethod
    def columns(cls) -> tuple[str, ...]:
        """This row's columns, in the schema's order."""
        return tuple(labelled_fields(cls))

    def cells(self) -> list[str]:
        """This row's cells, in column order."""
        return [value.isoformat() if isinstance(value, date) else value for value in (getattr(self, name) for name in type(self).model_fields)]

    def render(self) -> str:
        """This row as the table holds it."""
        return "| " + " | ".join(self.cells()) + " |"


RESULT_COLUMNS = ResultRow.columns()


def table_cells(line: str) -> list[str]:
    """The cells of one results table line, each stripped."""
    return [cell.strip() for cell in line.strip("|").split("|")]


def check_results_table(corpus: Corpus) -> Iterator[Finding]:
    """Fail a results table whose columns, cell counts, row contents or repeated keys break the schema's results shape."""
    spec = corpus.schema["kinds"]["results"]
    path = home_of(corpus, "results")
    if list(spec["columns"]) != list(RESULT_COLUMNS):
        yield fail(
            SCHEMA_HOME,
            f"schema declares results columns {spec['columns']}; memento's ResultRow knows {list(RESULT_COLUMNS)}; {AMEND_TOGETHER}",
        )
    rows = [(number, line) for number, line in enumerate(corpus.text(path).splitlines(), 1) if line.startswith("|")]
    if not rows:
        yield fail(path, "holds no table")
        return
    header = table_cells(rows[0][1])
    if header != list(RESULT_COLUMNS):
        yield fail(f"{path}:{rows[0][0]}", f"columns {header} are not the schema's {list(RESULT_COLUMNS)}")
    keys: dict[tuple[str, str], int] = {}
    for number, line in rows[2:]:
        cells = table_cells(line)
        if len(cells) != len(RESULT_COLUMNS):
            yield fail(f"{path}:{number}", f"{len(cells)} cells for {len(RESULT_COLUMNS)} columns")
            continue
        try:
            key = construct(ResultRow, **dict(zip(RESULT_COLUMNS, cells))).key()
        except Refusal as refusal:
            yield fail(f"{path}:{number}", str(refusal))
            continue
        if key in keys:
            yield fail(f"{path}:{number}", f"(approach, world) {key} already appears at {path}:{keys[key]}")
            continue
        keys[key] = number


class RealDateLoader(yaml.SafeLoader):
    """A safe YAML loader that reports a date or time that is not real as a YAML error."""


def construct_real_timestamp(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> date:
    """The date or time a timestamp node holds, or a YAML error naming it when it is not real."""
    try:
        return yaml.SafeLoader.construct_yaml_timestamp(loader, node)
    except ValueError as unreal:
        raise yaml.constructor.ConstructorError(None, None, f"'{node.value}' is not a real date or time", node.start_mark) from unreal


RealDateLoader.add_constructor("tag:yaml.org,2002:timestamp", construct_real_timestamp)


def load_yaml_mapping(path: str) -> dict[str, Any]:
    """The block-form YAML mapping a structured-input file holds, its own errors turned into a refusal."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise Refusal(f"'{path}' is not UTF-8") from error
    except OSError as error:
        raise Refusal(f"'{path}' does not read: {error}") from error
    try:
        data = yaml.load(text, Loader=RealDateLoader)
    except yaml.YAMLError as error:
        raise Refusal(f"'{path}' is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise Refusal(f"'{path}' is not a YAML mapping")
    for key in data:
        if not isinstance(key, str):
            raise Refusal(f"'{path}' names a key that is not text: {key!r}")
    return data


def replace_once_text(corpus: Corpus, path: str, old: str, new: str) -> str:
    """The text of path with its one occurrence of old replaced by new, refusing when old occurs zero or several times."""
    text = corpus.text(path)
    count = text.count(old)
    if count != 1:
        raise Refusal(f"'{old}' occurs {count} times in {path}; replace takes exactly one occurrence")
    return text.replace(old, new)


def land_text(environment: Environment, path: str, new_text: str) -> None:
    """Land a record's whole new text, refusing a candidate FAIL."""
    refuse_new_failures(environment, {path: new_text})
    with environment.store.transaction() as act:
        act.replace(path, new_text)


def replace_in_place(environment: Environment, path: str, old: str, new: str) -> None:
    """Replace one exact occurrence of old with new in path, refusing a candidate FAIL, and land it."""
    corpus = load_corpus(environment.store.root)
    new_text = replace_once_text(corpus, path, old, new)
    land_text(environment, path, new_text)


HEADING_LINE = re.compile(r"(?m)^## (?P<title>.+)$")
RULE_HEADER_LINE = re.compile(r"(?m)^### (?P<id>\S+)$")


def next_marker_after(text: str, position: int) -> int:
    """The start of the next '## ' or '### ' line after position, or the text's end."""
    markers = [match.start() for match in (*HEADING_LINE.finditer(text), *RULE_HEADER_LINE.finditer(text)) if match.start() > position]
    return min(markers, default=len(text))


def section_span(text: str, heading: str, path: str) -> tuple[int, int]:
    """The character span of a heading's section body: after its own line, up to the next '## ' line or the text's end."""
    match = next((match for match in HEADING_LINE.finditer(text) if match["title"].strip().lower() == heading.strip().lower()), None)
    if match is None:
        raise Refusal(f"heading '{heading}' is not one of the schema's headings")
    headings_after = [other.start() for other in HEADING_LINE.finditer(text) if other.start() > match.start()]
    return match.end() + 1, min(headings_after, default=len(text))


def block_span(text: str, rule_id: str, path: str) -> tuple[int, int]:
    """The character span of one rule's block: its header line through the line before the next header or heading."""
    match = next((match for match in RULE_HEADER_LINE.finditer(text) if match["id"] == rule_id), None)
    if match is None:
        raise Refusal(f"rule '{rule_id}' is not in {path}")
    return match.start(), next_marker_after(text, match.start())


def heading_of_block(text: str, position: int, path: str) -> str:
    """The heading whose section holds the block starting at position."""
    headings_before = [match for match in HEADING_LINE.finditer(text) if match.start() < position]
    if not headings_before:
        raise Refusal(f"'{path}' has no heading above this rule")
    return headings_before[-1]["title"]


BLOCK_SEPARATOR = "\n\n"


def closing(text: str, end: int) -> str:
    """The whitespace that follows a block ending at end: a blank-line separator, or a bare newline at the text's end."""
    return BLOCK_SEPARATOR if end < len(text) else "\n"


def insert_block(text: str, heading: str, path: str, block_text: str) -> str:
    """Text with a new block appended at the end of a heading's section, in its established one-blank-line form."""
    start, end = section_span(text, heading, path)
    existing = text[start:end].strip("\n")
    prefix = f"\n{existing}{BLOCK_SEPARATOR}" if existing else "\n"
    return text[:start] + prefix + block_text.rstrip("\n") + closing(text, end) + text[end:]


def directive_input(path: str) -> tuple[str, str, DirectiveRule]:
    """The id, heading and labelled body a rule.yaml file declares."""
    data = load_yaml_mapping(path)
    try:
        rule_id, heading = data.pop("id"), data.pop("heading")
    except KeyError as missing:
        raise Refusal(f"'{path}' is missing {missing.args[0]!r}") from missing
    if not isinstance(rule_id, str):
        raise Refusal(f"'{path}' names a non-text id")
    if not isinstance(heading, str):
        raise Refusal(f"'{path}' names a non-text heading")
    return rule_id, heading, construct(DirectiveRule, **data)


def directive_corpus(environment: Environment) -> tuple[Corpus, str, int]:
    """The current corpus, the directive home and its schema-declared label width."""
    corpus = load_corpus(environment.store.root)
    spec = corpus.schema["kinds"]["directive"]
    return corpus, home_of(corpus, "directive"), spec["label_width"]


def directive_add(environment: Environment, rule_path: str) -> tuple[str, str]:
    """Add a new rule block at the end of its heading's section, refusing a candidate FAIL."""
    rule_id, heading, rule = directive_input(rule_path)
    corpus, path, width = directive_corpus(environment)
    new_text = insert_block(corpus.text(path), heading, path, f"### {rule_id}\n{rule.render(width)}")
    land_text(environment, path, new_text)
    return path, rule_id


def block_replaced(text: str, start: int, end: int, new_block: str) -> str:
    """The text with the block spanning start to end replaced by new_block, the whitespace after it as the text holds it."""
    return text[:start] + new_block.rstrip("\n") + closing(text, end) + text[end:]


def directive_body(text: str, rule_id: str, width: int, path: str) -> dict[str, str]:
    """The labelled values of the rule block with this id, refused when its lines do not parse."""
    _headings, blocks = directive_blocks(text)
    rule_lines = next((lines for _number, found, lines in blocks if found == rule_id), None)
    if rule_lines is None:
        raise Refusal(f"rule '{rule_id}' is not in {path}")
    body, problems = labelled_lines(rule_lines, width)
    if body is None:
        raise Refusal(f"rule '{rule_id}' does not parse: {problems[0][1]}; 'memento directive replace' writes it whole")
    return {label: value for label, (_line, value) in body.items()}


def directive_set(environment: Environment, rule_id: str, label: str, value: str) -> tuple[str, str]:
    """Set one label of the rule with this id, the whole new rule built as replace builds it and kept under its heading, refusing a candidate FAIL."""
    known_name("directive", "label", label, DIRECTIVE_LABELS)
    corpus, path, width = directive_corpus(environment)
    text = corpus.text(path)
    start, end = block_span(text, rule_id, path)
    rule = construct(DirectiveRule, **{**directive_body(text, rule_id, width, path), label: value})
    land_text(environment, path, block_replaced(text, start, end, f"### {rule_id}\n{rule.render(width)}"))
    return path, rule_id


def directive_replace(environment: Environment, rule_path: str) -> tuple[str, str]:
    """Replace the rule block with this id, moved to a new heading's section when its heading changed."""
    rule_id, heading, rule = directive_input(rule_path)
    corpus, path, width = directive_corpus(environment)
    text = corpus.text(path)
    start, end = block_span(text, rule_id, path)
    new_block = f"### {rule_id}\n{rule.render(width)}"
    if heading_of_block(text, start, path).strip().lower() == heading.strip().lower():
        new_text = block_replaced(text, start, end, new_block)
    else:
        without_old = text[:start] + text[end:]
        new_text = insert_block(without_old, heading, path, new_block)
    land_text(environment, path, new_text)
    return path, rule_id


def directive_drop(environment: Environment, rule_id: str) -> str:
    """Delete the rule block with this id, refusing a candidate FAIL (a dangling citation included)."""
    corpus, path, _width = directive_corpus(environment)
    text = corpus.text(path)
    start, end = block_span(text, rule_id, path)
    new_text = text[:start] + text[end:]
    land_text(environment, path, new_text)
    return path


def find_row_line(lines: list[str], key: tuple[str, str], path: str) -> int:
    """The line index of the one data row holding this (approach, world) key, refusing when none does."""
    for index, line in enumerate(lines):
        if index < 2 or not line.startswith("|"):
            continue
        cells = table_cells(line)
        if len(cells) >= 2 and (cells[0], cells[1]) == key:
            return index
    raise Refusal(f"no row in {path} carries key {key}")


def results_lines(environment: Environment) -> tuple[str, list[str]]:
    """The results home and its current lines."""
    corpus = load_corpus(environment.store.root)
    path = home_of(corpus, "results")
    return path, corpus.text(path).splitlines()


def land_lines(environment: Environment, path: str, lines: list[str]) -> None:
    """Land a table record whose text is these lines, refusing a candidate FAIL."""
    land_text(environment, path, "\n".join(lines) + "\n")


def results_add(environment: Environment, row_path: str) -> tuple[str, tuple[str, str]]:
    """Add a new row at the end of the table, refusing a candidate FAIL (an existing key included)."""
    row = construct(ResultRow, **load_yaml_mapping(row_path))
    path, lines = results_lines(environment)
    land_lines(environment, path, [*lines, row.render()])
    return path, row.key()


def results_replace(environment: Environment, row_path: str) -> tuple[str, tuple[str, str]]:
    """Replace the row holding this row's (approach, world) key, refusing a candidate FAIL."""
    row = construct(ResultRow, **load_yaml_mapping(row_path))
    path, lines = results_lines(environment)
    lines[find_row_line(lines, row.key(), path)] = row.render()
    land_lines(environment, path, lines)
    return path, row.key()


def results_set(environment: Environment, approach: str, world: str, column: str, value: str) -> tuple[str, tuple[str, str]]:
    """Set one column of the row holding this (approach, world) key, the whole new row built as replace builds it, refusing a candidate FAIL."""
    known_name("results", "column", column, RESULT_COLUMNS)
    path, lines = results_lines(environment)
    index = find_row_line(lines, (approach, world), path)
    cells = table_cells(lines[index])
    if len(cells) != len(RESULT_COLUMNS):
        raise Refusal(f"the row in {path} carrying key {(approach, world)} has {len(cells)} cells for {len(RESULT_COLUMNS)} columns")
    row = construct(ResultRow, **{**dict(zip(RESULT_COLUMNS, cells)), column: value})
    lines[index] = row.render()
    land_lines(environment, path, lines)
    return path, row.key()


def results_drop(environment: Environment, approach: str, world: str) -> str:
    """Delete the row holding this (approach, world) key, refusing a candidate FAIL."""
    path, lines = results_lines(environment)
    del lines[find_row_line(lines, (approach, world), path)]
    land_lines(environment, path, lines)
    return path


def spec_add(environment: Environment, name: str, draft_path: str) -> str:
    """Add a new spec from a draft file's bytes, refusing a name outside the schema's pattern or an existing spec."""
    if not RULE_ID.fullmatch(name):
        raise Refusal(f"spec name '{name}' is not lowercase words joined by hyphens")
    corpus = load_corpus(environment.store.root)
    path = spec_path(corpus.schema, name)
    if corpus.exists(path):
        raise Refusal(f"'{path}' already exists")
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as error:
        raise Refusal(f"'{draft_path}' does not read: {error}") from error
    refuse_new_failures(environment, {path: text})
    with environment.store.transaction() as act:
        act.replace(path, text)
    return path


def spec_archive(environment: Environment, name: str) -> tuple[str, str]:
    """Move a spec whole to the archive, dated by today, refusing when it is absent or the destination exists."""
    corpus = load_corpus(environment.store.root)
    path = spec_path(corpus.schema, name)
    if not corpus.exists(path):
        raise Refusal(f"'{path}' does not exist")
    archive_path = spec_archive_path(corpus.schema, name, environment.now().date().isoformat())
    if corpus.exists(archive_path):
        raise Refusal(f"'{archive_path}' already exists")
    text = corpus.text(path)
    refuse_new_failures(environment, {path: None, archive_path: text})
    with environment.store.transaction() as act:
        act.remove(path)
        act.replace(archive_path, text)
    return path, archive_path


@directive_group.command(
    "add",
    help=(
        "Add a new rule at the end of its heading's section in the directive record, from a block-form YAML file "
        "naming id, heading, when, do, never, why, ground and except. Prints '<directive home>: <id>'. Refuses: "
        "a file that does not read, is not UTF-8, is not valid YAML or is not a mapping; a key that is not text; "
        "a missing or non-text id or heading; a label that is not a rule label; a required label (when, do, "
        "ground) is missing; a label value that is not text; a label value that is empty, blank or carries a line "
        "break; the id is not lowercase words joined by hyphens; the heading is not one of the schema's; a ground "
        "naming no committed path, SHA, record heading or dated owner ruling; the id already exists; any other "
        "candidate FAIL."
    ),
)
def directive_add_command(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help="Path to the rule.yaml file."),
    quiet: bool = quiet_option(),
) -> None:
    """Add a new directive rule from a rule.yaml file."""
    with refusals_reported():
        path, rule_id = directive_add(ctx.obj, rule)
    announce(f"{path}: {rule_id}", quiet)


@directive_group.command(
    "replace",
    help=(
        "Replace the rule this id names with a rule.yaml file's content, moved to a new heading's section when its "
        "heading changed. Prints '<directive home>: <id>'. Refuses: the id does not exist; any refusal 'directive "
        "add' names."
    ),
)
def directive_replace_command(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help="Path to the rule.yaml file."),
    quiet: bool = quiet_option(),
) -> None:
    """Replace a directive rule from a rule.yaml file."""
    with refusals_reported():
        path, rule_id = directive_replace(ctx.obj, rule)
    announce(f"{path}: {rule_id}", quiet)


@directive_group.command(
    "set",
    help=(
        "Set one label of the rule this id names to a value: the whole new rule is built from the rule's own labelled "
        "values with that label replaced, validated as 'directive replace' validates a rule file, and landed in place "
        "under its heading. Reads .claude/memento.yaml and the directive record. Prints '<directive home>: <id>'. "
        "Refuses: a label that is not a rule label (naming the labels); the id does not exist; a rule whose lines "
        "do not parse; a value that is empty, blank or carries a line break; a ground naming no committed path, SHA, "
        "record heading or dated owner ruling; any other candidate FAIL; a worktree."
    ),
)
def directive_set_command(
    ctx: typer.Context,
    rule_id: str = typer.Argument(..., help="The rule's id."),
    label: str = typer.Argument(..., help="The label to set: when, do, never, why, ground or except."),
    value: str = typer.Argument(..., help="The label's new value, one line."),
    quiet: bool = quiet_option(),
) -> None:
    """Set one label of a directive rule."""
    with refusals_reported():
        path, found = directive_set(ctx.obj, rule_id, label, value)
    announce(f"{path}: {found}", quiet)


@directive_group.command(
    "drop",
    help=(
        "Delete the rule this id names from the directive record. Prints '<directive home>: <id>'. Refuses: the "
        "id does not exist; the rule is cited as a directive rule in a record or in tracked code."
    ),
)
def directive_drop_command(
    ctx: typer.Context,
    rule_id: str = typer.Argument(..., help="The rule's id."),
    quiet: bool = quiet_option(),
) -> None:
    """Delete a directive rule by its id."""
    with refusals_reported():
        path = directive_drop(ctx.obj, rule_id)
    announce(f"{path}: {rule_id}", quiet)


@results_group.command(
    "add",
    help=(
        "Add a new row at the end of the results record, from a block-form YAML file naming the twelve schema columns "
        "(date a YAML date). Prints '<results home>: (<approach>, <world>)'. Refuses: a file that does not read, "
        "is not UTF-8, is not valid YAML or is not a mapping; a key that is not text; a column that is not a "
        "results column; a column is missing; a cell that is not text; the (approach, world) key already exists; a "
        "cell that is empty, blank, holds '|' or carries a line break; a date that is not a real YYYY-MM-DD; any "
        "other candidate FAIL."
    ),
)
def results_add_command(
    ctx: typer.Context,
    row: str = typer.Argument(..., help="Path to the row.yaml file."),
    quiet: bool = quiet_option(),
) -> None:
    """Add a new results row from a row.yaml file."""
    with refusals_reported():
        path, key = results_add(ctx.obj, row)
    announce(f"{path}: {key}", quiet)


@results_group.command(
    "replace",
    help=(
        "Replace the row holding a row.yaml file's (approach, world) key. Prints '<results home>: (<approach>, "
        "<world>)'. Refuses: the key does not exist; any refusal 'results add' names."
    ),
)
def results_replace_command(
    ctx: typer.Context,
    row: str = typer.Argument(..., help="Path to the row.yaml file."),
    quiet: bool = quiet_option(),
) -> None:
    """Replace a results row from a row.yaml file."""
    with refusals_reported():
        path, key = results_replace(ctx.obj, row)
    announce(f"{path}: {key}", quiet)


@results_group.command(
    "set",
    help=(
        "Set one column of the row holding this (approach, world) key to a value: the whole new row is built from the "
        "row's own cells with that column replaced, validated as 'results replace' validates a row file, and landed in "
        "place. Reads .claude/memento.yaml and the results record. Prints '<results home>: (<approach>, <world>)' of "
        "the row after the change. Refuses: a column that is not a results column (naming the columns); no row holds "
        "the key; a row whose cell count differs from the columns; a value that is empty, blank, holds '|' or carries "
        "a line break; a date that is not a real YYYY-MM-DD; a key the change would repeat or any other candidate "
        "FAIL; a worktree."
    ),
)
def results_set_command(
    ctx: typer.Context,
    approach: str = typer.Argument(..., help="The row's approach cell."),
    world: str = typer.Argument(..., help="The row's world cell."),
    column: str = typer.Argument(..., help="The column to set: a results column, such as verdict or date."),
    value: str = typer.Argument(..., help="The cell's new value, one line."),
    quiet: bool = quiet_option(),
) -> None:
    """Set one column of a results row."""
    with refusals_reported():
        path, key = results_set(ctx.obj, approach, world, column, value)
    announce(f"{path}: {key}", quiet)


@results_group.command(
    "drop",
    help="Delete the row holding this (approach, world) key. Prints '<results home>: (<approach>, <world>)'. Refuses: no such row.",
)
def results_drop_command(
    ctx: typer.Context,
    approach: str = typer.Argument(..., help="The row's approach cell."),
    world: str = typer.Argument(..., help="The row's world cell."),
    quiet: bool = quiet_option(),
) -> None:
    """Delete a results row by its (approach, world) key."""
    with refusals_reported():
        path = results_drop(ctx.obj, approach, world)
    announce(f"{path}: ({approach}, {world})", quiet)


@fact_group.command(
    "replace",
    help=(
        "Replace the one occurrence of old with new in the fact record. Prints '<fact home>'. "
        "Refuses: old occurs zero or several times in the file, naming the count; any candidate FAIL."
    ),
)
def fact_replace_command(
    ctx: typer.Context,
    old: str = typer.Argument(..., help="The exact passage to replace."),
    new: str = typer.Argument(..., help="The passage to put in its place."),
    quiet: bool = quiet_option(),
) -> None:
    """Replace one exact passage of the fact record."""
    with refusals_reported():
        path = home_of(load_corpus(ctx.obj.store.root), "fact")
        replace_in_place(ctx.obj, path, old, new)
    announce(path, quiet)


@string_recipes_group.command(
    "replace",
    help=(
        "Replace the one occurrence of old with new in the string_recipes record. Prints "
        "'<string_recipes home>'. Refuses: old occurs zero or several times in the file, naming the "
        "count; any candidate FAIL."
    ),
)
def string_recipes_replace_command(
    ctx: typer.Context,
    old: str = typer.Argument(..., help="The exact passage to replace."),
    new: str = typer.Argument(..., help="The passage to put in its place."),
    quiet: bool = quiet_option(),
) -> None:
    """Replace one exact passage of the string_recipes record."""
    with refusals_reported():
        path = home_of(load_corpus(ctx.obj.store.root), "string_recipes")
        replace_in_place(ctx.obj, path, old, new)
    announce(path, quiet)


@spec_group.command(
    "add",
    help=(
        "Add <spec home>/<name>.md holding a draft file's bytes. Prints the path written. Refuses: the name exists; "
        "the name is not lowercase words joined by hyphens; any candidate FAIL."
    ),
)
def spec_add_command(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The spec's name."),
    draft: str = typer.Argument(..., help="Path to the draft file whose bytes become the spec."),
    quiet: bool = quiet_option(),
) -> None:
    """Add a new spec from a draft file."""
    with refusals_reported():
        path = spec_add(ctx.obj, name, draft)
    announce(path, quiet)


@spec_group.command(
    "replace",
    help=(
        "Replace the one occurrence of old with new in <spec home>/<name>.md. Prints the path. Refuses: no such "
        "spec; old occurs zero or several times, naming the count; any candidate FAIL."
    ),
)
def spec_replace_command(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The spec's name."),
    old: str = typer.Argument(..., help="The exact passage to replace."),
    new: str = typer.Argument(..., help="The passage to put in its place."),
    quiet: bool = quiet_option(),
) -> None:
    """Replace one exact passage of a spec."""
    with refusals_reported():
        corpus = load_corpus(ctx.obj.store.root)
        path = spec_path(corpus.schema, name)
        if not corpus.exists(path):
            raise Refusal(f"'{path}' does not exist")
        replace_in_place(ctx.obj, path, old, new)
    announce(path, quiet)


@spec_group.command(
    "archive",
    help=(
        "Move <spec home>/<name>.md whole to <archived home>/spec-<name>-<today>.md. Prints both "
        "paths. Refuses: no such spec; the destination already exists."
    ),
)
def spec_archive_command(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="The spec's name."),
    quiet: bool = quiet_option(),
) -> None:
    """Archive a spec whole, dated by today."""
    with refusals_reported():
        path, archive_path = spec_archive(ctx.obj, name)
    announce(f"{path} -> {archive_path}", quiet)
