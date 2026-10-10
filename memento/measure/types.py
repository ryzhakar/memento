"""The measurement log's documents: every type an entry is built from, and the one block form the log holds."""

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    PlainSerializer,
    StringConstraints,
    Tag,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from memento import Refusal
from memento.actor import Actor, parse_actor
from memento.measure.registry import Dimension, metric_name, registered_metric

FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True, regex_engine="python-re")
DOCUMENT_START = "---\n"
UNWRAPPED_WIDTH = 1_000_000
QUESTION_MAX_CHARACTERS = 160
RAW_MAX_LINES = 60
SLEEP_VOIDING_NS = 1_000_000_000
MEASURED_INTERPRETER = re.compile(r"python[0-9.]*")
SAMPLING_INTERVAL_STATED = re.compile(r"interval\w*[\s:=]*\d", re.IGNORECASE)
TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
STRING_TAG = "tag:yaml.org,2002:str"
LITERAL_STYLE = "|"
TAG_LESS = "a mapping with exactly one key naming its variant, or the bare name of a variant without fields"

Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MeasurementId = Annotated[str, StringConstraints(pattern=r"^m-\d{8}-\d{3}$")]
RunTarget = Annotated[str, StringConstraints(pattern=r"^m-\d{8}-\d{3}(#[1-9]\d*)?$")]
Label = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
ScaleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
RepoPath = Annotated[str, StringConstraints(pattern=r"^(?!/)\S+$")]
PathLine = Annotated[str, StringConstraints(pattern=r"^\S+:[1-9]\d*$")]
WorldName = Annotated[str, StringConstraints(pattern=r"^\S+ × \S+(, \S+)*$")]
Text = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]


def one_line(text: str) -> str:
    """Admit text as one line opening on a visible character."""
    if not text[:1].strip() or text.splitlines() != [text]:
        raise ValueError(f"'{text}' is not one line opening on a visible character")
    return text


def short_question(text: str) -> str:
    """Admit a question of at most the question's character limit."""
    if len(text) > QUESTION_MAX_CHARACTERS:
        raise ValueError(f"question runs {len(text)} characters, over {QUESTION_MAX_CHARACTERS}")
    return text


def bounded_raw(text: str) -> str:
    """Admit raw instrument text of at least one and at most the raw line limit's lines."""
    lines = len(text.splitlines())
    if not 1 <= lines <= RAW_MAX_LINES:
        raise ValueError(f"raw holds {lines} lines; it holds 1 to {RAW_MAX_LINES}")
    return text


def actor_from_text(value: object) -> object:
    """The actor an events-line spelling names, its refusal raised as a validation error."""
    if not isinstance(value, str):
        return value
    try:
        return parse_actor(value)
    except Refusal as refusal:
        raise ValueError(str(refusal)) from refusal


def variant_tag(value: object) -> str | None:
    """The variant a tagged union's value names: the bare name, the one key of a mapping, or a model's one field."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(next(iter(value))) if len(value) == 1 else None
    if isinstance(value, BaseModel):
        return next(iter(type(value).model_fields))
    return None


TAGGED = Discriminator(variant_tag, custom_error_type="tagged_union", custom_error_message=f"not {TAG_LESS}")

OneLine = Annotated[str, AfterValidator(one_line)]
Question = Annotated[str, AfterValidator(one_line), AfterValidator(short_question)]
Raw = Annotated[str, AfterValidator(bounded_raw)]
ActorField = Annotated[Actor, BeforeValidator(actor_from_text), PlainSerializer(lambda actor: actor.render(), return_type=str)]


class Frozen(BaseModel):
    """A strict, immutable record shape refusing unknown keys."""

    model_config = FROZEN


class Memory(Frozen):
    """A memory reading in bytes, with the bytes one unit of its last printed digit spans."""

    bytes: Count
    resolution_bytes: Positive


class UnitAmbiguous(Frozen):
    """A memory reading whose text states no unit, kept as that text and never used as a number."""

    unit_ambiguous: Text


class Duration(Frozen):
    """A duration reading in nanoseconds, with the nanoseconds one unit of its last printed digit spans."""

    ns: Count
    resolution_ns: Positive


class Rate(Frozen):
    """A throughput reading: a count per a span of nanoseconds, with the count's resolution."""

    count: Count
    per_ns: Positive
    resolution_count: Positive


