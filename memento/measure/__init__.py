"""The measurement log: every performance reading of the pipeline, with its context."""

import typer

measurement_group = typer.Typer(
    no_args_is_help=True, help="The measurement log: run, log, add, void, find, compare, quote."
)
