# Every read and write of the project's records: just memento <verb>, or a cross-kind verb.
repo := justfile_directory()
# memento is its own project: the recipes run this working tree through uvx as an editable install, so an edit to the tool takes effect on the next call. A plain `--from <path>` serves a cached build and misses edits.
memento_command := "uvx --from " + repo + " --with-editable " + repo + " memento"

# Check every record against .claude/memento.yaml: one line per finding, exit 1 on any FAIL.
[positional-arguments]
check *args:
    @cd "{{repo}}" && {{memento_command}} check "$@"

# Commit records after the check is green.
[positional-arguments]
commit *args:
    @cd "{{repo}}" && {{memento_command}} commit "$@"

# Print the baseline, the events tail, the found state and the anomalies.
[positional-arguments]
patdown *args:
    @cd "{{repo}}" && {{memento_command}} patdown "$@"

# Events: open a span, add a line to its events file, tail it.
[positional-arguments]
events *args:
    @cd "{{repo}}" && {{memento_command}} events "$@"

# The span's narrative: add a paragraph, close the span, print the newest close block.
[positional-arguments]
digest *args:
    @cd "{{repo}}" && {{memento_command}} digest "$@"

# Failures: add an entry with its events line.
[positional-arguments]
failure *args:
    @cd "{{repo}}" && {{memento_command}} failure "$@"

# The change log: add an entry, supersede one, set one of its fields, repair one, age the oldest out.
[positional-arguments]
change *args:
    @cd "{{repo}}" && {{memento_command}} change "$@"

# Conventions: add, replace, set a label of, or drop a rule.
[positional-arguments]
directive *args:
    @cd "{{repo}}" && {{memento_command}} directive "$@"

# Capabilities: replace one exact passage.
[positional-arguments]
fact *args:
    @cd "{{repo}}" && {{memento_command}} fact "$@"

# Specifications: add one, replace one exact passage, archive one.
[positional-arguments]
spec *args:
    @cd "{{repo}}" && {{memento_command}} spec "$@"

# The measurement log: add an entry, void one.
[positional-arguments]
measurement *args:
    @cd "{{repo}}" && {{memento_command}} measurement "$@"
