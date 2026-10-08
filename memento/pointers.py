"""Checks spanning records: cross-record pointers, grounds, budgets, hard wraps, history admission, NUL bytes."""

import fnmatch
import re
from collections.abc import Iterator
from pathlib import PurePosixPath

from memento.findings import DATE, SHA, Corpus, Finding, fail, normalized
from memento.journal import JOURNAL_FILE_PATTERNS, RATIFIED_ON

ORCHESTRATION = "docs/orchestration_log"
LIVING_KINDS = ["charter", "frame", "directive", "fact", "results", "map", "schema"]
POINTER_CHECKED_KINDS = ["charter", "frame", "directive", "fact", "results", "map", "schema", "string_recipes", "deferral"]
POINTER_SCANNED_CODE = ("matchtuner/", "scripts/", "tests/")
HISTORY_PREFIX = ["docs", "orchestration_log", "history"]
HISTORY_DATE_PART = len(HISTORY_PREFIX)
HISTORY_FILE_PART = HISTORY_DATE_PART + 1
TEMPLATE_MARKS = "{*<"
ORCHESTRATION_RELATIVE_PREFIXES = ("history/", "recon/")
PROSE_STRUCTURE_MARKS = ("#", "|", "```", "---", ">", "- ", "* ", "@")
PROSE_BREAKING_MARKS = ("#", "|", "```", "---", ">", "@")
FENCE = "```"
NO_WRAP_KINDS = ["charter", "frame", "fact", "results"]

PATH_TOKEN = re.compile(
    r"(?<![\w/.:^-])((?:\.claude|docs|scripts|matchtuner|tests|exemplars|history|recon)/[\w./{}*<>@:-]*[\w/])"
)
REVISION_POINTER = re.compile(r"\b([0-9a-f]{7,40}\^*):((?:\.claude|docs|scripts|matchtuner|tests|exemplars)/[\w./-]*\w)")
HEADING_REF = re.compile(
    r"(capabilities|ground-truth|conventions|results|string_comparison_recipes|architecture_log)(?:\.md)?`?"
    r"\s*§\s*([^;,()\n`]+)"
)
RULE_REF = re.compile(r"conventions(?:\.md)?`?\)?\s+rules?\s+((?:`[a-z0-9-]+`(?:,?\s+and\s+|,\s*)?)+)")
GROUND_PATH = re.compile(r"(?<![\w/.-])([\w*.-]+(?:/[\w*.{}-]*)+|[\w-]+\.(?:md|py|yaml|sh|json))(?![\w/])")
POINTER_SUFFIX = re.compile(r"(@[\w.-]+|:\d+(-\d+)?|#.*)$")


def resolve_pointer(token: str) -> str | None:
    """The project-relative file a path token in a record names, or None when it names a pattern or a template."""
    token = POINTER_SUFFIX.sub("", token).rstrip(".:")
    if any(mark in token for mark in TEMPLATE_MARKS):
        return None
    if token.startswith(ORCHESTRATION_RELATIVE_PREFIXES):
        return normalized(PurePosixPath(ORCHESTRATION, token))
    return normalized(token)


def path_is_committed(corpus: Corpus, token: str) -> bool:
    """Whether a ground's path token names a committed file, a directory, or a pattern matching a tracked path."""
    if "*" in token:
        return any(fnmatch.fnmatch(path, token) for path in corpus.tracked)
    target = resolve_pointer(token)
    if target is None or not corpus.exists(target):
        return False
    return corpus.is_dir(target) or target in corpus.tracked


def commit_exists(corpus: Corpus, sha: str) -> bool:
    """Whether a SHA names a commit git holds."""
    return corpus.holds_object(f"{sha}^{{commit}}")


def check_ground(corpus: Corpus, where: str, ground: str) -> Iterator[Finding]:
    """Fail a ground naming an uncommitted path, or naming no path, SHA, record heading or dated owner ruling."""
    paths = GROUND_PATH.findall(ground)
    for token in paths:
        if not path_is_committed(corpus, token):
            yield fail(where, f"ground names '{token}', which is not a committed path")
    headings = HEADING_REF.findall(ground)
    has_ruling = bool(re.search(DATE, ground)) and "owner" in ground
    has_commit = any(commit_exists(corpus, sha) for sha in SHA.findall(ground))
    if not (paths or headings or has_ruling or has_commit):
        yield fail(where, f"ground names no committed path, SHA, record heading or dated owner ruling: {ground.strip()!r}")


def record_headings(corpus: Corpus, path: str) -> list[str]:
    """Every heading of a record, lowercased, without its `#` marks or `§`."""
    return [line.lstrip("#").strip().lstrip("§").strip().lower() for line in corpus.text(path).splitlines() if line.startswith("#")]


def names_a_heading(wanted: str, headings: list[str]) -> bool:
    """Whether a `§` citation names a heading, the citation possibly running on into its sentence."""
    return any(wanted.startswith(heading) or heading.startswith(wanted) for heading in headings if heading)


def home_of(corpus: Corpus, kind: str) -> str:
    """The project-relative home the schema gives a kind."""
    return normalized(corpus.schema["kinds"][kind]["home"])


