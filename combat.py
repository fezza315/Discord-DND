"""Combat / action engine: attacks, items, effects, end-of-turn processing.

Pure game logic - no discord imports. Every function works on the `state` dict
(and the character files) and returns text for main.py to post.
"""
from __future__ import annotations

import random

import effects_engine as fx
from common import (STATS, ent_get, ent_save, is_monster, load_effects,
                    load_items, mod, roll, save_char, settings, stat_total, load_script)

UNARMED = {"id": None, "name": "bare hands", "type": "weapon", "slot": "hand", "damage": "1d2",
           "hit": 65, "stat": "str", "armor": 0, "durability": None, "effects": [], "description": ""}


def T():
    return load_script("player data/player turn.py")


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------------ display
def effect_text(obj: dict) -> str:
    parts = []
    for b in obj.get("buffs", []):
        dur = "permanent" if b["turns"] < 0 else f"{b['turns']} turn{'s' if b['turns'] != 1 else ''} left"
        parts.append(f"{b['id']} L{b['level']} ({dur})")
    return ", ".join(parts)


def status_line(obj: dict) -> str:
    eff = effect_text(obj)
    return f"**{obj['name']}** — HP {obj['hp']}/{obj['max_hp']}" + (f" — effects: {eff}" if eff else "")


# ------------------------------------------------------------------ buffs
def add_buff(obj: dict, eid: str, turns: int = 3, level: int = 1) -> None:
    """Add or refresh a buff/debuff. turns < 0 means permanent."""
    eid = eid.lower()
    for b in obj.setdefault("buffs", []):
        if b["id"] == eid:
            b["turns"] = -1 if (turns < 0 or b["turns"] < 0) else max(b["turns"], turns)
            b["level"] = max(b["level"], level)
            return
    obj["buffs"].append({"id": eid, "level": level, "turns": turns})


def _passive(obj: dict, dmg: int, direction: str) -> int:
    """Run every passive effect on `obj` over a damage number. direction: output|input"""
    effects = load_effects()
    for b in obj.get("buffs", []):
        e = effects.get(b["id"])
        if not e or "passive" not in e["hooks"]:
            continue
        ctx = fx.make_ctx(obj, b["level"], dmg if direction == "output" else 0,
                          dmg if direction == "input" else 0)
        try:
            fx.run_hook(e, "passive", ctx)
        except fx.EffectError:
            continue
        dmg = ctx["plr"][direction]["dmg"]
    return max(int(dmg), 0)


# ------------------------------------------------------------------ hp / death
def kill(state: dict, key: str, obj: dict) -> None:
    T().remove(state, key)
    if is_monster(key):
        state["monsters"].pop(key, None)
    else:
        obj["hp"] = 0
        obj["buffs"] = []
        save_char(obj)


def apply_damage(state: dict, key: str, obj: dict, amount: int) -> bool:
    """Returns True if that killed it. Saves the entity either way."""
    obj["hp"] = obj["hp"] - max(int(amount), 0)
    if obj["hp"] <= 0:
        obj["hp"] = 0
        kill(state, key, obj)
        return True
    ent_save(state, key, obj)
    return False


def heal(obj: dict, amount: int) -> int:
    before = obj["hp"]
    obj["hp"] = min(obj["max_hp"], obj["hp"] + max(int(amount), 0))
    return obj["hp"] - before


# ------------------------------------------------------------------ inventory helpers
def find_in_inv(char: dict, item_ref: str | None):
    """Locate an item in a char's inventory. item_ref may be an item id or a bag slot number.
    Returns (where, key, entry) or None.  where: 'equipped' | 'bag'"""
    inv = char["inv"]
    if item_ref is None:
        return None
    ref = str(item_ref).strip().lower()
    for slot, e in inv["equipped"].items():
        if ref in (slot, e["item"]):
            return "equipped", slot, e
    for k, e in inv["bag"].items():
        if ref in (k, e["item"]):
            return "bag", k, e
    return None


def remove_entry(char: dict, where: str, key: str) -> None:
    char["inv"][where].pop(key, None)


def wear(state: dict, char: dict, where: str, key: str, item: dict) -> str:
    """Durability tick. Returns a message when the item breaks."""
    e = char["inv"][where].get(key)
    if not settings(state)["item_durability"] or not e or e.get("dur") is None:
        return ""
    e["dur"] -= 1
    if e["dur"] <= 0:
        remove_entry(char, where, key)
        return f"\n💥 {char['name']}'s **{item['name']}** broke!"
    return ""


def armor_of(obj: dict, items: dict) -> int:
    if is_monster(obj.get("id", "")):
        return obj.get("armor", 0)
    total = 0
    for e in obj["inv"]["equipped"].values():
        it = items.get(e["item"])
        if it:
            total += it["armor"]
    return total


