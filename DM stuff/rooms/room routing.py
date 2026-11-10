"""Moving around the map: who can walk where, what happens when a room is entered.

Connections come from "DM stuff/rooms/path.pt", rooms from "tables/rooms.dt".
Players move with /move; entering an undescribed room halts the turn until the DM writes /room-dsc.

    python "room routing.py" route <from> <to>
    python "room routing.py" show
"""
import pathlib
import sys
from collections import deque

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (IMPORTANT_DT, PATH_PT, ROOMS_DIR, cols, data_lines, key_value_file, load_bindings,  # noqa: E402
                    load_rooms, load_script, save_rooms, say, to_int, write_key_value, write_text)


# ----------------------------------------------------------------------------- graph
def load_paths() -> dict[int, set[int]]:
    g: dict[int, set[int]] = {}
    for line in data_lines(PATH_PT):
        c = cols(line)
        a, b = to_int(c[0]), to_int(c[1]) if len(c) > 1 else None
        if a is not None and b is not None:
            g.setdefault(a, set()).add(b)
            g.setdefault(b, set()).add(a)
    return g


def neighbors(rid: int) -> list[int]:
    return sorted(load_paths().get(int(rid), set()))


def route(a: int, b: int) -> list[int] | None:
    """Shortest list of region ids from a to b (inclusive), or None."""
    g = load_paths()
    a, b = int(a), int(b)
    prev, q = {a: None}, deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            path = []
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        for n in sorted(g.get(cur, ())):
            if n not in prev:
                prev[n] = cur
                q.append(n)
    return None


# ----------------------------------------------------------------------------- room data
def room_data_path(rid: int) -> pathlib.Path:
    return ROOMS_DIR / f"room {rid} data.dt"


def load_room_data(rid: int) -> dict:
    return key_value_file(room_data_path(rid))


def save_room_data(rid: int, data: dict) -> None:
    write_key_value(room_data_path(rid), data, f"room {rid} data. (event / monsters / loot / notes / spawned)")


def load_important() -> dict[int, dict]:
    out = {}
    for line in data_lines(IMPORTANT_DT):
        c = cols(line) + ["", ""]
        rid = to_int(c[0])
        if rid is not None:
            out[rid] = {"type": c[1].lower() or "important", "note": c[2]}
    return out


def set_important(rid: int, kind: str, note: str = "") -> None:
    imp = load_important()
    if kind.lower() in ("", "-", "none", "off"):
        imp.pop(int(rid), None)
    else:
        imp[int(rid)] = {"type": kind.lower(), "note": note}
    lines = ["# room id | type (boss/treasure/safe/...) | note        (set with /edit map important:...)"]
    lines += [f"{r} | {v['type']} | {v['note']}" for r, v in sorted(imp.items())]
    write_text(IMPORTANT_DT, "\n".join(lines) + "\n")


def edit_room(rid: int, field: str, value: str) -> str:
    rooms = load_rooms()
    rid = int(rid)
    if rid not in rooms:
        raise ValueError(f"no room {rid}.")
    field = field.lower()
    if field == "description":
        rooms[rid]["description"] = value.strip()
    elif field == "marked":
        rooms[rid]["marked"] = value.strip().lower() in ("true", "yes", "on", "1")
    elif field == "kind":
        if value.lower() not in ("room", "hall", "door"):
            raise ValueError("kind must be room, hall or door")
        rooms[rid]["kind"] = value.lower()
    elif field == "important":
        set_important(rid, value)
        return f"room {rid} important: {value}"
    elif field in ("event", "monsters", "loot", "notes"):
        d = load_room_data(rid)
        d[field] = value.strip()
        d.setdefault("spawned", "false")
        save_room_data(rid, d)
        return f"room {rid} {field} set."
    else:
        raise ValueError("field must be description, marked, kind, important, event, monsters, loot or notes")
    save_rooms(rooms)
    return f"room {rid} {field} set."


def describe(rid: int) -> str:
    r = load_rooms().get(int(rid))
    if not r:
        return f"room {rid}"
    return f"{r['kind']} {rid}" + (f": {r['description']}" if r["description"] else "")


# ----------------------------------------------------------------------------- map session
def activate(state: dict) -> str:
    """/spawn map: put everybody on the spawn room and show the first bit of map."""
    if not state["map"].get("loaded"):
        raise ValueError("no map loaded - /create map first.")
    start = state["map"]["start_room"]
    rooms = load_rooms()
    rooms[start]["marked"] = True
    save_rooms(rooms)
    state["map"]["spawned"] = True
    for cid in load_bindings():
        state["positions"][cid] = start
    for m in state["monsters"].values():
        if m.get("room") is None:
            m["room"] = start
    publish_map(state, f"The map is live. The party starts in room {start}.")
    return f"map spawned, party placed in room {start}."


def publish_map(state: dict, text: str) -> None:
    img = load_script("DM stuff/create/create rooms.py").render_discovered()
    state["outbox"] = [o for o in state["outbox"] if o["channel"] != "map_view"]  # only the newest map matters
    say(state, "map_view", text, file=str(img) if img else None)


def enter_room(state: dict, cid: str, rid: int) -> dict:
    """Move a char. Returns {'ok', 'error'|..., 'need_desc', 'event', 'first'}"""
    rooms = load_rooms()
    rid, cid = int(rid), str(cid)
    if rid not in rooms:
        return {"ok": False, "error": f"there is no room {rid}."}
    cur = state["positions"].get(cid, state["map"].get("start_room"))
    if cur is not None and rid != cur and rid not in load_paths().get(cur, set()):
        opts = ", ".join(str(n) for n in neighbors(cur)) or "none"
        return {"ok": False, "error": f"room {rid} isn't connected to where you are ({cur}). you can go to: {opts}."}
    state["positions"][cid] = rid
    room = rooms[rid]
    first = not room["marked"]
    if first:
        room["marked"] = True
        save_rooms(rooms)
        publish_map(state, f"Discovered: {room['kind']} {rid}")
    need_desc = room["kind"] == "room" and not room["description"]
    ev = load_script("DM stuff/start event.py").wants_event(rid, first)
    return {"ok": True, "first": first, "need_desc": need_desc, "event": ev, "room": room}


def set_description(state: dict, rid: int, text: str) -> None:
    rooms = load_rooms()
    rooms[int(rid)]["description"] = text.strip()
    rooms[int(rid)]["marked"] = True
    save_rooms(rooms)


def cli(argv: list[str]) -> str:
    if argv[:1] == ["route"] and len(argv) == 3:
        r = route(argv[1], argv[2])
        return " -> ".join(map(str, r)) if r else "no route"
    g = load_paths()
    return "\n".join(f"{k}: {sorted(v)}" for k, v in sorted(g.items())) or "(no paths yet - import a map)"


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
