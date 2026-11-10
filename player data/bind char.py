"""Bind characters to Discord users, so users can be shuffled or hold several chars.

    python "bind char.py" list
    python "bind char.py" bind <char id> <user id>      (moves the char if it already belongs to someone)
    python "bind char.py" unbind <char id>
    python "bind char.py" shuffle                       (randomly swap who plays which char)

Bindings live in "player data/bindings.dt" ("char id | user id" per line).
"""
import pathlib
import random
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import list_char_ids, load_bindings, load_char, load_script, load_state, save_bindings, save_state  # noqa: E402


def owner_of(cid) -> str | None:
    return load_bindings().get(str(cid))


def chars_of(user_id) -> list[str]:
    return [c for c, u in load_bindings().items() if u == str(user_id)]


def bind(cid, user_id) -> str | None:
    """Returns the previous owner (or None)."""
    cid, user_id = str(cid), str(user_id)
    if not load_char(cid):
        raise ValueError(f"no character #{cid}")
    b = load_bindings()
    prev = b.get(cid)
    b[cid] = user_id
    save_bindings(b)
    return prev


def unbind(cid) -> str | None:
    b = load_bindings()
    prev = b.pop(str(cid), None)
    save_bindings(b)
    return prev


def shuffle() -> dict[str, str]:
    """Randomly redistribute the owners among the bound chars (everybody keeps the same number of chars)."""
    b = load_bindings()
    cids = list(b)
    owners = list(b.values())
    if len(set(owners)) > 1:
        for _ in range(20):
            random.shuffle(owners)
            if any(owners[i] != b[c] for i, c in enumerate(cids)):
                break
    new = dict(zip(cids, owners))
    save_bindings(new)
    return new


def sync_turns(state: dict) -> None:
    """Call after any binding change: new initiative for newly bound chars, rebuild the order."""
    turn = load_script("player data/player turn.py")
    for cid in load_bindings():
        if cid not in state["turn"]["initiative"]:
            turn.roll_initiative(state, cid)
    turn.rebuild(state)
    state["role_sync"] = True


def cli(argv: list[str]) -> str:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        b = load_bindings()
        rows = [f"#{cid} {load_char(cid)['name']} -> {b.get(cid, '(unbound)')}" for cid in list_char_ids()]
        return "\n".join(rows) or "(no characters)"
    if cmd == "bind" and len(argv) == 3:
        prev = bind(argv[1], argv[2])
    elif cmd == "unbind" and len(argv) == 2:
        prev = unbind(argv[1])
    elif cmd == "shuffle":
        shuffle()
        prev = None
    else:
        return __doc__
    state = load_state()
    sync_turns(state)
    save_state(state)
    return "done." + (f" (was {prev})" if prev else "")


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
