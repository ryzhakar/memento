# memento

@docs/ground-truth.md
@docs/conventions.md
@docs/architecture_log.md
@.claude/memento.yaml

## Condition

Each session opens with no memory of the last. What is written here and in the records is all that carries over.

## Session start

1. Init: `memento:init`. Ground: the condition above.

## Goals

### Goal 1 · typed records for agents · standing · active

- object: memento gives agents typed reads and writes of a project's records, checked against the schema in the project's `.claude/memento.yaml`, and is tended toward efficiency and ergonomics for its users, who are agents.
- ground: owner, 2026-10-14: the README's opening sentence is the purpose, with efficiency and ergonomics for agents as the optimisation target; the project is perpetually maintained, taking agent feedback into account.
- constraints: it does not become a one-stop shop for other needs (owner, 2026-10-14).

### Goal 2 · tool and skills close · standing · active

- object: the memento tool and the memento plugin's skills (plugin/ in this repository) work as one system: each skill step a verb performs names that verb, the two release together, and neither contradicts what the other refuses.
- ground: owner, 2026-10-14: "bring the tool and the skill closer. figure out the synergy between them, etc. that's your goal", given after the plugin moved here from claude-skills with the owner's blessing.
- constraints: Goal 1's limit holds: it does not become a one-stop shop (owner, 2026-10-14).

## Prime directives

None stated by the owner.

## Pointers

- Record map: `.claude/memento-map.md`.
- On need: `docs/capabilities.md` (what the tool is and does).
