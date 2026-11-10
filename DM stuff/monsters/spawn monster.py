"""Spawn a live monster from a template into the current session (state.json).

    python "spawn monster.py" <template> [--count 2] [--name "Big Goblin"] [--room 3]

Also turns a dead player's character into a hostile monster (/spawn player):
    python "spawn monster.py" --from-char <char id>
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (STATS, load_char, load_items, load_script, load_state, mod, save_state,  # noqa: E402
                    stat_total, dice_avg)


def _turn():
    return load_script("player data/player turn.py")


def _unique_name(state: dict, base: str) -> str:
    names = {m["name"] for m in state["monsters"].values()}
    if base not in names:
        return base
    n = 2
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


def _new_key(state: dict) -> str:
    key = f"m{state['next_monster']}"
    state["next_monster"] += 1
    return key


def _default_room(state: dict):
    return state["map"].get("start_room") if state["map"].get("spawned") else None


def spawn(state: dict, template: str, count: int = 1, name: str | None = None, room: int | None = None) -> list[str]:
    """Create `count` live monsters. Returns their ids (m1, m2...)."""
    tpl = load_script("DM stuff/monsters/create monster.py").load_template(template)
    keys = []
    for _ in range(max(1, min(int(count), 20))):
        key = _new_key(state)
        base = name if (name and count == 1) else (name or tpl["name"])
        state["monsters"][key] = {
            "id": key, "name": _unique_name(state, base), "template": tpl["template"],
            "hp": tpl["hp"], "max_hp": tpl["hp"], "damage": tpl["damage"], "hit": tpl["hit"],
            "evasion": tpl["evasion"], "armor": tpl["armor"], "stat_mod": tpl["stat_mod"],
            "level": tpl["level"], "effects": tpl["effects"], "boss": tpl["boss"],
            "buffs": [], "room": room if room is not None else _default_room(state),
            "description": tpl["description"]}
        keys.append(key)
    _turn().rebuild(state)
    return keys


def spawn_from_char(state: dict, cid: str) -> str:
    """A dead player comes back as a hostile monster the DM controls."""
    c = load_char(cid)
    if not c:
        raise ValueError(f"no character #{cid}")
    if c["hp"] > 0:
        raise ValueError(f"{c['name']} isn't dead - only dead players can be spawned as monsters.")
    items = load_items()
    best = ("1d4", 60)
    best_score = 0
    for e in list(c["inv"]["equipped"].values()) + list(c["inv"]["bag"].values()):
        it = items.get(e["item"])
        if it and it["type"] == "weapon" and it["damage"]:
            score = dice_avg(it["damage"]) * it["hit"]
            if score > best_score:
                best, best_score = (it["damage"], it["hit"]), score
    key = _new_key(state)
    hp = max(1, c["max_hp"] // 2)
    state["monsters"][key] = {
        "id": key, "name": _unique_name(state, f"{c['name']} (risen)"), "template": f"char{cid}",
        "hp": hp, "max_hp": hp, "damage": best[0], "hit": best[1], "evasion": mod(stat_total(c, "dex")) * 2,
        "armor": 0, "stat_mod": max(0, mod(stat_total(c, "str"))), "level": c["level"], "effects": [],
        "boss": False, "buffs": [], "room": state["positions"].get(str(cid)) or _default_room(state),
        "from_char": str(cid), "description": f"{c['name']} has returned... and is not friendly."}
    _turn().rebuild(state)
    return key


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="spawn monster.py")
    ap.add_argument("template", nargs="?")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--name")
    ap.add_argument("--room", type=int)
    ap.add_argument("--from-char")
    a = ap.parse_args(argv)
    state = load_state()
    if a.from_char:
        keys = [spawn_from_char(state, a.from_char)]
    elif a.template:
        keys = spawn(state, a.template, a.count, a.name, a.room)
    else:
        return "give a template name or --from-char"
    state["role_sync"] = True
    save_state(state)
    return "spawned: " + ", ".join(f"{k} ({state['monsters'][k]['name']})" for k in keys)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
