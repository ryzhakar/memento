"""Who an events line names as its author: the owner, this session, a named agent, or a named tool."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from memento import Refusal

ACTOR_NAME = r"[\w.-]+"
ACTOR_FORMS = ("owner", "self", "agent:<name>", "tool:<name>")
NAMED_ACTOR = re.compile(rf"(?P<role>agent|tool):(?P<name>{ACTOR_NAME})")
FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True, regex_engine="python-re")

ActorName = Annotated[str, StringConstraints(pattern=rf"^{ACTOR_NAME}$")]


class Owner(BaseModel):
    """The human whose project this is."""

    model_config = FROZEN

    def render(self) -> str:
        """The actor as an events line spells it."""
        return "owner"


class Self_(BaseModel):
    """The session writing the record."""

    model_config = FROZEN

    def render(self) -> str:
        """The actor as an events line spells it."""
        return "self"


class Agent(BaseModel):
    """A dispatched agent, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"agent:{self.name}"


class Tool(BaseModel):
    """A tool, by name."""

    model_config = FROZEN
    name: ActorName

    def render(self) -> str:
        """The actor as an events line spells it."""
        return f"tool:{self.name}"


type Actor = Owner | Self_ | Agent | Tool


def parse_actor(text: str) -> Actor:
    """The actor an events line's actor field spells, refusing any other spelling."""
    if text == "owner":
        return Owner()
    if text == "self":
        return Self_()
    named = NAMED_ACTOR.fullmatch(text)
    if named is None:
        raise Refusal(f"actor '{text}' is not owner, self, agent:<name> or tool:<name>")
    if named["role"] == "agent":
        return Agent(name=named["name"])
    return Tool(name=named["name"])
