# Conventions

## Commit & merge

### commit-message
when    committing
do      one line, `type: subject`, at most ten words
never   a body, bullets or trailers
why     the message is an index entry
ground  owner ruling 2026-08-04

### rebase-only
when    integrating a branch
do      rebase onto the target, then fast-forward
never   merge commits
ground  owner ruling 2026-10-02

## Pipeline code

### explicit-seeds
when    drawing at random
do      take the seed from config
never   a module-level seed
why     a run must reproduce
ground  capabilities § Scoring
except  a test that seeds by loop index

## Measuring & quoting

### count-with-tools
when    reporting a count
do      derive it with the tool that computes it and state the selection rule
never   a count from prose or memory
why     a pretty-printer double-counted
ground  owner ruling 2026-08-18

## Owner

### plain-names
when    writing anything the owner reads
do      use canonical names
never   invented codenames
ground  owner ruling 2026-08-11
