"""The measurement log: every performance reading of the pipeline, with its context."""

import typer

from memento.measure.add import add_command
from memento.measure.void import void_command

measurement_group = typer.Typer(
    no_args_is_help=True, help="The measurement log: add an entry, void one."
)
measurement_group.command(
    "add",
    help=(
        "Build one entry from an entry spec (question, subject, paths, data, design, items, supersedes; each item a "
        "'run_record: <file>' the run or log verb wrote, or a 'transcribed:' run or summary written inline with text "
        "readings such as '17.27 GB'), lift machine and env from the items, validate it, allocate its id m-YYYYMMDD-NNN, "
        "and append it to the measurement log. Reads .claude/memento.yaml, the log, the spec, its run records, and git "
        "at every code sha. Prints '<log>: <id>'. Refuses: a reading with no unit, a unit or number its raw text lacks, a "
        "number its raw text prints only beside other closed units (a unit printed apart from its number, as in a "
        "separate table column, is accepted), or one finer than the print beside its unit; a run record's reading its raw "
        "text does not give, in number or unit, or one finer than the number it matches (wall, user_cpu, sys_cpu, max_rss "
        "and footprint are read from their `time -l` or `time -v` lines, in the unit the format fixes; every other metric "
        "from a number with its closed unit printed beside it, on a line naming the span or quantity for the named ones); "
        "a transcribed raw text not within 40 lines of its source line; a hash its file no longer has; declared_solo with "
        "neighbours, declared_beside with none; runs out of order or overlapping, a run ending before it starts or after "
        "the entry is recorded; machine or env differing between items; an item's code differing from its level's; an "
        "unregistered metric, or one its run does not admit (max_rss or hwm_increase from a shared process, footprint "
        "under a wrapper argv[0], swap_growth or scratch_peak on a run not observed, sampled_rss_max whose raw text "
        "states no interval, span_wall or throughput whose raw text has no line naming its span or quantity with a "
        "number); a slept valid run; a level with no item, or an item off the design's levels; a run record file holding "
        "a transcription, or an inline run that is none; a missing operation harness; a stage the project's CLI at a "
        "code sha lacks; a path missing at a code sha; a sha that is no commit; a supersedes id not earlier in the log; a "
        "day with no id left; a worktree; a change adding a records FAIL."
    ),
)(add_command)
measurement_group.command(
    "void",
    help=(
        "Append a void to the measurement log: a later finding that an earlier entry (m-YYYYMMDD-NNN) or its k-th "
        "item (m-YYYYMMDD-NNN#k) does not stand, with its reason and its evidence. Reads .claude/memento.yaml, the log "
        "and the evidence. Prints '<log>: <id>'. Refuses: a target that is not an earlier measurement or an item it "
        "lacks; evidence that is neither path:line within an existing file nor a commit sha; a reason that is not one "
        "line; a worktree; a change adding a records FAIL."
    ),
)(void_command)
