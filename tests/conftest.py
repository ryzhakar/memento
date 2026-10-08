"""Record trees shaped by the project's schema, committed in a fresh git repository."""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from memento import Environment
from memento.__main__ import CHECKS, app
from memento.findings import Check
from memento.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ".claude/memento.yaml"
PROSE_KEYS = ("format", "admission")
AGING_TEXT = "the oldest entries move to the archive"
CLOSED_SPAN_DAY = "2026-09-30"
OPEN_SPAN_DAY = "2026-10-01"
HISTORY = "docs/orchestration_log/history"
FIXED_NOW = datetime.fromisoformat("2026-10-01T10:30:42+02:00")


def fixture_schema() -> dict[str, Any]:
    """The project's schema with its prose removed, so the tree owes no pointer to the prose's paths."""
    real = yaml.safe_load((PROJECT_ROOT / SCHEMA_PATH).read_text(encoding="utf-8"))
    kinds = {name: {key: value for key, value in spec.items() if key not in PROSE_KEYS} for name, spec in real["kinds"].items()}
    for spec in kinds.values():
        if "aging" in spec:
            spec["aging"] = AGING_TEXT
    return {
        "load_levels": real["load_levels"],
        "kinds": kinds,
        "events": real["events"],
        "token_estimate": {"tokens_per_word": real["token_estimate"]["tokens_per_word"]},
        "products": [],
        "retired": [],
    }


def charter_text(spec: dict[str, Any]) -> str:
    """A charter with every section the schema names, one goal and one prime directive."""
    lines = ["# Charter", ""]
    for name in spec["sections"]:
        lines += [f"## {name}", ""]
        if name == "Goals":
            lines += ["### Goal 1 · pilot bar · finite · achieved", ""]
            lines += [f"- {field}: the {field} of the goal" for field in spec["goal_fields"]]
        elif name == "Prime directives":
            lines.append("- Measure before quoting. Ground: owner, 2026-08-10.")
        else:
            lines.append(f"The {name.lower()} section.")
        lines.append("")
    return "\n".join(lines)


def directive_text(spec: dict[str, Any]) -> str:
    """Conventions with every schema heading and one grounded rule under the first."""
    width = spec["label_width"]
    lines = ["# Conventions", ""]
    for number, heading in enumerate(spec["headings"]):
        lines += [f"## {heading}", ""]
        if number == 0:
            lines += [
                "### measure-first",
                "when".ljust(width) + "quoting a number",
                "do".ljust(width) + "measure it first",
                "ground".ljust(width) + "owner ruling 2026-08-04",
                "",
            ]
    return "\n".join(lines)


def change_log_text(spec: dict[str, Any]) -> str:
    """A change log holding one entry with every required field."""
    width = spec["label_width"]
    values = {"KIND": "computed", "FROM → TO": "one base → another base", "WHY": "the first was weak", "INVALIDATES": "nothing"}
    fields = [f"{name}:".ljust(width) + values[name] for name in spec["required_fields"]]
    return "\n".join(["# Architecture Log", "", "## 2026-04-09 — The frozen base changes", *fields, ""])


def results_text(spec: dict[str, Any]) -> str:
    """A results table with the schema's columns and one dated row."""
    columns = spec["columns"]
    row = ["contender"] * (len(columns) - 1) + ["2026-10-01"]
    return "\n".join(
        [
            "# Results",
            "",
            "| " + " | ".join(columns) + " |",
            "|" + "---|" * len(columns),
            "| " + " | ".join(row) + " |",
            "",
        ]
    )


def map_text(schema: dict[str, Any]) -> str:
    """The map: one row per kind, path and load as the schema gives them."""
    rows = [f"| {spec['home']} | {kind} | {spec['load']} | the {kind} record |" for kind, spec in schema["kinds"].items()]
    return "\n".join(["# memento map", "", "| path | kind | load | purpose |", "|---|---|---|---|", *rows, ""])


