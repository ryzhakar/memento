"""A group's help names the verbs it has and no verb it lacks."""

import re
from pathlib import Path

import click
import pytest
import typer.main

from memento.__main__ import app
from tests.conftest import run_memento

UNBUILT_VERBS = frozenset({"render", "run", "log", "find", "compare", "quote"})
LIST_MARK = ": "


def group_verbs() -> dict[str, frozenset[str]]:
    """The verbs each registered group holds, by group name."""
    commands = typer.main.get_command(app).commands
    return {name: frozenset(command.commands) for name, command in commands.items() if isinstance(command, click.Group)}


def group_help(group: str) -> str:
    """The help text a group registers."""
    return typer.main.get_command(app).commands[group].help


def verbs_named(help_text: str) -> frozenset[str]:
    """The verb names, built or unbuilt, among the words a help text lists after its group name."""
    every_verb = UNBUILT_VERBS.union(*group_verbs().values())
    return frozenset(re.findall(r"[a-z]+", help_text.partition(LIST_MARK)[2])) & every_verb


@pytest.mark.parametrize("group", sorted(group_verbs()))
def test_a_groups_help_names_only_verbs_it_has(group: str) -> None:
    assert LIST_MARK in group_help(group)
    assert verbs_named(group_help(group)) <= group_verbs()[group]


def test_the_measurement_group_help_names_add_and_void() -> None:
    assert verbs_named(group_help("measurement")) == {"add", "void"}


def test_the_map_group_has_one_verb_and_its_help_names_it() -> None:
    assert group_verbs()["map"] == frozenset({"add"})
    assert verbs_named(group_help("map")) == frozenset({"add"})


@pytest.mark.parametrize(("group", "phrase"), [("results", "(naming the columns)"), ("directive", "(naming the labels)"), ("change", "(naming the fields)")])
def test_each_set_verbs_help_names_the_refusal_that_lists_the_known_names(record_tree: Path, group: str, phrase: str) -> None:
    result = run_memento(record_tree, group, "set", "--help")
    assert (result.exit_code, phrase in " ".join(result.stdout.split())) == (0, True)


def test_help_lists_every_verb_of_every_group_compactly(record_tree: Path) -> None:
    result = run_memento(record_tree, "help")
    assert result.exit_code == 0
    *verb_lines, note = result.stdout.splitlines()
    assert "memento help --full" in note
    listed = {line.split(":", 1)[0] for line in verb_lines}
    root = typer.main.get_command(app)
    expected = {f"{group} {verb}" for group, verbs in group_verbs().items() for verb in verbs} | {name for name, command in root.commands.items() if not isinstance(command, click.Group)}
    assert listed == expected


def test_help_full_prints_each_verbs_whole_text_and_refuses_both_modes(record_tree: Path) -> None:
    full = run_memento(record_tree, "help", "--full")
    assert full.exit_code == 0
    assert "memento baseline set" in full.stdout
    assert "Refuses: a schema declaring no baseline kind" in full.stdout
    both = run_memento(record_tree, "help", "--full", "--compact")
    assert (both.exit_code, both.stdout) == (1, "")
    assert "exclude each other" in both.stderr
