"""The `memento` command: the one way into and out of the project's records."""

import os
from pathlib import Path

import typer

from memento import Environment, changelog, clock, journal, living, pointers, schema
from memento.findings import FAIL, Check, Corpus, Finding
from memento.git import tracked_files
from memento.store import Store

FAILED_CHECK_EXIT_CODE = 1

CHECKS: tuple[Check, ...] = (
    living.check_charter,
    living.check_map,
    changelog.check_change_log,
    journal.check_traces,
    journal.check_close_blocks,
    living.check_directives,
    changelog.check_change_log_pointers,
    pointers.check_no_hard_wrap,
    living.check_results_table,
    journal.check_failures,
    journal.check_spans_closed_in_digest,
    pointers.check_pointers,
    pointers.check_budgets,
    pointers.check_history_admission,
    pointers.check_nul_bytes,
    schema.check_structure,
)

app = typer.Typer(name="memento", no_args_is_help=True, add_completion=False)
app.add_typer(journal.trace_group, name="trace")


def load_corpus(root: Path) -> Corpus:
    """The corpus every check reads: root's schema, its tracked paths, and its working tree."""
    return Corpus(root=root, schema=schema.load_schema(root), tracked=tracked_files(root))


def run_checks(corpus: Corpus) -> list[Finding]:
    """Every finding of every registered check, in registry order."""
    return [finding for check_records in CHECKS for finding in check_records(corpus)]


@app.command(
    help=(
        "Check every record under the root checkout against .claude/memento.yaml. Prints one 'LEVEL  where  message' "
        "line per finding and a 'records: <n> FAIL, <m> WARN' summary on stdout; exits 1 on any FAIL. Writes nothing."
    )
)
def check(ctx: typer.Context) -> None:
    """Report every record finding and exit 1 on any failing one."""
    environment: Environment = ctx.obj
    findings = run_checks(load_corpus(environment.store.root))
    for finding in findings:
        typer.echo(f"{finding.level}  {finding.where}  {finding.message}")
    failures = sum(finding.level == FAIL for finding in findings)
    typer.echo(f"records: {failures} FAIL, {len(findings) - failures} WARN")
    raise typer.Exit(FAILED_CHECK_EXIT_CODE if failures else 0)


def main() -> None:
    """Run the command against the checkout this package sits in, with the wall clock and atomic file replacement."""
    root = Path(__file__).resolve().parent.parent
    app(obj=Environment(store=Store(root=root, replace_file=os.replace), now=clock.now))


if __name__ == "__main__":
    main()
