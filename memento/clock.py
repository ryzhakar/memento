"""The wall clock, read once per command at the composition root."""

from datetime import datetime

MINUTE_FORMAT = "%Y-%m-%dT%H:%M"


def now() -> datetime:
    """The current local time, carrying its UTC offset."""
    return datetime.now().astimezone()


def minute(at: datetime) -> str:
    """The minute a record line is stamped with: `YYYY-MM-DDTHH:MM`."""
    return at.strftime(MINUTE_FORMAT)