QUANTITY_KEYS = {"bytes": "memory", "unit_ambiguous": "unit_ambiguous", "ns": "duration", "count": "rate"}
QUANTITY_FORMS = "memory {bytes, resolution_bytes}, {unit_ambiguous}, duration {ns, resolution_ns} or rate {count, per_ns, resolution_count}"


def quantity_kind(value: object) -> str | None:
    """Which quantity a reading is, told by the key naming its unit."""
    keys = value.keys() if isinstance(value, Mapping) else type(value).model_fields if isinstance(value, BaseModel) else ()
    return next((QUANTITY_KEYS[key] for key in keys if key in QUANTITY_KEYS), None)


type Quantity = Annotated[
    Annotated[Memory, Tag("memory")]
    | Annotated[UnitAmbiguous, Tag("unit_ambiguous")]
    | Annotated[Duration, Tag("duration")]
    | Annotated[Rate, Tag("rate")],
    Discriminator(quantity_kind, custom_error_type="quantity", custom_error_message=f"not a reading: {QUANTITY_FORMS}"),
]

DIMENSION_TYPES: dict[Dimension, tuple[type[BaseModel], ...]] = {
    "memory": (Memory, UnitAmbiguous),
    "duration": (Duration,),
    "rate": (Rate,),
}


class Commit(Frozen):
    """A commit, by full sha."""

    sha: Sha


class CommitCode(Frozen):
    """Code as one commit holds it."""

    commit: Commit


class Dirty(Frozen):
    """A commit plus uncommitted changes, identified by their diff's hash and kept diff file."""

    base: Sha
    diff_sha256: Sha256
    diff_path: Text


class DirtyCode(Frozen):
    """Code as a commit plus a recorded diff holds it."""

    dirty: Dirty


type Code = Annotated[Annotated[CommitCode, Tag("commit")] | Annotated[DirtyCode, Tag("dirty")], TAGGED]


def code_sha(code: CommitCode | DirtyCode) -> str:
    """The commit a code state stands on."""
    return code.commit.sha if isinstance(code, CommitCode) else code.dirty.base


class MachineFacts(Frozen):
    """A machine as read on it."""

    cpu: Text
    ram_bytes: Positive
    os: Text
    arch: Text


class ObservedMachine(Frozen):
    """A machine read at run time."""

    observed: MachineFacts


class MachineStatement(Frozen):
    """A machine as a record line states it."""

    text: Text
    source: PathLine


class StatedMachine(Frozen):
    """A machine stated by a record line."""

    stated: MachineStatement


type Machine = Annotated[Annotated[ObservedMachine, Tag("observed")] | Annotated[StatedMachine, Tag("stated")], TAGGED]


class Versions(Frozen):
    """Package versions read inside the measured interpreter."""

    versions: dict[str, Text]


class ObservedEnv(Frozen):
    """An environment read at run time."""

    observed: Versions


class VersionStatement(Frozen):
    """Package versions as a record states them."""

    versions: dict[str, Text]
    source: Text


class StatedEnv(Frozen):
    """An environment stated by a record."""

    stated: VersionStatement


class Why(Frozen):
    """Why a scope is unknown."""

    why: Text


class UnknownEnv(Frozen):
    """An environment nobody recorded."""

    unknown: Why


type Env = Annotated[
    Annotated[ObservedEnv, Tag("observed")] | Annotated[StatedEnv, Tag("stated")] | Annotated[UnknownEnv, Tag("unknown")],
    TAGGED,
]


class StageCommand(Frozen):
    """A command of the project's CLI."""

    command: Text


class Stage(Frozen):
    """A pipeline stage run as its CLI command."""

    stage: StageCommand


class OperationCall(Frozen):
    """A callable by dotted name and the driver script calling it."""

    callable: Text
    harness: Text


