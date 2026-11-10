"""Pause / resume the turn order (the file name has a unicode fraction slash instead of a real "/").

While paused nobody can take their turn. Rooms that need a description, and events, pause the game by themselves;
/resume (or this script) lets it carry on.

    python "play⁄pause players turns.py" pause [reason...]
    python "play⁄pause players turns.py" resume
    python "play⁄pause players turns.py" status
"""
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import load_state, save_state, say  # noqa: E402


def is_paused(state: dict) -> bool:
    return bool(state["campaign"]["paused"])


def pause(state: dict, reason: str = "paused by the DM", announce: bool = True) -> str:
    state["campaign"]["paused"] = True
    state["campaign"]["pause_reason"] = reason
    state["role_sync"] = True
    if announce:
        say(state, "play", f"\u23f8\ufe0f **Turns paused** \u2014 {reason}")
    return f"paused ({reason})."


def resume(state: dict, announce: bool = True) -> str:
    was = state["campaign"]["paused"]
    state["campaign"]["paused"] = False
    state["campaign"]["pause_reason"] = None
    state["event"] = None
    state["awaiting_room_desc"] = None
    state["role_sync"] = True
    if announce and was:
        say(state, "play", "\u25b6\ufe0f **Turns resumed.**")
    return "resumed." if was else "wasn't paused."


def status(state: dict) -> str:
    c = state["campaign"]
    return f"paused: {c['pause_reason']}" if c["paused"] else "running" if c["started"] else "campaign not started"


def cli(argv: list[str]) -> str:
    state = load_state()
    cmd = argv[0] if argv else "status"
    if cmd == "pause":
        out = pause(state, " ".join(argv[1:]) or "paused by the DM")
    elif cmd == "resume":
        out = resume(state)
    else:
        return status(state)
    save_state(state)
    return out


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
