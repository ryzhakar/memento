"""Without uv the plugin says so and sends the agent to the skills' steps."""

import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugin"
BARE_PATH = "/usr/bin:/bin"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "CLAUDE.md").write_text("# x\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "memento.yaml").write_text("kinds: {}\n")
    return tmp_path


def without_uv() -> None:
    if shutil.which("uv", path=BARE_PATH):
        pytest.skip("uv sits on the bare path of this machine")


def test_the_session_hook_names_the_by_hand_route_when_uv_is_absent(project: Path) -> None:
    without_uv()
    done = subprocess.run(["bash", str(PLUGIN / "hooks/session-start.sh")], capture_output=True, text=True, env={"PATH": BARE_PATH, "CLAUDE_PROJECT_DIR": str(project)})
    assert done.returncode == 0
    assert "uv is not installed" in done.stdout
    assert "memento patdown:" not in done.stdout


def test_the_launcher_refuses_cleanly_when_uv_is_absent() -> None:
    without_uv()
    done = subprocess.run(["bash", str(PLUGIN / "bin/memento"), "check"], capture_output=True, text=True, env={"PATH": BARE_PATH})
    assert done.returncode == 1
    assert "uv is not installed" in done.stderr
    assert "Traceback" not in done.stderr


def test_every_skill_naming_a_verb_frames_it_as_an_option_where_uv_is_available() -> None:
    for skill in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        if "`memento " in text:
            assert "`uv` is available" in text, f"{skill.parent.name} names a memento verb without framing it as an option where uv is available"


def test_every_verb_a_skill_names_exists_in_the_tool() -> None:
    import re

    import click
    import typer.main

    from memento.__main__ import app

    root = typer.main.get_command(app)
    for skill in sorted((PLUGIN / "skills").glob("*/SKILL.md")):
        for words in re.findall(r"`memento ([a-z][a-z-]*(?: [a-z][a-z-]*)?)", skill.read_text(encoding="utf-8")):
            first, *rest = words.split()
            assert first in root.commands, f"{skill.parent.name} names `memento {first}`, which the tool lacks"
            command = root.commands[first]
            if rest and isinstance(command, click.Group):
                assert rest[0] in command.commands, f"{skill.parent.name} names `memento {first} {rest[0]}`, which the tool lacks"
