"""`memento patdown` measures the found state against the newest close block and writes nothing."""

from datetime import datetime
from pathlib import Path

from tests.conftest import HISTORY, commit_tree, git, replace_once, run_memento

OPEN_EVENTS_FILE = f"{HISTORY}/2026-10-01/events.md"
EVENING = datetime.fromisoformat("2026-10-01T19:00:00+02:00")


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file of the tree outside git's directory, by path, with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.parts}


def test_patdown_reports_what_moved_since_the_newest_close(record_tree: Path) -> None:
    (record_tree / "notes.txt").write_text("left over\n", encoding="utf-8")
    base = git(record_tree, "rev-parse", "--short", "HEAD").strip()
    run_memento(record_tree, "digest", "close", "the verbs stand", "none", "review")
    records = git(record_tree, "rev-parse", "--short", commit_tree(record_tree, "close the span")).strip()
    replace_once(record_tree, "docs/capabilities.md", "slices its input.", "slices its input in pieces.")
    pieces = git(record_tree, "rev-parse", "--short", commit_tree(record_tree, "edit capabilities")).strip()
    (record_tree / "draft.txt").write_text("new\n", encoding="utf-8")
    run_memento(record_tree, "events", "open", "the evening", now=EVENING)
    before = tree_bytes(record_tree)
    result = run_memento(record_tree, "patdown")
    found = f"{pieces} (dirty: {HISTORY}/2026-10-01/events.md, draft.txt)"
    assert (result.exit_code, result.stdout) == (
        0,
        "\n".join(
            [
                "baseline",
                "## Close — 2026-10-01T10:30",
                "",
                f"HEAD    {base} (dirty: notes.txt)",
                "state   the verbs stand",
                "open    none",
                "next    review",
                "",
                "tail 2026-10-01",
                "2026-10-01T19:00 | self | span-event | open; the evening",
                "",
                "found",
                f"HEAD    {found}",
                "span    2026-10-01 open since 2026-10-01T19:00",
                "",
                "anomalies",
                f"commit  {pieces} edit capabilities",
                f"commit  {records} close the span",
                f"dirty   + {HISTORY}/2026-10-01/events.md",
                "dirty   + draft.txt",
                "dirty   - notes.txt",
                "span    2026-10-01 open since 2026-10-01T19:00; the baseline closed it at 2026-10-01T10:30",
                "check   0 FAIL",
                "",
            ]
        ),
    )
    assert tree_bytes(record_tree) == before


def test_patdown_counts_the_failing_findings(record_tree: Path) -> None:
    replace_once(record_tree, "docs/results.md", "2026-10-01", "October")
    assert run_memento(record_tree, "patdown").stdout.endswith("check   1 FAIL\n")


def test_patdown_names_a_baseline_head_the_repository_does_not_hold(record_tree: Path) -> None:
    assert "commit  baseline HEAD abc1234 is not in this repository\n" in run_memento(record_tree, "patdown").stdout


def test_patdown_names_a_baseline_close_block_it_cannot_read(record_tree: Path) -> None:
    replace_once(record_tree, f"{HISTORY}/2026-09-30/session.md", "abc1234 (clean)", "abc1234, clean")
    stdout = run_memento(record_tree, "patdown").stdout
    assert "close   the baseline close block is unreadable: HEAD is not '<sha> (clean)' or '<sha> (dirty: <paths>)'\n" in stdout
    assert "commit  " not in stdout


def test_patdown_with_no_close_block_reports_against_no_baseline(record_tree: Path) -> None:
    (record_tree / f"{HISTORY}/2026-09-30/session.md").write_text("# 2026-09-30\n", encoding="utf-8")
    stdout = run_memento(record_tree, "patdown").stdout
    assert stdout.startswith("baseline\nnone\n\ntail 2026-10-01\n")
    assert f"dirty   + {HISTORY}/2026-09-30/session.md\nspan    2026-10-01 open since 2026-10-01T09:00\ncheck   " in stdout


def test_patdown_takes_the_later_written_of_close_blocks_stamped_one_minute_as_its_baseline(record_tree: Path) -> None:
    run_memento(record_tree, "digest", "close", "s", "o", "first")
    run_memento(record_tree, "events", "open", "again")
    run_memento(record_tree, "digest", "close", "s", "o", "second")
    assert "open    o\nnext    second\n\ntail 2026-10-01\n" in run_memento(record_tree, "patdown").stdout


def test_patdown_where_head_names_no_commit_is_refused(record_tree: Path) -> None:
    git(record_tree, "checkout", "-q", "--orphan", "unborn")
    result = run_memento(record_tree, "patdown")
    assert (result.exit_code, result.stdout, result.stderr) == (1, "", "refused: HEAD names no commit yet; git status has nothing to measure against\n")
