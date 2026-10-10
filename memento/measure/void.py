"""`measurement void`: a later finding that an earlier entry, or one of its runs, does not stand."""

from pathlib import Path

import typer
from pydantic import TypeAdapter

from memento import Environment, Refusal, refusals_reported
from memento.actor import Self_
from memento.candidate import refuse_new_failures
from memento.measure.add import resolved
from memento.measure.check import MEASUREMENT_KIND, document_problems, logged_documents
from memento.measure.ids import next_id
from memento.measure.stages import CodeHistory
from memento.measure.types import Void, canonical_text, validated
from memento.schema import agreeing_schema

VOID_KIND = "void"
LINE_MARK = ":"
VOID = TypeAdapter(Void)


def refuse_missing_evidence_line(root: Path, evidence: str) -> None:
    """Refuse `path:line` evidence whose file is absent or shorter than the line."""
    if LINE_MARK not in evidence:
        return
    path, _, line = evidence.rpartition(LINE_MARK)
    file = resolved(root, path)
    if not file.is_file():
        raise Refusal(f"evidence {path} is not a file")
    lines = len(file.read_text(encoding="utf-8").splitlines())
    if int(line) > lines:
        raise Refusal(f"evidence {evidence} points past the end of {path}, which holds {lines} lines")


def record_void(environment: Environment, target: str, reason: str, evidence: str) -> tuple[str, str]:
    """Append one void naming its target, reason and evidence to the measurement log, returning the log's path and the void's id."""
    root = environment.store.root
    with environment.store.transaction() as act:
        log_path = agreeing_schema(root)["kinds"][MEASUREMENT_KIND]["home"]
        log_text = (root / log_path).read_text(encoding="utf-8")
        earlier = logged_documents(log_text)
        recorded = environment.now().replace(microsecond=0)
        fields = {"target": target, "reason": reason, "evidence": evidence, "by": Self_().render()}
        identity = {"kind": VOID_KIND, "id": next_id((document.id for document in earlier), recorded), "recorded": recorded.isoformat()}
        void = validated(VOID, identity | fields)
        problems = document_problems(void, earlier, CodeHistory(root))
        if problems:
            raise Refusal("; ".join(problems))
        refuse_missing_evidence_line(root, void.evidence)
        document = canonical_text(void)
        refuse_new_failures(environment, {log_path: log_text + document})
        act.append(log_path, document)
    return log_path, void.id


def void_command(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="m-YYYYMMDD-NNN for a whole entry, or m-YYYYMMDD-NNN#k for its k-th item."),
    reason: str = typer.Option(..., "--reason", help="Why the target does not stand, one line."),
    evidence: str = typer.Option(..., "--evidence", help="path:line of the finding, or the 40-hex sha of the commit holding it."),
) -> None:
    """Append one void of an earlier entry or run to the measurement log."""
    with refusals_reported():
        log_path, void_id = record_void(ctx.obj, target, reason, evidence)
    typer.echo(f"{log_path}: {void_id}")
