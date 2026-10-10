"""The closed metric registry: every reading's name, its dimension, its instrument, and when a run may carry it."""

import re
from dataclasses import dataclass
from typing import Literal

type Dimension = Literal["memory", "duration", "rate"]
type Admission = Literal["always", "fresh_process", "interpreter_argv0", "observed_run", "interval_in_raw", "named_line_in_raw"]

FAMILY_SEPARATOR = ":"
NAMED_METRIC = re.compile(rf"(?P<family>[a-z_]+){FAMILY_SEPARATOR}(?P<name>[a-z0-9_]+)")
NUMERAL = r"\d+(?:\.\d+)?"
WHOLE_NUMERAL = r"\d+"
CLOCK = r"(?:\d+:)?\d+:\d+(?:\.\d+)?"


@dataclass(frozen=True)
class RawLine:
    """A line an instrument prints a metric on: its pattern, whose `number` group holds the numeral, and the closed unit that numeral is in."""

    pattern: re.Pattern[str]
    unit: str


def numeral_before_label(label: str, numeral: str, unit: str) -> RawLine:
    """The BSD `time -l` line printing a numeral before its label."""
    return RawLine(re.compile(rf"(?P<number>{numeral})\s+{re.escape(label)}(?![A-Za-z])"), unit)


def numeral_after_label(label: str, numeral: str, unit: str) -> RawLine:
    """The GNU `time -v` line printing a numeral after its label."""
    return RawLine(re.compile(rf"{re.escape(label)}:\s*(?P<number>{numeral})"), unit)


@dataclass(frozen=True)
class Metric:
    """One registered metric: what it measures in, which instrument reads it, the run condition admitting it, and the raw lines the instrument prints it on."""

    dimension: Dimension
    instrument: str
    admission: Admission
    lines: tuple[RawLine, ...] = ()


EXACT_METRICS = {
    "wall": Metric(
        "duration",
        "`time -l` real or `time -v` elapsed, wall clock including sleep",
        "always",
        (
            numeral_before_label("real", NUMERAL, "s"),
            numeral_after_label("Elapsed (wall clock) time (h:mm:ss or m:ss)", CLOCK, "s"),
        ),
    ),
    "user_cpu": Metric(
        "duration",
        "`time -l` user or `time -v` user time, summed over reaped descendants",
        "always",
        (numeral_before_label("user", NUMERAL, "s"), numeral_after_label("User time (seconds)", NUMERAL, "s")),
    ),
    "sys_cpu": Metric(
        "duration",
        "`time -l` sys or `time -v` system time, summed over reaped descendants",
        "always",
        (numeral_before_label("sys", NUMERAL, "s"), numeral_after_label("System time (seconds)", NUMERAL, "s")),
    ),
    "max_rss": Metric(
        "memory",
        "`time -l` maximum resident set size in bytes or `time -v` in KiB, a max over reaped descendants",
        "fresh_process",
        (
            numeral_before_label("maximum resident set size", WHOLE_NUMERAL, "B"),
            numeral_after_label("Maximum resident set size (kbytes)", WHOLE_NUMERAL, "KiB"),
        ),
    ),
    "footprint": Metric(
        "memory",
        "`time -l` peak memory footprint of the direct child",
        "interpreter_argv0",
        (numeral_before_label("peak memory footprint", WHOLE_NUMERAL, "B"),),
    ),
    "sampled_rss_max": Metric("memory", "`ps -o rss` of one pid at the interval its raw text states", "interval_in_raw"),
    "swap_growth": Metric("memory", "`sysctl vm.swapusage` used, maximum minus start", "observed_run"),
    "scratch_peak": Metric("memory", "`du` of the run's private TMPDIR at every interval", "observed_run"),
}
NAMED_METRICS = {
    "artifact_bytes": Metric("memory", "`stat` size of the named file", "always"),
    "span_wall": Metric("duration", "the program's own timer line for the named span, present in raw", "named_line_in_raw"),
    "hwm_increase": Metric("memory", "the pipeline's increase of the process's high-water mark for the named span", "fresh_process"),
    "throughput": Metric("rate", "the program's own printed rate line for the named quantity, present in raw", "named_line_in_raw"),
}
REGISTERED = ", ".join([*EXACT_METRICS, *(f"{family}{FAMILY_SEPARATOR}<name>" for family in NAMED_METRICS)])


def metric_name(key: str) -> str:
    """The name a named metric's key carries after its family."""
    return key.partition(FAMILY_SEPARATOR)[2]


def registered_metric(key: str) -> Metric:
    """The registered metric a reading's key names, raising when the registry holds none."""
    named = NAMED_METRIC.fullmatch(key)
    metric = NAMED_METRICS.get(named["family"]) if named else EXACT_METRICS.get(key)
    if metric is None:
        raise ValueError(f"metric '{key}' is not registered ({REGISTERED})")
    return metric
