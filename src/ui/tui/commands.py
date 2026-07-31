"""Command definitions and matching for the TUI.

Each command has a name (invoked as ``/name``), a short description shown
in the palette, and an action callback receiving the app instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Command:
    name: str
    description: str
    handler: callable  # (app, args: str) -> None
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _cmd_new(app, args: str) -> None:
    app._new_chat()


def _cmd_session(app, args: str) -> None:
    code = args.strip()
    if not code:
        app._append_error("Usage: /session <session-id>")
        return
    app._switch_session(code)


def get_commands() -> list[Command]:
    """All available commands."""
    return [
        Command("new", "Start a new chat", _cmd_new),
        Command("session", "Switch to a session: /session <id>", _cmd_session),
    ]


def match_commands(query: str) -> list[Command]:
    """Return commands matching the typed prefix (after the leading slash)."""
    commands = get_commands()
    if not query:
        return commands
    q = query.lower()
    matches = [c for c in commands if c.name.startswith(q) or any(a.startswith(q) for a in c.aliases)]
    return matches
