"""Read the per-player rows in "total group power.st", then write the total there and the
average + head count to "avg group power.st".

    python "combine and avg group power.py"          (just combines what's already there)
    python "combine and avg group power.py" refresh  (recalculates every player first)
"""
import pathlib
import sys

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "main.py").exists())
sys.path.insert(0, str(ROOT))

from common import AVG_POWER_ST, TOTAL_POWER_ST, cols, data_lines, load_script, write_text  # noqa: E402


def combine() -> tuple[float, float, int]:
    """Returns (total, average, player count)."""
    rows = {}
    for line in data_lines(TOTAL_POWER_ST):
        c = cols(line)
        if len(c) == 2:
            try:
                rows[c[0]] = float(c[1])
            except ValueError:
                pass
    total = round(sum(rows.values()), 1)
    avg = round(total / len(rows), 1) if rows else 0.0
    lines = ["# char id | power"] + [f"{k} | {v}" for k, v in rows.items()] + [f"total: {total}"]
    write_text(TOTAL_POWER_ST, "\n".join(lines) + "\n")
    write_text(AVG_POWER_ST, f"avg: {avg}\ncount: {len(rows)}\n")
    return total, avg, len(rows)


def refresh() -> tuple[float, float, int]:
    load_script("player data/calculate per player power.py").calculate()
    return combine()


def cli(argv: list[str]) -> str:
    total, avg, n = refresh() if argv and argv[0] == "refresh" else combine()
    return f"{n} player(s): total power {total}, average {avg}"


if __name__ == "__main__":
    print(cli(sys.argv[1:]))
