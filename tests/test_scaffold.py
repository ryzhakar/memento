"""`memento init` writes a schema and stubs that pass `memento check` at once, and writes over nothing."""

import subprocess
import sys

import yaml
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


def test_the_plugins_shipped_default_is_the_scaffold_schema() -> None:
    from memento.scaffold import shipped_default

    shipped = Path(__file__).resolve().parent.parent / "plugin" / "config" / "default.yaml"
    assert shipped.read_text() == shipped_default()


def test_check_in_a_repository_with_no_commit_refuses_without_a_traceback(tmp_path: Path) -> None:
    root = fresh_repository(tmp_path)
    run(root, "init", "--quiet")
    checked = run(root, "check")
    assert checked.returncode != 0
    assert "Traceback" not in checked.stderr
    assert "HEAD names no commit" in checked.stderr + checked.stdout


def test_the_tool_and_the_plugin_carry_one_version() -> None:
    import json
    import tomllib

    root = Path(__file__).resolve().parent.parent
    tool = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((root / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"]
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())["plugins"][0]["version"]
    assert tool == plugin == marketplace


def test_a_verb_in_a_project_with_no_schema_refuses_without_a_traceback(tmp_path: Path) -> None:
    root = fresh_repository(tmp_path)
    (root / "a.txt").write_text("a\n")
    commit_all(root)
    for verb in ("patdown", "check"):
        done = run(root, verb)
        assert done.returncode != 0
        assert "Traceback" not in done.stderr
        assert "memento init" in done.stderr + done.stdout


def test_the_scaffold_declares_none_of_the_five_kinds_only_the_first_project_used() -> None:
    from memento.scaffold import shipped_default

    declared = yaml.safe_load(shipped_default())["kinds"]
    assert not {"results", "string_recipes", "label_audit", "label_defects", "report"} & declared.keys()
