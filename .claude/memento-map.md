# memento map

| path | kind | load | purpose |
|---|---|---|---|
| CLAUDE.md | charter | self | goals, session start, prime directives, pointers |
| docs/ground-truth.md | frame | self | the problem as given |
| docs/conventions.md | directive | self | how to work: rules with grounds |
| docs/architecture_log.md | change | self | how the tool's core changed |
| .claude/memento.yaml | schema | self | record kinds, homes, formats, admission |
| .claude/memento-map.md | map | start | index of records |
| docs/orchestration_log/history/{date}/events*.md | events | start (tail) | one line per event |
| docs/orchestration_log/history/{date}/session*.md | digest | start (close block) | a span's narrative and close block |
| docs/capabilities.md | fact | need | what the tool is and does, dated |
| docs/results.md | results | need | unused: no contenders; memento requires the table |
| docs/measurements.yaml | measurement | need | every performance reading, with its context |
| docs/specs/ | spec | need | current specifications of unbuilt work |
| docs/user_deferred_items.md | deferral | need | the owner's postponed work |
| docs/string_comparison_recipes.md | string_recipes | need | unused: no string comparison; memento requires the file |
| docs/label-audit-methodology.md | label_audit | need | unused: no labels; memento requires the file |
| docs/label-data-defects.md | label_defects | need | unused: no labels; memento requires the file |
| docs/orchestration_log/history/{date}/failures*.md | failure | latent | diagnosed failures |
| docs/reference/ | reference | latent | upstream specifications as the owner supplied them |
| docs/orchestration_log/history/{date}/ | report | latent | nothing stood at ratification; closed to new files |
| docs/orchestration_log/archive/ | archived | latent | out of force, kept |
| docs/orchestration_log/recon/{date}/ | scratch | latent | drafts, claims, worker returns |