class Operation(Frozen):
    """One callable measured through a harness."""

    operation: OperationCall


class BenchCase(Frozen):
    """A benchmark script and its case."""

    script: Text
    case: Text


class Bench(Frozen):
    """One case of a benchmark script."""

    bench: BenchCase


type Subject = Annotated[
    Annotated[Stage, Tag("stage")] | Annotated[Operation, Tag("operation")] | Annotated[Bench, Tag("bench")], TAGGED
]


class SeedFold(Frozen):
    """The fold one split seed draws."""

    seed: int


type Fold = Annotated[Annotated[SeedFold, Tag("seed")] | Annotated[Literal["world_level"], Tag("world_level")], TAGGED]
type Scale = Annotated[dict[ScaleName, Count], Field(min_length=1)]


class WorldScope(Frozen):
    """A client world, its fold, the content addresses it read, and its sizes."""

    world: WorldName
    fold: Fold
    inputs: list[Sha256]
    scale: Scale


class World(Frozen):
    """Data from a client world."""

    world: WorldScope


class SyntheticScope(Frozen):
    """A generated dataset: its generator, seed and sizes."""

    generator: Text
    seed: int
    scale: Scale


class Synthetic(Frozen):
    """Generated data."""

    synthetic: SyntheticScope


class FixturePath(Frozen):
    """A committed fixture."""

    path: RepoPath


class Fixture(Frozen):
    """Fixture data."""

    fixture: FixturePath


class UnknownData(Frozen):
    """Data nobody recorded."""

    unknown: Why


type Data = Annotated[
    Annotated[World, Tag("world")]
    | Annotated[Synthetic, Tag("synthetic")]
    | Annotated[Fixture, Tag("fixture")]
    | Annotated[UnknownData, Tag("unknown")],
    TAGGED,
]


class FixedCode(Frozen):
    """One code state."""

    code: Code


class Fixed(Frozen):
    """A design holding everything fixed."""

    fixed: FixedCode


class CodeLevels(Frozen):
    """Code states by level label."""

    levels: Annotated[dict[Label, Code], Field(min_length=2)]


class VariesCode(Frozen):
    """A design varying the code over its levels."""

    varies_code: CodeLevels


class ParameterLevels(Frozen):
    """A named parameter's values by level label, at one code state."""

    name: Text
    code: Code
    levels: Annotated[dict[Label, Text | int], Field(min_length=2)]


class VariesParameter(Frozen):
    """A design varying one named parameter over its levels."""

    varies_parameter: ParameterLevels


type Design = Annotated[
    Annotated[Fixed, Tag("fixed")] | Annotated[VariesCode, Tag("varies_code")] | Annotated[VariesParameter, Tag("varies_parameter")],
    TAGGED,
]


def design_levels(design: Fixed | VariesCode | VariesParameter) -> dict[str, Any] | None:
    """The design's levels by label, or None when it holds everything fixed."""
    match design:
        case Fixed():
            return None
        case VariesCode():
            return dict(design.varies_code.levels)
        case VariesParameter():
            return dict(design.varies_parameter.levels)


def level_code(design: Fixed | VariesCode | VariesParameter, level: str | None) -> CommitCode | DirtyCode:
    """The code state a design runs at a level."""
    match design:
        case Fixed():
            return design.fixed.code
        case VariesCode():
            return design.varies_code.levels[level or ""]
        case VariesParameter():
            return design.varies_parameter.code


def design_codes(design: Fixed | VariesCode | VariesParameter) -> list[CommitCode | DirtyCode]:
    """Every code state a design runs."""
    match design:
        case Fixed():
            return [design.fixed.code]
        case VariesCode():
            return list(design.varies_code.levels.values())
        case VariesParameter():
            return [design.varies_parameter.code]


class ParsedLog(Frozen):
    """A log an instrument wrote, by path and hash."""

    log: Text
    log_sha256: Sha256


class Parsed(Frozen):
    """A run parsed from its instrument's log."""

    parsed: ParsedLog


