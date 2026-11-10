"""Tiny interpreter for the effect scripts in tables/effects.dt.

    exsample effect: {
        when.plr.turn:            # runs at the END of the holder's own turn
            dmg.plr(5)
        when.other.plr.turn:      # runs at the end of anybody else's turn
            none
        when.passive:             # runs whenever damage is worked out
            if plr.output.dmg > 5:
                plr.output.dmg() = plr.output.dmg() - 5
            else:
                plr.output.dmg() = 1
    }

Names available inside a script:
    plr.output.dmg   damage the holder is about to deal   (read / assign)
    plr.input.dmg    damage the holder is about to take   (read / assign)
    plr.hp, plr.max_hp, level (the effect's level)
Statements: if/elif/else, assignment, `none`, dmg.plr(n), heal.plr(n).
A single `=` inside an if/elif condition is accepted as `==`.
"""
from __future__ import annotations

import ast
import operator
import re

HOOKS = ("plr.turn", "other.plr.turn", "passive")
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCS = {"max": max, "min": min, "abs": abs, "int": int, "round": round}


class EffectError(ValueError):
    pass


# ------------------------------------------------------------------ parsing
def parse_effects(text: str) -> dict[str, dict]:
    """Return {effect id: {'id', 'hooks': {hook: [stmt]}, 'source': str}}"""
    effects: dict[str, dict] = {}
    cur_name, cur_lines = None, []
    for raw in text.splitlines():
        if cur_name is None:
            m = re.match(r"^\s*(?!#)(.+?)\s*:\s*\{\s*$", raw)
            if m:
                cur_name, cur_lines = m.group(1).strip(), []
            continue
        if raw.strip() == "}":
            eid = cur_name.lower()
            body = [l for l in cur_lines if l.strip() and not l.strip().startswith("#")]
            effects[eid] = {"id": eid, "name": cur_name, "hooks": _parse_hooks(body, eid),
                            "source": "\n".join(cur_lines)}
            cur_name = None
        else:
            cur_lines.append(raw)
    return effects


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t").replace("\t", " "))


def _norm(lines):
    return [l.replace("\t", "    ").rstrip() for l in lines]


def _parse_hooks(lines, eid):
    lines = _norm(lines)
    hooks: dict[str, list] = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)when\.([\w.]+)\s*:\s*$", lines[i])
        if not m:
            raise EffectError(f"[{eid}] expected 'when.<hook>:' but found: {lines[i].strip()!r}")
        hook, hind = m.group(2), len(m.group(1))
        if hook not in HOOKS:
            raise EffectError(f"[{eid}] unknown hook when.{hook} (use {', '.join('when.' + h for h in HOOKS)})")
        i += 1
        if i < len(lines) and _indent(lines[i]) > hind:
            stmts, i = _parse_block(lines, i, _indent(lines[i]), eid)
        else:
            stmts = []
        hooks[hook] = stmts
    return hooks


def _parse_block(lines, i, indent, eid):
    stmts = []
    while i < len(lines):
        line = lines[i]
        ind = _indent(line)
        if ind < indent:
            break
        if ind > indent:
            raise EffectError(f"[{eid}] unexpected indent at: {line.strip()!r}")
        text = line.strip()
        if text.startswith("if ") and text.endswith(":"):
            branches, else_body = [], []
            cond = text[3:-1].strip()
            i += 1
            body, i = _child_block(lines, i, indent, eid, text)
            branches.append((_compile_expr(cond, eid), body))
            while i < len(lines) and _indent(lines[i]) == indent:
                t = lines[i].strip()
                if t.startswith("elif ") and t.endswith(":"):
                    i += 1
                    body, i = _child_block(lines, i, indent, eid, t)
                    branches.append((_compile_expr(t[5:-1].strip(), eid), body))
                elif t == "else:":
                    i += 1
                    else_body, i = _child_block(lines, i, indent, eid, t)
                    break
                else:
                    break
            stmts.append(("if", branches, else_body))
            continue
        i += 1
        if text == "none":
            continue
        m = re.match(r"^([\w.]+)(?:\(\))?\s*=(?!=)\s*(.+)$", text)
        if m:
            stmts.append(("assign", m.group(1), _compile_expr(m.group(2), eid)))
            continue
        m = re.match(r"^([\w.]+)\((.*)\)$", text)
        if m:
            args = [_compile_expr(a, eid) for a in _split_args(m.group(2))]
            stmts.append(("call", m.group(1), args))
            continue
        raise EffectError(f"[{eid}] can't understand: {text!r}")
    return stmts, i


