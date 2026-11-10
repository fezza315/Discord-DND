"""Import / export characters.

A character is the four files in a "player <id>/" folder (stats.dt, other.dt, inv.dt, buffs⁄debuffs.st).
You can import
  * a .zip containing those files (flat or inside a folder)  - what export produces
  * a .json file shaped like the dict load_char() returns (handy for hand-written chars)
The imported char gets a fresh id and is NOT bound to anyone - use "bind char.py" or /bind-char.

    python "import char.py" import <file.zip|file.json>
    python "import char.py" export <char id> [out.zip]
"""
import io
import json
import pathlib
import shutil
import sys
import zipfile

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import (BUFFS_FILE, STATS, char_dir, clean, load_char, load_items, next_char_id,  # noqa: E402
                    read_text, save_char)

FILES = ("stats.dt", "other.dt", "inv.dt", BUFFS_FILE)
MAX_FILE = 200_000


def export_char(cid) -> bytes:
    c = load_char(cid)
    if not c:
        raise ValueError(f"no character #{cid}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.writestr(f"{c['name']}/{f}", read_text(char_dir(cid) / f))
    return buf.getvalue()


def _from_zip(data: bytes, dest: pathlib.Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        found = {}
        for info in z.infolist():
            base = info.filename.replace("\\", "/").split("/")[-1]
            if base.endswith("debuffs.st"):          # tolerate a real '/' that split the name
                base = BUFFS_FILE
            if base in FILES and not info.is_dir():
                if info.file_size > MAX_FILE:
                    raise ValueError(f"{base} is too big")
                found[base] = z.read(info).decode("utf-8", "replace")
        if "stats.dt" not in found or "other.dt" not in found:
            raise ValueError("zip needs at least stats.dt and other.dt")
        dest.mkdir(parents=True, exist_ok=True)
        for base, text in found.items():
            (dest / base).write_text(text, encoding="utf-8")


def _from_json(data: bytes, cid: str) -> None:
    d = json.loads(data.decode("utf-8"))
    stats = {}
    for s in STATS:
        v = d.get("stats", {}).get(s, 10)
        stats[s] = [int(v[0]), int(v[1])] if isinstance(v, list) else [int(v), 0]
    hp = int(d.get("max_hp", 10))
    inv = d.get("inv") or {}
    c = {"id": cid, "name": clean(d.get("name", f"Imported {cid}"))[:32], "origin": str(d.get("origin", "")).lower(),
         "class": str(d.get("class", "")).lower(), "stats": stats, "max_hp": hp, "max_hp_org": hp,
         "hp": int(d.get("hp", hp)), "level": int(d.get("level", 1)), "xp": int(d.get("xp", 0)),
         "relationships": {}, "buffs": d.get("buffs", []),
         "inv": {"equipped": inv.get("equipped", {}), "bag_type": inv.get("bag_type"),
                 "bag_max": inv.get("bag_max", 10), "bag": inv.get("bag", {})}}
    c["nicname"] = c["name"]
    save_char(c)


def import_char(data: bytes, filename: str = "") -> tuple[str, list[str]]:
    """Returns (new char id, warnings)."""
    cid = next_char_id()
    dest = char_dir(cid)
    try:
        if filename.lower().endswith(".json") or data.lstrip()[:1] == b"{":
            _from_json(data, cid)
        else:
            _from_zip(data, dest)
        c = load_char(cid)
        if not c:
            raise ValueError("couldn't read that character.")
        save_char(c)                                  # rewrite in the normal format
        c["nicname"] = c["nicname"] or c["name"]
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    items = load_items()
    warn = []
    entries = list(c["inv"]["equipped"].values()) + list(c["inv"]["bag"].values())
    for e in entries:
        if e["item"] not in items:
            warn.append(f"unknown item '{e['item']}' (add it to tables/items.dt)")
    from common import load_other
    other = load_other()
    if c["origin"] not in other["race"]:
        warn.append(f"unknown origin '{c['origin']}'")
    if c["class"] not in other["class"]:
        warn.append(f"unknown class '{c['class']}'")
    return cid, warn


def cli(argv: list[str]) -> str:
    if len(argv) >= 2 and argv[0] == "import":
        p = pathlib.Path(argv[1])
        cid, warn = import_char(p.read_bytes(), p.name)
        return f"imported as char #{cid} ({load_char(cid)['name']})" + "".join(f"\n  warning: {w}" for w in warn)
    if len(argv) >= 2 and argv[0] == "export":
        out = pathlib.Path(argv[2] if len(argv) > 2 else f"char_{argv[1]}.zip")
        out.write_bytes(export_char(argv[1]))
        return f"wrote {out}"
    return __doc__


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