def journal_texts() -> dict[str, str]:
    """One closed span with a close block and a failure, and one open span after it."""
    closed, opened = f"{HISTORY}/{CLOSED_SPAN_DAY}", f"{HISTORY}/{OPEN_SPAN_DAY}"
    return {
        f"{closed}/events.md": "\n".join(
            [
                f"{CLOSED_SPAN_DAY}T09:00 | self | span-event | open; fixture span",
                f"{CLOSED_SPAN_DAY}T09:05 | owner | decision | the fixture is decided",
                f"{CLOSED_SPAN_DAY}T10:00 | self | failure | A fixture failure",
                f"{CLOSED_SPAN_DAY}T18:00 | self | span-event | close; HEAD abc1234 (clean); next: rest",
                "",
            ]
        ),
        f"{closed}/session.md": "\n".join(
            [
                f"# {CLOSED_SPAN_DAY}",
                "",
                "The span's narrative.",
                "",
                f"## Close — {CLOSED_SPAN_DAY}T18:00",
                "",
                "HEAD    abc1234 (clean)",
                "state   the fixture stands",
                "open    none",
                "next    rest",
                "",
            ]
        ),
        f"{closed}/failures.md": "\n".join(
            [
                f"# {CLOSED_SPAN_DAY} — failures",
                "",
                "## A fixture failure",
                "",
                "What happened: a line was written twice.",
                "",
                "Mechanism: nothing refused the second write.",
                "",
                "Correction: the writer refuses it.",
                "",
            ]
        ),
        f"{opened}/events.md": "\n".join(
            [
                f"{OPEN_SPAN_DAY}T09:00 | self | span-event | open; the next span",
                f"{OPEN_SPAN_DAY}T09:10 | agent:J-b | receipt | verdict delivered",
                "",
            ]
        ),
    }


def record_texts(schema: dict[str, Any]) -> dict[str, str]:
    """Every record of a valid tree, by path relative to its root."""
    kinds = schema["kinds"]
    shaped = {
        "charter": charter_text(kinds["charter"]),
        "directive": directive_text(kinds["directive"]),
        "change": change_log_text(kinds["change"]),
        "results": results_text(kinds["results"]),
        "map": map_text(schema),
        "schema": yaml.safe_dump(schema, sort_keys=False, allow_unicode=True),
        "fact": "# Capabilities\n\n## Scoring\n\nThe scorer slices its input.\n",
    }
    texts: dict[str, str] = {}
    for kind, spec in kinds.items():
        home = spec["home"]
        if "{" in home:
            continue
        if home.endswith("/"):
            texts[f"{home}placeholder.md"] = f"# {kind}\n"
        else:
            texts[home] = shaped.get(kind, f"# {kind}\n")
    return texts | journal_texts()


def git(root: Path, *args: str) -> str:
    """Run git in the tree and return its standard output."""
    identity = ["-c", "user.name=fixture", "-c", "user.email=fixture@example.com"]
    return subprocess.run(["git", "-C", str(root), *identity, *args], capture_output=True, text=True, check=True).stdout


def commit_all(root: Path) -> None:
    """Commit every file in the tree."""
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")


def build_record_tree(root: Path) -> Path:
    """Write a valid record tree at root and commit it in a new repository."""
    for path, text in record_texts(fixture_schema()).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root)
    return root


def replace_once(root: Path, path: str, old: str, new: str) -> None:
    """Replace the one occurrence of old in a tree file."""
    target = root / path
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {path}"
    target.write_text(text.replace(old, new), encoding="utf-8")


def run_memento(root: Path, *args: str, now: datetime = FIXED_NOW, checks: tuple[Check, ...] = CHECKS) -> Any:
    """Invoke the memento CLI on a tree with a fixed clock and the given checks."""
    environment = Environment(store=Store(root=root, replace_file=os.replace), now=lambda: now, checks=checks)
    return CliRunner().invoke(app, list(args), obj=environment)


@pytest.fixture
def record_tree(tmp_path: Path) -> Path:
    """A valid record tree committed in its own repository."""
    return build_record_tree(tmp_path / "tree")
