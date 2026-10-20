"""The `memento` command: the one way into and out of the project's records."""

import os
from functools import partial
from pathlib import Path

import typer

from memento import REFUSED_EXIT_CODE, Environment, Refusal, baseline, changelog, clock, commit, journal, journal_checks, living, measure, pointers, read, refusals_reported, scaffold, schema, tree_help
from memento.findings import FAIL, Check, run_checks
from memento.git import git_exit_status
from memento.measure.check import check_measurement_log
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
    "baseline": baseline.baseline_group,
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
    journal_checks.check_span_file_names,
    journal_checks.check_journal_history,
    journal_checks.check_journal_corrections,
    pointers.check_pointers,
    pointers.check_budgets,
    pointers.check_history_admission,
    pointers.check_nul_bytes,
    check_measurement_log,
    baseline.check_baseline,
    partial(schema.check_structure, registered_groups=tuple(KIND_GROUPS)),
)

app = typer.Typer(name="memento", no_args_is_help=True, add_completion=False)
for group_name, group in KIND_GROUPS.items():
    app.add_typer(group, name=group_name)
app.command("commit", help=commit.COMMIT_HELP)(commit.commit)
app.command("patdown", help=read.PATDOWN_HELP)(read.patdown)
app.command("init", help=scaffold.INIT_HELP)(scaffold.init)
app.command("help", help=tree_help.HELP_HELP)(tree_help.help_command)


def refuse_unborn_repository(root: Path) -> None:
    """Refuse a repository whose HEAD names no commit: the journal checks read its history."""
    if git_exit_status(root, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        raise Refusal("HEAD names no commit; commit the records once, so the checks have a history to read")


@app.command(
    help=(
        "Check every record under the project's git checkout (the top level of the working directory's repository) "
        "against .claude/memento.yaml. Prints one 'LEVEL  where  message' line per finding and a 'records: <n> FAIL, "
        "<m> WARN' summary on stdout; exits 1 on any FAIL. Writes nothing. Refuses a repository whose HEAD names no commit."
    )
)
def check(ctx: typer.Context) -> None:
    """Report every record finding and exit 1 on any failing one."""
    environment: Environment = ctx.obj
    with refusals_reported():
        refuse_unborn_repository(environment.store.root)
        findings = run_checks(load_corpus(environment.store.root), environment.checks)
    for finding in findings:
        typer.echo(f"{finding.level}  {finding.where}  {finding.message}")
    failures = sum(finding.level == FAIL for finding in findings)
    typer.echo(f"records: {failures} FAIL, {len(findings) - failures} WARN")
    raise typer.Exit(FAILED_CHECK_EXIT_CODE if failures else 0)


def project_root() -> Path:
    """The top level of the git repository the command runs in, refusing a working directory outside any."""
    shown = git_exit_status(Path.cwd(), "rev-parse", "--show-toplevel")
    if shown.returncode != 0:
        typer.echo(f"refused: {Path.cwd()} is not inside a git repository", err=True)
        raise typer.Exit(REFUSED_EXIT_CODE)
    return Path(shown.stdout.decode("utf-8").strip()).resolve()


def git_identity(root: Path) -> str | None:
    """The git user name configured for the checkout, the owner's identity, or None when none is set."""
    named = git_exit_status(root, "config", "user.name")
    return named.stdout.decode("utf-8").strip() or None if named.returncode == 0 else None


def model_from_environment() -> str | None:
    """The model this session runs, as the MEMENTO_MODEL variable names it, or None when unset."""
    return os.environ.get("MEMENTO_MODEL", "").strip() or None


def main() -> None:
    """Run the command against the checkout the working directory sits in, with the wall clock, atomic replacement, every check and git."""
    root = project_root()
    app(obj=Environment(store=Store(root=root, replace_file=os.replace), now=clock.now, checks=CHECKS, run_git=git_exit_status, owner_identity=partial(git_identity, root), model=model_from_environment))


if __name__ == "__main__":
    main()
