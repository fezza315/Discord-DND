"""Turn a map image into rooms.

Draw the map in Dungeon Scrawl (old school module), lock the grid to 1x1 and drop ONE green square on the
start cell, then export a PNG. The green square also tells the bot how big one grid cell is in pixels.

How the algorithm reads the picture
  1. green square  -> cell size, grid alignment and the spawn cell
  2. every cell that is light (floor) vs dark (wall / rock)         [floor_lum, default 150 of 255]
  3. a floor cell whose 8 neighbours are all floor is "open ground"; open ground (+1 cell around it) = a ROOM
  4. floor cells left over that touch a room                       = a DOOR
  5. the rest of the leftovers (corridors)                          = a HALL
  6. regions that touch each other are linked -> path.pt
The room with the spawn cell is room 1. Everything gets written to tables/rooms.dt (unmarked, except the spawn
room), "DM stuff/rooms/path.pt" and one "room <id> data.dt" per room. Wrong guess? fix it with /edit map.

    python "create rooms.py" <map.png> [--floor-lum 150] [--yes]     (without --yes it only writes a preview)
"""
import argparse
import json
import pathlib
import shutil
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops, ImageDraw, ImageFont  # noqa: E402

from common import IMPORTANT_DT, PATH_PT, ROOMS_DIR, load_state, save_rooms, save_state, write_text  # noqa: E402

PENDING_IMG = ROOMS_DIR / "map_pending.png"
PENDING_JSON = ROOMS_DIR / "map_pending.json"
PREVIEW_IMG = ROOMS_DIR / "map_preview.png"
MAP_IMG = ROOMS_DIR / "map.png"
MAP_JSON = ROOMS_DIR / "map_regions.json"
VIEW_IMG = ROOMS_DIR / "map_view.png"

KIND_ORDER = {"room": 0, "hall": 1, "door": 2}
COLORS = {"room": (60, 120, 255, 110), "hall": (255, 200, 40, 130), "door": (255, 60, 60, 160)}
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
N8 = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0))