class SourceLine(Frozen):
    """A line of a record a reading was copied from."""

    source: Text
    line: Positive


class Transcribed(Frozen):
    """A run copied by hand from a record."""

    transcribed: SourceLine


type Provenance = Annotated[
    Annotated[Literal["observed"], Tag("observed")] | Annotated[Parsed, Tag("parsed")] | Annotated[Transcribed, Tag("transcribed")],
    TAGGED,
]


class Position(Frozen):
    """A run's position among the runs one process made."""

    position: Positive


class SharedProcess(Frozen):
    """A run sharing its process with earlier runs."""

    shared: Position


type Process = Annotated[Annotated[Literal["fresh"], Tag("fresh")] | Annotated[SharedProcess, Tag("shared")], TAGGED]


class Basis(Frozen):
    """How run times were derived."""

    basis: Text


class DerivedClock(Frozen):
    """Run times derived from other evidence."""

    derived: Basis


class ClockSource(Frozen):
    """Where run times were stated."""

    source: Text


class StatedClock(Frozen):
    """Run times stated by a record."""

    stated: ClockSource


type Clock = Annotated[
    Annotated[Literal["observed"], Tag("observed")] | Annotated[DerivedClock, Tag("derived")] | Annotated[StatedClock, Tag("stated")],
    TAGGED,
]


class Neighbour(Frozen):
    """A process seen beside a run."""

    pid: Positive
    command: Text
    max_rss_bytes: Count
    max_cpu_pct: Count
    first_seen: AwareDatetime
    last_seen: AwareDatetime


class ContentionWatch(Frozen):
    """What every-interval process sampling saw beside a run, and the floors it watched above."""

    interval_ms: Positive
    cpu_floor_pct: Count
    rss_floor_bytes: Count
    names_always: list[Text]
    neighbours: list[Neighbour]


class ObservedContention(Frozen):
    """Contention sampled while the run ran."""

    observed: ContentionWatch


class SoloCheck(Frozen):
    """How a run was checked to be alone."""

    how_checked: Text

    @model_validator(mode="before")
    @classmethod
    def refuse_neighbours(cls, data: Any) -> Any:
        """Refuse a solo declaration naming neighbours."""
        if isinstance(data, Mapping) and "neighbours" in data:
            raise ValueError(f"declared_solo takes no neighbours, yet names {data['neighbours']}; a run with a neighbour is declared_beside")
        return data


class DeclaredSolo(Frozen):
    """A run declared alone, with how that was checked."""

    declared_solo: SoloCheck


class Beside(Frozen):
    """The jobs a run ran beside."""

    neighbours: list[Text]

    @model_validator(mode="after")
    def refuse_nobody(self) -> Self:
        """Refuse a declaration of running beside nobody."""
        if not self.neighbours:
            raise ValueError("declared_beside names no neighbour; a run beside nothing is declared_solo")
        return self


class DeclaredBeside(Frozen):
    """A run declared beside named jobs."""

    declared_beside: Beside


type Contention = Annotated[
    Annotated[ObservedContention, Tag("observed")]
    | Annotated[DeclaredSolo, Tag("declared_solo")]
    | Annotated[DeclaredBeside, Tag("declared_beside")]
    | Annotated[Literal["unstated"], Tag("unstated")],
    TAGGED,
]


class PressureSamples(Frozen):
    """The highest memory-pressure level sampled and the sample count."""

    max_level: Count
    samples: Positive


class ObservedPressure(Frozen):
    """Memory pressure sampled while the run ran."""

    observed: PressureSamples


class PressureText(Frozen):
    """Memory pressure as declared."""

    text: Text


class DeclaredPressure(Frozen):
    """Memory pressure declared by whoever ran it."""

    declared: PressureText


type Pressure = Annotated[
    Annotated[ObservedPressure, Tag("observed")]
    | Annotated[DeclaredPressure, Tag("declared")]
    | Annotated[Literal["unstated"], Tag("unstated")],
    TAGGED,
]
type PowerState = Literal["ac", "battery"]


