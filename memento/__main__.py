"""The `memento` command: the one way into and out of the project's records."""

import os
from functools import partial
from pathlib import Path

import typer

from memento import Environment, changelog, clock, commit, journal, journal_checks, living, measure, pointers, read, schema
from memento.findings import FAIL, Check, run_checks
from memento.git import git_exit_status
from memento.schema import load_corpus
from memento.store import Store

FAILED_CHECK_EXIT_CODE = 1

KIND_GROUPS = {
    "events": journal.events_group,
    "failure": journal.failure_group,
    "digest": journal.digest_group,
    "change": changelog.change_group,
    "directive": living.directive_group,
    "results": living.results_group,
    "fact": living.fact_group,
    "string_recipes": living.string_recipes_group,
    "spec": living.spec_group,
    "map": living.map_group,
    "measurement": measure.measurement_group,
}

CHECKS: tuple[Check, ...] = (
    living.check_charter,
    living.check_map,
    changelog.check_change_log,
    journal_checks.check_event_lines,
    journal_checks.check_close_blocks,
    living.check_directives,
    changelog.check_change_log_pointers,
    pointers.check_no_hard_wrap,
    living.check_results_table,
    journal_checks.check_failures,
    journal_checks.check_spans_closed_in_digest,
    journal_checks.check_journal_history,
    pointers.check_pointers,
    pointers.check_budgets,
    pointers.check_history_admission,
    pointers.check_nul_bytes,
    partial(schema.check_structure, registered_groups=tuple(KIND_GROUPS)),
)

app = typer.Typer(name="memento", no_args_is_help=True, add_completion=False)
for group_name, group in KIND_GROUPS.items():
    app.add_typer(group, name=group_name)
app.command("commit", help=commit.COMMIT_HELP)(commit.commit)
app.command("patdown", help=read.PATDOWN_HELP)(read.patdown)


@app.command(
    help=(
        "Check every record under the root checkout against .claude/memento.yaml. Prints one 'LEVEL  where  message' "
        "line per finding and a 'records: <n> FAIL, <m> WARN' summary on stdout; exits 1 on any FAIL. Writes nothing."
    )
)
def check(ctx: typer.Context) -> None:
    """Report every record finding and exit 1 on any failing one."""
    environment: Environment = ctx.obj
    findings = run_checks(load_corpus(environment.store.root), environment.checks)
    for finding in findings:
        typer.echo(f"{finding.level}  {finding.where}  {finding.message}")
    failures = sum(finding.level == FAIL for finding in findings)
    typer.echo(f"records: {failures} FAIL, {len(findings) - failures} WARN")
    raise typer.Exit(FAILED_CHECK_EXIT_CODE if failures else 0)


def main() -> None:
    """Run the command against the checkout this package sits in, with the wall clock, atomic replacement, every check and git."""
    root = Path(__file__).resolve().parent.parent
    app(obj=Environment(store=Store(root=root, replace_file=os.replace), now=clock.now, checks=CHECKS, run_git=git_exit_status))


if __name__ == "__main__":
    main()
