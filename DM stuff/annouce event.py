"""Announce something to the whole table (posts in the play channel).

    python "annouce event.py" "The ground shakes..." [--title "Earthquake"] [--ping]

(the file name keeps the original spelling.)  --ping mentions every player.
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import load_bindings, load_state, save_state, say  # noqa: E402


def announce(state: dict, text: str, title: str | None = None, ping: bool = False, channel: str = "play") -> str:
    text = text.strip()
    if not text:
        raise ValueError("nothing to announce.")
    msg = (f"\U0001f4e2 **{title}**\n" if title else "\U0001f4e2 ") + text
    users = sorted(set(load_bindings().values())) if ping else []
    if users:
        msg += "\n" + " ".join(f"<@{u}>" for u in users)
    say(state, channel, msg, ping_users=users)
    return "announced."


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="annouce event.py")
    ap.add_argument("text", nargs="+")
    ap.add_argument("--title")
    ap.add_argument("--ping", action="store_true")
    a = ap.parse_args(argv)
    state = load_state()
    out = announce(state, " ".join(a.text), a.title, a.ping)
    save_state(state)
    return out


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
