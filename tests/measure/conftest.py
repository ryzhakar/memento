"""A record tree holding two matchtuner code states, a measured log in scratch, and builders for run records and entry specs."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.memento.conftest import commit_all, git, run_memento

MEASUREMENT_LOG = "docs/measurements.yaml"
SPEC_DIRECTORY = "docs/orchestration_log/recon/2026-10-01/memento-in"
OLD_COMMANDS = '''import typer

app = typer.Typer()


@app.callback()
def main() -> None:
    """Configure."""


@app.command()
def select() -> None:
    """Select."""


@app.command(name="build-store")
def build() -> None:
    """Build."""


@app.command("score-all")
def score() -> None:
    """Score."""
'''
NEW_COMMANDS = OLD_COMMANDS + '''

@app.command()
def run_audit() -> None:
    """Audit."""
'''
TIME_LOG = """start 09:00:00 head=old
selecting folds: 1/1 (53s elapsed, ~0s left)
       57.78 real       201.61 user        95.21 sys
         18791546880  maximum resident set size
             9438336  peak memory footprint
end 09:00:58
"""
TIME_RAW = """       57.78 real       201.61 user        95.21 sys
         18791546880  maximum resident set size
"""
REPORT_TEXT = """# A report

| Point | Peak RSS (GB) | Wall (s) |
|---|---|---|
| 1 | 17.27 GB | 75.60 s |
"""
REPORT_RAW = "| 1 | 17.27 GB | 75.60 s |\n"
WORLD = "sephora.de × flaconi.de"
INPUT_ADDRESS = "1ec1c72cc285e12cec252b24739064b1d87a5f016c506ea7625bbea119e8313f"


@dataclass(frozen=True)
class MeasuredTree:
    """A record tree with two committed code states and a scratch directory outside it."""

    root: Path
    old: str
    new: str
    scratch: Path


def sha256_of(path: Path) -> str:
    """The hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_yaml(path: Path, data: Any) -> Path:
    """Write data as block-form YAML at path, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def commit_matchtuner(root: Path, commands: str) -> str:
    """Commit a matchtuner CLI module holding the given commands and return the commit's sha."""
    (root / "matchtuner").mkdir(exist_ok=True)
    (root / "matchtuner/__main__.py").write_text(commands, encoding="utf-8")
    commit_all(root)
    return git(root, "rev-parse", "HEAD").strip()


@pytest.fixture
def measured(record_tree: Path, tmp_path: Path) -> MeasuredTree:
    """The record tree with an old and a new matchtuner commit, and a scratch directory holding a time log and a report."""
    old = commit_matchtuner(record_tree, OLD_COMMANDS)
    new = commit_matchtuner(record_tree, NEW_COMMANDS)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "select.log").write_text(TIME_LOG, encoding="utf-8")
    (scratch / "report.md").write_text(REPORT_TEXT, encoding="utf-8")
    return MeasuredTree(root=record_tree, old=old, new=new, scratch=scratch)


def code(sha: str) -> dict[str, Any]:
    """A committed code state."""
    return {"commit": {"sha": sha}}


def stated_machine() -> dict[str, Any]:
    """A machine stated by a report line."""
    return {"stated": {"text": "M2 Max 32 GB", "source": "docs/orchestration_log/recon/2026-10-01/report.md:3"}}


def stated_env() -> dict[str, Any]:
    """An environment stated by a report line."""
    return {"stated": {"versions": {"polars": "1.39.3"}, "source": "docs/orchestration_log/recon/2026-10-01/report.md:3"}}


def parsed_run(tree: MeasuredTree, started: str, ended: str, **fields: Any) -> dict[str, Any]:
    """A parsed run of select over the scratch time log, valid and solo as declared."""
    log = tree.scratch / "select.log"
    run = {
        "item": "run",
        "provenance": {"parsed": {"log": str(log), "log_sha256": sha256_of(log)}},
        "argv": ["/repo/.venv/bin/python", "-m", "matchtuner", "select"],
        "process": "fresh",
        "started": started,
        "ended": ended,
        "clock": {"stated": {"source": "select.log lines 1 and 6"}},
        "contention": {"declared_solo": {"how_checked": "pressure 1 and no matchtuner stage at launch"}},
        "pressure": {"declared": {"text": "1 at launch"}},
        "power": "unstated",
        "sleep": "unchecked",
        "cache": "unknown",
        "exit": {"code": 0},
        "validity": "valid",
        "readings": {
            "wall": {"ns": 57780000000, "resolution_ns": 10000000},
            "max_rss": {"bytes": 18791546880, "resolution_bytes": 1},
        },
        "raw": TIME_RAW,
        "evidence": {"path": str(log), "sha256": sha256_of(log), "durability": "scratchpad"},
    }
    return run | fields


def run_record(sha: str, item: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """A run record: the code, machine and environment one run was measured under, and the run."""
    return {"code": code(sha), "machine": stated_machine(), "env": stated_env(), "item": item} | fields


def transcribed_run(tree: MeasuredTree, started: str, ended: str, **fields: Any) -> dict[str, Any]:
    """A run transcribed from the scratch report's table row, its readings written as text with units."""
    run = parsed_run(tree, started, ended)
    report = tree.scratch / "report.md"
    run |= {
        "provenance": {"transcribed": {"source": str(report), "line": 5}},
        "readings": {"wall": "75.60 s", "max_rss": "17.27 GB"},
        "raw": REPORT_RAW,
        "evidence": {"path": str(report), "sha256": sha256_of(report), "durability": "scratchpad"},
    }
    return run | fields


def entry_spec(tree: MeasuredTree, items: list[Any], **fields: Any) -> dict[str, Any]:
    """An entry spec for select on fold seed 42 at the old code, over the given items."""
    spec = {
        "question": "select peak at the old code",
        "subject": {"stage": {"command": "select"}},
        "paths": ["matchtuner/"],
        "data": {
            "world": {"world": WORLD, "fold": {"seed": 42}, "inputs": [INPUT_ADDRESS], "scale": {"selection_nonmatches": 30714629}}
        },
        "design": {"fixed": {"code": code(tree.old)}},
        "items": items,
        "supersedes": [],
    }
    return spec | fields


def record_item(tree: MeasuredTree, name: str, record: dict[str, Any]) -> dict[str, str]:
    """An entry-spec item naming a run record written to the scratch directory."""
    return {"run_record": str(write_yaml(tree.scratch / f"{name}.yaml", record))}


def add_entry(tree: MeasuredTree, spec: dict[str, Any], name: str = "entry.yaml") -> Any:
    """Write an entry spec under the tree's memento-in directory and run `measurement add` on it."""
    path = write_yaml(tree.root / SPEC_DIRECTORY / name, spec)
    return run_memento(tree.root, "measurement", "add", str(path.relative_to(tree.root)))


def measurement_log(tree: MeasuredTree) -> str:
    """The measurement log's text."""
    return (tree.root / MEASUREMENT_LOG).read_text(encoding="utf-8")
