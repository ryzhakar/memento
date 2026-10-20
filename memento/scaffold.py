"""`memento init`: write a new project's schema and the stub record of every kind, so `memento check` passes once its files are committed."""

from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import typer
import yaml

from memento import Refusal, announce, quiet_option, refusals_reported
from memento.git import git_output
from memento.schema import SCHEMA_HOME

PLACEHOLDER_RATIFIED_ON = "@@RATIFIED_ON@@"
PLACEHOLDER_OWNER = "@@OWNER@@"
PLACEHOLDER_TODAY = "@@TODAY@@"
CHARTER_STUB = """# {name}

@docs/ground-truth.md
@docs/conventions.md
@docs/architecture_log.md
@.claude/memento.yaml

## Condition

Each session opens with no memory of the last. What is written here and in the records is all that carries over.

## Session start

1. Init: `memento:init`. Ground: the condition above.

## Goals

None stated by the owner.

## Prime directives

None stated by the owner.

## Pointers

- Record map: `.claude/memento-map.md`.
- On need: `docs/capabilities.md` (what the system is and does).
"""
STUBS = {
    "docs/ground-truth.md": "# Project Ground Truth\n\nNot yet written: the owner authors this file.\n",
    "docs/conventions.md": (
        "# Conventions\n\n## commit & merge\n\n## code\n\n## measuring & quoting\n\n## reviewing & tests\n\n"
        "## writing craft\n\n## delegating agents\n\n## owner\n"
    ),
    "docs/architecture_log.md": "# Architecture Log\n",
    "docs/capabilities.md": "# Capabilities\n",
    "docs/measurements.yaml": "",
    "docs/user_deferred_items.md": "# Deferred items\n",
    "docs/specs/.gitkeep": "",
    "docs/reference/.gitkeep": "",
    "docs/orchestration_log/archive/.gitkeep": "",
}
MAP_ROWS = (
    ("CLAUDE.md", "charter", "self", "goals, session start, prime directives, pointers"),
    ("docs/ground-truth.md", "frame", "self", "the problem as given"),
    ("docs/conventions.md", "directive", "self", "how to work: rules with grounds"),
    ("docs/architecture_log.md", "change", "self", "how the system's core changed"),
    (".claude/memento.yaml", "schema", "self", "record kinds, homes, formats, admission"),
    (".claude/memento-map.md", "map", "start", "index of records"),
    ("docs/orchestration_log/history/{date}/events*.md", "events", "start (tail)", "one line per event"),
    ("docs/orchestration_log/history/{date}/session*.md", "digest", "start (close block)", "a span's narrative and close block"),
    ("docs/capabilities.md", "fact", "need", "what the system is and does, dated"),
    ("docs/measurements.yaml", "measurement", "need", "every performance reading, with its context"),
    ("docs/specs/", "spec", "need", "current specifications of unbuilt work"),
    ("docs/user_deferred_items.md", "deferral", "need", "the owner's postponed work"),
    ("docs/orchestration_log/history/{date}/failures*.md", "failure", "latent", "diagnosed failures"),
    ("docs/reference/", "reference", "latent", "upstream specifications as the owner supplied them"),
    ("docs/orchestration_log/archive/", "archived", "latent", "out of force, kept"),
    ("docs/orchestration_log/recon/{date}/", "scratch", "latent", "drafts, claims, worker returns"),
)
MAP_STUB_HEAD = "# {name} map\n\n| path | kind | load | purpose |\n|---|---|---|---|\n"


def map_stub(name: str) -> str:
    """The map of a fresh project: one row per non-latent record and one pattern row per latent home."""
    rows = "".join(f"| {path} | {kind} | {load} | {purpose} |\n" for path, kind, load, purpose in MAP_ROWS)
    return MAP_STUB_HEAD.format(name=name) + rows


def project_block(ratified_on: str, main_branch: str, code_roots: list[str], measured_cli: str, commit_type: str, words: int) -> str:
    """The schema's `project:` block, as YAML text."""
    block = {
        "project": {
            "ratified_on": ratified_on,
            "main_branch": main_branch,
            "record_space": "docs/",
            "code_roots": code_roots,
            "history_start_file": SCHEMA_HOME,
            "measured_cli": measured_cli,
            "commit_type": commit_type,
            "commit_subject_words": words,
        }
    }
    return yaml.safe_dump(block, sort_keys=False)


def schema_text(owner: str, ratified_on: str, today: str, project: str) -> str:
    """The scaffold schema with its placeholders filled and the `project:` block appended."""
    template = resources.files("memento").joinpath("scaffold/memento.yaml").read_text()
    filled = template.replace(PLACEHOLDER_RATIFIED_ON, ratified_on).replace(PLACEHOLDER_OWNER, owner).replace(PLACEHOLDER_TODAY, today)
    return filled + "\n" + project


def shipped_default() -> str:
    """The text of the plugin's `config/default.yaml`: the scaffold schema with neutral values, generated so the two never differ."""
    project = project_block("2000-01-01", "main", [], "", "docs", 9)
    return schema_text("the owner", "2000-01-01", "2000-01-01", project)


def scaffold(root: Path, name: str, owner: str, ratified_on: str, main_branch: str, code_roots: list[str], measured_cli: str, commit_type: str, words: int) -> list[str]:
    """Write the schema and every stub under root, refusing to touch a path that already exists; return the paths written."""
    project = project_block(ratified_on, main_branch, code_roots, measured_cli, commit_type, words)
    files = {
        SCHEMA_HOME: schema_text(owner, ratified_on, date.today().isoformat(), project),
        "CLAUDE.md": CHARTER_STUB.format(name=name),
        ".claude/memento-map.md": map_stub(name),
        **STUBS,
    }
    present = [path for path in files if (root / path).exists()]
    if present:
        raise Refusal(f"init writes nothing over an existing path; present: {', '.join(present)}")
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return list(files)


INIT_HELP = (
    "Write a new project's schema (.claude/memento.yaml, its project: block from the options and git) and the stub record "
    "of every kind, so 'memento check' passes once the files are committed. Reads git (the checkout's top level and branch). Writes the "
    "files and commits nothing. Prints one path per file written. Refuses: a path that already exists, naming every one "
    "present, before writing any; a directory that is not inside a git checkout."
)


def init(
    name: str | None = typer.Option(None, help="The project's name, for the charter's title; the checkout's directory name when omitted."),
    owner: str = typer.Option("the owner", help="The owner, as the provenance form names them."),
    ratified_on: str | None = typer.Option(None, help="The day before setup, 'YYYY-MM-DD'; yesterday when omitted, so no date exempts any file."),
    code_root: list[str] = typer.Option([], help="A directory of code the pointer scan reads; repeat for several."),
    measured_cli: str = typer.Option("", help="The CLI file whose commands the measurement stage check reads; empty when the project has none."),
    commit_type: str = typer.Option("docs", help="The type word `memento commit` prefixes."),
    words: int = typer.Option(9, help="The most words a `memento commit` subject may hold."),
    quiet: bool = quiet_option(),
) -> None:
    """Write a new project's schema and the stub of every record kind, then stop; commit nothing."""
    with refusals_reported():
        top = Path(git_output(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        branch = git_output(top, "branch", "--show-current").strip() or "main"
        day = ratified_on or (date.today() - timedelta(days=1)).isoformat()
        written = scaffold(top, name or top.name, owner, day, branch, list(code_root), measured_cli, commit_type, words)
    announce("\n".join(written), quiet)