class PowerReading(Frozen):
    """A power source read at run time."""

    state: PowerState


class ObservedPower(Frozen):
    """Power read while the run ran."""

    observed: PowerReading


class PowerStatement(Frozen):
    """A power source as a record states it."""

    state: PowerState
    source: Text


class DeclaredPower(Frozen):
    """Power declared by a record."""

    declared: PowerStatement


type Power = Annotated[
    Annotated[ObservedPower, Tag("observed")]
    | Annotated[DeclaredPower, Tag("declared")]
    | Annotated[Literal["unstated"], Tag("unstated")],
    TAGGED,
]


class Slept(Frozen):
    """Nanoseconds the machine slept during the run."""

    slept_ns: Count


class ObservedSleep(Frozen):
    """Sleep measured across the run."""

    observed: Slept


class Preventer(Frozen):
    """What kept the machine awake."""

    by: Text


class PreventedSleep(Frozen):
    """Sleep prevented for the run."""

    prevented: Preventer


type Sleep = Annotated[
    Annotated[ObservedSleep, Tag("observed")]
    | Annotated[PreventedSleep, Tag("prevented")]
    | Annotated[Literal["unchecked"], Tag("unchecked")],
    TAGGED,
]


class WarmAfter(Frozen):
    """The item a warm cache was left by."""

    after: Positive


class WarmCache(Frozen):
    """Caches warmed by an earlier item."""

    warm: WarmAfter


class PurgedBy(Frozen):
    """What emptied the caches."""

    purged_by: Text


class ColdCache(Frozen):
    """Caches emptied before the run."""

    cold: PurgedBy


type Cache = Annotated[
    Annotated[Literal["unknown"], Tag("unknown")] | Annotated[WarmCache, Tag("warm")] | Annotated[ColdCache, Tag("cold")],
    TAGGED,
]


class ExitCode(Frozen):
    """A process ending with an exit code."""

    code: int


class ExitSignal(Frozen):
    """A process ended by a signal."""

    signal: Positive


class UnknownExit(Frozen):
    """A process whose exit status nothing recorded, with why."""

    unknown: Why


type Exit = Annotated[
    Annotated[ExitCode, Tag("code")] | Annotated[ExitSignal, Tag("signal")] | Annotated[UnknownExit, Tag("unknown")], TAGGED
]


class VoidReason(Frozen):
    """Why a run's readings do not stand."""

    reason: OneLine


class VoidRun(Frozen):
    """A run voided when recorded."""

    void: VoidReason


type Validity = Annotated[Annotated[Literal["valid"], Tag("valid")] | Annotated[VoidRun, Tag("void")], TAGGED]
type Durability = Literal["committed", "card", "recon", "scratchpad", "report_only"]


class Evidence(Frozen):
    """The file a reading's raw text came from, its hash, and how long it lasts."""

    path: Text
    sha256: Sha256
    durability: Durability


def refuse_unregistered(readings: Mapping[str, object]) -> None:
    """Refuse a reading whose metric the registry does not hold, or whose quantity is not its metric's dimension."""
    for key, quantity in readings.items():
        metric = registered_metric(key)
        if isinstance(quantity, BaseModel) and not isinstance(quantity, DIMENSION_TYPES[metric.dimension]):
            raise ValueError(f"metric '{key}' is {metric.dimension}, but its reading is {type(quantity).__name__}")


class Run[Reading](Frozen):
    """One process execution: how it was launched and under what conditions, its readings, and its instrument's raw text."""

    item: Literal["run"]
    level: Label | None = None
    provenance: Provenance
    argv: Annotated[list[Text], Field(min_length=1)]
    process: Process
    started: AwareDatetime
    ended: AwareDatetime
    clock: Clock
    contention: Contention
    pressure: Pressure
    power: Power
    sleep: Sleep
    cache: Cache
    exit: Exit
    validity: Validity
    readings: Annotated[dict[str, Reading], Field(min_length=1)]
    raw: Raw
    evidence: Evidence

    @model_validator(mode="after")
    def refuse_inadmissible(self) -> Self:
        """Refuse readings the run's conditions do not admit, a slept valid run, and an end before the start."""
        refuse_unregistered(self.readings)
        for key in self.readings:
            refuse_unadmitted(self, key)
        if isinstance(self.sleep, ObservedSleep) and self.sleep.observed.slept_ns > SLEEP_VOIDING_NS and self.validity == "valid":
            raise ValueError(f"the run slept {self.sleep.observed.slept_ns} ns, over {SLEEP_VOIDING_NS}; a slept run is void, not valid")
        if self.ended < self.started:
            raise ValueError(f"the run ends {self.ended.isoformat()}, before it starts {self.started.isoformat()}")
        return self