# ------------------------------------------------------------------ attack
def _weapon(state, att, item_ref, items):
    """Pick the weapon for a char attack. Returns (item, where, key) or raises ValueError."""
    if item_ref:
        loc = find_in_inv(att, item_ref)
        if not loc:
            raise ValueError(f"you don't have '{item_ref}'.")
        where, key, e = loc
        it = items.get(e["item"])
        if not it or not it["damage"] or it["type"] == "consumable":
            raise ValueError(f"{e['item']} can't be used to attack.")
        return it, where, key
    e = att["inv"]["equipped"].get("hand")
    if e and items.get(e["item"]) and items[e["item"]]["damage"] and items[e["item"]]["type"] != "consumable":
        return items[e["item"]], "equipped", "hand"
    return UNARMED, None, None


def _same_room(state, a_key, b_key, a_obj, b_obj) -> bool:
    if not state["map"].get("spawned"):
        return True

    def room(k, o):
        return o.get("room") if is_monster(k) else state["positions"].get(str(k))

    ra, rb = room(a_key, a_obj), room(b_key, b_obj)
    return ra is None or rb is None or ra == rb


def attack(state: dict, att_key: str, tgt_key: str, item_ref: str | None = None) -> dict:
    items = load_items()
    s = settings(state)
    att_key, tgt_key = str(att_key), str(tgt_key)
    att, tgt = ent_get(state, att_key), ent_get(state, tgt_key)
    if not att:
        return {"ok": False, "error": "attacker not found."}
    if not tgt or tgt["hp"] <= 0:
        return {"ok": False, "error": "that target doesn't exist (or is already dead)."}
    if att_key == tgt_key:
        return {"ok": False, "error": "you can't attack yourself."}
    if not _same_room(state, att_key, tgt_key, att, tgt):
        return {"ok": False, "error": f"{tgt['name']} isn't in the same room."}

    where = key = None
    if is_monster(att_key):
        weapon = {"name": att.get("attack_name") or "its attack", "damage": att["damage"], "hit": att["hit"],
                  "effects": att.get("effects", []), "id": None}
        smod = att.get("stat_mod", 0)
        lvl = 1
    else:
        try:
            weapon, where, key = _weapon(state, att, item_ref, items)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        smod = mod(stat_total(att, weapon["stat"])) if weapon["stat"] in STATS else 0
        lvl = att["inv"][where][key].get("level", 1) if where else 1

    evasion = tgt.get("evasion", 0) if is_monster(tgt_key) else mod(stat_total(tgt, "dex")) * 2
    chance = clamp(weapon["hit"] + smod * 2 - evasion, 5, 95)
    hit_roll = random.randint(1, 100)
    hit = hit_roll <= chance

    text = (f"⚔️ **{att['name']}** tried attacking **{tgt['name']}** and "
            f"{'**hit**' if hit else '**missed**'} with a {hit_roll}/100")
    msgs = ""
    if where:
        msgs += wear(state, att, where, key, weapon)
    if not hit:
        if not is_monster(att_key):
            ent_save(state, att_key, att)
        return {"ok": True, "hit": False, "killed": False,
                "text": text + msgs + "\n" + status_line(att), "attacker": att, "target": tgt}

    dmg_roll, detail = roll(weapon["damage"])
    raw = max(dmg_roll + smod + ((lvl - 1) if s["item_level"] and where else 0), 1)
    dmg = _passive(att, raw, "output")
    dmg = _passive(tgt, dmg, "input")
    armor = armor_of(tgt, items)
    dmg = max(dmg - armor, 1) if raw > 0 else 0

    applied = []
    for ref in weapon.get("effects", []):
        add_buff(tgt, ref["id"], ref["turns"], ref["level"])
        applied.append(ref["id"])

    max_hp = tgt["max_hp"]
    if not is_monster(att_key):
        ent_save(state, att_key, att)
    killed = apply_damage(state, tgt_key, tgt, dmg)
    shown = detail + (f"{smod:+d}" if smod else "")
    text += (f"\nit hit with a {raw} ({shown}), doing {dmg} out of {max_hp} total health, "
             f"leaving {tgt['name']} at {tgt['hp']}/{max_hp} — "
             f"{'killing' if killed else 'not killing'} it.")
    if armor:
        text += f" (armor absorbed {armor})"
    if applied:
        text += f"\n{tgt['name']} is now affected by: {', '.join(applied)}."
    if killed:
        text += f"\n💀 **{tgt['name']}** has fallen."
    text += msgs + "\n" + status_line(att) + "\n" + (status_line(tgt) if not killed else "")
    return {"ok": True, "hit": True, "killed": killed, "damage": dmg, "text": text.rstrip(),
            "attacker": att, "target": tgt}


