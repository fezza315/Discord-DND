"""Add an effect script to tables/effects.dt. The script is checked (parsed + dry run) before it is saved.

    python "create effect.py" --id burning --file burning.txt
    python "create effect.py" --id burning --body "when.plr.turn:\n    dmg.plr(3)"

(see the top of tables/effects.dt for the hooks and actions available)
"""
import argparse
import pathlib
import sys
import textwrap

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

import effects_engine as fx  # noqa: E402
from common import EFFECTS_DT, load_effects, read_text, slug, write_text  # noqa: E402


def create_effect(id: str, body: str) -> str:
    eid = slug(id)
    if not eid:
        raise ValueError("an effect needs an id.")
    if eid in load_effects():
        raise ValueError(f"effect '{eid}' already exists.")
    body = textwrap.indent(textwrap.dedent(body.replace("\t", "    ")).strip("\n"), "    ")
    try:
        fx.validate(body, eid)
    except fx.EffectError as e:
        raise ValueError(f"effect script problem: {e}") from None
    text = read_text(EFFECTS_DT)
    write_text(EFFECTS_DT, text + ("" if text.endswith("\n") or not text else "\n") + f"\n{eid}: {{\n{body}\n}}\n")
    return eid


def cli(argv: list[str]) -> str:
    ap = argparse.ArgumentParser(prog="create effect.py")
    ap.add_argument("--id", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--body")
    a = ap.parse_args(argv)
    body = pathlib.Path(a.file).read_text(encoding="utf-8") if a.file else a.body.replace("\\n", "\n")
    return "created effect " + create_effect(a.id, body)


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
