"""Work out a 'power' number for every bound, living character and write it to "total group power.st".

power = max hp + (sum of stats)/2 + level*5 + best weapon (avg damage x hit chance x 4) + armor*3

The same formula (power_of_monster) is used for monsters so /spawn can tell the DM how
hard a monster is compared with the party.

    python "calculate per player power.py"
"""
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (STATS, TOTAL_POWER_ST, dice_avg, load_bindings, load_char, load_items,  # noqa: E402
                    mod, stat_total, write_text)


def power_of_char(c: dict, items: dict | None = None) -> float:
    items = items or load_items()
    best, armor = 0.0, 0
    entries = list(c["inv"]["equipped"].values()) + list(c["inv"]["bag"].values())
    for e in entries:
        it = items.get(e["item"])
        if it and it["type"] == "weapon" and it["damage"]:
            smod = mod(stat_total(c, it["stat"])) if it["stat"] in STATS else 0
            best = max(best, (dice_avg(it["damage"]) + smod) * (it["hit"] + smod * 2) / 100)
    for e in c["inv"]["equipped"].values():
        it = items.get(e["item"])
        if it:
            armor += it["armor"]
    stats = sum(stat_total(c, s) for s in STATS)
    return round(c["max_hp"] + stats / 2 + c["level"] * 5 + best * 4 + armor * 3, 1)


def power_of_monster(m: dict) -> float:
    dmg = dice_avg(m["damage"]) + m.get("stat_mod", 0)
    return round(m["hp"] + dmg * m["hit"] / 100 * 4 + m.get("armor", 0) * 3 + m.get("level", 1) * 5
                 + m.get("evasion", 0) * 0.2, 1)


def calculate() -> dict[str, float]:
    """{char id: power} for bound, living chars. Also rewrites "total group power.st"."""
    items = load_items()
    out = {}
    for cid in load_bindings():
        c = load_char(cid)
        if c and c["hp"] > 0:
            out[cid] = power_of_char(c, items)
    lines = ["# char id | power   (written by calculate per player power.py)"]
    lines += [f"{cid} | {p}" for cid, p in out.items()]
    lines.append(f"total: {round(sum(out.values()), 1)}")
    write_text(TOTAL_POWER_ST, "\n".join(lines) + "\n")
    return out


def cli(argv: list[str]) -> str:
    out = calculate()
    return "\n".join(f"char #{k}: {v}" for k, v in out.items()) or "(no bound characters)"


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
