"""Measurement ids: `m-YYYYMMDD-NNN`, one past the day's highest, and the `#k` run targets beside them."""

from collections.abc import Iterable
from datetime import datetime

from memento import Refusal

ID_PREFIX = "m-"
DAY_FORMAT = "%Y%m%d"
SEQUENCE_DIGITS = 3
LAST_SEQUENCE = 10**SEQUENCE_DIGITS - 1
RUN_MARK = "#"


def next_id(logged_ids: Iterable[str], at: datetime) -> str:
    """The id one past the highest the log holds for the day of `at`, refusing past the day's last."""
    day = f"{ID_PREFIX}{at.strftime(DAY_FORMAT)}-"
    sequence = max((int(logged.removeprefix(day)) for logged in logged_ids if logged.startswith(day)), default=0) + 1
    if sequence > LAST_SEQUENCE:
        raise Refusal(f"the log holds {LAST_SEQUENCE} documents for {day}NNN; no id is left for that day")
    return f"{day}{sequence:0{SEQUENCE_DIGITS}d}"


def split_target(target: str) -> tuple[str, int | None]:
    """The entry id a target names and the run position after its `#`, or None when it names the whole entry."""
    entry, _, position = target.partition(RUN_MARK)
    return entry, int(position) if position else None
