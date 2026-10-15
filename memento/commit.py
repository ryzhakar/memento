"""Committing records: named record paths, after a green check, in the root checkout, under one typed subject line."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import AfterValidator, BaseModel, ConfigDict

from memento import Environment, Refusal, announce, construct, quiet_option, refusals_reported
from memento.findings import FAIL, normalized, run_checks
from memento.git import git_exit_status, git_output
from memento.journal_shapes import visible_single_line
from memento.journal_spans import Span, named_span, span_files, span_or_file
from memento.project import Project, project_of
from memento.schema import Schema, agreeing_schema, kind_for, load_corpus
from memento.store import record_lock, refuse_worktree

KINDS_NO_VERB_WRITES = ("label_audit", "label_defects", "report")
OUTSIDE_MEMENTO_KIND = "scratch"
COMMIT_HELP = (
    "Commit record paths: run every record check, then 'git add' and 'git commit' the named paths alone in the root "
    "checkout, or, with --span naming a suffixed span, in a linked worktree, with the one-line message "
    "'<commit_type>: <subject>' and no body, the type and the subject's word cap read from the schema's project "
    "block. Reads .claude/memento.yaml, every record, and git; prints '<short sha> <commit_type>: <subject>' and one "
    "line per path. Refuses: a path no record kind covers, a scratch path or a product path; a record of a kind no "
    "memento verb writes (label_audit, label_defects, report); a path with no change; a subject over the cap or of "
    "more than one line; any FAIL finding; a worktree without --span naming a suffixed span; with --span, a path "
    "that is not that span's events, session or failures file; with --fold, a HEAD on a remote branch or one that "
    "changed a path the fold does not name; a commit git rejects, after which the named paths are unstaged."
)
COMMIT_FOLD_HELP = (
    "Amend the previous commit with these paths instead of adding one, under the given subject. Refuses while HEAD is on "
    "a remote branch, or when HEAD changed a path the fold does not name."
)
COMMIT_SPAN_HELP = (
    "Commit only this span's own events, session and failures files: '<date>' or '<date>-<suffix>'. A suffixed span "
    "may commit from a linked worktree, in that worktree. Omitted, the span a .memento-span file names; unlike the journal verbs, commit does not read MEMENTO_SPAN."
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


def refuse_worktree_unless_suffixed(root: Path, span: Span | None) -> None:
    """Refuse a linked worktree unless the commit names a suffixed span."""
    if span is None or span.suffix is None:
        refuse_worktree(root)


def refuse_paths_outside_span(schema: Schema, span: Span | None, records: list[str]) -> None:
    """Refuse a path that is no events, session or failures file of the named span; any path passes when no span is named."""
    if span is None:
        return
    own_files = span_files(schema, span)
    for path in records:
        if path not in own_files:
            raise Refusal(f"{path} is outside span {span.name}'s own files: {', '.join(sorted(own_files))}")


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


def refuse_unfoldable_head(root: Path, records: list[str]) -> None:
    """Refuse a fold unless HEAD is on no remote branch and changed only the named paths."""
    if git_output(root, "branch", "-r", "--contains", "HEAD").strip():
        raise Refusal("HEAD is on a remote branch; a fold rewrites only a commit not yet pushed")
    head_paths = set(git_output(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split())
    outside = sorted(head_paths - set(records))
    if outside:
        raise Refusal(f"HEAD also changed {', '.join(outside)}, which the fold does not name; commit these paths on their own")


def commit_records(environment: Environment, subject: str, paths: list[str], span: Span | None, fold: bool = False) -> tuple[str, str, list[str]]:
    """Commit the named record paths alone under '<commit_type>: <subject>' once every check passes, returning the commit's short sha, its message and the paths; a named span confines the paths to its own journal files."""
    root = environment.store.root
    refuse_worktree_unless_suffixed(root, span)
    message = construct(CommitMessage, subject=subject)
    schema = agreeing_schema(root)
    project = project_of(schema)
    refuse_long_subject(subject, project)
    records = [normalized(path) for path in paths]
    refuse_paths_outside_span(schema, span, records)
    with record_lock(root):
        for path in records:
            refuse_unwritten_path(schema, path)
            refuse_unchanged_path(root, path)
        refuse_failing_records(environment)
        if fold:
            refuse_unfoldable_head(root, records)
        git_output(root, "add", "--", *records)
        amend = ["--amend"] if fold else []
        committed = git_exit_status(root, "commit", "-q", *amend, "-m", message.render(project), "--", *records)
        if committed.returncode != 0:
            git_output(root, "reset", "-q", "--", *records)
            raise Refusal(f"git did not commit, so the records are unstaged: {committed.stderr.decode(errors='replace').strip()}")
    return git_output(root, "rev-parse", "--short", "HEAD").strip(), message.render(project), records


def commit(
    ctx: typer.Context,
    subject: str = typer.Argument(..., help="The commit subject after the commit type, within the project's word cap."),
    paths: list[str] = typer.Argument(..., help="The record paths to commit, relative to the project root."),
    span: str | None = typer.Option(None, "--span", callback=span_or_file, help=COMMIT_SPAN_HELP),
    fold: bool = typer.Option(False, "--fold", help=COMMIT_FOLD_HELP),
    quiet: bool = quiet_option(),
) -> None:
    """Commit the named record paths after a green check."""
    with refusals_reported():
        sha, message, records = commit_records(ctx.obj, subject, paths, named_span(span), fold)
    announce("\n".join([f"{sha} {message}", *records]), quiet)
