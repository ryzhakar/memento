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

## Prime directives

None stated by the owner.

## Pointers

- Record map: `.claude/memento-map.md`.
- On need: `docs/capabilities.md` (what the tool is and does).
