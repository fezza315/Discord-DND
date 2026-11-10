"""Message log for the Dev section's `logs` channel.

Every server message gets ONE embed in the log channel. That same embed is edited later:
  * edited message  -> the original and every edited version are kept as fields
  * deleted message -> the embed turns red with a 🗑️ DELETED mark (the content stays)

The id of each log embed is kept in message_log.json so it still works after a restart.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import discord

from common import ROOT, write_text

LOG_FILE = ROOT / "message_log.json"
GREY, ORANGE, RED = 0x6B7280, 0xF0A020, 0xE03030
FIELD_MAX = 1000
BUDGET = 5200          # keep each embed under Discord's 6000 character cap


def _cut(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


class MessageLog:
    def __init__(self, path: Path = LOG_FILE, cap: int = 10000):
        self.path, self.cap = path, cap
        self.entries: dict[str, dict] = {}
        self.dirty = False

    # ------------------------------------------------------------ storage
    def load(self) -> None:
        try:
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = {}

    def save(self) -> None:
        if not self.dirty:
            return
        while len(self.entries) > self.cap:
            self.entries.pop(next(iter(self.entries)))
        write_text(self.path, json.dumps(self.entries))
        self.dirty = False

    def get(self, mid) -> dict | None:
        return self.entries.get(str(mid))

    # ------------------------------------------------------------ recording
    def record(self, msg: discord.Message) -> dict:
        atts = [f"{a.filename}: {a.url}" for a in msg.attachments]
        if msg.stickers:
            atts += [f"sticker: {s.name}" for s in msg.stickers]
        e = {"id": msg.id, "ch": msg.channel.id, "chname": getattr(msg.channel, "name", "?"),
             "gid": msg.guild.id if msg.guild else 0, "author": msg.author.id,
             "aname": str(msg.author.display_name), "bot": msg.author.bot,
             "created": msg.created_at.timestamp(), "versions": [{"c": msg.content or "", "t": msg.created_at.timestamp()}],
             "deleted": None, "log": None, "atts": atts, "embeds": len(msg.embeds)}
        self.entries[str(msg.id)] = e
        self.dirty = True
        return e

    def stub(self, mid: int, ch: int, gid: int, chname: str = "?", author: int = 0, aname: str = "unknown") -> dict:
        """A message we never saw being created (it is older than the bot)."""
        e = {"id": mid, "ch": ch, "chname": chname, "gid": gid, "author": author, "aname": aname, "bot": False,
             "created": ((mid >> 22) + 1420070400000) / 1000, "versions": [{"c": None, "t": None}],
             "deleted": None, "log": None, "atts": [], "embeds": 0}
        self.entries[str(mid)] = e
        self.dirty = True
        return e

    def edited(self, e: dict, new_content: str, ts: float | None = None) -> bool:
        if (e["versions"][-1]["c"] or "") == (new_content or ""):
            return False
        e["versions"].append({"c": new_content or "", "t": ts or time.time()})
        self.dirty = True
        return True

    def deleted(self, e: dict, ts: float | None = None) -> None:
        e["deleted"] = ts or time.time()
        self.dirty = True

    # ------------------------------------------------------------ the embed
    def embed(self, e: dict) -> discord.Embed:
        vers = e["versions"]
        edited = len(vers) > 1
        colour = RED if e["deleted"] else ORANGE if edited else GREY
        status = []
        if e["deleted"]:
            status.append("🗑️ **DELETED**")
        if edited:
            status.append(f"✏️ edited {len(vers) - 1}×")
        current = vers[-1]["c"]
        body = "(content not available — original message predates logging, or the Message Content intent is off)" \
            if current is None else (current or "*(no text)*")
        emb = discord.Embed(colour=colour, description=_cut(body, 3000),
                            url=f"https://discord.com/channels/{e['gid']}/{e['ch']}/{e['id']}",
                            timestamp=datetime.fromtimestamp(e["created"], timezone.utc))
        emb.title = _cut(("🗑️ " if e["deleted"] else "✏️ " if edited else "") + f"#{e['chname']} — {e['aname']}"
                         + (" 🤖" if e.get("bot") else ""), 250)
        if status:
            emb.add_field(name="Status", value=" · ".join(status), inline=False)
        used = len(emb.description or "") + len(emb.title or "")
        if edited:
            shown, skipped = [], 0
            # always keep the original, then as many of the newest edits as fit
            order = [0] + list(range(len(vers) - 1, 0, -1))
            for idx in order:
                v = vers[idx]
                text = _cut("(unknown)" if v["c"] is None else (v["c"] or "*(no text)*"), FIELD_MAX)
                cost = len(text) + 20
                if used + cost > BUDGET or len(shown) >= 22:
                    skipped += 1
                    continue
                used += cost
                when = f" — <t:{int(v['t'])}:T>" if v.get("t") and idx else ""
                shown.append((idx, f"{'Original' if idx == 0 else f'Edit {idx}'}{when}", text))
            for idx, name, text in sorted(shown):
                emb.add_field(name=name, value=text, inline=False)
            if skipped:
                emb.add_field(name="…", value=f"{skipped} middle edit(s) omitted to fit Discord's embed limit.", inline=False)
        if e["deleted"]:
            emb.add_field(name="Deleted", value=f"<t:{int(e['deleted'])}:F> (<t:{int(e['deleted'])}:R>)", inline=False)
        if e["atts"]:
            emb.add_field(name="Attachments", value=_cut("\n".join(e["atts"]), FIELD_MAX), inline=False)
        emb.set_footer(text=f"message {e['id']} • author {e['author']}")
        return emb
