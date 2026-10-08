"""Refusing an in-place change whose candidate records would fail a check the current records pass."""

import re
from collections.abc import Iterable, Mapping

from memento import Environment, Refusal
from memento.findings import FAIL, Finding, run_checks
from memento.schema import load_corpus

LINE_SUFFIX = re.compile(r":\d+$")


def failure_identities(findings: Iterable[Finding]) -> list[tuple[str, str]]:
    """Each FAIL as its path and message, its line number dropped because edits shift lines."""
    return [(LINE_SUFFIX.sub("", finding.where), finding.message) for finding in findings if finding.level == FAIL]


def refuse_new_failures(environment: Environment, changes: Mapping[str, str | None]) -> None:
    """Refuse changes whose candidate corpus holds a FAIL the current corpus lacks, naming each new one."""
    current = load_corpus(environment.store.root)
    present = set(failure_identities(run_checks(current, environment.checks)))
    candidate = failure_identities(run_checks(current.substituted(changes), environment.checks))
    added = list(dict.fromkeys(identity for identity in candidate if identity not in present))
    if added:
        listed = "; ".join(f"{path}  {message}" for path, message in added)
        raise Refusal(f"the change adds {len(added)} FAIL: {listed}")
