#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"

# Gate: no charter, no project for memento to orient in.
[ -f "$PROJECT_DIR/CLAUDE.md" ] || exit 0

cat "$SCRIPT_DIR/templates/orientation-reminder.txt"

# With a memento schema in the project, print the measure itself; a failure leaves the reminder alone.
if [ -f "$PROJECT_DIR/.claude/memento.yaml" ]; then
  echo
  if command -v uv >/dev/null 2>&1; then
    echo "memento patdown:"
    (cd "$PROJECT_DIR" && "$SCRIPT_DIR/../bin/memento" patdown 2>&1) || echo "patdown did not run; read the records yourself, as the pat-down skill says."
    echo
    echo "memento verbs:"
    (cd "$PROJECT_DIR" && "$SCRIPT_DIR/../bin/memento" help 2>&1) || echo "memento help did not run."
  else
    echo "uv is not installed, so the memento tool is unavailable: follow the steps each skill describes; read the records as the pat-down skill says."
  fi
fi
exit 0
