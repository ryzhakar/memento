"""`memento init` writes a schema and stubs that pass `memento check` at once, and writes over nothing."""

import subprocess
import sys
from pathlib import Path


def run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "memento", *arguments], cwd=root, capture_output=True, text=True)


def fresh_repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    return tmp_path


def commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "init"], cwd=root, check=True)


def test_a_fresh_project_passes_the_check_after_init(tmp_path: Path) -> None:
    root = fresh_repository(tmp_path)
    done = run(root, "init")
    assert done.returncode == 0, done.stderr
    commit_all(root)
    checked = run(root, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "records: 0 FAIL" in checked.stdout


def test_init_refuses_over_an_existing_path_and_writes_nothing(tmp_path: Path) -> None:
    root = fresh_repository(tmp_path)
    (root / "CLAUDE.md").write_text("mine\n")
    done = run(root, "init")
    assert done.returncode != 0
    assert "CLAUDE.md" in done.stderr
    assert not (root / ".claude").exists()
    assert (root / "CLAUDE.md").read_text() == "mine\n"


def test_init_under_quiet_writes_and_prints_nothing(tmp_path: Path) -> None:
    root = fresh_repository(tmp_path)
    done = run(root, "init", "--quiet")
    assert done.returncode == 0
    assert done.stdout == ""
    assert (root / ".claude" / "memento.yaml").is_file()
