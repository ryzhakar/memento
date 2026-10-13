"""`measurement add`: one entry built from an entry spec and its run records, validated, and appended to the measurement log."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, Field, Tag, TypeAdapter

from memento import Environment, Refusal, announce, quiet_option, refusals_reported
from memento.actor import Self_
from memento.candidate import refuse_new_failures
from memento.measure.check import MEASUREMENT_KIND, document_problems, logged_documents
from memento.measure.ids import next_id
from memento.measure.registry import Dimension, Metric, metric_name, registered_metric
from memento.measure.stages import CodeHistory
from memento.measure.types import (
    TAGGED,
    Code,
    Data,
    Design,
    DirtyCode,
    Duration,
    Env,
    Frozen,
    Item,
    Machine,
    Measurement,
    MeasurementId,
    Memory,
    Operation,
    Parsed,
    Quantity,
    Question,
    Rate,
    RepoPath,
    Run,
    Subject,
    Text,
    Transcribed,
    UnitAmbiguous,
    canonical_text,
    code_sha,
    level_code,
    line_names_with_number,
    loaded_yaml,
    validated,
)
from memento.project import project_of
from memento.schema import agreeing_schema

MEMORY_UNITS = {"B": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30}
DURATION_UNITS = {"ns": 1, "us": 10**3, "ms": 10**6, "s": 10**9, "min": 60 * 10**9, "h": 3600 * 10**9}
NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
RAW_NUMBER = re.compile(rf"(?<![\d.,]){NUMBER}(?![\d,]|\.\d)")
THOUSANDS_SEPARATOR = ","
CLOCK_SEPARATOR = ":"
CLOCK_SCALES = (60, 3600)
TRANSCRIPTION_WINDOW_LINES = 40

type ReadingText = Text | UnitAmbiguous


@dataclass(frozen=True)
class UnitScale:
    """How one dimension's readings are written as text: the closed units, what joins number and unit, and the base unit."""

    units: Mapping[str, int]
    separator: str
    base_unit: str
    definitions: str
    printed_between: str


UNIT_SCALES: dict[Dimension, UnitScale] = {
    "memory": UnitScale(MEMORY_UNITS, " ", "byte", " (kB = 10^3 B, GB = 10^9 B, KiB = 2^10 B, GiB = 2^30 B)", r"\s*"),
    "duration": UnitScale(DURATION_UNITS, " ", "nanosecond", "", r"\s*"),
    "rate": UnitScale(DURATION_UNITS, "/", "count", "", "/"),
}


def unit_beside_number(scale: UnitScale) -> re.Pattern[str]:
    """The pattern of a numeral with one of the scale's closed units printed beside it."""
    units = "|".join(re.escape(unit) for unit in scale.units)
    return re.compile(rf"(?<![\d.,])(?P<number>{NUMBER}){scale.printed_between}(?P<unit>{units})(?![A-Za-z])")


UNITS_BESIDE_NUMBERS = {dimension: unit_beside_number(scale) for dimension, scale in UNIT_SCALES.items()}


class Record[Reading](Frozen):
    """One measured item with the code, machine and environment it was measured under."""

    code: Code
    machine: Machine
    env: Env
    item: Item[Reading]


class RunRecordPath(Frozen):
    """A run record file the run and log verbs wrote."""

    run_record: Text


class InlineRecord(Frozen):
    """A run or summary transcribed by hand into the entry spec, its readings written as text with units."""

    transcribed: Record[ReadingText]


type SpecItem = Annotated[Annotated[RunRecordPath, Tag("run_record")] | Annotated[InlineRecord, Tag("transcribed")], TAGGED]


class EntrySpec(Frozen):
    """What an entry answers and over what scope and design, and where its items come from."""

    question: Question
    subject: Subject
    paths: Annotated[list[RepoPath], Field(min_length=1)]
    data: Data
    design: Design
    items: Annotated[list[SpecItem], Field(min_length=1)]
    supersedes: list[MeasurementId]


ENTRY_SPEC = TypeAdapter(EntrySpec)
MEASURED_RECORD = TypeAdapter(Record[Quantity])
TRANSCRIBED_RECORD = TypeAdapter(Record[ReadingText])
MEASUREMENT = TypeAdapter(Measurement)


def resolved(root: Path, path: str) -> Path:
    """A path as a record writes it: absolute as given, otherwise beneath root."""
    return Path(path) if Path(path).is_absolute() else root / path


