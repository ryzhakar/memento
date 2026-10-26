# memento

Typed reads and writes of a project's records, checked against the schema in the project's `.claude/memento.yaml`.

Run it from any directory inside the project's git checkout:

    uvx --from git+https://github.com/ryzhakar/memento@v0.10.1 memento check

The skills that teach agents to keep records with it ship as a plugin from this repository, at the same version:

    /plugin marketplace add ryzhakar/memento
    /plugin install memento --marketplace ryzhakar/memento

`memento --help` lists the verbs. The project declares itself in `.claude/memento.yaml`: its kinds and their homes, and a top-level `project:` block holding the values memento cannot derive (ratification date, main branch, record space, code roots, history start file, measured CLI, commit type and subject word cap), and optionally `journal_corrections`: the owner-ruled commits the append-only history check skips, each a full `commit` sha and the `ruling` it rests on, an events path and minute that must resolve to an `owner | decision` line.

Develop: `uv sync && uv run python -m pytest tests -q`. Needs Python 3.12 or newer.

## Moving an older schema to the current shape

`memento check` refuses a schema of an older dialect with a line naming what is missing. Fix them by hand, running `memento check` after each step; `memento init` in a scratch repository writes a current schema to copy kinds from.

1. Add the top-level `project:` block (see above). `measured_cli` must name an existing file.
2. Rename an event kind that is not `events` (`trace` in gym) and every route that points at it, under `events:` and in the map.
3. Declare the kinds memento is built on: `measurement`, `spec`, `reference`, with a home, tier, load and verification each; create each home (`docs/measurements.yaml`, `docs/specs/`, `docs/reference/`). Kinds of your own (an opinion map, a subject) stay as they are, each with `home`, `tier`, `load` and `verification`.
4. Give the failure home the form `.../{date}/failures*.md`.
5. Give the map exactly one row per declared kind that has a home, a pattern row for a wildcard home such as `docs/subjects/*.md`, and rewrite a row whose kind or path changed.
6. List every other file under the record space as a map row, a declared product, or a retired path (a harness lock file goes in `.gitignore`).
7. Write a change-log pointer in a fact as `decided YYYY-MM-DD, see docs/architecture_log.md`, and pair each failure entry with a failure events line (`memento failure add` does both).

Checked on a copy of gym's schema: steps 1 to 3 turn the refusal into findings; steps 4 to 7 are what the remaining findings name, and I have not re-run that copy to zero. A schema in the 15-kind dialect with `baseline` (product-catalog's) already passes `memento check` at 0.10.0.
