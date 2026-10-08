"""The schema `.claude/memento.yaml` declares, and what memento knows of it."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from memento import Refusal
from memento.actor import ACTOR_FORMS
from memento.findings import Corpus, Finding, fail

SCHEMA_HOME = ".claude/memento.yaml"
DATE_PLACEHOLDER = "{date}"
SEGMENT_WILDCARD = "*"
DIRECTORY_MARK = "/"
ISO_DATE_SEGMENT = r"\d{4}-\d{2}-\d{2}"
ANY_SEGMENT_RUN = r"[^/]*"
ANYTHING_BENEATH = r".+"
AMEND_TOGETHER = "amend the module with the schema"
EXACT_HOME_RANK = 0
WILDCARD_HOME_RANK = 1
DIRECTORY_HOME_RANK = 2

KNOWN_KINDS = (
    "charter",
    "frame",
    "directive",
    "change",
    "schema",
    "map",
    "fact",
    "results",
    "measurement",
    "deferral",
    "string_recipes",
    "label_audit",
    "label_defects",
    "reference",
    "events",
    "digest",
    "failure",
    "report",
    "scratch",
    "archived",
)

EventKind = Literal["decision", "discovery", "commitment", "delegation", "receipt", "attenuation"]
EVENTS_KIND = "events"
EVENT_KINDS: tuple[str, ...] = get_args(EventKind)
KNOWN_EVENT_ROUTES = {kind: EVENTS_KIND for kind in EVENT_KINDS} | {
    "span-event": EVENTS_KIND,
    "failure": "failure",
    "change": "change",
}

type Schema = dict[str, Any]


def load_schema(root: Path) -> Schema:
    """The schema as root's `.claude/memento.yaml` declares it."""
    return yaml.safe_load((root / SCHEMA_HOME).read_text(encoding="utf-8"))


def structure_disagreements(schema: Schema) -> list[str]:
    """Each place the schema's declared structure differs from the structure memento is built for."""
    declared_kinds = list(schema["kinds"])
    declared_actors = list(schema["kinds"].get(EVENTS_KIND, {}).get("actors", []))
    disagreements = []
    if set(declared_kinds) != set(KNOWN_KINDS):
        disagreements.append(f"schema declares kinds {declared_kinds}; memento knows {list(KNOWN_KINDS)}")
    if schema["events"] != KNOWN_EVENT_ROUTES:
        disagreements.append(f"schema declares events {schema['events']}; memento knows {KNOWN_EVENT_ROUTES}")
    if declared_actors != list(ACTOR_FORMS):
        disagreements.append(f"schema declares events actors {declared_actors}; memento knows {list(ACTOR_FORMS)}")
    return disagreements


def agreeing_schema(root: Path) -> Schema:
    """The schema at root, refused when its structure disagrees with memento's."""
    schema = load_schema(root)
    disagreements = structure_disagreements(schema)
    if disagreements:
        raise Refusal("; ".join(disagreements) + f"; {AMEND_TOGETHER}")
    return schema


def check_structure(corpus: Corpus) -> Iterator[Finding]:
    """Fail the schema wherever its declared structure differs from memento's."""
    for disagreement in structure_disagreements(corpus.schema):
        yield fail(SCHEMA_HOME, f"{disagreement}; {AMEND_TOGETHER}")


def home_pattern(home: str) -> re.Pattern[str]:
    """The paths a home covers, by the schema's home grammar."""
    pieces = re.split(rf"({re.escape(DATE_PLACEHOLDER)}|{re.escape(SEGMENT_WILDCARD)})", home)
    translated = {DATE_PLACEHOLDER: ISO_DATE_SEGMENT, SEGMENT_WILDCARD: ANY_SEGMENT_RUN}
    pattern = "".join(translated.get(piece, re.escape(piece)) for piece in pieces)
    return re.compile(pattern + ANYTHING_BENEATH if home.endswith(DIRECTORY_MARK) else pattern)


def home_precedence(home: str) -> int:
    """A home's rank in resolution: an exact path first, then a wildcard, then a bare directory."""
    if home.endswith(DIRECTORY_MARK):
        return DIRECTORY_HOME_RANK
    if DATE_PLACEHOLDER in home or SEGMENT_WILDCARD in home:
        return WILDCARD_HOME_RANK
    return EXACT_HOME_RANK


def kind_for(schema: Schema, path: str) -> str | None:
    """The kind whose home covers a project-relative path, or None when no home does."""
    covering = [kind for kind, spec in schema["kinds"].items() if home_pattern(spec["home"]).fullmatch(path)]
    return min(covering, key=lambda kind: home_precedence(schema["kinds"][kind]["home"]), default=None)


def events_home(schema: Schema) -> str:
    """The home the schema gives the events kind."""
    return schema["kinds"][EVENTS_KIND]["home"]