def sha256_of(path: Path) -> str:
    """The hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_yaml(root: Path, path: str) -> Any:
    """The data a YAML file holds, refused when the file is absent."""
    file = resolved(root, path)
    if not file.is_file():
        raise Refusal(f"{path} is not a file")
    return loaded_yaml(file.read_text(encoding="utf-8"))


def item_record(root: Path, item: RunRecordPath | InlineRecord) -> Record[Quantity]:
    """The record an entry-spec item names, read from its run record file or converted from its transcription."""
    match item:
        case RunRecordPath():
            record = validated(MEASURED_RECORD, read_yaml(root, item.run_record))
            refuse_transcription_in_file(record)
            refuse_readings_outside_raw(record.item)
            return record
        case InlineRecord():
            return converted_record(item.transcribed)


def refuse_transcription_in_file(record: Record[Quantity]) -> None:
    """Refuse a run record file holding anything but a parsed or observed run."""
    if isinstance(record.item.provenance, Transcribed):
        raise Refusal("a run record file holds a parsed or observed run; a transcription is written inline under 'transcribed'")


def refuse_readings_outside_raw(item: Item[Quantity]) -> None:
    """Refuse a record's readings that its raw text does not support."""
    for key, quantity in item.readings.items():
        refuse_quantity_outside_raw(key, quantity, item.raw)


def converted_record(record: Record[ReadingText]) -> Record[Quantity]:
    """A transcribed record with its text readings converted to quantities against its raw text."""
    if isinstance(record.item, Run) and not isinstance(record.item.provenance, Transcribed):
        raise Refusal("an inline run is transcribed; a parsed or observed run is a run record file named by 'run_record'")
    quantities = {key: quantity_from_text(key, reading, record.item.raw) for key, reading in record.item.readings.items()}
    data = TRANSCRIBED_RECORD.dump_python(record, mode="json", exclude_none=True)
    data["item"]["readings"] = {key: quantity.model_dump(mode="json") for key, quantity in quantities.items()}
    return validated(MEASURED_RECORD, data)


def quantity_from_text(key: str, reading: str | UnitAmbiguous, raw: str) -> Quantity:
    """A reading's text as a quantity in base units, refused when its unit or number is not its raw text's."""
    dimension = registered_metric(key).dimension
    if isinstance(reading, UnitAmbiguous):
        refuse_text_outside_raw(key, reading.unit_ambiguous, raw)
        return reading
    scale = UNIT_SCALES[dimension]
    number, unit = number_and_unit(key, reading, scale)
    refuse_unit_outside_raw(key, reading, unit, raw)
    beside = printed_beside_units(raw, dimension, number)
    refuse_unit_beside_another_number(key, reading, unit, beside)
    refuse_number_outside_raw(key, reading, number, raw, [numeral for numeral, printed_unit in beside if printed_unit == unit])
    return scaled_quantity(key, reading, number, scale.units[unit], dimension)


def printed_decimal(printed: str) -> Decimal:
    """The number a printed numeral writes: thousands separators dropped, a clock time h:mm:ss or m:ss counted in seconds."""
    seconds, *larger_parts = reversed(printed.replace(THOUSANDS_SEPARATOR, "").split(CLOCK_SEPARATOR))
    return Decimal(seconds) + sum(int(part) * scale for part, scale in zip(larger_parts, CLOCK_SCALES))


def number_and_unit(key: str, reading: str, scale: UnitScale) -> tuple[Decimal, str]:
    """The number and the closed unit a reading's text writes, refusing a missing or unknown unit."""
    form = f"'<number>{scale.separator}<unit>'"
    shape = re.fullmatch(rf"(?P<number>{NUMBER})(?:{re.escape(scale.separator)}(?P<unit>\S+))?", reading)
    if shape is None:
        raise Refusal(f"{key} reading '{reading}' is not {form}")
    if shape["unit"] is None:
        raise Refusal(f"{key} reading '{reading}' carries no unit; write {form} with one of {', '.join(scale.units)}{scale.definitions}")
    if shape["unit"] not in scale.units:
        raise Refusal(f"{key} reading '{reading}' names unit '{shape['unit']}', not one of {', '.join(scale.units)}{scale.definitions}")
    return printed_decimal(shape["number"]), shape["unit"]


def refuse_text_outside_raw(key: str, text: str, raw: str) -> None:
    """Refuse an ambiguous reading whose text its raw text does not hold."""
    if text not in raw:
        raise Refusal(f"{key} reading '{text}' is not in its raw text")


