"""Who's turn is it?

main.py runs `check()` before it lets anybody act. It returns
    (char id,   discord user id)   for a player's character
    (monster id, "DM")             for a DM creature (DM + creatures always go first)
    (None, None)                   when nobody can act

The turn order itself lives in state.json -> "turn".
"""
import pathlib
import random
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import controller_of, ent_get, load_bindings, load_char, load_state, mod, save_state, stat_total  # noqa: E402


def current_key(state: dict):
    t = state["turn"]
    order = t["order"]
    if not order:
        return None
    return order[min(max(t["index"], 0), len(order) - 1)]


def check(state: dict | None = None):
    """(entity id, controller) of whoever's turn it is."""
    state = state or load_state()
    key = current_key(state)
    if key is None:
        return None, None
    return key, controller_of(key)


def rebuild(state: dict) -> list[str]:
    """Recompute the order: DM creatures first, then bound living chars high -> low initiative."""
    t = state["turn"]
    cur = current_key(state)
    monsters = list(state["monsters"].keys())
    chars = []
    for cid in load_bindings():
        c = load_char(cid)
        if c and c["hp"] > 0:
            chars.append(cid)
    chars.sort(key=lambda k: (-t["initiative"].get(k, 0), len(k), k))
    t["order"] = monsters + chars
    if cur in t["order"]:
        t["index"] = t["order"].index(cur)
    else:
        t["index"] = min(max(t["index"], 0), max(len(t["order"]) - 1, 0))
    return t["order"]


def roll_initiative(state: dict, cid: str) -> tuple[int, int]:
    """d20 + dex modifier. Returns (total, raw d20)."""
    c = load_char(cid)
    d20 = random.randint(1, 20)
    total = d20 + (mod(stat_total(c, "dex")) if c else 0)
    state["turn"]["initiative"][str(cid)] = total
    return total, d20


def remove(state: dict, key: str) -> None:
    """Take an entity out of the order (death, unbinding) without skipping anyone."""
    t = state["turn"]
    if key in t["order"]:
        i = t["order"].index(key)
        t["order"].pop(i)
        if i <= t["index"]:
            t["index"] -= 1


def advance(state: dict):
    """Move to the next entity. Returns (entity id, controller)."""
    t = state["turn"]
    if not t["order"]:
        return None, None
    t["index"] += 1
    if t["index"] >= len(t["order"]):
        t["index"] = 0
        t["round"] += 1
    return check(state)


def order_text(state: dict) -> str:
    t = state["turn"]
    lines = []
    for n, key in enumerate(t["order"]):
        obj = ent_get(state, key)
        if not obj:
            continue
        who = "DM" if key.startswith("m") else f"<@{controller_of(key)}>"
        mark = "▶ " if n == t["index"] else "   "
        init = "" if key.startswith("m") else f" (init {t['initiative'].get(key, '?')})"
        lines.append(f"{mark}{n + 1}. {obj['name']} — {who}{init}")
    return "\n".join(lines) or "(nobody in the turn order yet)"


def describe_turn(state: dict) -> str:
    key, ctrl = check(state)
    if key is None:
        return "nobody's turn"
    obj = ent_get(state, key)
    if ctrl == "DM":
        return f"the DM's turn ({obj['name']})"
    return f"<@{ctrl}>'s turn ({obj['name']})"


def cli(argv: list[str]) -> str:
    state = load_state()
    if argv and argv[0] == "rebuild":
        rebuild(state)
        save_state(state)
    return f"current: {check(state)}\n{order_text(state)}"


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
