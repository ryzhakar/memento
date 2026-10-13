"""A group's help names the verbs it has and no verb it lacks."""

import re

import click
import pytest
import typer.main

from memento.__main__ import app

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


def test_the_map_group_has_no_verb_and_its_help_names_none() -> None:
    assert group_verbs()["map"] == frozenset()
    assert verbs_named(group_help("map")) == frozenset()
