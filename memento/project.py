"""The project memento serves: what `.claude/memento.yaml`'s `project:` block declares, and the paths its kind homes imply."""

import re
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from memento import Refusal
from memento.findings import DATE
from memento.schema import DIRECTORY_MARK, SCHEMA_HOME, Schema

PROJECT_KEY = "project"
ARCHIVE_FILE_NAME = "{stem}-{first}--{last}.md"
SPEC_ARCHIVE_FILE_NAME = "spec-{name}-{day}.md"
FULL_SHA = r"[0-9a-f]{40}"
ASCII_MINUTE_TEXT = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
RULING_SEPARATOR = " "


class JournalCorrection(BaseModel):
    """A commit the owner ruled may change written journal lines, and the events path and minute of the owner decision line recording that ruling."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    commit: Annotated[str, StringConstraints(pattern=rf"^{FULL_SHA}$")]
    ruling: Annotated[str, StringConstraints(pattern=rf"^\S+{RULING_SEPARATOR}{ASCII_MINUTE_TEXT}$")]

    @property
    def ruling_path(self) -> str:
        """The events file the ruling's decision line is in."""
        return self.ruling.rpartition(RULING_SEPARATOR)[0]

    @property
    def ruling_minute(self) -> str:
        """The minute the ruling's decision line is stamped with."""
        return self.ruling.rpartition(RULING_SEPARATOR)[2]


class Project(BaseModel):
    """The values memento cannot derive from the kinds' homes: one key per project-specific fact."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    ratified_on: Annotated[str, StringConstraints(pattern=rf"^{DATE}$")]
    main_branch: str
    record_space: str
    code_roots: list[str]
    history_start_file: str
    measured_cli: str
    commit_type: str
    commit_subject_words: int
    journal_corrections: list[JournalCorrection] = Field(default_factory=list)


def project_of(schema: Schema) -> Project:
    """The schema's `project:` block, refused when it is absent or does not fit."""
    if PROJECT_KEY not in schema:
        raise Refusal(f"{SCHEMA_HOME} declares no '{PROJECT_KEY}:' block")
    try:
        return Project.model_validate(schema[PROJECT_KEY])
    except ValidationError as invalid:
        problems = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in invalid.errors())
        raise Refusal(f"{SCHEMA_HOME} '{PROJECT_KEY}:' block does not fit: {problems}") from invalid


def home_of_kind(schema: Schema, kind: str) -> str:
    """The home the schema gives a kind, spelled as written."""
    return schema["kinds"][kind]["home"]


def directory_of_dated_home(schema: Schema, kind: str) -> PurePosixPath:
    """The directory holding a kind's dated directories: its home without the trailing `<date>/`."""
    return PurePosixPath(home_of_kind(schema, kind).removesuffix(DIRECTORY_MARK)).parent


def history_directory(schema: Schema) -> str:
    """The directory holding one directory per span day: the report kind's home without its `{date}/`."""
    return str(directory_of_dated_home(schema, "report"))


def history_glob(schema: Schema) -> str:
    """The glob matching every span day's directory."""
    return f"{history_directory(schema)}/*"


def journal_space(schema: Schema) -> str:
    """The directory holding the journal, the bench and the archive, spelled with its trailing slash."""
    return f"{PurePosixPath(history_directory(schema)).parent}{DIRECTORY_MARK}"


def journal_relative_prefixes(schema: Schema) -> tuple[str, str]:
    """The journal-space-relative first segments a record's path token may start with: the history and bench directories."""
    history, bench = (f"{directory_of_dated_home(schema, kind).name}{DIRECTORY_MARK}" for kind in ("report", "scratch"))
    return history, bench


def config_space(schema: Schema) -> str:
    """The directory holding the schema, spelled with its trailing slash."""
    return f"{PurePosixPath(SCHEMA_HOME).parent}{DIRECTORY_MARK}"


def pointer_roots(schema: Schema) -> list[str]:
    """The top-level directories a record's path token may start with, besides the journal's own."""
    project = project_of(schema)
    return [config_space(schema), project.record_space, *project.code_roots]


def record_stem(schema: Schema, kind: str) -> str:
    """The name a record is cited by: its home's file name without extension."""
    return PurePosixPath(home_of_kind(schema, kind)).stem


def archive_home(schema: Schema) -> str:
    """The directory a record kind's archived texts move to."""
    return home_of_kind(schema, "archived")


def change_log_archive_path(schema: Schema, first: str, last: str) -> str:
    """Where the change log's oldest entries move: the archive home, a file named for the log and the dates it spans."""
    name = ARCHIVE_FILE_NAME.format(stem=record_stem(schema, "change"), first=first, last=last)
    return f"{archive_home(schema)}{name}"


def spec_path(schema: Schema, name: str) -> str:
    """The home one spec's name resolves to."""
    return f"{home_of_kind(schema, 'spec')}{name}.md"


def spec_archive_path(schema: Schema, name: str, day: str) -> str:
    """Where an archived spec moves, dated by the day it moves."""
    return f"{archive_home(schema)}{SPEC_ARCHIVE_FILE_NAME.format(name=name, day=day)}"


def change_log_mention(schema: Schema) -> str:
    """The change log's file name, as a record cites it."""
    return PurePosixPath(home_of_kind(schema, "change")).name


def change_log_pointer(schema: Schema) -> re.Pattern[str]:
    """The one form a record may cite the change log in: `decided YYYY-MM-DD, see <change log home>`."""
    return re.compile(rf"decided ({DATE}), see {re.escape(home_of_kind(schema, 'change'))}")


def change_log_pointer_form(schema: Schema) -> str:
    """The citation form `change_log_pointer` matches, as findings and help spell it."""
    return f"decided YYYY-MM-DD, see {home_of_kind(schema, 'change')}"