def refuse_unit_outside_raw(key: str, reading: str, unit: str, raw: str) -> None:
    """Refuse a reading declaring a unit its raw text never writes."""
    if not re.search(rf"(?<![A-Za-z]){re.escape(unit)}(?![A-Za-z])", raw):
        raise Refusal(f"{key} reading '{reading}' declares {unit}; its raw text does not contain '{unit}'")


def printed_beside_units(raw: str, dimension: Dimension, number: Decimal) -> list[tuple[str, str]]:
    """Each numeral equal to the number that the raw text prints beside a closed unit, with that unit."""
    return [(found["number"], found["unit"]) for found in UNITS_BESIDE_NUMBERS[dimension].finditer(raw) if printed_decimal(found["number"]) == number]


def refuse_unit_beside_another_number(key: str, reading: str, unit: str, beside: Sequence[tuple[str, str]]) -> None:
    """Refuse a reading whose number its raw text prints beside other closed units and never beside the declared one."""
    units = list(dict.fromkeys(printed_unit for _, printed_unit in beside))
    if beside and unit not in units:
        raise Refusal(f"{key} reading '{reading}': its raw text prints {beside[0][0]} beside {', '.join(units)}, never beside {unit}")


def decimal_places(number: Decimal) -> int:
    """The digits a number prints after its decimal point."""
    exponent = number.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def refuse_number_outside_raw(key: str, reading: str, number: Decimal, raw: str, beside_its_unit: Sequence[str]) -> None:
    """Refuse a reading whose number its raw text never prints, or prints only to fewer decimal places, judged on the prints beside its unit when there are any."""
    equal = [printed for printed in RAW_NUMBER.findall(raw) if printed_decimal(printed) == number]
    if not equal:
        raise Refusal(f"{key} reading '{reading}': its raw text prints no number equal to {number}")
    judged = beside_its_unit or equal
    places = decimal_places(number)
    if all(decimal_places(printed_decimal(printed)) < places for printed in judged):
        raise Refusal(f"{key} reading '{reading}' has a finer resolution than its raw text, which prints {judged[0]}")


def printed_quantity(number: Decimal, factor: int, dimension: Dimension) -> Memory | Duration | Rate | None:
    """A printed number of a unit as its dimension's quantity at its last printed digit's resolution, None when that is finer than one base unit."""
    places = decimal_places(number)
    if dimension == "rate":
        return Rate(count=int(number.scaleb(places)), per_ns=factor * 10**places, resolution_count=1)
    resolution = Decimal(factor).scaleb(-places)
    if resolution < 1:
        return None
    value, step = int((number * factor).to_integral_value(ROUND_HALF_EVEN)), int(resolution.to_integral_value(ROUND_CEILING))
    return Memory(bytes=value, resolution_bytes=step) if dimension == "memory" else Duration(ns=value, resolution_ns=step)


def scaled_quantity(key: str, reading: str, number: Decimal, factor: int, dimension: Dimension) -> Quantity:
    """A number of a unit as its dimension's quantity, refused when its last printed digit is finer than one base unit."""
    quantity = printed_quantity(number, factor, dimension)
    if quantity is None:
        raise Refusal(f"{key} reading '{reading}' is finer than one {UNIT_SCALES[dimension].base_unit}")
    return quantity


@dataclass(frozen=True)
class PrintedReading:
    """A number its raw text prints on one line, read in one closed unit: what it comes to in base units, and the span its last digit covers."""

    line: int
    printed: str
    value: Fraction
    resolution: Fraction


def value_and_resolution(quantity: Memory | Duration | Rate) -> tuple[Fraction, Fraction]:
    """What a quantity comes to in base units, and the span its last digit covers."""
    match quantity:
        case Memory():
            return Fraction(quantity.bytes), Fraction(quantity.resolution_bytes)
        case Duration():
            return Fraction(quantity.ns), Fraction(quantity.resolution_ns)
        case Rate():
            return Fraction(quantity.count, quantity.per_ns), Fraction(quantity.resolution_count, quantity.per_ns)


def printed_reading(line: int, numeral: str, unit: str, dimension: Dimension) -> PrintedReading | None:
    """A numeral read in one closed unit, None when its last digit is finer than one base unit."""
    scale = UNIT_SCALES[dimension]
    quantity = printed_quantity(printed_decimal(numeral), scale.units[unit], dimension)
    if quantity is None:
        return None
    value, resolution = value_and_resolution(quantity)
    printed = numeral if CLOCK_SEPARATOR in numeral else f"{numeral}{scale.separator}{unit}"
    return PrintedReading(line, printed, value, resolution)


