"""The schema `.claude/memento.yaml` declares, and what memento knows of it."""

import re
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from memento import Refusal
from memento.actor import ACTOR_FORMS
from memento.findings import Corpus, Finding, fail, schema_mapping
from memento.git import is_worktree, main_checkout, tracked_files

SCHEMA_HOME = ".claude/memento.yaml"
DATE_PLACEHOLDER = "{date}"
SEGMENT_WILDCARD = "*"
DIRECTORY_MARK = "/"
ISO_DATE_SEGMENT = r"\d{4}-\d{2}-\d{2}"
ANY_SEGMENT_RUN = r"[^/]*"
ANYTHING_BENEATH = r".+"
AMEND_TOGETHER = "amend the module with the schema"
OWNER_STATED = "owner-stated"
EXACT_HOME_RANK = 0
WILDCARD_HOME_RANK = 1
DIRECTORY_HOME_RANK = 2
OWN_KIND_FIELDS = ("home", "tier", "load", "verification")
SPAN_HOME = re.compile(r"[^*{}]+/\{date\}/[^*{}/]+\*\.md")

KNOWN_KINDS = (
    "charter",
    "frame",
    "directive",
    "change",
    "schema",
    "map",
    "fact",
    "events",
    "digest",
    "failure",
    "scratch",
    "archived",
)

EventKind = Literal["decision", "discovery", "commitment", "delegation", "receipt", "attenuation"]
EVENTS_KIND = "events"
DIGEST_KIND = "digest"
FAILURE_KIND = "failure"
SPAN_KINDS = (EVENTS_KIND, DIGEST_KIND, FAILURE_KIND)
EVENT_KINDS: tuple[str, ...] = get_args(EventKind)
KNOWN_EVENT_ROUTES = {kind: EVENTS_KIND for kind in EVENT_KINDS} | {
    "span-event": EVENTS_KIND,
    "failure": "failure",
    "change": "change",
}

type Schema = dict[str, Any]


def event_kinds_of(schema: Schema) -> tuple[str, ...]:
    """The kinds `events add` writes under this schema: the shipped ones, then each event kind the schema declares for its own events file."""
    own = [kind for kind, route in schema["events"].items() if route == EVENTS_KIND and kind not in KNOWN_EVENT_ROUTES]
    return (*EVENT_KINDS, *own)


def load_schema(root: Path) -> Schema:
    """The schema as root's `.claude/memento.yaml` declares it."""
    schema_file = root / SCHEMA_HOME
    if not schema_file.is_file():
        raise Refusal(f"{SCHEMA_HOME} not found under {root}; run `memento init` in a new project")
    return schema_mapping(yaml.safe_load(schema_file.read_text(encoding="utf-8")))


def load_corpus(root: Path) -> Corpus:
    """The corpus every check reads: root's schema, its tracked paths, and its working tree, with the main checkout beside it when root is a linked worktree."""
    return Corpus(root=root, schema=load_schema(root), tracked=tracked_files(root), main_root=main_checkout(root) if is_worktree(root) else None)


def structure_disagreements(schema: Schema) -> list[str]:
    """Each place the schema's declared structure differs from the structure memento is built for."""
    declared_kinds = list(schema["kinds"])
    declared_actors = list(schema["kinds"].get(EVENTS_KIND, {}).get("actors", []))
    disagreements = []
    missing = [kind for kind in KNOWN_KINDS if kind not in declared_kinds]
    if missing:
        disagreements.append(f"schema omits the kinds memento is built on: {missing}")
    for kind in declared_kinds:
        lacking = [field for field in OWN_KIND_FIELDS if field not in schema["kinds"][kind]]
        if kind not in KNOWN_KINDS and lacking:
            disagreements.append(f"schema's own kind {kind} lacks {lacking}")
    declared_routes = dict(schema["events"])
    if {kind: route for kind, route in declared_routes.items() if kind in KNOWN_EVENT_ROUTES} != KNOWN_EVENT_ROUTES:
        disagreements.append(f"schema declares events {declared_routes}; memento knows {KNOWN_EVENT_ROUTES}")
    misrouted = {kind: route for kind, route in declared_routes.items() if kind not in KNOWN_EVENT_ROUTES and route != EVENTS_KIND}
    if misrouted:
        disagreements.append(f"schema routes its own event kinds {misrouted} elsewhere than to {EVENTS_KIND}")
    if declared_actors != list(ACTOR_FORMS):
        disagreements.append(f"schema declares events actors {declared_actors}; memento knows {list(ACTOR_FORMS)}")
    return disagreements + span_home_disagreements(schema)


def span_home_disagreements(schema: Schema) -> list[str]:
    """Each span kind whose home leaves no slot for a span's suffix: a home ending `{date}/<name>*.md`."""
    homes = {kind: schema["kinds"].get(kind, {}).get("home", "") for kind in SPAN_KINDS}
    return [
        f"schema declares {kind} home {home}; memento knows homes ending '{DATE_PLACEHOLDER}/<name>{SEGMENT_WILDCARD}.md', the '{SEGMENT_WILDCARD}' standing for a span's suffix"
        for kind, home in homes.items()
        if kind in schema["kinds"] and not SPAN_HOME.fullmatch(home)
    ]


def agreeing_schema(root: Path) -> Schema:
    """The schema at root, refused when its structure disagrees with memento's."""
    schema = load_schema(root)
    disagreements = structure_disagreements(schema)
    if disagreements:
        raise Refusal("; ".join(disagreements) + f"; {AMEND_TOGETHER}")
    return schema


def owner_only_kinds(schema: Schema) -> list[str]:
    """The kinds the schema marks owner-stated, which memento never writes."""
    return [kind for kind, spec in schema["kinds"].items() if spec.get("verification") == OWNER_STATED]


def owner_only_group_disagreements(schema: Schema, registered_groups: Collection[str]) -> list[str]:
    """Each owner-only kind for which memento registers a command group."""
    return [
        f"schema marks {kind} {OWNER_STATED}; memento registers a '{kind}' group"
        for kind in owner_only_kinds(schema)
        if kind in registered_groups
    ]


def check_structure(corpus: Corpus, registered_groups: Collection[str]) -> Iterator[Finding]:
    """Fail the schema wherever its declared structure differs from memento's, or an owner-only kind has a group."""
    disagreements = structure_disagreements(corpus.schema) + owner_only_group_disagreements(corpus.schema, registered_groups)
    for disagreement in disagreements:
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
