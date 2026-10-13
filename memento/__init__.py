"""The project's records: every read and write of a record goes through this package."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import typer
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from memento.findings import Check
    from memento.git import GitRunner
    from memento.store import Store

REFUSED_EXIT_CODE = 1


class Refusal(Exception):
    """A verb declining its input or the world's state before anything lands."""


@dataclass(frozen=True)
class Environment:
    """What a command receives from the composition root: the record store, the clock, the record checks and the git runner."""

    store: Store
    now: Callable[[], datetime]
    checks: tuple[Check, ...]
    run_git: GitRunner


@contextmanager
def refusals_reported() -> Iterator[None]:
    """Turn a refusal into its message on stderr and exit status 1."""
    try:
        yield
    except Refusal as refusal:
        typer.echo(f"refused: {refusal}", err=True)
        raise typer.Exit(REFUSED_EXIT_CODE) from refusal


def construct[Model: BaseModel](model: type[Model], **fields: object) -> Model:
    """A model built from its fields, its first validation failure raised as a refusal."""
    try:
        return model(**fields)
    except ValidationError as invalid:
        first = invalid.errors()[0]
        raise Refusal(str(first.get("ctx", {}).get("error", first["msg"]))) from invalid


def known_name(owner: str, noun: str, name: str, known: Sequence[str]) -> str:
    """The name, refused when the record's model does not have it, the refusal naming the ones it has."""
    if name not in known:
        raise Refusal(f"'{name}' is not a {owner} {noun}; the {noun}s are: {', '.join(known)}")
    return name
