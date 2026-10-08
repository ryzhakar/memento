"""Living records, corrected in place: charter, map, conventions and results."""

import re
from collections.abc import Iterator

import typer

from memento.findings import DATE, SHA, Corpus, Finding, fail
from memento.pointers import ground_findings, home_of
from memento.schema import SCHEMA_HOME, home_pattern

directive_group = typer.Typer(no_args_is_help=True, help="Conventions: add, replace or drop a rule.")
results_group = typer.Typer(no_args_is_help=True, help="Results: add, replace or drop a contender's row.")
fact_group = typer.Typer(no_args_is_help=True, help="Capabilities: replace one exact passage.")
string_recipes_group = typer.Typer(no_args_is_help=True, help="String-comparison recipes: replace one exact passage.")
spec_group = typer.Typer(no_args_is_help=True, help="Specifications: add one, replace one exact passage, archive one.")
map_group = typer.Typer(no_args_is_help=True, help="The map: render it from the schema.")

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
RECORD_SPACE_ROOT = "docs/"
JOURNAL_SPACE_ROOT = "docs/orchestration_log/"
CONFIG_SPACE_ROOT = ".claude/"
TEMPLATE_HOME_MARKS = "{*"
DIRECTORY_MARK = "/"


def in_record_space(path: str) -> bool:
    """Whether a tracked path sits where only records, products and retired paths may live."""
    in_docs = path.startswith(RECORD_SPACE_ROOT) and not path.startswith(JOURNAL_SPACE_ROOT)
    at_root = DIRECTORY_MARK not in path and path.endswith(".md")
    in_config = path.startswith(CONFIG_SPACE_ROOT) and path.count(DIRECTORY_MARK) == 1
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
        if not in_record_space(path):
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
    """The conventions' section headings, lowercased, and each rule block with its numbered non-blank lines."""
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


def check_directives(corpus: Corpus) -> Iterator[Finding]:
    """Fail conventions headings, rule ids, label lines and grounds that break the schema's directive shape."""
    spec = corpus.schema["kinds"]["directive"]
    path = home_of(corpus, "directive")
    labels, width = spec["labels"], spec["label_width"]
    headings, blocks = directive_blocks(corpus.text(path))
    ids: set[str] = set()
    if headings != spec["headings"]:
        yield fail(path, f"headings {headings} are not the schema's {spec['headings']}")
    for number, rule_id, rule_lines in blocks:
        where = f"{path}:{number}"
        if not RULE_ID.fullmatch(rule_id):
            yield fail(where, f"rule id '{rule_id}' is not lowercase words joined by hyphens")
        if rule_id in ids:
            yield fail(where, f"rule id '{rule_id}' appears twice")
        ids.add(rule_id)
        order = []
        for line_number, line in rule_lines:
            label = line[:width].strip()
            if label not in labels or line[:width] != label.ljust(width):
                yield fail(f"{path}:{line_number}", f"not a '{'/'.join(labels)}' line padded to {width}")
                continue
            order.append(label)
            if label == "ground":
                yield from ground_findings(corpus, f"{path}:{line_number}", line[width:])
        missing = [label for label in spec["required_labels"] if label not in order]
        if missing:
            yield fail(where, f"{rule_id}: missing {', '.join(missing)}")
        if order != sorted(order, key=labels.index) or len(order) != len(set(order)):
            yield fail(where, f"{rule_id}: labels {order} not in the schema's order, or repeated")


def check_results_table(corpus: Corpus) -> Iterator[Finding]:
    """Fail a results table whose columns, cell counts or date cells break the schema's results shape."""
    spec = corpus.schema["kinds"]["results"]
    path = home_of(corpus, "results")
    rows = [(number, line) for number, line in enumerate(corpus.text(path).splitlines(), 1) if line.startswith("|")]
    if not rows:
        yield fail(path, "holds no table")
        return
    header = [cell.strip() for cell in rows[0][1].strip("|").split("|")]
    if header != spec["columns"]:
        yield fail(f"{path}:{rows[0][0]}", f"columns {header} are not the schema's {spec['columns']}")
    for number, line in rows[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(spec["columns"]):
            yield fail(f"{path}:{number}", f"{len(cells)} cells for {len(spec['columns'])} columns")
        elif not re.fullmatch(DATE, cells[-1]):
            yield fail(f"{path}:{number}", f"date cell '{cells[-1]}' is not YYYY-MM-DD")

