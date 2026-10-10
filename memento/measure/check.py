"""The measurement log's check: every document parses, stands in its canonical form, follows the ones before it, and names code that exists."""

from collections.abc import Iterator, Sequence

from memento import Refusal
from memento.findings import Corpus, Finding, fail
from memento.measure.ids import split_target
from memento.measure.stages import CodeHistory
from memento.measure.types import (
    DOCUMENT,
    DOCUMENT_START,
    Measurement,
    Stage,
    Void,
    canonical_text,
    code_sha,
    design_codes,
    loaded_yaml,
    validated,
)
from memento.pointers import home_of

MEASUREMENT_KIND = "measurement"
DOCUMENT_MARK = DOCUMENT_START.rstrip("\n")
NOT_CANONICAL = "is not the block form memento writes; the log is written by 'just memento measurement add' and 'void' only"


def split_documents(text: str) -> list[tuple[int, str]]:
    """Each document of the log with the line it starts on, text before the first document start counting as one."""
    chunks: list[tuple[int, list[str]]] = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        if line.rstrip("\n") == DOCUMENT_MARK or not chunks:
            chunks.append((number, []))
        chunks[-1][1].append(line)
    return [(number, "".join(lines)) for number, lines in chunks]


def parsed_document(text: str) -> Measurement | Void:
    """The document a log chunk holds, refused when it does not parse as one."""
    return validated(DOCUMENT, loaded_yaml(text))


def logged_documents(text: str) -> list[Measurement | Void]:
    """Every document of a log, refusing at the first that does not parse."""
    documents = []
    for number, chunk in split_documents(text):
        try:
            documents.append(parsed_document(chunk))
        except Refusal as refusal:
            raise Refusal(f"the measurement log's document at line {number} does not parse ({refusal}); 'just memento check' lists it") from refusal
    return documents


def identity_problems(document: Measurement | Void, earlier: Sequence[Measurement | Void]) -> list[str]:
    """A repeated id, or an id not after the last earlier document's."""
    ids = [logged.id for logged in earlier]
    if document.id in ids:
        return [f"id {document.id} repeats an earlier document's id"]
    if ids and document.id < max(ids):
        return [f"id {document.id} is not after the earlier id {max(ids)}"]
    return []


def earlier_measurements(earlier: Sequence[Measurement | Void]) -> dict[str, Measurement]:
    """The earlier measurements by id."""
    return {logged.id: logged for logged in earlier if isinstance(logged, Measurement)}


def target_problems(target: str, earlier: Sequence[Measurement | Void]) -> list[str]:
    """A void target that is no earlier measurement, or names a run position that measurement lacks."""
    entry, position = split_target(target)
    measurements = earlier_measurements(earlier)
    if entry not in measurements:
        return [f"void target {target} is not a measurement earlier in the log"]
    if position is not None and position > len(measurements[entry].items):
        return [f"void target {target} names item {position}, but {entry} holds {len(measurements[entry].items)} items"]
    return []


def code_problems(entry: Measurement, history: CodeHistory) -> list[str]:
    """Each code sha that is no commit, each path a sha lacks, and each sha at which the stage is no matchtuner command."""
    problems = []
    for sha in dict.fromkeys(code_sha(code) for code in design_codes(entry.design)):
        if not history.is_commit(sha):
            problems.append(f"'{sha}' is not a commit in this repository")
            continue
        problems += [f"path '{path}' does not exist at {sha}" for path in entry.paths if not history.holds_path(sha, path)]
        if isinstance(entry.subject, Stage):
            problems += stage_problems(entry.subject.stage.command, sha, history)
    return problems


def stage_problems(command: str, sha: str, history: CodeHistory) -> list[str]:
    """A stage the matchtuner CLI at sha does not declare, or a sha whose CLI declares no command at all."""
    commands = history.stage_commands(sha)
    if not commands:
        return [f"matchtuner/__main__.py at {sha} declares no command, so no stage can be checked there"]
    if command not in commands:
        return [f"stage '{command}' is not a matchtuner command at {sha} ({', '.join(sorted(commands))})"]
    return []


def document_problems(document: Measurement | Void, earlier: Sequence[Measurement | Void], history: CodeHistory) -> list[str]:
    """Everything wrong with a document against the documents before it and the git history beneath it."""
    problems = identity_problems(document, earlier)
    if isinstance(document, Void):
        problems += target_problems(document.target, earlier)
        if ":" not in document.evidence and not history.is_commit(document.evidence):
            problems.append(f"'{document.evidence}' is not a commit in this repository")
        return problems
    measurements = earlier_measurements(earlier)
    problems += [f"supersedes {superseded}, which is not a measurement earlier in the log" for superseded in document.supersedes if superseded not in measurements]
    return problems + code_problems(document, history)


def check_measurement_log(corpus: Corpus) -> Iterator[Finding]:
    """Fail a log document that does not parse, is not canonical, breaks id order or targets, or names code git lacks."""
    path = home_of(corpus, MEASUREMENT_KIND)
    history = CodeHistory(corpus.root)
    earlier: list[Measurement | Void] = []
    for number, chunk in split_documents(corpus.text(path)):
        where = f"{path}:{number}"
        try:
            document = parsed_document(chunk)
        except Refusal as refusal:
            yield fail(where, f"does not parse as a measurement or a void: {refusal}")
            continue
        if canonical_text(document) != chunk:
            yield fail(where, NOT_CANONICAL)
        for problem in document_problems(document, earlier, history):
            yield fail(where, problem)
        earlier.append(document)
