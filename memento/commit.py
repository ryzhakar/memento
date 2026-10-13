"""Committing records: named record paths, after a green check, in the root checkout, under one typed subject line."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import AfterValidator, BaseModel, ConfigDict

from memento import Environment, Refusal, announce, construct, quiet_option, refusals_reported
from memento.findings import FAIL, normalized, run_checks
from memento.git import git_exit_status, git_output
from memento.journal_shapes import visible_single_line
from memento.project import Project, project_of
from memento.schema import Schema, agreeing_schema, kind_for, load_corpus
from memento.store import record_lock, refuse_worktree

KINDS_NO_VERB_WRITES = ("label_audit", "label_defects", "report")
OUTSIDE_MEMENTO_KIND = "scratch"
COMMIT_HELP = (
    "Commit record paths: run every record check, then 'git add' and 'git commit' the named paths alone in the root "
    "checkout, with the one-line message '<commit_type>: <subject>' and no body, the type and the subject's word cap "
    "read from the schema's project block. Reads .claude/memento.yaml, every record, and git; prints '<short sha> "
    "<commit_type>: <subject>' and one line per path. Refuses: a path no record kind covers, a scratch "
    "path or a product path; a record of a kind no memento verb writes (label_audit, label_defects, report); a path "
    "with no change; a subject over the cap or of more than one line; any FAIL finding; a worktree; a commit git "
    "rejects, after which the named paths are unstaged."
)


class CommitMessage(BaseModel):
    """A record commit's one-line message: the project's commit type and a single-line subject."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    subject: Annotated[str, AfterValidator(visible_single_line("subject"))]

    def render(self, project: Project) -> str:
        """The message as git records it."""
        return f"{project.commit_type}: {self.subject}"


def refuse_long_subject(subject: str, project: Project) -> None:
    """Refuse a subject over the project's word cap."""
    words = len(subject.split())
    if words > project.commit_subject_words:
        raise Refusal(f"subject runs {words} words, over {project.commit_subject_words}")


def refuse_unwritten_path(schema: Schema, path: str) -> None:
    """Refuse a path no memento verb writes: no record at all, scratch, or a kind closed to every verb."""
    kind = kind_for(schema, path)
    if kind is None or kind == OUTSIDE_MEMENTO_KIND:
        raise Refusal(f"{path} is not a record; commit it with its code")
    if kind in KINDS_NO_VERB_WRITES:
        raise Refusal(f"{path} is a {kind} record, which no memento verb writes")


def refuse_unchanged_path(root: Path, path: str) -> None:
    """Refuse a path git reports no change at."""
    if not git_output(root, "status", "--porcelain", "--", path):
        raise Refusal(f"{path} has no change to commit")


def refuse_failing_records(environment: Environment) -> None:
    """Refuse while any record check fails."""
    findings = run_checks(load_corpus(environment.store.root), environment.checks)
    failures = sum(finding.level == FAIL for finding in findings)
    if failures:
        raise Refusal(f"the records hold {failures} {FAIL}; 'memento check' lists them")


def commit_records(environment: Environment, subject: str, paths: list[str]) -> tuple[str, str, list[str]]:
    """Commit the named record paths alone under '<commit_type>: <subject>' once every check passes, returning the commit's short sha, its message and the paths."""
    root = environment.store.root
    refuse_worktree(root)
    message = construct(CommitMessage, subject=subject)
    schema = agreeing_schema(root)
    project = project_of(schema)
    refuse_long_subject(subject, project)
    records = [normalized(path) for path in paths]
    with record_lock(root):
        for path in records:
            refuse_unwritten_path(schema, path)
            refuse_unchanged_path(root, path)
        refuse_failing_records(environment)
        git_output(root, "add", "--", *records)
        committed = git_exit_status(root, "commit", "-q", "-m", message.render(project), "--", *records)
        if committed.returncode != 0:
            git_output(root, "reset", "-q", "--", *records)
            raise Refusal(f"git did not commit, so the records are unstaged: {committed.stderr.decode(errors='replace').strip()}")
    return git_output(root, "rev-parse", "--short", "HEAD").strip(), message.render(project), records


def commit(
    ctx: typer.Context,
    subject: str = typer.Argument(..., help="The commit subject after the commit type, within the project's word cap."),
    paths: list[str] = typer.Argument(..., help="The record paths to commit, relative to the project root."),
    quiet: bool = quiet_option(),
) -> None:
    """Commit the named record paths after a green check."""
    with refusals_reported():
        sha, message, records = commit_records(ctx.obj, subject, paths)
    announce("\n".join([f"{sha} {message}", *records]), quiet)
