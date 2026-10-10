"""Checks spanning records: cross-record pointers, grounds, budgets, hard wraps, history admission, NUL bytes."""

import fnmatch
import re
from collections.abc import Iterator
from pathlib import PurePosixPath

from memento.findings import DATE, SHA, Corpus, Finding, fail, normalized
from memento.journal_spans import JOURNAL_FILE_PATTERNS
from memento.project import (
    config_space,
    history_directory,
    journal_relative_prefixes,
    journal_space,
    pointer_roots,
    project_of,
    record_stem,
)
from memento.schema import Schema

LIVING_KINDS = ["charter", "frame", "directive", "fact", "results", "map", "schema"]
POINTER_CHECKED_KINDS = ["charter", "frame", "directive", "fact", "results", "map", "schema", "string_recipes", "deferral"]
CITED_RECORD_KINDS = ("fact", "frame", "directive", "results", "string_recipes", "change")
TEMPLATE_MARKS = "{*<"
PROSE_STRUCTURE_MARKS = ("#", "|", "```", "---", ">", "- ", "* ", "@")
PROSE_BREAKING_MARKS = ("#", "|", "```", "---", ">", "@")
FENCE = "```"
NO_WRAP_KINDS = ["charter", "frame", "fact", "results"]

RULE_LIST = r"((?:`[a-z0-9-]+`(?:,?\s+and\s+|,\s*)?)+)"
GROUND_PATH = re.compile(r"(?<![\w/.-])([\w*.-]+(?:/[\w*.{}-]*)+|[\w-]+\.(?:md|py|yaml|sh|json))(?![\w/])")
POINTER_SUFFIX = re.compile(r"(@[\w.-]+|:\d+(-\d+)?|#.*)$")


def root_alternation(roots: list[str]) -> str:
    """A regex alternation of directory roots, each without its trailing slash."""
    return "|".join(re.escape(root.rstrip("/")) for root in roots)


def path_token_pattern(schema: Schema) -> re.Pattern[str]:
    """The path tokens a record may carry: a pointer root or a journal-relative directory, then a path."""
    roots = root_alternation([*pointer_roots(schema), *journal_relative_prefixes(schema)])
    return re.compile(rf"(?<![\w/.:^-])((?:{roots})/[\w./{{}}*<>@:-]*[\w/])")


def revision_pointer_pattern(schema: Schema) -> re.Pattern[str]:
    """The `<revision>:<path>` pointers a record may carry."""
    return re.compile(rf"\b([0-9a-f]{{7,40}}\^*):((?:{root_alternation(pointer_roots(schema))})/[\w./-]*\w)")


def heading_ref_pattern(schema: Schema) -> re.Pattern[str]:
    """A `<record> § <heading>` citation, the record named by its home's file stem."""
    stems = "|".join(re.escape(record_stem(schema, kind)) for kind in CITED_RECORD_KINDS)
    return re.compile(rf"({stems})(?:\.md)?`?\s*§\s*([^;,()\n`]+)")


def rule_ref_pattern(schema: Schema) -> re.Pattern[str]:
    """A citation of the directive record's rules by id."""
    stem = re.escape(record_stem(schema, "directive"))
    return re.compile(rf"{stem}(?:\.md)?`?\)?\s+rules?\s+{RULE_LIST}")


def resolve_pointer(schema: Schema, token: str) -> str | None:
    """The project-relative file a path token in a record names, or None when it names a pattern or a template."""
    token = POINTER_SUFFIX.sub("", token).rstrip(".:")
    if any(mark in token for mark in TEMPLATE_MARKS):
        return None
    if token.startswith(journal_relative_prefixes(schema)):
        return normalized(PurePosixPath(journal_space(schema), token))
    return normalized(token)


def path_is_committed(corpus: Corpus, token: str) -> bool:
    """Whether a ground's path token names a committed file, a directory, or a pattern matching a tracked path."""
    if "*" in token:
        return any(fnmatch.fnmatch(path, token) for path in corpus.tracked)
    target = resolve_pointer(corpus.schema, token)
    if target is None or not corpus.exists(target):
        return False
    return corpus.is_dir(target) or target in corpus.tracked


