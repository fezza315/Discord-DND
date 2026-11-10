"""Create a character: roll stats, apply origin + class, hand out the starting kit, save to "player <id>/".

Used by main.py (the /create-char flow) and runnable from the terminal:
    python "create char.py" --name Bram --origin dwarf --class fighter [--user 1234567890]
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (SLOTS, STATS, clean, load_items, load_other, load_script, load_state,  # noqa: E402
                    mod, next_char_id, roll_stat, save_char, save_state, stat_total, load_bindings)


def roll_all_stats() -> list[tuple[str, int, list[int]]]:
    """[(stat, value, the four d6)] - 4d6 drop lowest, one per stat."""
    out = []
    for s in STATS:
        v, rolls = roll_stat()
        out.append((s, v, rolls))
    return out


def validate(name: str, origin: str, cls: str) -> tuple[str, str, str]:
    other = load_other()
    name = clean(name)[:32]
    origin, cls = origin.strip().lower(), cls.strip().lower()
    if not name:
        raise ValueError("a character needs a name.")
    if origin not in other["race"]:
        raise ValueError(f"unknown origin '{origin}'. options: {', '.join(other['race']) or 'none yet'}")
    if cls not in other["class"]:
        raise ValueError(f"unknown class '{cls}'. options: {', '.join(other['class']) or 'none yet'}")
    return name, origin, cls


def build_character(name: str, origin: str, cls: str, rolled=None) -> dict:
    """Make the character dict (not saved yet). rolled = result of roll_all_stats()."""
    name, origin, cls = validate(name, origin, cls)
    other = load_other()
    race, klass = other["race"][origin], other["class"][cls]
    rolled = rolled or roll_all_stats()
    stats = {}
    for s, v, _ in rolled:
        stats[s] = [max(1, v + race["bonuses"].get(s, 0) + klass["bonuses"].get(s, 0)), 0]
    hp = max(1, klass["base_hp"] + mod(stats["con"][0]))
    c = {"id": next_char_id(), "name": name, "origin": origin, "class": cls, "nicname": name,
         "stats": stats, "max_hp": hp, "max_hp_org": hp, "hp": hp, "level": 1, "xp": 0,
         "relationships": {}, "buffs": [],
         "inv": {"equipped": {}, "bag_type": "backpack", "bag_max": 10, "bag": {}}}
    items = load_items()
    for iid in klass["start_items"]:
        it = items.get(iid)
        entry = {"item": iid, "level": 1, "dur": it["durability"] if it else None}
        if it and it["slot"] in SLOTS and it["slot"] not in c["inv"]["equipped"]:
            c["inv"]["equipped"][it["slot"]] = entry
        else:
            n = 1
            while str(n) in c["inv"]["bag"]:
                n += 1
            c["inv"]["bag"][str(n)] = entry
    return c


def create(name: str, origin: str, cls: str, user_id=None, rolled=None, state: dict | None = None) -> dict:
    """Build, save, bind to a user (optional) and roll initiative. Returns the saved char."""
    c = build_character(name, origin, cls, rolled)
    save_char(c)
    if user_id is not None:
        load_script("player data/bind char.py").bind(c["id"], user_id)
        own = state is None
        state = state or load_state()
        turn = load_script("player data/player turn.py")
        turn.roll_initiative(state, c["id"])
        turn.rebuild(state)
        state["role_sync"] = True
        if own:
            save_state(state)
    return c


def sheet_text(c: dict) -> str:
    items = load_items()
    other = load_other()
    race = other["race"].get(c["origin"], {}).get("name", c["origin"])
    klass = other["class"].get(c["class"], {}).get("name", c["class"])
    uid = load_bindings().get(c["id"])
    lines = [f"**{c['name']}** — {race} {klass}, level {c['level']}  (char #{c['id']}"
             + (f", played by <@{uid}>)" if uid else ", unbound)"),
             f"HP {c['hp']}/{c['max_hp']}   XP {c['xp']}"]
    lines.append("  ".join(f"{s.upper()} {stat_total(c, s)} ({mod(stat_total(c, s)):+d})" for s in STATS))

    def fmt(e):
        it = items.get(e["item"])
        if not it:
            return f"{e['item']} (unknown item)"
        bits = []
        if it["damage"]:
            bits.append(it["damage"] + (" heal" if it["type"] == "consumable" else ""))
        if it["type"] == "weapon":
            bits.append(f"{it['hit']}% hit")
        if it["armor"]:
            bits.append(f"armor {it['armor']}")
        if e.get("dur") is not None:
            bits.append(f"durability {e['dur']}")
        return f"{it['name']}" + (f" ({', '.join(bits)})" if bits else "")

    lines.append("**Equipped**")
    eq = c["inv"]["equipped"]
    lines += [f"  {slot}: {fmt(eq[slot])}" for slot in SLOTS if slot in eq] or ["  (nothing)"]
    bag = c["inv"]["bag"]
    lines.append(f"**Bag** ({len(bag)}/{c['inv'].get('bag_max', 10)})")
    lines += [f"  {k}: {fmt(bag[k])}" for k in sorted(bag, key=int)] or ["  (empty)"]
    if c["buffs"]:
        lines.append("**Effects:** " + ", ".join(f"{b['id']} L{b['level']}" for b in c["buffs"]))
    return "\n".join(lines)


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create char.py")
    ap.add_argument("--name", required=True)
    ap.add_argument("--origin", required=True)
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--user", help="discord user id to bind the new char to")
    a = ap.parse_args(argv)
    c = create(a.name, a.origin, a.cls, user_id=a.user)
    return f"created char #{c['id']}\n" + sheet_text(c)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
