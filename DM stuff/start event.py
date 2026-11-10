"""Start a full table event.

An event is started automatically when players walk into a room that has an `event:` line or `monsters:` in its
"room <id> data.dt" (or is listed as boss/treasure in "important room.dt"). It
  * spawns the room's monsters (once),
  * announces the event to the play channel and pings every player,
  * tells the DM in the event-planning channel,
  * pauses the turn order until the DM uses /resume.

    python "start event.py" <room id> [--title "..."] [--text "..."]
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import FSLASH, load_bindings, load_rooms, load_script, load_state, save_state, say  # noqa: E402


def _rr():
    return load_script("DM stuff/rooms/room routing.py")


def wants_event(rid: int, first_visit: bool = True) -> bool:
    data = _rr().load_room_data(rid)
    imp = _rr().load_important().get(int(rid))
    has_content = bool(data.get("event", "").strip() or data.get("monsters", "").strip())
    if data.get("spawned", "false").lower() == "true" and not data.get("event", "").strip():
        has_content = False
    return bool(first_visit and (has_content or (imp and imp["type"] in ("boss", "treasure"))))


def start_event(state: dict, rid: int, title: str | None = None, text: str | None = None) -> str:
    rid = int(rid)
    rr = _rr()
    data = rr.load_room_data(rid)
    imp = rr.load_important().get(rid)
    spawned = []
    names = [n.strip() for n in data.get("monsters", "").split(",") if n.strip()]
    if names and data.get("spawned", "false").lower() != "true":
        sp = load_script("DM stuff/monsters/spawn monster.py")
        for n in names:
            for key in sp.spawn(state, n, 1, None, rid):
                spawned.append(state["monsters"][key]["name"])
        data["spawned"] = "true"
        rr.save_room_data(rid, data)
    title = title or (f"{imp['type'].title()} room" if imp else f"Room {rid}")
    body = text or data.get("event", "").strip() or "Something stirs in the dark..."
    msg = f"\U0001f3b2 **EVENT \u2014 {title}**\n{body}"
    if spawned:
        msg += "\n\u2694\ufe0f Enemies: " + ", ".join(spawned)
    users = sorted(set(load_bindings().values()))
    if users:
        msg += "\n" + " ".join(f"<@{u}>" for u in users)
    say(state, "play", msg, ping_users=users)
    dm = state["setup"].get("dm_id")
    say(state, "event_planning", (f"<@{dm}> " if dm else "") + f"Event started in room {rid} ({title}). "
        f"Turns are paused \u2014 run the scene, then /resume.", ping_users=[dm] if dm else [])
    state["event"] = {"room": rid, "title": title}
    pp = load_script(f"DM stuff/play{FSLASH}pause players turns.py")
    pp.pause(state, f"event in room {rid}", announce=False)
    return msg


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="start event.py")
    ap.add_argument("room", type=int)
    ap.add_argument("--title")
    ap.add_argument("--text")
    a = ap.parse_args(argv)
    state = load_state()
    out = start_event(state, a.room, a.title, a.text)
    save_state(state)
    return out


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