# ----------------------------------------------------------------------------- analysis
def _components(cells: set, neighbours) -> list[list[tuple[int, int]]]:
    seen, out = set(), []
    for start in sorted(cells, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            c = stack.pop()
            comp.append(c)
            for dx, dy in neighbours:
                n = (c[0] + dx, c[1] + dy)
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(comp)
    return out


def analyse(img: Image.Image, floor_lum: int = 150) -> dict:
    img = img.convert("RGB")
    W, H = img.size
    r, g, b = img.split()
    mask = ImageChops.multiply(ImageChops.multiply(g.point(lambda v: 255 if v > 140 else 0),
                                                   r.point(lambda v: 255 if v < 110 else 0)),
                               b.point(lambda v: 255 if v < 110 else 0))
    bbox = mask.getbbox()
    if not bbox:
        raise ValueError("couldn't find the green start square on the map. Add one green square on the spawn cell.")
    gx0, gy0, gx1, gy1 = bbox
    cell = round(((gx1 - gx0) + (gy1 - gy0)) / 2)
    if cell < 4:
        raise ValueError("the green square is tiny - is it really one grid cell?")
    ox, oy = gx0 % cell, gy0 % cell
    cols_, rows_ = (W - ox) // cell, (H - oy) // cell
    if cols_ < 3 or rows_ < 3:
        raise ValueError("map is too small for that cell size.")
    if cols_ * rows_ > 250_000:
        raise ValueError("map has too many cells (over 250k) - lower the export resolution.")
    small = img.crop((ox, oy, ox + cols_ * cell, oy + rows_ * cell)).convert("L").resize(
        (cols_, rows_), Image.Resampling.BOX)
    lum = list(small.getdata())
    sc = min(max(((gx0 + gx1) // 2 - ox) // cell, 0), cols_ - 1)
    sr = min(max(((gy0 + gy1) // 2 - oy) // cell, 0), rows_ - 1)

    floor = {(c, r_) for r_ in range(rows_) for c in range(cols_) if lum[r_ * cols_ + c] >= floor_lum}
    floor.add((sc, sr))

    core = {c for c in floor if all((c[0] + dx, c[1] + dy) in floor for dx, dy in N8)}
    label: dict[tuple[int, int], tuple[str, int]] = {}
    raw: list[tuple[str, list]] = []

    for comp in _components(core, N8):
        cells = {c for c in comp}
        for c in comp:
            for dx, dy in N8:
                n = (c[0] + dx, c[1] + dy)
                if n in floor:
                    cells.add(n)
        cells = [c for c in cells if c not in label]
        if cells:
            for c in cells:
                label[c] = ("room", len(raw))
            raw.append(("room", cells))

    left = floor - set(label)
    door_cells = {c for c in left if any(label.get((c[0] + dx, c[1] + dy), ("",))[0] == "room" for dx, dy in N4)}
    for comp in _components(door_cells, N4):
        for c in comp:
            label[c] = ("door", len(raw))
        raw.append(("door", comp))
    for comp in _components(left - door_cells, N4):
        for c in comp:
            label[c] = ("hall", len(raw))
        raw.append(("hall", comp))

    start_idx = label[(sc, sr)][1]
    order = sorted(range(len(raw)), key=lambda i: (i != start_idx, KIND_ORDER[raw[i][0]],
                                                   min(c[1] for c in raw[i][1]), min(c[0] for c in raw[i][1])))
    new_id = {old: n + 1 for n, old in enumerate(order)}
    regions, grid = [], [[0] * cols_ for _ in range(rows_)]
    for old in order:
        kind, cells = raw[old]
        rid = new_id[old]
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        regions.append({"id": rid, "kind": kind,
                        "bounds": [ox + min(xs) * cell, oy + min(ys) * cell,
                                   ox + (max(xs) + 1) * cell - 1, oy + (max(ys) + 1) * cell - 1],
                        "cells": len(cells)})
        for c in cells:
            grid[c[1]][c[0]] = rid
    edges = set()
    for (c, r_), (_, i) in label.items():
        for dx, dy in ((1, 0), (0, 1)):
            n = label.get((c + dx, r_ + dy))
            if n and n[1] != i:
                a, b2 = sorted((new_id[i], new_id[n[1]]))
                edges.add((a, b2))
    return {"cell": cell, "ox": ox, "oy": oy, "cols": cols_, "rows": rows_, "size": [W, H], "start": [sc, sr],
            "start_room": 1, "regions": regions, "edges": sorted(edges), "grid": grid}


# ----------------------------------------------------------------------------- rendering
def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_preview(img: Image.Image, a: dict) -> pathlib.Path:
    base = img.convert("RGBA")
    over = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    cell, ox, oy = a["cell"], a["ox"], a["oy"]
    kind = {r["id"]: r["kind"] for r in a["regions"]}
    for y, row in enumerate(a["grid"]):
        for x, rid in enumerate(row):
            if rid:
                d.rectangle((ox + x * cell, oy + y * cell, ox + (x + 1) * cell - 1, oy + (y + 1) * cell - 1),
                            fill=COLORS[kind[rid]])
    out = Image.alpha_composite(base, over)
    d = ImageDraw.Draw(out)
    f = _font(max(10, cell // 2))
    for r in a["regions"]:
        x0, y0, x1, y1 = r["bounds"]
        d.text(((x0 + x1) // 2, (y0 + y1) // 2), str(r["id"]), fill=(0, 0, 0, 255), font=f, anchor="mm")
    out.convert("RGB").save(PREVIEW_IMG)
    return PREVIEW_IMG


def render_discovered() -> pathlib.Path | None:
    """The map with everything the players haven't found yet blacked out."""
    from common import load_rooms
    if not (MAP_IMG.exists() and MAP_JSON.exists()):
        return None
    a = json.loads(MAP_JSON.read_text(encoding="utf-8"))
    src = Image.open(MAP_IMG).convert("RGB")
    known = {rid for rid, r in load_rooms().items() if r["marked"]}
    cell, ox, oy = a["cell"], a["ox"], a["oy"]
    visible = set()
    for y, row in enumerate(a["grid"]):
        for x, rid in enumerate(row):
            if rid in known:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        visible.add((x + dx, y + dy))
    canvas = Image.new("RGB", src.size, (0, 0, 0))
    for x, y in visible:
        if 0 <= x < a["cols"] and 0 <= y < a["rows"]:
            box = (ox + x * cell, oy + y * cell, ox + (x + 1) * cell, oy + (y + 1) * cell)
            canvas.paste(src.crop(box), box[:2])
    canvas.thumbnail((2000, 2000))
    canvas.save(VIEW_IMG)
    return VIEW_IMG


# ----------------------------------------------------------------------------- stage / commit
def stage(data: bytes, floor_lum: int = 150) -> dict:
    """Analyse an uploaded image and keep it pending until /confirm. Returns a summary."""
    import io
    img = Image.open(io.BytesIO(data))
    a = analyse(img, floor_lum)
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(PENDING_IMG)
    PENDING_JSON.write_text(json.dumps(a), encoding="utf-8")
    render_preview(img, a)
    counts = {k: sum(1 for r in a["regions"] if r["kind"] == k) for k in KIND_ORDER}
    return {"preview": str(PREVIEW_IMG), "counts": counts, "cell": a["cell"], "regions": len(a["regions"])}


ROOM_DATA_TEMPLATE = (
    "# room {rid} data. everything is optional. (lines starting with # are comments)\n"
    "# event:    text announced to the whole table the moment someone walks in (starts a full table event)\n"
    "# monsters: monster template names, comma separated (monster1, boss1) - spawned the first time the room is entered\n"
    "# loot:     item ids the DM plans to hand out in this room\n"
    "# notes:    DM notes\n"
    "event: \nmonsters: \nloot: \nnotes: \nspawned: false\n"
)


def commit(state: dict | None = None) -> str:
    """Make the staged map the live one: rooms.dt, path.pt, room data files, map images."""
    if not PENDING_JSON.exists():
        raise ValueError("no map is waiting to be applied.")
    a = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
    shutil.copyfile(PENDING_IMG, MAP_IMG)
    shutil.copyfile(PENDING_JSON, MAP_JSON)
    rooms = {r["id"]: {"id": r["id"], "bounds": tuple(r["bounds"]), "marked": r["id"] == a["start_room"],
                       "kind": r["kind"], "description": ""} for r in a["regions"]}
    save_rooms(rooms)
    lines = ["# from | to    (rooms / halls / doors that touch - movement is allowed both ways)"]
    lines += [f"{x} | {y}" for x, y in a["edges"]]
    write_text(PATH_PT, "\n".join(lines) + "\n")
    write_text(IMPORTANT_DT, "# room id | type (boss/treasure/safe/...) | note        (set with /edit map important:...)\n")
    for old in ROOMS_DIR.glob("room * data.dt"):
        old.unlink()
    for r in a["regions"]:
        if r["kind"] == "room":
            write_text(ROOMS_DIR / f"room {r['id']} data.dt", ROOM_DATA_TEMPLATE.format(rid=r["id"]))
    PENDING_JSON.unlink(missing_ok=True)
    PENDING_IMG.unlink(missing_ok=True)
    if state is not None:
        state["map"] = {"loaded": True, "spawned": False, "start_room": a["start_room"]}
        state["positions"] = {}
    n = {k: sum(1 for r in a["regions"] if r["kind"] == k) for k in KIND_ORDER}
    return f"map loaded: {n['room']} rooms, {n['hall']} halls, {n['door']} doors. spawn room = {a['start_room']}."


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create rooms.py")
    ap.add_argument("image")
    ap.add_argument("--floor-lum", type=int, default=150)
    ap.add_argument("--yes", action="store_true", help="apply it straight away")
    a = ap.parse_args(argv)
    info = stage(pathlib.Path(a.image).read_bytes(), a.floor_lum)
    msg = f"found {info['counts']} (cell = {info['cell']}px). preview: {info['preview']}"
    if a.yes:
        state = load_state()
        msg += "\n" + commit(state)
        save_state(state)
    return msg


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