def instrument_readings(lines: Sequence[tuple[int, str]], metric: Metric) -> list[PrintedReading | None]:
    """The values a metric's instrument prints on its raw lines, in the unit its format fixes."""
    matches = [(number, raw_line, raw_line.pattern.search(line)) for number, line in lines for raw_line in metric.lines]
    return [printed_reading(number, match["number"], raw_line.unit, metric.dimension) for number, raw_line, match in matches if match]


def unit_beside_readings(lines: Sequence[tuple[int, str]], key: str, dimension: Dimension) -> list[PrintedReading | None]:
    """The numbers with a closed unit printed beside them, on the lines naming the metric's span or quantity when it has one."""
    name = metric_name(key)
    return [
        printed_reading(number, match["number"], match["unit"], dimension)
        for number, line in lines
        if not name or line_names_with_number(line, name)
        for match in UNITS_BESIDE_NUMBERS[dimension].finditer(line)
    ]


def printed_readings(raw: str, key: str) -> list[PrintedReading]:
    """Every value the raw text gives a metric: on the lines its instrument prints it on, or else beside a printed unit."""
    metric = registered_metric(key)
    lines = list(enumerate(raw.splitlines(), 1))
    readings = instrument_readings(lines, metric) if metric.lines else unit_beside_readings(lines, key, metric.dimension)
    return [reading for reading in readings if reading is not None]


def refuse_quantity_outside_raw(key: str, quantity: Quantity, raw: str) -> None:
    """Refuse a reading the raw text does not give, in its number, its unit and its line, or one finer than the resolution it is printed at."""
    if isinstance(quantity, UnitAmbiguous):
        refuse_text_outside_raw(key, quantity.unit_ambiguous, raw)
        return
    value, resolution = value_and_resolution(quantity)
    readings = printed_readings(raw, key)
    equal = [reading for reading in readings if reading.value == value]
    if not equal:
        raise Refusal(f"{key} reading {shown(quantity)} disagrees with its raw text, {what_raw_prints(key, readings)}")
    if all(reading.resolution > resolution for reading in equal):
        raise Refusal(f"{key} reading {shown(quantity)} has a finer resolution than its raw text, which prints {equal[0].printed} on line {equal[0].line}")


def what_raw_prints(key: str, readings: Sequence[PrintedReading]) -> str:
    """What a raw text gives a metric, as a refusal quotes it."""
    if readings:
        return "which prints " + "; ".join(f"{reading.printed} on line {reading.line}" for reading in readings)
    metric = registered_metric(key)
    if metric.lines:
        return f"which prints no line for it ({metric.instrument})"
    name = metric_name(key)
    return "which prints no number with its unit beside it" + (f" on a line naming '{name}'" if name else "")


def shown(scope: BaseModel) -> str:
    """A scope as one line of JSON, as a refusal quotes it."""
    return json.dumps(scope.model_dump(mode="json"), ensure_ascii=False)


def refuse_scope_drift(records: Sequence[Record[Quantity]]) -> None:
    """Refuse items measured on different machines or under different environments."""
    first = records[0]
    for position, record in enumerate(records[1:], 2):
        if record.machine != first.machine:
            raise Refusal(f"items 1 and {position} ran on different machines ({shown(first.machine)} vs {shown(record.machine)})")
        if record.env != first.env:
            raise Refusal(f"items 1 and {position} differ in env ({shown(first.env)} vs {shown(record.env)}); an entry varies only its design's variable")


def refuse_code_off_level(entry: Measurement, records: Sequence[Record[Quantity]]) -> None:
    """Refuse an item measured at code other than its level's."""
    for position, record in enumerate(records, 1):
        expected = level_code(entry.design, record.item.level)
        if record.code != expected:
            level = record.item.level or "fixed"
            raise Refusal(f"item {position} ran code {code_sha(record.code)}, but level '{level}' is {code_sha(expected)}")


def refuse_raw_outside_source(root: Path, transcription: Transcribed, raw: str) -> None:
    """Refuse a transcribed raw text whose lines its source does not hold near the cited line, while the source exists."""
    source, line = transcription.transcribed.source, transcription.transcribed.line
    file = resolved(root, source)
    if not file.is_file():
        return
    lines = file.read_text(encoding="utf-8").splitlines()
    window = "\n".join(lines[max(0, line - 1 - TRANSCRIPTION_WINDOW_LINES) : line + TRANSCRIPTION_WINDOW_LINES])
    for raw_line in (raw_line.strip() for raw_line in raw.splitlines()):
        if raw_line and raw_line not in window:
            raise Refusal(f"raw line '{raw_line}' is not in {source} within {TRANSCRIPTION_WINDOW_LINES} lines of line {line}")


