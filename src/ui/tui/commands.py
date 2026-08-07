from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Command:
    name: str
    description: str
    handler: callable
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _cmd_new(app, args: str) -> None:
    app._new_chat()


def _cmd_session(app, args: str) -> None:
    code = args.strip()
    if not code:
        app._open_session_picker()
        return
    app._switch_session(code)


def _cmd_exit(app, args: str) -> None:
    app.exit()


def _cmd_provider(app, args: str) -> None:
    app._open_provider_picker()


def _cmd_model(app, args: str) -> None:
    app._open_model_picker()


def _cmd_effort(app, args: str) -> None:
    app._open_strength_picker()


def get_commands() -> list[Command]:
    return [
        Command("new", "Start a new chat", _cmd_new),
        Command("session", "Switch to a session: /session <id>", _cmd_session),
        Command("provider", "Switch API provider", _cmd_provider),
        Command("model", "Switch model", _cmd_model),
        Command("effort", "Set model reasoning strength", _cmd_effort),
        Command("exit", "Exit xagent", _cmd_exit),
    ]


def match_commands(query: str) -> list[Command]:
    commands = get_commands()
    if not query:
        return commands
    q = query.lower()
    matches = [c for c in commands if c.name.startswith(q) or any(a.startswith(q) for a in c.aliases)]
    return matches
