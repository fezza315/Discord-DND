"""Shared helpers for the DND discord bot (lives in the main.py root).

Everything that touches the plain-text data files (.dt / .st / .pt) or the
runtime state goes through here, so the scripts in "DM stuff" and
"player data" stay small.

File names in this project contain spaces and the unicode FRACTION SLASH
(U+2044) where a real "/" would have been. Python can't `import` such files,
so scripts are loaded by path with `load_script()`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FSLASH = "⁄"  # looks like "/" but is legal in a file name
TABLES = ROOT / "tables"
PLAYER_DATA = ROOT / "player data"
DM_STUFF = ROOT / "DM stuff"
ROOMS_DIR = DM_STUFF / "rooms"
MONSTER_DIR = DM_STUFF / "monsters"
STATE_FILE = ROOT / "state.json"
BINDINGS_FILE = PLAYER_DATA / "bindings.dt"

ITEMS_DT = TABLES / "items.dt"
OTHER_DT = TABLES / "other.dt"
EFFECTS_DT = TABLES / "effects.dt"
ROOMS_DT = TABLES / "rooms.dt"
PATH_PT = ROOMS_DIR / "path.pt"
IMPORTANT_DT = ROOMS_DIR / "important room.dt"
TOTAL_POWER_ST = PLAYER_DATA / "total group power.st"
AVG_POWER_ST = PLAYER_DATA / "avg group power.st"
BUFFS_FILE = f"buffs{FSLASH}debuffs.st"

STATS = ("str", "dex", "con", "int", "wis", "cha")
SLOTS = ("hand", "offhand", "head", "body", "back", "feet")

# DM-toggleable optional fields (the ones marked * in "player data/how to.txt")
DEFAULT_SETTINGS = {
    "item_level": False,       # {item level*}   weapons gain +1 damage per level above 1
    "item_durability": True,   # {item durability*}  items wear out and break
    "bag_type": False,         # {bag type*}     bag is named (backpack, pouch...)
    "bag_max_slots": True,     # {max slots*}    bag capacity is enforced
}


# --------------------------------------------------------------------------- #
# script loading
# --------------------------------------------------------------------------- #
_script_cache: dict[str, object] = {}


def load_script(rel_path: str):
    """Load a .py file by its path relative to ROOT (spaces / odd chars are fine)."""
    if rel_path in _script_cache:
        return _script_cache[rel_path]
    path = ROOT / rel_path
    if not path.exists():
        raise FileNotFoundError(f"script not found: {rel_path}")
    name = "script_" + re.sub(r"\W+", "_", rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _script_cache[rel_path] = mod
    return mod


# --------------------------------------------------------------------------- #
# text-file helpers
# --------------------------------------------------------------------------- #
def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def data_lines(path: Path) -> list[str]:
    """Non-empty, non-comment lines of a data file."""
    out = []
    for raw in read_text(path).splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def cols(line: str) -> list[str]:
    return [c.strip() for c in line.split("|")]


def opt(v):
    """'-' / '' / 'none' -> None"""
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in ("", "-", "none", "null") else v


def clean(v) -> str:
    """Make a value safe to store in a '|' separated row."""
    return re.sub(r"\s+", " ", str(v).replace("|", "/")).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(s).strip().lower()).strip("_")


def to_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def key_value_file(path: Path) -> dict[str, str]:
    """Parse 'key: value' files (monster templates, room data)."""
    out = {}
    for line in data_lines(path):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def write_key_value(path: Path, data: dict, header: str = "") -> None:
    lines = [f"# {h}" for h in header.splitlines()] if header else []
    for k, v in data.items():
        lines.append(f"{k}: {v}")
    write_text(path, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# dice
# --------------------------------------------------------------------------- #
_DICE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.I)


def parse_dice(expr) -> tuple[int, int, int]:
    expr = str(expr).strip()
    if re.fullmatch(r"-?\d+", expr):
        return 0, 0, int(expr)
    m = _DICE.match(expr)
    if not m:
        raise ValueError(f"bad dice expression: {expr!r} (use NdM, NdM+K or a number)")
    n, sides = int(m[1] or 1), int(m[2])
    if not (1 <= n <= 100 and 2 <= sides <= 1000):
        raise ValueError("dice out of range")
    return n, sides, int((m[3] or "0").replace(" ", ""))


def roll(expr) -> tuple[int, str]:
    n, sides, bonus = parse_dice(expr)
    if n == 0:
        return bonus, str(bonus)
    rolls = [random.randint(1, sides) for _ in range(n)]
    total = sum(rolls) + bonus
    detail = f"{'+'.join(map(str, rolls))}" + (f"{bonus:+d}" if bonus else "")
    return total, detail


def dice_avg(expr) -> float:
    n, sides, bonus = parse_dice(expr)
    return bonus if n == 0 else n * (sides + 1) / 2 + bonus


def dice_max(expr) -> int:
    n, sides, bonus = parse_dice(expr)
    return bonus if n == 0 else n * sides + bonus


def roll_stat() -> tuple[int, list[int]]:
    """4d6, drop the lowest."""
    rolls = [random.randint(1, 6) for _ in range(4)]
    return sum(rolls) - min(rolls), rolls


def mod(score: int) -> int:
    return (int(score) - 10) // 2


# --------------------------------------------------------------------------- #
# runtime state (state.json)
# --------------------------------------------------------------------------- #
def default_state() -> dict:
    return {
        "setup": {"configured": False, "dm_id": None, "channels": {}, "roles": {}},
        "campaign": {
            "name": "Untitled campaign",
            "started": False,
            "paused": False,
            "pause_reason": None,
            "settings": dict(DEFAULT_SETTINGS),
        },
        "turn": {"order": [], "index": 0, "round": 1, "initiative": {}},
        "monsters": {},
        "next_monster": 1,
        "positions": {},
        "map": {"loaded": False, "spawned": False, "start_room": None},
        "pending": [],
        "awaiting_room_desc": None,
        "pending_advance": False,
        "event": None,
        "join_tokens": [],
        "outbox": [],
        "role_sync": False,
    }


def _merge(default, loaded):
    if isinstance(default, dict) and isinstance(loaded, dict):
        out = dict(loaded)
        for k, v in default.items():
            out[k] = _merge(v, loaded[k]) if k in loaded else v
        return out
    return loaded


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return _merge(default_state(), json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            os.replace(STATE_FILE, STATE_FILE.with_name("state.json.corrupt"))
    return default_state()


def save_state(state: dict) -> None:
    write_text(STATE_FILE, json.dumps(state, indent=2))


def say(state: dict, channel: str, content: str = "", *, file: str | None = None,
        ping_users: list | None = None) -> None:
    """Queue a message; the bot posts the outbox every couple of seconds.
    channel is one of: overview char_sheets mapping event_planning play apply_to_play map_view"""
    state["outbox"].append({"channel": channel, "content": content, "file": file,
                            "ping_users": [str(u) for u in (ping_users or [])]})


def settings(state: dict) -> dict:
    return {**DEFAULT_SETTINGS, **state["campaign"].get("settings", {})}


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
ITEM_FIELDS = ("id", "name", "type", "slot", "damage", "hit", "stat", "armor",
               "durability", "effects", "description")


def load_items() -> dict[str, dict]:
    items = {}
    for line in data_lines(ITEMS_DT):
        c = cols(line) + [""] * len(ITEM_FIELDS)
        it = dict(zip(ITEM_FIELDS, c))
        it["id"] = it["id"].lower()
        it["type"] = (it["type"] or "misc").lower()
        it["slot"] = opt(it["slot"])
        it["damage"] = opt(it["damage"])
        it["hit"] = to_int(it["hit"], 70)
        it["stat"] = (opt(it["stat"]) or "str").lower()
        it["armor"] = to_int(it["armor"], 0)
        it["durability"] = to_int(it["durability"])
        it["effects"] = parse_effect_refs(it["effects"])
        items[it["id"]] = it
    return items


def parse_effect_refs(text) -> list[dict]:
    """'poison:3, weakened:2:2'  ->  [{'id','turns','level'}]  (id:turns:level)"""
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part or part == "-":
            continue
        bits = [b.strip() for b in part.split(":")]
        out.append({"id": bits[0].lower(),
                    "turns": to_int(bits[1], 3) if len(bits) > 1 else 3,
                    "level": to_int(bits[2], 1) if len(bits) > 2 else 1})
    return out


def load_other() -> dict[str, dict[str, dict]]:
    """tables/other.dt  ->  {'race': {id: {...}}, 'class': {id: {...}}}"""
    out = {"race": {}, "class": {}}
    for line in data_lines(OTHER_DT):
        c = cols(line) + [""] * 7
        kind = c[0].lower()
        if kind == "race":
            out["race"][c[1].lower()] = {"id": c[1].lower(), "name": c[2], "description": c[3],
                                         "bonuses": parse_bonuses(c[4])}
        elif kind == "class":
            out["class"][c[1].lower()] = {"id": c[1].lower(), "name": c[2], "description": c[3],
                                          "bonuses": parse_bonuses(c[4]),
                                          "base_hp": to_int(c[5], 10),
                                          "start_items": [x.strip().lower() for x in c[6].split(",") if x.strip() and x.strip() != "-"]}
    return out


def parse_bonuses(text) -> dict[str, int]:
    out = {}
    for part in str(text or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            k = k.strip().lower()
            if k in STATS and to_int(v) is not None:
                out[k] = to_int(v)
    return out


def load_rooms() -> dict[int, dict]:
    """tables/rooms.dt:  id | x1xy1 - x2xy2 | marked | kind | description"""
    rooms = {}
    for line in data_lines(ROOMS_DT):
        c = cols(line) + [""] * 5
        rid = to_int(c[0])
        if rid is None:
            continue
        m = re.match(r"(\d+)x(\d+)\s*-\s*(\d+)x(\d+)", c[1])
        rooms[rid] = {"id": rid,
                      "bounds": tuple(int(g) for g in m.groups()) if m else None,
                      "marked": c[2].lower() == "true",
                      "kind": (c[3] or "room").lower(),
                      "description": c[4]}
    return rooms


ROOMS_HEADER = (
    "# rooms table. one row per room / hallway / door found on the map.\n"
    "# id | x1xy1 - x2xy2 (pixels on the uploaded map) | marked (discovered by players) | kind (room/hall/door) | description\n"
    "# 1234 | 128x245 - 247x510 | true | room | exsample row, ignored\n"
    "# real rooms below\n"
)


def save_rooms(rooms: dict[int, dict]) -> None:
    lines = [ROOMS_HEADER.rstrip("\n")]
    for rid in sorted(rooms):
        r = rooms[rid]
        b = r["bounds"]
        bounds = f"{b[0]}x{b[1]} - {b[2]}x{b[3]}" if b else "-"
        lines.append(f"{rid} | {bounds} | {'true' if r['marked'] else 'false'} | {r['kind']} | {clean(r.get('description', ''))}")
    write_text(ROOMS_DT, "\n".join(lines) + "\n")


def load_effects() -> dict[str, dict]:
    from effects_engine import parse_effects
    return parse_effects(read_text(EFFECTS_DT))


# --------------------------------------------------------------------------- #
# characters   ("player data/player <id>/...")
# --------------------------------------------------------------------------- #
def char_dir(cid) -> Path:
    return PLAYER_DATA / f"player {cid}"


def list_char_ids() -> list[str]:
    ids = []
    for p in PLAYER_DATA.glob("player *"):
        if p.is_dir() and (p / "stats.dt").exists():
            ids.append(p.name.split(" ", 1)[1])
    return sorted(ids, key=lambda x: (len(x), x))


def next_char_id() -> str:
    nums = [int(i) for i in list_char_ids() if i.isdigit()]
    return str(max(nums, default=0) + 1)


def _parse_stat(v: str) -> tuple[int, int]:
    if "+" in v[1:] or "-" in v[1:]:
        m = re.match(r"^\s*(-?\d+)\s*([+-])\s*(\d+)\s*$", v)
        if m:
            return int(m[1]), int(m[3]) * (1 if m[2] == "+" else -1)
    return to_int(v, 0), 0


def load_char(cid) -> dict | None:
    d = char_dir(cid)
    if not (d / "stats.dt").exists():
        return None
    c = {"id": str(cid), "stats": {}, "relationships": {}, "inv": {}, "buffs": []}
    raw = key_value_file(d / "stats.dt")
    for s in STATS:
        c["stats"][s] = list(_parse_stat(raw.get(s, "10")))
    org, diff = _parse_stat(raw.get("max_hp", "10"))
    c["max_hp"] = max(1, org + diff)
    c["max_hp_org"] = org
    c["hp"] = to_int(raw.get("hp"), c["max_hp"])
    c["level"] = to_int(raw.get("level"), 1)
    c["xp"] = to_int(raw.get("xp"), 0)
    c["initiative_bonus"] = 0

    other_text = read_text(d / "other.dt")
    other = {}
    in_rel = False
    for line in other_text.splitlines():
        s = line.strip()
        if s.startswith("relationship with other players"):
            in_rel = True
            continue
        if in_rel:
            if "|" in s:
                a, b = cols(s.rstrip(","))[:2]
                c["relationships"][a] = to_int(b, 0)
            continue
        if ":" in s and not s.startswith("#"):
            k, v = s.split(":", 1)
            other[k.strip().lower()] = v.strip()
    c["name"] = other.get("name", f"Character {cid}")
    c["origin"] = other.get("origin", "").lower()
    c["class"] = other.get("class", "").lower()
    c["nicname"] = other.get("nicname", c["name"])
    c["created_with"] = other.get("created", "")
    c["inv"] = _load_inv(d / "inv.dt")
    c["buffs"] = _load_buffs(d / BUFFS_FILE)
    return c


def _slot_entry(parts: list[str]) -> dict | None:
    item = opt(parts[1]) if len(parts) > 1 else None
    if not item:
        return None
    return {"item": item.lower(),
            "level": to_int(parts[2], 1) if len(parts) > 2 and opt(parts[2]) else 1,
            "dur": to_int(parts[3]) if len(parts) > 3 and opt(parts[3]) else None}


def _load_inv(path: Path) -> dict:
    inv = {"equipped": {}, "bag_type": None, "bag_max": 10, "bag": {}}
    for line in data_lines(path):
        if line.lower().startswith("bag:"):
            parts = cols(line[4:])
            inv["bag_type"] = opt(parts[0]) if parts else None
            inv["bag_max"] = to_int(parts[1], 10) if len(parts) > 1 else 10
            continue
        parts = cols(line)
        key = parts[0].lower()
        e = _slot_entry(parts)
        if key in SLOTS:
            if e:
                inv["equipped"][key] = e
        elif key.isdigit() and e:
            inv["bag"][key] = e
    return inv


def _load_buffs(path: Path) -> list[dict]:
    out = []
    for line in data_lines(path):
        c = cols(line) + ["", ""]
        if not c[0] or c[0].startswith(("[", "{")):
            continue
        out.append({"id": c[0].lower(), "level": to_int(c[1], 1), "turns": to_int(c[2], -1)})
    return out


def _fmt_slot(e: dict | None, key) -> str:
    if not e:
        return f"{key} | - | - | -"
    return f"{key} | {e['item']} | {e.get('level') or 1} | {e['dur'] if e.get('dur') is not None else '-'}"


def save_char(c: dict) -> None:
    d = char_dir(c["id"])
    d.mkdir(parents=True, exist_ok=True)
    lines = ["# org score + upgrades since creation"]
    for s in STATS:
        org, diff = c["stats"][s]
        lines.append(f"{s}: {org} + {diff}")
    lines.append("# max hp (org + upgrades), current hp, level, xp")
    lines.append(f"max_hp: {c.get('max_hp_org', c['max_hp'])} + {c['max_hp'] - c.get('max_hp_org', c['max_hp'])}")
    lines.append(f"hp: {c['hp']}")
    lines.append(f"level: {c['level']}")
    lines.append(f"xp: {c['xp']}")
    write_text(d / "stats.dt", "\n".join(lines) + "\n")

    o = [f"name: {c['name']}", f"origin: {c['origin']}", f"class: {c['class']}", f"nicname: {c['nicname']}"]
    o.append("relationship with other players: {")
    rels = list(c["relationships"].items())
    for n, (pid, lvl) in enumerate(rels):
        o.append("    {")
        o.append(f"        {pid} | {lvl}")
        o.append("    }" + ("," if n < len(rels) - 1 else ""))
    o.append("}")
    write_text(d / "other.dt", "\n".join(o) + "\n")

    inv = c["inv"]
    il = ["# held/equiped slots"]
    for slot in SLOTS:
        il.append(_fmt_slot(inv["equipped"].get(slot), slot))
    il.append("# other bag slots")
    il.append(f"bag: {inv.get('bag_type') or '-'} | {inv.get('bag_max', 10)}")
    for k in sorted(inv["bag"], key=int):
        il.append(_fmt_slot(inv["bag"][k], k))
    write_text(d / "inv.dt", "\n".join(il) + "\n")

    bl = ["# buff/debuff ID | level | turns until cleared (-1 = permanent)"]
    for b in c["buffs"]:
        bl.append(f"{b['id']} | {b['level']} | {b['turns']}")
    write_text(d / BUFFS_FILE, "\n".join(bl) + "\n")


def stat_total(c: dict, s: str) -> int:
    org, diff = c["stats"][s]
    return org + diff


# --------------------------------------------------------------------------- #
# bindings: which Discord user controls which character
# --------------------------------------------------------------------------- #
def load_bindings() -> dict[str, str]:
    out = {}
    for line in data_lines(BINDINGS_FILE):
        c = cols(line)
        if len(c) >= 2 and c[1]:
            out[c[0]] = c[1]
    return out


def save_bindings(b: dict[str, str]) -> None:
    lines = ["# char id | discord user id   (a user may own several chars; edit with /bind-char)"]
    lines += [f"{cid} | {uid}" for cid, uid in sorted(b.items(), key=lambda kv: (len(kv[0]), kv[0]))]
    write_text(BINDINGS_FILE, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# uniform entity access. chars are "1", "2"...  live monsters are "m1", "m2"...
# --------------------------------------------------------------------------- #
def is_monster(key: str) -> bool:
    return str(key).startswith("m")


def ent_get(state: dict, key: str) -> dict | None:
    key = str(key)
    if is_monster(key):
        return state["monsters"].get(key)
    return load_char(key)


def ent_save(state: dict, key: str, obj: dict) -> None:
    if is_monster(key):
        state["monsters"][str(key)] = obj
    else:
        save_char(obj)


def ent_name(obj: dict) -> str:
    return obj.get("name", "?")


def controller_of(key: str) -> str:
    """Discord user id (str) that controls an entity, or 'DM'."""
    if is_monster(key):
        return "DM"
    return load_bindings().get(str(key), "DM")
