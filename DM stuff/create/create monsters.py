"""Bulk monster template tools (the single-monster version is "DM stuff/monsters/create monster.py").

    python "create monsters.py" list
    python "create monsters.py" bulk "Goblin | 12 | 1d6 | 60 ; Wolf | 9 | 1d6+1 | 65 | 10"

bulk rows:  name | hp | damage | [hit] | [evasion] | [armor] | [boss yes/no]   (separate rows with ';' or new lines)
"""
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import cols, load_script  # noqa: E402


def _mod():
    return load_script("DM stuff/monsters/create monster.py")


def create_many(spec: str) -> list[str]:
    made = []
    for row in spec.replace(";", "\n").splitlines():
        if not row.strip():
            continue
        c = cols(row) + [""] * 7
        if not c[0] or not c[1].isdigit() or not c[2]:
            raise ValueError(f"bad row {row!r}: need  name | hp | damage")
        p = _mod().create_template(c[0], int(c[1]), c[2], int(c[3] or 60), int(c[4] or 0), int(c[5] or 0),
                                   boss=c[6].lower() in ("y", "yes", "true", "boss"))
        made.append(p.name)
    return made


def list_all() -> str:
    rows = [f"{k}: {t['name']}  hp {t['hp']}  dmg {t['damage']}  hit {t['hit']}%  evasion {t['evasion']}  armor {t['armor']}"
            + ("  [BOSS]" if t["boss"] else "") for k, t in _mod().list_templates().items()]
    return "\n".join(rows) or "(no monster templates)"


def cli(argv: list[str]) -> str:
    if argv and argv[0] == "bulk" and len(argv) > 1:
        return "created: " + ", ".join(create_many(" ".join(argv[1:])))
    return list_all()


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
