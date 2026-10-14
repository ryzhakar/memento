# Record reads and writes, the record check included: see memento.just
mod memento

default: gate

# Commit non-record files (code, build files, .gitignore) by plain git, naming the paths alone: just code-commit "<type>: <subject>" <path>... The subject is one line of at most ten words; records go through `just memento commit`; nothing is pushed.
[positional-arguments]
code-commit message *paths:
    #!/usr/bin/env bash
    set -euo pipefail
    message="$1"; shift
    [ "$#" -gt 0 ] || { echo "code-commit: name at least one path" >&2; exit 1; }
    case "$message" in *$'\n'*) echo "code-commit: the subject is one line" >&2; exit 1 ;; esac
    [ "$(wc -w <<< "$message")" -le 10 ] || { echo "code-commit: the subject is at most ten words" >&2; exit 1; }
    cd "{{justfile_directory()}}"
    git add -- "$@"
    git commit -m "$message" -- "$@"

# The merge gate: the whole suite, then `just memento check` against .claude/memento.yaml.
gate:
    UV_FROZEN=1 uv run --no-sync python -m pytest tests/ -q
    just memento check
