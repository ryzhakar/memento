"""The model this session runs, read from the harness's own record of the session's replies."""

import json
from collections.abc import Mapping
from pathlib import Path

TAIL_BYTES = 262144
SYNTHETIC = "<synthetic>"


def last_model_in(lines: list[str]) -> str | None:
    """The model on the newest assistant entry among the transcript lines, skipping the synthetic one."""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model and model != SYNTHETIC:
            return model
    return None


def tail_lines(path: Path) -> list[str]:
    """The last lines of a file, read from its end, the first (possibly cut) line dropped when the file is longer than the window."""
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        handle.seek(max(0, size - TAIL_BYTES))
        text = handle.read().decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if size > TAIL_BYTES else lines


def detect_model(environ: Mapping[str, str]) -> str | None:
    """The model of this Claude Code session from its transcript, else the MEMENTO_MODEL variable, else None."""
    session = environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    config = environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if session and config:
        for transcript in sorted(Path(config).glob(f"projects/*/{session}.jsonl")):
            model = last_model_in(tail_lines(transcript))
            if model:
                return model
    return environ.get("MEMENTO_MODEL", "").strip() or None
