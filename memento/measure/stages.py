"""What git holds at a code state: whether a sha is a commit, whether a path exists there, and the commands its CLI module declares."""

import ast
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from memento.git import git_exit_status

COMMAND_DECORATOR = "command"
COMMAND_NAME_KEYWORD = "name"


def declared_command_name(function: ast.FunctionDef, decorator: ast.expr) -> str | None:
    """The command name a `@<app>.command(...)` decorator gives a function, or None when the decorator is another."""
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call else decorator
    if not (isinstance(target, ast.Attribute) and target.attr == COMMAND_DECORATOR):
        return None
    named = [keyword.value for keyword in call.keywords if keyword.arg == COMMAND_NAME_KEYWORD] if call else []
    given = named or (call.args[:1] if call else [])
    if given and isinstance(given[0], ast.Constant) and isinstance(given[0].value, str):
        return given[0].value
    return function.name.replace("_", "-")


def declared_commands(source: str) -> frozenset[str]:
    """Every command name a CLI module's source declares through `@<app>.command`."""
    functions = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)]
    names = (declared_command_name(function, decorator) for function in functions for decorator in function.decorator_list)
    return frozenset(name for name in names if name is not None)


@dataclass
class CodeHistory:
    """The git history beneath one root, read once per sha."""

    root: Path
    cli_module: str
    commands: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def cli_name(self) -> str:
        """The name the CLI module's package gives the project's commands."""
        return PurePosixPath(self.cli_module).parent.name

    def is_commit(self, sha: str) -> bool:
        """Whether git holds a commit at sha."""
        return git_exit_status(self.root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

    def holds_path(self, sha: str, path: str) -> bool:
        """Whether the commit at sha holds a file or directory at path."""
        return git_exit_status(self.root, "cat-file", "-e", f"{sha}:{path.rstrip('/')}").returncode == 0

    def stage_commands(self, sha: str) -> frozenset[str]:
        """The commands the CLI module at sha declares, none when it holds no CLI module."""
        if sha not in self.commands:
            shown = git_exit_status(self.root, "show", f"{sha}:{self.cli_module}")
            self.commands[sha] = declared_commands(shown.stdout.decode("utf-8")) if shown.returncode == 0 else frozenset()
        return self.commands[sha]