def _child_block(lines, i, indent, eid, header):
    if i >= len(lines) or _indent(lines[i]) <= indent:
        raise EffectError(f"[{eid}] '{header}' needs an indented body")
    return _parse_block(lines, i, _indent(lines[i]), eid)


def _split_args(s: str) -> list[str]:
    s = s.strip()
    if not s:
        return []
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _compile_expr(text: str, eid: str):
    text = re.sub(r"(?<![=!<>])=(?!=)", "==", text.strip())  # lone '=' means '=='
    try:
        return ast.parse(text, mode="eval").body
    except SyntaxError as e:
        raise EffectError(f"[{eid}] bad expression {text!r}: {e.msg}") from None


# ---------------------------------------------------------------- evaluating
def _dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _lookup(ctx: dict, path: str):
    cur = ctx
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            raise EffectError(f"unknown name '{path}'")
        cur = cur[p]
    return cur


def _assign(ctx: dict, path: str, value):
    parts = path.split(".")
    cur = ctx
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            raise EffectError(f"can't assign to '{path}'")
        cur = cur[p]
    cur[parts[-1]] = value


def _eval(node, ctx):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _lookup(ctx, _dotted(node))
    if isinstance(node, ast.Call):
        if node.args:
            if isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
                return _FUNCS[node.func.id](*[_eval(a, ctx) for a in node.args])
            raise EffectError("only max/min/abs/int/round can take arguments in an expression")
        return _lookup(ctx, _dotted(node.func))  # name() just reads the value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.UAdd):
            return +v
    if isinstance(node, ast.BoolOp):
        vals = (_eval(v, ctx) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            if type(op) not in _CMP or not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    raise EffectError(f"unsupported expression ({type(node).__name__})")


def _exec(stmts, ctx, actions):
    for st in stmts:
        if st[0] == "if":
            for cond, body in st[1]:
                if _eval(cond, ctx):
                    _exec(body, ctx, actions)
                    break
            else:
                _exec(st[2], ctx, actions)
        elif st[0] == "assign":
            val = _eval(st[2], ctx)
            _assign(ctx, st[1], int(val) if isinstance(val, float) and val == int(val) else val)
        elif st[0] == "call":
            args = [_eval(a, ctx) for a in st[2]]
            if st[1] in ("dmg.plr", "heal.plr") and len(args) == 1:
                actions.append({"type": st[1].split(".")[0], "amount": int(args[0])})
            else:
                raise EffectError(f"unknown action {st[1]}({len(args)} args)")


def make_ctx(entity: dict, level: int = 1, dmg_out: int = 0, dmg_in: int = 0) -> dict:
    return {"plr": {"output": {"dmg": dmg_out}, "input": {"dmg": dmg_in},
                    "hp": entity.get("hp", 0), "max_hp": entity.get("max_hp", 0)},
            "level": level}


def run_hook(effect: dict, hook: str, ctx: dict) -> list[dict]:
    """Run one hook of an effect against ctx (modified in place). Returns actions."""
    actions: list[dict] = []
    _exec(effect["hooks"].get(hook, []), ctx, actions)
    return actions


def validate(source_block: str, eid: str = "new") -> None:
    """Parse and dry-run every hook; raises EffectError when something is wrong."""
    effs = parse_effects(f"{eid}: {{\n{source_block}\n}}\n")
    if eid not in effs:
        raise EffectError("could not read the effect body")
    for hook in effs[eid]["hooks"]:
        run_hook(effs[eid], hook, make_ctx({"hp": 10, "max_hp": 10}, 1, 7, 7))
