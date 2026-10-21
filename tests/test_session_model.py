"""The session's model comes from the harness transcript first, the MEMENTO_MODEL variable second."""

import json
from pathlib import Path

from memento.session_model import TAIL_BYTES, detect_model, last_model_in


def assistant(model: str) -> str:
    return json.dumps({"type": "assistant", "message": {"model": model}})


def write_transcript(config: Path, session: str, lines: list[str]) -> None:
    folder = config / "projects" / "-some-project"
    folder.mkdir(parents=True)
    (folder / f"{session}.jsonl").write_text("\n".join(lines) + "\n")


def test_newest_assistant_entry_wins_and_synthetic_is_skipped():
    lines = [assistant("old-model"), "not json", json.dumps({"type": "user"}), assistant("new-model"), assistant("<synthetic>")]
    assert last_model_in(lines) == "new-model"


def test_transcript_beats_the_environment_variable(tmp_path):
    write_transcript(tmp_path, "abc", [assistant("claude-from-transcript")])
    environ = {"CLAUDE_CODE_SESSION_ID": "abc", "CLAUDE_CONFIG_DIR": str(tmp_path), "MEMENTO_MODEL": "claude-from-variable"}
    assert detect_model(environ) == "claude-from-transcript"


def test_variable_answers_when_no_transcript_exists(tmp_path):
    environ = {"CLAUDE_CODE_SESSION_ID": "missing", "CLAUDE_CONFIG_DIR": str(tmp_path), "MEMENTO_MODEL": "claude-from-variable"}
    assert detect_model(environ) == "claude-from-variable"


def test_nothing_known_gives_none():
    assert detect_model({}) is None


def test_a_transcript_longer_than_the_window_still_yields_its_newest_model(tmp_path):
    filler = json.dumps({"type": "user", "text": "x" * 1000})
    lines = [assistant("old-model")] + [filler] * (TAIL_BYTES // 1000 + 50) + [assistant("newest-model")]
    write_transcript(tmp_path, "big", lines)
    assert detect_model({"CLAUDE_CODE_SESSION_ID": "big", "CLAUDE_CONFIG_DIR": str(tmp_path)}) == "newest-model"