def line_names_with_number(line: str, name: str) -> bool:
    """Whether a line holds the name as a whole token and a digit."""
    named = re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", line)
    return named is not None and any(character.isdigit() for character in line)


def refuse_unstated_in_raw(raw: str, key: str) -> None:
    """Refuse a reading whose metric needs a line in the raw text that the raw text lacks."""
    match registered_metric(key).admission:
        case "interval_in_raw":
            if not SAMPLING_INTERVAL_STATED.search(raw):
                raise ValueError(f"reads {key}, but its raw text states no sampling interval")
        case "named_line_in_raw":
            name = metric_name(key)
            if not any(line_names_with_number(line, name) for line in raw.splitlines()):
                raise ValueError(f"reads {key}, but its raw text has no line naming '{name}' with a number")
        case "always" | "fresh_process" | "interpreter_argv0" | "observed_run":
            pass


def refuse_unadmitted(run: Run[Any], key: str) -> None:
    """Refuse a reading whose metric's admission the run's conditions or raw text fail."""
    match registered_metric(key).admission:
        case "fresh_process":
            if isinstance(run.process, SharedProcess):
                raise ValueError(
                    f"reads {key} from a shared process (position {run.process.shared.position}); "
                    "a process's high-water mark never decreases, so only a fresh process yields it"
                )
        case "interpreter_argv0":
            program = run.argv[0].rsplit("/", 1)[-1]
            if not MEASURED_INTERPRETER.fullmatch(program):
                raise ValueError(f"reads {key}, but argv[0] is '{run.argv[0]}', a wrapper; {key} describes the direct child only")
        case "observed_run":
            if run.provenance != "observed":
                raise ValueError(f"reads {key}, which only a run the run verb observed carries")
        case "interval_in_raw" | "named_line_in_raw":
            refuse_unstated_in_raw(run.raw, key)
        case "always":
            pass


class Summary[Reading](Frozen):
    """A statistic over runs whose own readings are lost, transcribed from a record."""

    item: Literal["summary"]
    level: Label | None = None
    statistic: Literal["median", "min", "max", "mean"]
    n: Annotated[int, Field(ge=2)]
    readings: Annotated[dict[str, Reading], Field(min_length=1)]
    raw: Raw
    evidence: Evidence
    provenance: Transcribed

    @model_validator(mode="after")
    def refuse_unregistered_readings(self) -> Self:
        """Refuse readings whose metric the registry does not hold or whose raw text lacks the line the metric needs."""
        refuse_unregistered(self.readings)
        for key in self.readings:
            refuse_unstated_in_raw(self.raw, key)
        return self


type Item[Reading] = Annotated[Run[Reading] | Summary[Reading], Field(discriminator="item")]


