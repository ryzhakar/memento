"""`memento help`: every verb of every group, found by walking the command tree."""

from collections.abc import Iterator
from typing import Annotated

import click
import typer

HELP_HELP = (
    "Print every verb of the tool, one per line with its first sentence (--compact, the default), or each verb with its "
    "full help text (--full). Reads nothing from the project and writes nothing. Refuses: both --compact and --full."
)


def verbs(group: click.Group, prefix: str = "") -> Iterator[tuple[str, click.Command]]:
    """Each leaf command beneath a group, with its space-joined path."""
    for name, command in sorted(group.commands.items()):
        path = f"{prefix}{name}"
        if isinstance(command, click.Group):
            yield from verbs(command, f"{path} ")
        else:
            yield path, command


def first_sentence(text: str) -> str:
    """The text up to its first full stop followed by a space or the end, on one line."""
    flat = " ".join((text or "").split())
    end = flat.find(". ")
    return flat if end == -1 else flat[: end + 1]


def help_command(
    ctx: typer.Context,
    full: Annotated[bool, typer.Option("--full", help="Print each verb's full help text.")] = False,
    compact: Annotated[bool, typer.Option("--compact", help="Print one line per verb (the default).")] = False,
) -> None:
    """Print the whole tool tree."""
    if full and compact:
        typer.echo("refused: --compact and --full exclude each other", err=True)
        raise typer.Exit(1)
    root = ctx.find_root().command
    assert isinstance(root, click.Group)
    for path, command in verbs(root):
        if full:
            typer.echo(f"memento {path}\n{command.help or ''}\n")
        else:
            typer.echo(f"{path}: {first_sentence(command.help or '')}")
