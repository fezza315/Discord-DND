"""Add a class to tables/other.dt.

    python "create class.py" --id rogue --name Rogue --description "..." --bonuses dex:+2 --base-hp 8 --items dagger,leather_armor
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import OTHER_DT, clean, load_items, load_other, parse_bonuses, read_text, slug, write_text  # noqa: E402


def create_class(id: str, name: str, description: str = "", bonuses: str = "", base_hp: int = 8, items: str = "") -> str:
    cid = slug(id)
    if not cid:
        raise ValueError("a class needs an id.")
    if cid in load_other()["class"]:
        raise ValueError(f"class '{cid}' already exists.")
    if bonuses and not parse_bonuses(bonuses):
        raise ValueError("bonuses look like  str:+2,con:+1")
    known = load_items()
    start = [i.strip().lower() for i in items.split(",") if i.strip() and i.strip() != "-"]
    for i in start:
        if i not in known:
            raise ValueError(f"unknown item '{i}' (create it first with /create item)")
    if int(base_hp) < 1:
        raise ValueError("base hp must be at least 1.")
    text = read_text(OTHER_DT)
    row = (f"class | {cid} | {clean(name) or cid} | {clean(description)} | {clean(bonuses) or '-'} | "
           f"{int(base_hp)} | {','.join(start) or '-'}")
    write_text(OTHER_DT, text + ("" if text.endswith("\n") or not text else "\n") + row + "\n")
    return cid


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create class.py")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--bonuses", default="")
    ap.add_argument("--base-hp", type=int, default=8)
    ap.add_argument("--items", default="")
    a = ap.parse_args(argv)
    return "created class " + create_class(a.id, a.name, a.description, a.bonuses, a.base_hp, a.items)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
