"""The change log: entries appended oldest first, and the fact record's pointers into it."""

import re
from collections.abc import Iterator

from memento.findings import DATE, Corpus, Finding, fail, split_entries
from memento.pointers import home_of

ENTRY_HEADER = re.compile(rf"^## (?P<start>{DATE})(?: → {DATE})? — \S")
FIELD_LABEL = re.compile(r"^(?P<label>[A-Z][A-Z →-]*[A-Z]):(?P<pad> *)")
NEVER_COMPRESSED_FIELD = "INVALIDATES"
KIND_FIELD = "KIND"
CHANGE_LOG_MENTION = "architecture_log.md"
CHANGE_LOG_POINTER = re.compile(rf"decided ({DATE}), see docs/architecture_log\.md")
ENTRY_DATE = re.compile(rf"(?m)^## ({DATE})")


def check_change_log(corpus: Corpus) -> Iterator[Finding]:
    """Fail change-log entries whose header, fields, order, padding or word count break the schema's change shape."""
    spec = corpus.schema["kinds"]["change"]
    path = home_of(corpus, "change")
    width, fields = spec["label_width"], spec["fields"]
    previous_start = ""
    for line_number, header, body in split_entries(corpus.text(path)):
        where = f"{path}:{line_number}"
        shape = ENTRY_HEADER.match(header)
        if not shape:
            yield fail(where, "header is not '## YYYY-MM-DD — Title' or '## YYYY-MM-DD → YYYY-MM-DD — Title'")
            continue
        if shape["start"] < previous_start:
            yield fail(where, f"dated {shape['start']} after an entry dated {previous_start}; entries run oldest first")
        previous_start = max(previous_start, shape["start"])
        seen: list[str] = []
        blocks: dict[str, list[str]] = {}
        for offset, line in enumerate(body, 1):
            label = FIELD_LABEL.match(line)
            if label and label["label"] in fields:
                name = label["label"]
                if len(name) + 1 + len(label["pad"]) != width:
                    yield fail(f"{path}:{line_number + offset}", f"{name} label is not padded to column {width}")
                if name in blocks:
                    yield fail(where, f"{name} appears twice")
                seen.append(name)
                blocks[name] = [line[width:]]
            elif label and label["pad"]:
                yield fail(f"{path}:{line_number + offset}", f"unknown field {label['label']}")
            elif line.strip() and not seen:
                yield fail(f"{path}:{line_number + offset}", "text before the first field")
            elif line.strip():
                if not line.startswith(" " * width):
                    yield fail(f"{path}:{line_number + offset}", f"continuation not indented to column {width}")
                blocks[seen[-1]].append(line.strip())
        missing = [name for name in spec["required_fields"] if name not in blocks]
        if missing:
            yield fail(where, f"missing required field(s): {', '.join(missing)}")
        if seen != sorted(seen, key=fields.index):
            yield fail(where, f"fields out of order: {' / '.join(seen)}")
        kind_value = " ".join(blocks.get(KIND_FIELD, [""])).strip()
        if KIND_FIELD in blocks and kind_value not in spec["kind_values"]:
            yield fail(where, f"KIND '{kind_value}' is not one of {spec['kind_values']}")
        for name in spec["one_line_fields"]:
            if len(blocks.get(name, [])) > 1:
                yield fail(where, f"{name} spans {len(blocks[name])} lines; it takes one")
        words = len(header.split()) + sum(len(" ".join(lines).split()) for name, lines in blocks.items() if name != NEVER_COMPRESSED_FIELD)
        if words > spec["word_cap"]:
            yield fail(where, f"{words} words outside INVALIDATES, over the cap of {spec['word_cap']}")


def check_change_log_pointers(corpus: Corpus) -> Iterator[Finding]:
    """Fail the fact record reaching the change log other than as 'decided YYYY-MM-DD, see docs/architecture_log.md'."""
    entry_dates = set(ENTRY_DATE.findall(corpus.text(home_of(corpus, "change"))))
    fact = home_of(corpus, "fact")
    for number, line in enumerate(corpus.text(fact).splitlines(), 1):
        where = f"{fact}:{number}"
        mentions = line.count(CHANGE_LOG_MENTION)
        pointers = CHANGE_LOG_POINTER.findall(line)
        if mentions > len(pointers):
            yield fail(where, "cites the change log other than as 'decided YYYY-MM-DD, see docs/architecture_log.md'")
        for date in pointers:
            if date not in entry_dates:
                yield fail(where, f"points at a change-log entry dated {date}; none exists")
