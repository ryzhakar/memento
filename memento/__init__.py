"""The project's records: every read and write of a record goes through this package."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import typer
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from memento.findings import Check
    from memento.store import Store

REFUSED_EXIT_CODE = 1


class Refusal(Exception):
    """A verb declining its input or the world's state before anything lands."""


@dataclass(frozen=True)
class Environment:
    """What a command receives from the composition root: the record store, the clock and the record checks."""

    store: Store
    now: Callable[[], datetime]
    checks: tuple[Check, ...]


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