# ------------------------------------------------------------------ use / equip
def use_item(state: dict, key: str, item_ref: str) -> dict:
    char = ent_get(state, key)
    items = load_items()
    loc = find_in_inv(char, item_ref)
    if not loc:
        return {"ok": False, "error": f"you don't have '{item_ref}'."}
    where, k, e = loc
    it = items.get(e["item"])
    if not it or it["type"] != "consumable":
        return {"ok": False, "error": f"{e['item']} isn't a consumable (use /attack or /equip)."}
    healed = 0
    if it["damage"]:
        amount, _ = roll(it["damage"])
        healed = heal(char, amount)
    for ref in it["effects"]:
        add_buff(char, ref["id"], ref["turns"], ref["level"])
    remove_entry(char, where, k)
    save_char(char)
    text = f"🧪 **{char['name']}** used **{it['name']}**"
    if healed:
        text += f" and recovered {healed} HP"
    if it["effects"]:
        text += f" (gained: {', '.join(r['id'] for r in it['effects'])})"
    return {"ok": True, "text": text + "\n" + status_line(char)}


def equip(state: dict, key: str, item_ref: str) -> dict:
    char = ent_get(state, key)
    items = load_items()
    loc = find_in_inv(char, item_ref)
    if not loc or loc[0] != "bag":
        return {"ok": False, "error": f"'{item_ref}' isn't in your bag."}
    _, k, e = loc
    it = items.get(e["item"])
    if not it or not it["slot"]:
        return {"ok": False, "error": f"{e['item']} can't be equipped."}
    slot = it["slot"]
    old = char["inv"]["equipped"].get(slot)
    char["inv"]["bag"].pop(k)
    if old:
        char["inv"]["bag"][k] = old  # swap into the freed bag slot
    char["inv"]["equipped"][slot] = e
    save_char(char)
    return {"ok": True, "text": f"🛡️ **{char['name']}** equipped **{it['name']}** ({slot})."}


def give_item(char: dict, item_id: str, state: dict | None = None, level: int = 1) -> bool:
    """Put an item into the first free bag slot. False if the bag is full."""
    items = load_items()
    it = items.get(item_id)
    entry = {"item": item_id, "level": level, "dur": it["durability"] if it else None}
    inv = char["inv"]
    s = settings(state) if state else {"bag_max_slots": True}
    if s["bag_max_slots"] and len(inv["bag"]) >= inv.get("bag_max", 10):
        return False
    n = 1
    while str(n) in inv["bag"]:
        n += 1
    inv["bag"][str(n)] = entry
    return True


# ------------------------------------------------------------------ end of turn
def finish_turn(state: dict) -> dict:
    """End the current turn: run effects, tick durations, advance. Returns
    {'log': [...], 'key': next entity, 'controller': ..., 'round': n}"""
    t = T()
    tstate = state["turn"]
    cur = t.current_key(state)
    log: list[str] = []
    effects = load_effects()

    def run(key, hook):
        obj = ent_get(state, key)
        if not obj:
            return False
        died = False
        for b in list(obj.get("buffs", [])):
            e = effects.get(b["id"])
            if not e or hook not in e["hooks"]:
                continue
            ctx = fx.make_ctx(obj, b["level"])
            try:
                actions = fx.run_hook(e, hook, ctx)
            except fx.EffectError as err:
                log.append(f"⚠️ effect '{b['id']}' is broken: {err}")
                continue
            for a in actions:
                if a["type"] == "dmg":
                    log.append(f"☠️ {obj['name']} takes {a['amount']} from {b['id']}")
                    if apply_damage(state, key, obj, a["amount"]):
                        log.append(f"💀 **{obj['name']}** has fallen.")
                        died = True
                        break
                elif a["type"] == "heal":
                    log.append(f"💚 {obj['name']} recovers {heal(obj, a['amount'])} from {b['id']}")
            if died:
                break
        if not died:
            ent_save(state, key, obj)
        return died

    if cur is not None:
        cur_died = run(cur, "plr.turn")
        for other in list(tstate["order"]):
            if other != cur:
                run(other, "other.plr.turn")
        if not cur_died:
            obj = ent_get(state, cur)
            if obj:
                keep = []
                for b in obj.get("buffs", []):
                    if b["turns"] > 0:
                        b["turns"] -= 1
                        if b["turns"] == 0:
                            log.append(f"✨ {b['id']} wore off {obj['name']}")
                            continue
                    keep.append(b)
                obj["buffs"] = keep
                ent_save(state, cur, obj)

    key, ctrl = t.advance(state)
    return {"log": log, "key": key, "controller": ctrl, "round": tstate["round"]}
