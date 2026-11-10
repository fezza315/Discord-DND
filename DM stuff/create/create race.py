"""Add an origin (race) to tables/other.dt.

    python "create race.py" --id elf --name Elf --description "..." --bonuses dex:+2,int:+1
"""
import argparse
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import OTHER_DT, clean, load_other, parse_bonuses, read_text, slug, write_text  # noqa: E402


def create_race(id: str, name: str, description: str = "", bonuses: str = "") -> str:
    rid = slug(id)
    if not rid:
        raise ValueError("a race needs an id.")
    if rid in load_other()["race"]:
        raise ValueError(f"race '{rid}' already exists.")
    if bonuses and not parse_bonuses(bonuses):
        raise ValueError("bonuses look like  str:+2,con:+1")
    text = read_text(OTHER_DT)
    row = f"race | {rid} | {clean(name) or rid} | {clean(description)} | {clean(bonuses) or '-'}"
    write_text(OTHER_DT, text + ("" if text.endswith("\n") or not text else "\n") + row + "\n")
    return rid


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create race.py")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--bonuses", default="")
    a = ap.parse_args(argv)
    return "created race " + create_race(a.id, a.name, a.description, a.bonuses)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
