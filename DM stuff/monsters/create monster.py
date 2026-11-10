"""Create a monster template file in "DM stuff/monsters/" (monster<N>.dt, or boss<N>.dt for bosses).

    python "create monster.py" --name Goblin --hp 12 --damage 1d6 [--hit 60] [--evasion 5] [--armor 0]
                               [--stat-mod 0] [--level 1] [--effects poison:3:1] [--boss] [--description "..."]

Only the designated DM (or the bot terminal) may run this.
"""
import argparse
import pathlib
import re
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (MONSTER_DIR, clean, key_value_file, load_effects, parse_dice, parse_effect_refs,  # noqa: E402
                    to_int, write_key_value)

HEADER = "monster template.  name: value   (comments start with #)"


def next_file(boss: bool) -> pathlib.Path:
    prefix = "boss" if boss else "monster"
    nums = [int(m.group(1)) for p in MONSTER_DIR.glob(f"{prefix}*.dt") if (m := re.fullmatch(rf"{prefix}(\d+)\.dt", p.name))]
    return MONSTER_DIR / f"{prefix}{max(nums, default=0) + 1}.dt"


def create_template(name: str, hp: int, damage: str, hit: int = 60, evasion: int = 0, armor: int = 0,
                    stat_mod: int = 0, level: int = 1, effects: str = "-", boss: bool = False,
                    description: str = "") -> pathlib.Path:
    name = clean(name)
    if not name:
        raise ValueError("a monster needs a name.")
    if int(hp) < 1:
        raise ValueError("hp must be at least 1.")
    parse_dice(damage)
    known = load_effects()
    for ref in parse_effect_refs(effects):
        if ref["id"] not in known:
            raise ValueError(f"unknown effect '{ref['id']}' (see tables/effects.dt)")
    path = next_file(bool(boss))
    write_key_value(path, {
        "name": name, "hp": int(hp), "damage": damage.strip(), "hit": int(hit), "evasion": int(evasion),
        "armor": int(armor), "stat_mod": int(stat_mod), "level": int(level), "effects": effects.strip() or "-",
        "boss": "true" if boss else "false", "description": clean(description)}, HEADER)
    return path


def read_template(path: pathlib.Path) -> dict:
    d = key_value_file(path)
    return {"template": path.stem, "name": d.get("name", path.stem), "hp": to_int(d.get("hp"), 10),
            "damage": d.get("damage", "1d4"), "hit": to_int(d.get("hit"), 60), "evasion": to_int(d.get("evasion"), 0),
            "armor": to_int(d.get("armor"), 0), "stat_mod": to_int(d.get("stat_mod"), 0),
            "level": to_int(d.get("level"), 1), "effects": parse_effect_refs(d.get("effects", "")),
            "boss": d.get("boss", "false").lower() == "true", "description": d.get("description", "")}


def list_templates() -> dict[str, dict]:
    return {p.stem: read_template(p) for p in sorted(MONSTER_DIR.glob("*.dt"))}


def load_template(ref: str) -> dict:
    """Find a template by file stem (monster1) or by monster name."""
    ref = ref.strip().lower()
    tpls = list_templates()
    if ref in tpls:
        return tpls[ref]
    for t in tpls.values():
        if t["name"].lower() == ref:
            return t
    raise ValueError(f"no monster template '{ref}'. have: {', '.join(tpls) or 'none'}")


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create monster.py")
    ap.add_argument("--name", required=True)
    ap.add_argument("--hp", type=int, required=True)
    ap.add_argument("--damage", required=True)
    ap.add_argument("--hit", type=int, default=60)
    ap.add_argument("--evasion", type=int, default=0)
    ap.add_argument("--armor", type=int, default=0)
    ap.add_argument("--stat-mod", type=int, default=0)
    ap.add_argument("--level", type=int, default=1)
    ap.add_argument("--effects", default="-")
    ap.add_argument("--boss", action="store_true")
    ap.add_argument("--description", default="")
    a = ap.parse_args(argv)
    p = create_template(a.name, a.hp, a.damage, a.hit, a.evasion, a.armor, a.stat_mod, a.level, a.effects, a.boss, a.description)
    return f"created {p.name}"


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