def refuse_changed_file(root: Path, path: str, recorded_sha256: str) -> None:
    """Refuse a recorded hash its file no longer has, while the file exists."""
    file = resolved(root, path)
    if file.is_file() and sha256_of(file) != recorded_sha256:
        raise Refusal(f"{path} hashes {sha256_of(file)}, not the recorded {recorded_sha256}")


def refuse_unverifiable_record(root: Path, record: Record[Quantity]) -> None:
    """Refuse a record whose files present now disagree with it: its raw outside its source, or a hash its file lacks."""
    item = record.item
    refuse_changed_file(root, item.evidence.path, item.evidence.sha256)
    if isinstance(item.provenance, Transcribed):
        refuse_raw_outside_source(root, item.provenance, item.raw)
    if isinstance(item.provenance, Parsed):
        refuse_changed_file(root, item.provenance.parsed.log, item.provenance.parsed.log_sha256)
    if isinstance(record.code, DirtyCode):
        refuse_changed_file(root, record.code.dirty.diff_path, record.code.dirty.diff_sha256)


def refuse_missing_harness(root: Path, entry: Measurement) -> None:
    """Refuse an operation whose harness script does not exist."""
    if isinstance(entry.subject, Operation) and not resolved(root, entry.subject.operation.harness).is_file():
        raise Refusal(f"harness {entry.subject.operation.harness} is not a file")


def assembled_entry(spec: EntrySpec, records: Sequence[Record[Quantity]], entry_id: str, recorded: datetime) -> Measurement:
    """The measurement an entry spec and its records make, machine and environment lifted from the records."""
    refuse_scope_drift(records)
    fields = ENTRY_SPEC.dump_python(spec, mode="json", exclude={"items"})
    items = [MEASURED_RECORD.dump_python(record, mode="json", exclude_none=True)["item"] for record in records]
    machine_and_env = MEASURED_RECORD.dump_python(records[0], mode="json", include={"machine", "env"})
    data = {"kind": MEASUREMENT_KIND, "id": entry_id, "recorded": recorded.isoformat(), "by": Self_().render()}
    entry = validated(MEASUREMENT, data | fields | machine_and_env | {"items": items})
    refuse_code_off_level(entry, records)
    return entry


def record_measurement(environment: Environment, spec_path: str) -> tuple[str, str]:
    """Append one entry built from an entry spec to the measurement log, returning the log's path and the entry's id."""
    root = environment.store.root
    with environment.store.transaction() as act:
        schema = agreeing_schema(root)
        log_path = schema["kinds"][MEASUREMENT_KIND]["home"]
        log_text = (root / log_path).read_text(encoding="utf-8")
        earlier = logged_documents(log_text)
        spec = validated(ENTRY_SPEC, read_yaml(root, spec_path))
        records = [prefixed_item_record(root, position, item) for position, item in enumerate(spec.items, 1)]
        recorded = environment.now().replace(microsecond=0)
        entry = assembled_entry(spec, records, next_id((document.id for document in earlier), recorded), recorded)
        refuse_missing_harness(root, entry)
        problems = document_problems(entry, earlier, CodeHistory(root, project_of(schema).measured_cli))
        if problems:
            raise Refusal("; ".join(problems))
        document = canonical_text(entry)
        refuse_new_failures(environment, {log_path: log_text + document})
        act.append(log_path, document)
    return log_path, entry.id


def prefixed_item_record(root: Path, position: int, item: RunRecordPath | InlineRecord) -> Record[Quantity]:
    """An item's record, verified against the files present now, any refusal naming the item's position."""
    try:
        record = item_record(root, item)
        refuse_unverifiable_record(root, record)
    except Refusal as refusal:
        raise Refusal(f"item {position}: {refusal}") from refusal
    return record


def add_command(
    ctx: typer.Context,
    entry_spec: str = typer.Argument(..., help="Block-form YAML entry spec, a scratch file outside the committed records."),
    quiet: bool = quiet_option(),
) -> None:
    """Append one entry built from an entry spec to the measurement log."""
    with refusals_reported():
        log_path, entry_id = record_measurement(ctx.obj, entry_spec)
    announce(f"{log_path}: {entry_id}", quiet)
