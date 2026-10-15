"""A schema in a foreign shape fails the check with findings; it never crashes it."""

import subprocess
import sys
from pathlib import Path

PLUGIN_DEFAULT = Path(__file__).resolve().parent.parent / "plugin" / "config" / "default.yaml"


def project_with(tmp_path: Path, schema_text: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "memento.yaml").write_text(schema_text)
    (tmp_path / "CLAUDE.md").write_text("# x\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_a_foreign_schema_is_refused_or_failed_without_a_traceback(tmp_path: Path) -> None:
    root = project_with(tmp_path, PLUGIN_DEFAULT.read_text())
    done = subprocess.run([sys.executable, "-m", "memento", "check"], cwd=root, capture_output=True, text=True)
    assert done.returncode != 0
    assert "Traceback" not in done.stderr
    assert "project:" in done.stderr + done.stdout or "FAIL" in done.stdout
