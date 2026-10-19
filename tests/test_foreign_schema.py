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


def test_a_check_bug_keyerror_is_not_taken_for_a_missing_schema_key() -> None:
    from memento.findings import Corpus, run_checks

    def buggy(corpus: Corpus):
        raise KeyError("a bug, not the schema")
        yield

    corpus = Corpus(root=Path("/nonexistent-root"), schema={}, tracked=[])
    try:
        run_checks(corpus, [buggy])
    except KeyError as raised:
        assert "a bug, not the schema" in str(raised)
    else:
        raise AssertionError("the KeyError was swallowed")


def test_a_file_not_found_outside_the_project_is_not_taken_for_a_missing_record() -> None:
    from memento.findings import Corpus, run_checks

    def missing_tool(corpus: Corpus):
        raise FileNotFoundError(2, "No such file", "git")
        yield

    corpus = Corpus(root=Path("/nonexistent-root"), schema={}, tracked=[])
    try:
        run_checks(corpus, [missing_tool])
    except FileNotFoundError:
        return
    raise AssertionError("the FileNotFoundError was swallowed")


def test_patdown_on_a_schema_without_the_events_kind_refuses_with_exit_one(tmp_path: Path) -> None:
    root = project_with(tmp_path, PLUGIN_DEFAULT.read_text().replace("  events:\n    home:", "  trace:\n    home:", 1))
    done = subprocess.run([sys.executable, "-m", "memento", "patdown"], cwd=root, capture_output=True, text=True)
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert done.stderr.startswith("refused:")
