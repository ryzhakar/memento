# Tool and skills as one system

Scope: how the memento tool and the plugin in `plugin/` couple. Unbuilt; this is the design.

## Decisions

1. The skills name the tool's verbs and the tool is part of the plugin's contract: the coupling runs both ways, and the two release as one. Rests on: owner, 2026-10-14, offering the choice between one-way coupling and embracing it for consistent optimisation; the steward chose embracing it, because goal 1 optimises for agents' efficiency, every user of the plugin also runs the tool, and the merge of the two repositories removed the cost of drift. Open to the owner's override.
2. The skills keep the reasoning and the refusals and hand the mechanics to a verb: a step the tool performs reads "run `memento <verb>`" and says why the step exists, not how to compute it. Rests on: the measurement in the events of 2026-10-14 (init and pat-down cost about 1,200 words that `memento patdown` replaces; the clock stamps events, which removed the hand-written stamp errors).
3. The plugin ships a launcher `plugin/bin/memento` that runs the tool at the tag equal to `plugin/.claude-plugin/plugin.json`'s version (`uvx --from git+https://github.com/ryzhakar/memento@v<version> memento`), so plugin and tool never differ in version. Rests on: decision 1; the installed plugin holds only `plugin/`, so the launcher cannot read the tool from beside it.
4. The `SessionStart` hook runs `memento patdown` through the launcher when the project has `CLAUDE.md` and `.claude/memento.yaml`, and prints its output with the reminder. Rests on: decision 3.
5. The shipped default schema `plugin/config/default.yaml` passes `memento check`, and a test in this repository loads it. Rests on: the predecessor's finding that the shipped default fails the tool (session record of 2026-10-14, unverified: read the source before building).
6. Build order: skills `pat-down` and `event-capture` first (their verbs match exactly), then `span-closure`, `record-writing`, `record-promotion`, `corpus-reconciliation`, `schema-resolution`; the launcher and hook after the first two skills; a release tags plugin and tool together. Rests on: the verb map in the events of 2026-10-14.
