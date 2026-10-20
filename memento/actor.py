"""Who an events line names as its author: the owner, this session, a dispatched subagent, a peer session, or a named tool."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from memento import Refusal

ACTOR_NAME = r"[\w.-]+"
ACTOR_FORMS = ("owner", "self", "agent:<name>", "tool:<name>")
ACTOR_SPELLINGS = "owner[:<identity>], self[:<model>], subagent:<name>, peer:<name>, agent:<name> or tool:<name>"
NAMED_ACTOR = re.compile(rf"(?P<role>agent|subagent|peer|tool):(?P<name>{ACTOR_NAME})")
OWNER_ACTOR = re.compile(r"owner:(?P<identity>[^|\n]*[^|\s\n])")
SELF_ACTOR = re.compile(rf"self:(?P<model>{ACTOR_NAME})")
FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True, regex_engine="python-re")

ActorName = Annotated[str, StringConstraints(pattern=rf"^{ACTOR_NAME}$")]


class Owner(BaseModel):
    """The human whose project this is."""

    model_config = FROZEN
    identity: str | None = None

    def render(self) -> str:
        """The actor as an events line spells it: the owner, or the owner's identity once the tool has stamped it."""
        return "owner" if self.identity is None else f"owner:{self.identity}"


class Self_(BaseModel):
    """The session writing the record."""

    model_config = FROZEN
    model: str | None = None

    def render(self) -> str:
        """The actor as an events line spells it: this session, or its model once the tool has stamped it."""
        return "self" if self.model is None else f"self:{self.model}"


class Agent(BaseModel):
    """A dispatched agent, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"agent:{self.name}"


class Subagent(BaseModel):
    """An agent this session dispatched, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"subagent:{self.name}"


class Peer(BaseModel):
    """Another session working beside this one, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"peer:{self.name}"


class Tool(BaseModel):
    """A tool, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"tool:{self.name}"


type Actor = Owner | Self_ | Agent | Subagent | Peer | Tool


def parse_actor(text: str) -> Actor:
    """The actor an events line's actor field spells, refusing any other spelling."""
    if text == "owner":
        return Owner()
    if text == "self":
        return Self_()
    owner = OWNER_ACTOR.fullmatch(text)
    if owner is not None:
        return Owner(identity=owner["identity"])
    stamped_self = SELF_ACTOR.fullmatch(text)
    if stamped_self is not None:
        return Self_(model=stamped_self["model"])
    named = NAMED_ACTOR.fullmatch(text)
    if named is None:
        raise Refusal(f"actor '{text}' is not {ACTOR_SPELLINGS}")
    roles = {"agent": Agent, "subagent": Subagent, "peer": Peer, "tool": Tool}
    return roles[named["role"]](name=named["name"])
