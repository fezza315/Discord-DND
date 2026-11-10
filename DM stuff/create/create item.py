"""Add an item to tables/items.dt.

    python "create item.py" --id iron_sword --name "Iron Sword" --type weapon --slot hand --damage 1d8 --hit 75 --stat str
                            [--armor 0] [--durability 40] [--effects poison:3:1] [--description "..."]
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (ITEMS_DT, SLOTS, STATS, clean, load_effects, load_items, parse_dice,  # noqa: E402
                    parse_effect_refs, read_text, slug, write_text)

TYPES = ("weapon", "armor", "consumable", "misc")


def create_item(id: str, name: str, type: str = "weapon", slot: str = "-", damage: str = "-", hit: int = 70,
                stat: str = "str", armor: int = 0, durability: str = "-", effects: str = "-",
                description: str = "") -> str:
    iid = slug(id)
    if not iid:
        raise ValueError("an item needs an id (letters, numbers, underscores).")
    if iid in load_items():
        raise ValueError(f"item '{iid}' already exists.")
    type, slot, stat = type.lower(), (slot or "-").lower(), stat.lower()
    if type not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    if slot != "-" and slot not in SLOTS:
        raise ValueError(f"slot must be - or one of {', '.join(SLOTS)}")
    if stat != "-" and stat not in STATS:
        raise ValueError(f"stat must be - or one of {', '.join(STATS)}")
    if damage not in ("", "-"):
        parse_dice(damage)
    if str(durability) not in ("", "-") and not str(durability).isdigit():
        raise ValueError("durability must be a number or -")
    known = load_effects()
    for ref in parse_effect_refs(effects):
        if ref["id"] not in known:
            raise ValueError(f"unknown effect '{ref['id']}' (see tables/effects.dt)")
    row = " | ".join([iid, clean(name) or iid, type, slot, damage or "-", str(int(hit)), stat, str(int(armor)),
                      str(durability or "-"), effects or "-", clean(description)])
    text = read_text(ITEMS_DT)
    write_text(ITEMS_DT, text + ("" if text.endswith("\n") or not text else "\n") + row + "\n")
    return iid


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create item.py")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--type", default="weapon")
    ap.add_argument("--slot", default="-")
    ap.add_argument("--damage", default="-")
    ap.add_argument("--hit", type=int, default=70)
    ap.add_argument("--stat", default="str")
    ap.add_argument("--armor", type=int, default=0)
    ap.add_argument("--durability", default="-")
    ap.add_argument("--effects", default="-")
    ap.add_argument("--description", default="")
    a = ap.parse_args(argv)
    return "created item " + create_item(a.id, a.name, a.type, a.slot, a.damage, a.hit, a.stat, a.armor,
                                         a.durability, a.effects, a.description)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