def check_pointers(corpus: Corpus) -> Iterator[Finding]:
    """Fail every cross-record pointer, rule citation and path whose target is absent, uncommitted or out of bounds."""
    record_files = {
        "capabilities": home_of(corpus, "fact"),
        "ground-truth": home_of(corpus, "frame"),
        "conventions": home_of(corpus, "directive"),
        "results": home_of(corpus, "results"),
        "string_comparison_recipes": home_of(corpus, "string_recipes"),
        "architecture_log": home_of(corpus, "change"),
    }
    rule_ids = set(re.findall(r"(?m)^### ([a-z0-9-]+)$", corpus.text(record_files["conventions"])))
    tracked_set = set(corpus.tracked)
    retired = {entry["path"] for entry in corpus.schema.get("retired", [])}
    living = {home_of(corpus, kind) for kind in LIVING_KINDS}
    checked = [home_of(corpus, kind) for kind in POINTER_CHECKED_KINDS]
    code = [path for path in corpus.tracked if path.endswith(".py") and path.startswith(POINTER_SCANNED_CODE)]
    for path in checked + code:
        text = corpus.text(path)
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{path}:{number}"
            for match in RULE_REF.finditer(line):
                for rule_id in re.findall(r"`([a-z0-9-]+)`", match.group(1)):
                    if rule_id not in rule_ids:
                        yield fail(where, f"cites conventions rule '{rule_id}', which does not exist")
            if path not in checked:
                continue
            for record, heading in HEADING_REF.findall(line):
                if record == "conventions":
                    yield fail(where, "cites a conventions heading; conventions are cited by rule id")
                    continue
                wanted = heading.strip().rstrip(".").lower()
                if not names_a_heading(wanted, record_headings(corpus, record_files[record])):
                    yield fail(where, f"'{record} § {heading.strip()}' names no heading in {record_files[record]}")
            for revision, revision_path in REVISION_POINTER.findall(line):
                if not corpus.holds_object(f"{revision}:{revision_path}"):
                    yield fail(where, f"points at '{revision}:{revision_path}', which git does not hold")
            for token in PATH_TOKEN.findall(line):
                target = resolve_pointer(token)
                if target is None:
                    continue
                if token.startswith("recon/") or "/recon/" in token:
                    if path in living:
                        yield fail(where, f"points into recon/, which is gitignored and carries no provenance: {token}")
                elif not corpus.exists(target):
                    yield fail(where, f"points at '{token}', which does not exist")
                elif corpus.is_file(target) and target not in tracked_set and target not in retired:
                    yield fail(where, f"points at '{token}', which is not committed")


def check_no_hard_wrap(corpus: Corpus) -> Iterator[Finding]:
    """Fail authored prose continuing the line above: a paragraph or list item is one line."""
    for kind in NO_WRAP_KINDS:
        path = home_of(corpus, kind)
        fenced, previous = False, ""
        for number, line in enumerate(corpus.text(path).splitlines(), 1):
            text = line.strip()
            if text.startswith(FENCE):
                fenced = not fenced
            elif not fenced and text and previous and not text.startswith(PROSE_STRUCTURE_MARKS) and not re.match(r"\d+\. ", text):
                if not previous.startswith(PROSE_BREAKING_MARKS):
                    yield fail(f"{path}:{number}", "prose continues a line above; authored prose carries no hard wrap")
            previous = "" if fenced and not text.startswith(FENCE) else text
            if text.startswith(FENCE) and not fenced:
                previous = FENCE


def check_budgets(corpus: Corpus) -> Iterator[Finding]:
    """Fail every record whose estimated tokens exceed its kind's budget."""
    per_word = corpus.schema["token_estimate"]["tokens_per_word"]
    for kind, spec in corpus.schema["kinds"].items():
        if "budget_tokens" not in spec:
            continue
        path = home_of(corpus, kind)
        estimate = round(len(corpus.text(path).split()) * per_word)
        if estimate > spec["budget_tokens"]:
            remedy = "; age the oldest entries as the schema's aging rule says" if "aging" in spec else ""
            yield fail(path, f"~{estimate} tokens, over the {kind} budget of {spec['budget_tokens']}{remedy}")


def check_history_admission(corpus: Corpus) -> Iterator[Finding]:
    """Fail any file history/ admitted after ratification other than a session, events or failures journal."""
    for path in corpus.tracked:
        parts = path.split("/")
        if len(parts) <= HISTORY_FILE_PART or parts[:HISTORY_DATE_PART] != HISTORY_PREFIX:
            continue
        if parts[HISTORY_DATE_PART] <= RATIFIED_ON:
            continue
        is_journal = any(fnmatch.fnmatch(parts[HISTORY_FILE_PART], pattern) for pattern in JOURNAL_FILE_PATTERNS)
        if len(parts) != HISTORY_FILE_PART + 1 or not is_journal:
            yield fail(path, "history/ admits only session*, events* and failures.md after 2026-09-23; other artifacts go to recon/")


def check_nul_bytes(corpus: Corpus) -> Iterator[Finding]:
    """Fail every tracked record text holding a NUL byte."""
    for path in corpus.tracked:
        is_record_text = path.endswith((".md", ".yaml")) and (path.startswith(("docs/", ".claude/")) or "/" not in path)
        if is_record_text and b"\x00" in corpus.raw(path):
            yield fail(path, "contains NUL bytes")
