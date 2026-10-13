# memento

Typed reads and writes of a project's records, checked against the schema in the project's `.claude/memento.yaml`.

Run it from any directory inside the project's git checkout:

    uvx --from git+https://github.com/ryzhakar/memento@v0.2.1 memento check

`memento --help` lists the verbs. The project declares itself in `.claude/memento.yaml`: its kinds and their homes, and a top-level `project:` block holding the values memento cannot derive (ratification date, main branch, record space, code roots, history start file, measured CLI, commit type and subject word cap).

Develop: `uv sync && uv run python -m pytest tests -q`. Needs Python 3.12 or newer.