class Measurement(Frozen):
    """One question answered by runs sharing one scope, under one design."""

    kind: Literal["measurement"]
    id: MeasurementId
    recorded: AwareDatetime
    by: ActorField
    question: Question
    subject: Subject
    paths: Annotated[list[RepoPath], Field(min_length=1)]
    data: Data
    machine: Machine
    env: Env
    design: Design
    items: Annotated[list[Item[Quantity]], Field(min_length=1)]
    supersedes: list[MeasurementId]

    @model_validator(mode="after")
    def refuse_inconsistent_items(self) -> Self:
        """Refuse items whose levels miss the design, runs out of order or overlapping, and a recording before a run ends."""
        refuse_level_mismatch(self.design, self.items)
        runs = [(position, item) for position, item in enumerate(self.items, 1) if isinstance(item, Run)]
        for (_, earlier), (position, later) in zip(runs, runs[1:]):
            if later.started < earlier.ended:
                raise ValueError(
                    f"item {position} starts {later.started.isoformat()}, before the run before it ends {earlier.ended.isoformat()}; "
                    "runs are listed in execution order and do not overlap"
                )
        for position, run in runs:
            if run.ended > self.recorded:
                raise ValueError(f"item {position} ends {run.ended.isoformat()}, after the entry is recorded {self.recorded.isoformat()}")
        return self


def refuse_level_mismatch(design: Fixed | VariesCode | VariesParameter, items: list[Run[Any] | Summary[Any]]) -> None:
    """Refuse a level on an item of a fixed design, a missing or unknown level on a varying one, and a level with no item."""
    levels = design_levels(design)
    for position, item in enumerate(items, 1):
        if levels is None and item.level is not None:
            raise ValueError(f"item {position} carries level '{item.level}', but the design is fixed")
        if levels is not None and item.level not in levels:
            raise ValueError(f"item {position} level {item.level!r} is not one of the design's levels ({', '.join(levels)})")
    for label in levels or {}:
        if all(item.level != label for item in items):
            raise ValueError(f"level '{label}' has no item")


class Void(Frozen):
    """A later finding that an earlier entry or one of its runs does not stand."""

    kind: Literal["void"]
    id: MeasurementId
    recorded: AwareDatetime
    by: ActorField
    target: RunTarget
    reason: OneLine
    evidence: PathLine | Sha


type Document = Annotated[Measurement | Void, Field(discriminator="kind")]

DOCUMENT = TypeAdapter[Measurement | Void](Document)


class TimestampsAsText(yaml.SafeLoader):
    """A safe YAML loader that leaves timestamps as the text they are written as."""


TimestampsAsText.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != TIMESTAMP_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class BlockDumper(yaml.SafeDumper):
    """A safe YAML dumper writing multi-line text as literal blocks."""


def represent_text(dumper: yaml.SafeDumper, text: str) -> yaml.ScalarNode:
    """Text as a literal block when it spans lines, plain or quoted otherwise."""
    return dumper.represent_scalar(STRING_TAG, text, style=LITERAL_STYLE if "\n" in text else None)


BlockDumper.add_representer(str, represent_text)


def loaded_yaml(text: str) -> Any:
    """The data a YAML text holds, timestamps kept as text."""
    return yaml.load(text, Loader=TimestampsAsText)


def validated[Model](adapter: TypeAdapter[Model], data: Any) -> Model:
    """Data validated as JSON would carry it, each validation failure named by where it sits, raised as one refusal."""
    try:
        return adapter.validate_json(json.dumps(data))
    except ValidationError as invalid:
        raise Refusal("; ".join(validation_message(error) for error in invalid.errors())) from invalid
    except TypeError as unserializable:
        raise Refusal(f"holds a value YAML cannot carry as data: {unserializable}") from unserializable


def validation_message(error: Any) -> str:
    """One validation failure as its dotted location, a variant's tag named once, and its message."""
    parts = [str(part) for part in error["loc"]]
    where = ".".join(part for position, part in enumerate(parts) if position == 0 or part != parts[position - 1])
    message = str(error.get("ctx", {}).get("error", error["msg"]))
    return f"{where}: {message}" if where else message


def block_text(data: Any) -> str:
    """Data as block-form YAML: keys in their order, multi-line text as literal blocks, no line wrapping."""
    return yaml.dump(data, Dumper=BlockDumper, sort_keys=False, allow_unicode=True, width=UNWRAPPED_WIDTH, default_flow_style=False)


def canonical_text(document: Measurement | Void) -> str:
    """The one text the measurement log holds for a document: a document start, then its block form."""
    return DOCUMENT_START + block_text(DOCUMENT.dump_python(document, mode="json", exclude_none=True))