def commit_exists(corpus: Corpus, sha: str) -> bool:
    """Whether a SHA names a commit git holds."""
    return corpus.holds_object(f"{sha}^{{commit}}")


def ground_findings(corpus: Corpus, where: str, ground: str) -> Iterator[Finding]:
    """Fail a ground naming an uncommitted path, or naming no path, SHA, record heading or dated owner ruling."""
    paths = GROUND_PATH.findall(ground)
    for token in paths:
        if not path_is_committed(corpus, token):
            yield fail(where, f"ground names '{token}', which is not a committed path")
    headings = heading_ref_pattern(corpus.schema).findall(ground)
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
    schema = corpus.schema
    record_files = {record_stem(schema, kind): home_of(corpus, kind) for kind in CITED_RECORD_KINDS}
    directive_stem = record_stem(schema, "directive")
    heading_ref, rule_ref = heading_ref_pattern(schema), rule_ref_pattern(schema)
    path_token, revision_pointer = path_token_pattern(schema), revision_pointer_pattern(schema)
    rule_ids = set(re.findall(r"(?m)^### ([a-z0-9-]+)$", corpus.text(record_files[directive_stem])))
    tracked_set = set(corpus.tracked)
    retired = {entry["path"] for entry in corpus.schema.get("retired", [])}
    living = {home_of(corpus, kind) for kind in LIVING_KINDS}
    bench_prefix = journal_relative_prefixes(schema)[1]
    checked = [home_of(corpus, kind) for kind in POINTER_CHECKED_KINDS]
    code = [path for path in corpus.tracked if path.endswith(".py") and path.startswith(tuple(project_of(schema).code_roots))]
    for path in checked + code:
        text = corpus.text(path)
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{path}:{number}"
            for match in rule_ref.finditer(line):
                for rule_id in re.findall(r"`([a-z0-9-]+)`", match.group(1)):
                    if rule_id not in rule_ids:
                        yield fail(where, f"cites {directive_stem} rule '{rule_id}', which does not exist")
            if path not in checked:
                continue
            for record, heading in heading_ref.findall(line):
                if record == directive_stem:
                    yield fail(where, f"cites a {directive_stem} heading; {directive_stem} are cited by rule id")
                    continue
                wanted = heading.strip().rstrip(".").lower()
                if not names_a_heading(wanted, record_headings(corpus, record_files[record])):
                    yield fail(where, f"'{record} § {heading.strip()}' names no heading in {record_files[record]}")
            for revision, revision_path in revision_pointer.findall(line):
                if not corpus.holds_object(f"{revision}:{revision_path}"):
                    yield fail(where, f"points at '{revision}:{revision_path}', which git does not hold")
            for token in path_token.findall(line):
                target = resolve_pointer(schema, token)
                if target is None:
                    continue
                if token.startswith(bench_prefix) or f"/{bench_prefix}" in token:
                    if path in living:
                        yield fail(where, f"points into {bench_prefix}, which is gitignored and carries no provenance: {token}")
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
    history = history_directory(corpus.schema).split("/")
    date_part, file_part = len(history), len(history) + 1
    history_name, bench_name = journal_relative_prefixes(corpus.schema)
    ratified_on = project_of(corpus.schema).ratified_on
    for path in corpus.tracked:
        parts = path.split("/")
        if len(parts) <= file_part or parts[:date_part] != history:
            continue
        if parts[date_part] <= ratified_on:
            continue
        is_journal = any(fnmatch.fnmatch(parts[file_part], pattern) for pattern in JOURNAL_FILE_PATTERNS)
        if len(parts) != file_part + 1 or not is_journal:
            yield fail(path, f"{history_name} admits only session*, events* and failures.md after {ratified_on}; other artifacts go to {bench_name}")


def check_nul_bytes(corpus: Corpus) -> Iterator[Finding]:
    """Fail every tracked record text holding a NUL byte."""
    record_roots = (project_of(corpus.schema).record_space, config_space(corpus.schema))
    for path in corpus.tracked:
        is_record_text = path.endswith((".md", ".yaml")) and (path.startswith(record_roots) or "/" not in path)
        if is_record_text and b"\x00" in corpus.raw(path):
            yield fail(path, "contains NUL bytes")
