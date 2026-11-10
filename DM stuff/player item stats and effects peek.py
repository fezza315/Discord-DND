"""DM peek: a character's items (with their real stats) and every effect on them, and what those effects do.

    python "player item stats and effects peek.py" <char id | monster id like m1>
"""
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import STATS, ent_get, is_monster, load_effects, load_items, load_state, mod, stat_total  # noqa: E402


def _item_line(e: dict, items: dict) -> str:
    it = items.get(e["item"])
    if not it:
        return f"{e['item']} (not in tables/items.dt)"
    bits = [it["type"]]
    if it["damage"]:
        bits.append(("heals " if it["type"] == "consumable" else "dmg ") + it["damage"])
    if it["type"] == "weapon":
        bits.append(f"hit {it['hit']}% ({it['stat']})")
    if it["armor"]:
        bits.append(f"armor {it['armor']}")
    if e.get("dur") is not None:
        bits.append(f"durability {e['dur']}/{it['durability'] if it['durability'] is not None else '?'}")
    if e.get("level", 1) != 1:
        bits.append(f"level {e['level']}")
    if it["effects"]:
        bits.append("effects " + ", ".join(f"{r['id']}({r['turns']}t, L{r['level']})" for r in it["effects"]))
    return f"{it['name']} [{e['item']}] \u2014 " + "; ".join(bits)


def peek(state: dict, key: str) -> str:
    obj = ent_get(state, key)
    if not obj:
        raise ValueError(f"nothing with id '{key}'.")
    items, effects = load_items(), load_effects()
    lines = [f"**{obj['name']}**  HP {obj['hp']}/{obj['max_hp']}"]
    if is_monster(key):
        lines.append(f"dmg {obj['damage']}  hit {obj['hit']}%  evasion {obj['evasion']}  armor {obj['armor']}  "
                     f"stat mod {obj['stat_mod']}  level {obj['level']}  room {obj.get('room')}")
        if obj.get("effects"):
            lines.append("on-hit effects: " + ", ".join(f"{r['id']}({r['turns']}t)" for r in obj["effects"]))
    else:
        lines.append("  ".join(f"{s.upper()} {stat_total(obj, s)} ({mod(stat_total(obj, s)):+d})" for s in STATS))
        lines.append("**Equipped**")
        lines += [f"  {slot}: {_item_line(e, items)}" for slot, e in obj["inv"]["equipped"].items()] or ["  (nothing)"]
        lines.append("**Bag**")
        lines += [f"  {k}: {_item_line(e, items)}" for k, e in sorted(obj["inv"]["bag"].items(), key=lambda kv: int(kv[0]))] or ["  (empty)"]
    lines.append("**Active effects**")
    if not obj.get("buffs"):
        lines.append("  (none)")
    for b in obj.get("buffs", []):
        dur = "permanent" if b["turns"] < 0 else f"{b['turns']} turns left"
        lines.append(f"  {b['id']} L{b['level']} \u2014 {dur}")
        e = effects.get(b["id"])
        if e:
            lines.append("```\n" + "\n".join(l for l in e["source"].splitlines() if l.strip())[:600] + "\n```")
        else:
            lines.append("  (no such effect in tables/effects.dt)")
    return "\n".join(lines)


def cli(argv: list[str]) -> str:
    if not argv:
        return __doc__
    return peek(load_state(), argv[0])


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
