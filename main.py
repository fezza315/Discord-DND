#!/usr/bin/env python3
"""DND discord bot.

    1. fill in BOT_TOKEN and GUILD_ID in config.py
    2. pip install -r requirements.txt
    3. python main.py          (python main.py --check  tests the data files without connecting)
    4. in your server run /first-start dm:@YourDM   (see SETUP.txt for the Discord portal / server settings)

Layout: this file is the "main.py root". Game rules live in combat.py / common.py / effects_engine.py, the
scripts in "DM stuff" and "player data" do the file work, this file only talks to Discord.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import logging
import shlex
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import tasks  # noqa: E402

import combat  # noqa: E402
import config  # noqa: E402
from common import (FSLASH, ROOT, STATS, DEFAULT_SETTINGS, default_state, ent_get, ent_save, is_monster,
                    list_char_ids, load_bindings, load_char, load_effects, load_items, load_other, load_rooms,
                    load_script, load_state, parse_dice, parse_effect_refs, save_rooms, save_state, say)  # noqa: E402
from msglog import MessageLog  # noqa: E402

log = logging.getLogger("dndbot")

# --------------------------------------------------------------------------- scripts
PT = load_script("player data/player turn.py")
CC = load_script("player data/create char.py")
BC = load_script("player data/bind char.py")
IC = load_script("player data/import char.py")
CALC = load_script("player data/calculate per player power.py")
COMB = load_script("player data/combine and avg group power.py")
RR = load_script("DM stuff/rooms/room routing.py")
CR = load_script("DM stuff/create/create rooms.py")
CI = load_script("DM stuff/create/create item.py")
CRACE = load_script("DM stuff/create/create race.py")
CCLASS = load_script("DM stuff/create/create class.py")
CEFF = load_script("DM stuff/create/create effect.py")
CMON = load_script("DM stuff/monsters/create monster.py")
SMON = load_script("DM stuff/monsters/spawn monster.py")
SE = load_script("DM stuff/start event.py")
AN = load_script("DM stuff/annouce event.py")
PP = load_script(f"DM stuff/play{FSLASH}pause players turns.py")
PEEK = load_script("DM stuff/player item stats and effects peek.py")

GUILD = discord.Object(id=config.GUILD_ID or 0)
LOCK = asyncio.Lock()
MLOG = MessageLog()
UPLOADS = ROOT / "_uploads"

# which channel keys exist, and where each kind of thing is mirrored
CHANNEL_KEYS = ("overview", "char_sheets", "mapping", "event_planning", "dm_logs",
                "dev_logs", "server_messages", "play", "map_view", "apply_to_play")
LOG_CHANNELS = ("dm_logs", "dev_logs", "server_messages")
DM_SECTION = ("overview", "char_sheets", "mapping", "event_planning")   # mirrored to server-messages
PLAYER_SECTION = ("play", "map_view", "char_sheets")                    # mirrored to dm-logs
NO_PINGS = discord.AllowedMentions.none()
PINGS = discord.AllowedMentions(users=True, roles=False, everyone=False)

# --------------------------------------------------------------------------- log queue
LOGQ: list[tuple[str, str]] = []


def qlog(channel: str, text: str) -> None:
    """Queue a line for dm_logs / server_messages (posted by the pump, never pings anyone)."""
    LOGQ.append((channel, f"<t:{int(time.time())}:T> {text}"))


# --------------------------------------------------------------------------- bot
class LoggingTree(app_commands.CommandTree):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        try:
            if i.type == discord.InteractionType.application_command and i.command:
                args = []
                for k, v in i.namespace:
                    if isinstance(v, discord.Attachment):
                        v = v.filename
                    elif hasattr(v, "mention"):
                        v = v.mention
                    args.append(f"{k}={str(v)[:120]}")
                st = load_state()
                where = f" in <#{i.channel_id}>" if i.channel_id else ""
                line = f"`/{i.command.qualified_name}` {' '.join(args)}{where}"
                if str(i.user.id) == str(st["setup"].get("dm_id")):
                    qlog("server_messages", f"🎛️ **DM** {i.user.mention} used {line}")
                else:
                    qlog("dm_logs", f"🕹️ {i.user.mention} used {line}")
        except Exception:
            log.exception("command logging failed")
        return True


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True            # roles + nicknames          (privileged - enable in the portal)
        intents.message_content = True    # the message log            (privileged - enable in the portal)
        super().__init__(intents=intents)
        self.tree = LoggingTree(self)
        self.started_at = time.time()

    async def setup_hook(self):
        self.add_dynamic_items(JoinButton, ApprovalButton)
        MLOG.load()
        await self.tree.sync(guild=GUILD)
        pump.start()
        threading.Thread(target=console_loop, args=(asyncio.get_running_loop(),), daemon=True).start()

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        qlog("server_messages", f"🟢 Bot online as **{self.user}** (guild {config.GUILD_ID}).")

    async def close(self):
        qlog("server_messages", "🔴 Bot shutting down.")
        with contextlib.suppress(Exception):
            await drain_logq()
            MLOG.save()
        await super().close()


client = Bot()
tree = client.tree


# --------------------------------------------------------------------------- state helpers
@contextlib.asynccontextmanager
async def mutate():
    """with mutate() as state:  -> load, let the block change it, save. One at a time."""
    async with LOCK:
        state = load_state()
        yield state
        save_state(state)


def guild() -> Optional[discord.Guild]:
    return client.get_guild(config.GUILD_ID)


def channel_for(state: dict, key: str):
    cid = state["setup"]["channels"].get(key)
    g = guild()
    return g.get_channel(int(cid)) if (g and cid) else None


def is_dm_user(state: dict, uid) -> bool:
    return str(uid) == str(state["setup"].get("dm_id"))


def reply_text(text: str) -> str:
    return text if len(text) <= 1990 else text[:1980] + "…"


async def reply(i: discord.Interaction, text: str = None, **kw):
    kw.setdefault("ephemeral", True)
    if text is not None:
        text = reply_text(text)
    if i.response.is_done():
        await i.followup.send(text, **kw)
    else:
        await i.response.send_message(text, **kw)


def chunks(text: str, n: int = 1900) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > n:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:n])
            line = line[n:]
        if len(cur) + len(line) + 1 > n:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


# --------------------------------------------------------------------------- outbox / logs / roles
async def drain_logq() -> None:
    if not LOGQ:
        return
    items, LOGQ[:] = list(LOGQ), []
    async with LOCK:
        state = load_state()
    by: dict[str, list[str]] = {}
    for ch, text in items:
        by.setdefault(ch, []).append(text)
    for ch, lines in by.items():
        target = channel_for(state, ch)
        if not target:
            continue
        for part in chunks("\n".join(lines)):
            try:
                await target.send(part, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.exception("couldn't write to log channel %s", ch)


async def deliver(state: dict, m: dict) -> None:
    key = m["channel"]
    target = channel_for(state, key)
    text = m.get("content") or ""
    mirror = text if text else "(file)"
    if m.get("file"):
        mirror += " 🖼️"
    if key in PLAYER_SECTION:
        qlog("dm_logs", f"📜 **[#{key}]** {mirror[:1500]}")
    if key in DM_SECTION:
        qlog("server_messages", f"🗂️ **[#{key}]** {mirror[:1500]}")
    if not target:
        log.warning("no channel configured for '%s' - run /first-start", key)
        return
    parts = chunks(text) if text else [""]
    path = m.get("file")
    for n, part in enumerate(parts):
        f = discord.File(path) if (path and n == len(parts) - 1 and Path(path).exists()) else None
        if not part and not f:
            continue
        try:
            await target.send(part or None, file=f, allowed_mentions=PINGS)
        except discord.HTTPException:
            log.exception("couldn't post to %s", key)
            qlog("server_messages", f"⚠️ couldn't post a message to **#{key}** (check the bot's permissions there).")


async def flush() -> None:
    """Post everything queued in state['outbox'], sync roles/nicknames if asked, post queued log lines."""
    async with LOCK:
        state = load_state()
        items, want_sync = state["outbox"], state["role_sync"]
        if items or want_sync:
            state["outbox"], state["role_sync"] = [], False
            save_state(state)
    for m in items:
        await deliver(state, m)
    if want_sync:
        await sync_roles()
    await drain_logq()


@tasks.loop(seconds=2)
async def pump():
    if not client.is_ready():
        return
    try:
        await flush()
        MLOG.save()
    except Exception:
        log.exception("pump failed")


async def sync_roles() -> None:
    """player / current turn / not current turn roles + nicknames from the real game state."""
    state = load_state()
    g = guild()
    if not g or not state["setup"]["configured"]:
        return
    R = {k: g.get_role(int(v)) for k, v in state["setup"]["roles"].items() if v}
    player, cur, notcur = R.get("player"), R.get("current_turn"), R.get("not_current_turn")
    if not (player and cur and notcur):
        return
    key, ctrl = PT.check(state)
    camp = state["campaign"]
    active = ctrl if (camp["started"] and not camp["paused"] and ctrl not in (None, "DM")) else None
    by_user: dict[str, list[str]] = {}
    for cid, uid in load_bindings().items():
        by_user.setdefault(uid, []).append(cid)
    for uid, cids in by_user.items():
        member = g.get_member(int(uid))
        if member is None:
            try:
                member = await g.fetch_member(int(uid))
            except discord.HTTPException:
                continue
        want = {player, cur if uid == active else notcur}
        have = set(member.roles)
        add = [r for r in want if r not in have]
        rem = [r for r in ({cur, notcur} - want) if r in have]
        try:
            if add:
                await member.add_roles(*add, reason="DND bot turn sync")
            if rem:
                await member.remove_roles(*rem, reason="DND bot turn sync")
        except discord.HTTPException as e:
            qlog("server_messages", f"⚠️ role sync failed for {member.mention}: {e} (is the bot's role above mine?)")
        cid = key if uid == active else sorted(cids, key=int)[0]
        c = load_char(cid)
        nick = (c["nicname"] if c else "")[:32]
        if c and nick and member.nick != nick:
            try:
                await member.edit(nick=nick, reason="DND bot: character name")
            except discord.HTTPException:
                pass  # the server owner / higher roles can't be renamed
    for member in player.members:
        if str(member.id) not in by_user:
            with contextlib.suppress(discord.HTTPException):
                await member.remove_roles(player, cur, notcur, reason="no characters bound")


# --------------------------------------------------------------------------- message log (Dev section: logs)
_entry_locks: dict[int, asyncio.Lock] = {}


def _excluded_channel(state: dict, channel_id: int) -> bool:
    chans = state["setup"]["channels"]
    return any(str(chans.get(k)) == str(channel_id) for k in LOG_CHANNELS)


async def push_entry(entry: dict) -> None:
    """Create or edit the one log embed that belongs to this message."""
    state = load_state()
    target = channel_for(state, "dev_logs")
    if not target:
        return
    lock = _entry_locks.setdefault(entry["id"], asyncio.Lock())
    async with lock:
        emb = MLOG.embed(entry)
        try:
            if entry.get("log"):
                await target.get_partial_message(entry["log"]).edit(embed=emb)
            else:
                sent = await target.send(embed=emb, allowed_mentions=NO_PINGS)
                entry["log"] = sent.id
                MLOG.dirty = True
        except discord.NotFound:
            sent = await target.send(embed=emb, allowed_mentions=NO_PINGS)
            entry["log"] = sent.id
            MLOG.dirty = True
        except discord.HTTPException:
            log.exception("message log failed")
    if len(_entry_locks) > 2000:
        _entry_locks.clear()


@client.event
async def on_message(m: discord.Message):
    if m.guild is None or m.guild.id != config.GUILD_ID:
        return
    if _excluded_channel(load_state(), m.channel.id):
        return
    await push_entry(MLOG.record(m))


@client.event
async def on_raw_message_edit(p: discord.RawMessageUpdateEvent):
    if p.guild_id != config.GUILD_ID or "content" not in p.data:
        return
    if _excluded_channel(load_state(), p.channel_id):
        return
    e = MLOG.get(p.message_id)
    if e is None:
        ch = client.get_channel(p.channel_id)
        a = p.data.get("author") or {}
        e = MLOG.stub(p.message_id, p.channel_id, p.guild_id, getattr(ch, "name", "?"),
                      int(a.get("id", 0) or 0), a.get("username", "unknown"))
        await push_entry(e)
    if MLOG.edited(e, p.data["content"]):
        await push_entry(e)


@client.event
async def on_raw_message_delete(p: discord.RawMessageDeleteEvent):
    await _mark_deleted(p.guild_id, p.channel_id, p.message_id)


@client.event
async def on_raw_bulk_message_delete(p: discord.RawBulkMessageDeleteEvent):
    for mid in p.message_ids:
        await _mark_deleted(p.guild_id, p.channel_id, mid)


async def _mark_deleted(guild_id, channel_id, message_id) -> None:
    if guild_id != config.GUILD_ID or _excluded_channel(load_state(), channel_id):
        return
    e = MLOG.get(message_id)
    if e is None:
        ch = client.get_channel(channel_id)
        e = MLOG.stub(message_id, channel_id, guild_id, getattr(ch, "name", "?"))
    MLOG.deleted(e)
    await push_entry(e)


# misc server events -> server-messages
@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id == config.GUILD_ID:
        qlog("server_messages", f"👋 {member.mention} (`{member}`) joined the server.")


@client.event
async def on_member_remove(member: discord.Member):
    if member.guild.id == config.GUILD_ID:
        qlog("server_messages", f"🚪 `{member}` left the server.")


@client.event
async def on_guild_channel_create(ch):
    if ch.guild.id == config.GUILD_ID:
        qlog("server_messages", f"➕ channel created: **#{ch.name}**")


@client.event
async def on_guild_channel_delete(ch):
    if ch.guild.id == config.GUILD_ID:
        qlog("server_messages", f"➖ channel deleted: **#{ch.name}**")


# --------------------------------------------------------------------------- errors
@tree.error
async def on_tree_error(i: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return await reply(i, str(error) or "You can't use that.")
    err = getattr(error, "original", error)
    if isinstance(err, ValueError):
        return await reply(i, f"❌ {err}")
    log.error("command error: %s", "".join(traceback.format_exception(error)))
    qlog("server_messages", f"💥 error in `/{i.command.qualified_name if i.command else '?'}` by {i.user.mention}: `{err!r}`"[:1800])
    await reply(i, "💥 Something went wrong - the DM has been told (see #server-messages).")


def dm_only():
    async def pred(i: discord.Interaction) -> bool:
        if is_dm_user(load_state(), i.user.id):
            return True
        raise app_commands.CheckFailure("🔒 Only the DM can use that.")
    return app_commands.check(pred)


def configured_only():
    async def pred(i: discord.Interaction) -> bool:
        if load_state()["setup"]["configured"]:
            return True
        raise app_commands.CheckFailure("The server isn't set up yet - an admin needs to run /first-start.")
    return app_commands.check(pred)


# --------------------------------------------------------------------------- autocomplete
def _choices(pairs, current: str):
    cur = (current or "").lower()
    out = [app_commands.Choice(name=n[:100], value=str(v)[:100]) for n, v in pairs if cur in n.lower() or cur in str(v).lower()]
    return out[:25]


async def ac_chars(i, current):
    return _choices(((f"#{c} {load_char(c)['name']}", c) for c in list_char_ids()), current)


async def ac_targets(i, current):
    st = load_state()
    pairs = [(f"{m['name']} ({k}) {m['hp']}/{m['max_hp']} HP", k) for k, m in st["monsters"].items()]
    for cid in load_bindings():
        c = load_char(cid)
        if c and c["hp"] > 0:
            pairs.append((f"{c['name']} (player #{cid}) {c['hp']}/{c['max_hp']} HP", cid))
    return _choices(pairs, current)


async def ac_monsters(i, current):
    return _choices(((f"{m['name']} ({k})", k) for k, m in load_state()["monsters"].items()), current)


async def ac_templates(i, current):
    return _choices(((f"{t['name']} [{k}] hp {t['hp']}", k) for k, t in CMON.list_templates().items()), current)


async def ac_items_all(i, current):
    return _choices(((f"{it['name']} [{k}]", k) for k, it in load_items().items()), current)


async def ac_my_items(i, current):
    st = load_state()
    key, ctrl = PT.check(st)
    cids = [key] if (key and ctrl == str(i.user.id)) else BC.chars_of(i.user.id)
    pairs = []
    items = load_items()
    for cid in cids:
        c = load_char(cid)
        if not c:
            continue
        for where in ("equipped", "bag"):
            for slot, e in c["inv"][where].items():
                it = items.get(e["item"])
                pairs.append((f"{it['name'] if it else e['item']} ({where} {slot})", e["item"]))
    return _choices(pairs, current)


async def ac_effects(i, current):
    return _choices(((e["name"], k) for k, e in load_effects().items()), current)


async def ac_races(i, current):
    return _choices(((r["name"], k) for k, r in load_other()["race"].items()), current)


async def ac_classes(i, current):
    return _choices(((r["name"], k) for k, r in load_other()["class"].items()), current)


async def ac_move(i, current):
    st = load_state()
    key, _ = PT.check(st)
    rooms = load_rooms()
    here = st["positions"].get(str(key), st["map"].get("start_room"))
    ids = RR.neighbors(here) if here else sorted(rooms)
    return _choices(((f"{rooms[r]['kind']} {r}" + (f" — {rooms[r]['description'][:50]}" if rooms[r]["description"] and rooms[r]["marked"] else ""), r)
                     for r in ids if r in rooms), current)


async def ac_rooms(i, current):
    return _choices(((f"{r['kind']} {rid}", rid) for rid, r in load_rooms().items()), current)


def find_entity(state: dict, ref: str) -> Optional[str]:
    ref = (ref or "").strip()
    if ref in state["monsters"]:
        return ref
    if ref.isdigit() and load_char(ref):
        return ref
    low = ref.lower()
    for k, m in state["monsters"].items():
        if m["name"].lower() == low:
            return k
    for cid in load_bindings():
        c = load_char(cid)
        if c and c["name"].lower() == low:
            return cid
    return None


# --------------------------------------------------------------------------- turn flow helpers
def guard_turn(state: dict, uid) -> tuple[Optional[str], Optional[str]]:
    """(char id, error). Always asks player turn.py (check) who is up."""
    c = state["campaign"]
    if not c["started"]:
        return None, "The campaign hasn't started yet."
    if c["paused"]:
        return None, f"Turns are paused ({c['pause_reason']})."
    key, ctrl = PT.check(state)
    if key is None or ctrl == "DM":
        return None, "It's not a player's turn."
    if ctrl != str(uid):
        return None, "It's not your turn."
    return key, None


def end_turn_text(state: dict) -> str:
    f = combat.finish_turn(state)
    lines = list(f["log"])
    players = [k for k in state["turn"]["order"] if not is_monster(k)]
    if state["campaign"]["started"] and not players:
        lines.append("☠️ **The party has fallen.** The DM will take it from here.")
        return "\n" + "\n".join(lines)
    lines.append(f"➡️ It is now {PT.describe_turn(state)}." + (f"  (round {f['round']})" if f["key"] and state['turn']['index'] == 0 else ""))
    return "\n" + "\n".join(lines)


async def finish_action(text: str, state: dict) -> None:
    say(state, "play", text)
    state["role_sync"] = True


# --------------------------------------------------------------------------- character creation (views)
class JoinButton(discord.ui.DynamicItem[discord.ui.Button], template=r"dnd:join"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Join the session", style=discord.ButtonStyle.green,
                                           custom_id="dnd:join", emoji="🎲"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, i: discord.Interaction):
        uid = str(i.user.id)
        async with mutate() as st:
            if uid not in st["join_tokens"]:
                st["join_tokens"].append(uid)
        qlog("dm_logs", f"🎲 {i.user.mention} clicked **Join the session**.")
        try:
            await i.user.send(creation_template_text(), view=LauncherView(i.user.id))
            await reply(i, "📬 Check your DMs - I've sent you the character creator.")
        except discord.Forbidden:
            await reply(i, "I can't DM you. Turn on *Allow direct messages from server members* for this server "
                           "(Server name ▸ Privacy Settings), then press the button again - or just run `/create-char`.")


class ApprovalButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"dnd:cc:(?P<act>allow|deny):(?P<uid>\d+)"):
    def __init__(self, act: str, uid: int):
        super().__init__(discord.ui.Button(
            label="Allow" if act == "allow" else "Disallow",
            style=discord.ButtonStyle.green if act == "allow" else discord.ButtonStyle.red,
            custom_id=f"dnd:cc:{act}:{uid}"))
        self.act, self.uid = act, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["uid"]))

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if is_dm_user(load_state(), i.user.id):
            return True
        await reply(i, "Only the DM can answer this.")
        return False

    async def callback(self, i: discord.Interaction):
        user = await client.fetch_user(self.uid)
        if self.act == "allow":
            async with mutate() as st:
                if str(self.uid) not in st["join_tokens"]:
                    st["join_tokens"].append(str(self.uid))
            await i.response.edit_message(content=f"✅ Allowed {user.mention} to create a char.", view=None)
            qlog("server_messages", f"✅ DM allowed {user.mention} to create a character.")
            qlog("dm_logs", f"✅ {user.mention} was allowed to create a character.")
            with contextlib.suppress(discord.HTTPException):
                await user.send("✅ The DM said yes! Create your character below.\n\n" + creation_template_text(),
                                view=LauncherView(self.uid))
        else:
            await i.response.edit_message(content=f"❌ Disallowed {user.mention}.", view=None)
            qlog("server_messages", f"❌ DM disallowed {user.mention}'s character request.")
            with contextlib.suppress(discord.HTTPException):
                await user.send("❌ The DM said no to a new character for now.")


def creation_template_text() -> str:
    o = load_other()
    return ("**Create your character**\nPress the button below, or in the server run:\n"
            "`/create-char name:<your name> race:<origin> class:<class>`\n"
            f"Origins: {', '.join(o['race']) or '(none yet)'}\nClasses: {', '.join(o['class']) or '(none yet)'}\n"
            "⚠️ Name, origin and class **can't be changed** once you confirm. Your stats are rolled live (4d6, drop the lowest).")


class LauncherView(discord.ui.View):
    def __init__(self, uid: int):
        super().__init__(timeout=3600)
        self.uid = uid

    @discord.ui.button(label="Open character creator", style=discord.ButtonStyle.primary, emoji="🧙")
    async def open(self, i: discord.Interaction, b: discord.ui.Button):
        v = CreatorView(i.user.id)
        await i.response.send_message(embed=v.embed(), view=v)


class _Pick(discord.ui.Select):
    def __init__(self, kind: str, options: dict):
        opts = [discord.SelectOption(label=v["name"][:100], value=k, description=(v["description"] or "")[:100] or None)
                for k, v in list(options.items())[:25]]
        super().__init__(placeholder=f"Choose your {'origin' if kind == 'race' else 'class'}", options=opts, row=0 if kind == "race" else 1)
        self.kind = kind

    async def callback(self, i: discord.Interaction):
        setattr(self.view, "race" if self.kind == "race" else "cls_", self.values[0])
        await i.response.edit_message(embed=self.view.embed(), view=self.view)


class NameModal(discord.ui.Modal, title="Character name"):
    name = discord.ui.TextInput(label="Name", max_length=32, placeholder="e.g. Bram Ironfist")

    def __init__(self, view):
        super().__init__()
        self.view_ = view

    async def on_submit(self, i: discord.Interaction):
        self.view_.name = str(self.name).strip()
        await i.response.edit_message(embed=self.view_.embed(), view=self.view_)


class CreatorView(discord.ui.View):
    def __init__(self, uid: int, name=None, race=None, cls=None):
        super().__init__(timeout=1800)
        self.uid, self.name, self.race, self.cls_ = uid, name, race, cls
        o = load_other()
        if o["race"]:
            self.add_item(_Pick("race", o["race"]))
        if o["class"]:
            self.add_item(_Pick("class", o["class"]))

    def embed(self) -> discord.Embed:
        o = load_other()
        e = discord.Embed(title="🧙 Character creator", colour=0x7C5CFF)
        e.add_field(name="Name", value=self.name or "*not set*")
        e.add_field(name="Origin", value=o["race"].get(self.race, {}).get("name", "*not set*") if self.race else "*not set*")
        e.add_field(name="Class", value=o["class"].get(self.cls_, {}).get("name", "*not set*") if self.cls_ else "*not set*")
        e.set_footer(text="Pick an origin and class, set a name, then press Continue.")
        return e

    @discord.ui.button(label="Set name", style=discord.ButtonStyle.secondary, row=2)
    async def set_name(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(NameModal(self))

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary, row=2)
    async def go(self, i: discord.Interaction, b: discord.ui.Button):
        if not (self.name and self.race and self.cls_):
            return await reply(i, "Set a name, origin and class first.")
        await show_confirm(i, self.uid, self.name, self.race, self.cls_)


async def show_confirm(i: discord.Interaction, uid: int, name: str, race: str, cls: str):
    try:
        name, race, cls = CC.validate(name, race, cls)
    except ValueError as e:
        return await reply(i, f"❌ {e}")
    o = load_other()
    text = (f"**Please confirm — this cannot be changed.**\nName: **{name}**\nOrigin: **{o['race'][race]['name']}** "
            f"({o['race'][race]['description']})\nClass: **{o['class'][cls]['name']}** ({o['class'][cls]['description']})")
    v = ConfirmView(uid, name, race, cls)
    if i.response.is_done():
        await i.followup.send(text, view=v, ephemeral=True)
    elif i.type == discord.InteractionType.component:
        await i.response.edit_message(content=text, embed=None, view=v)
    else:
        await i.response.send_message(text, view=v, ephemeral=True)


class ConfirmView(discord.ui.View):
    def __init__(self, uid, name, race, cls):
        super().__init__(timeout=900)
        self.uid, self.name, self.race, self.cls = uid, name, race, cls

    @discord.ui.button(label="Confirm & roll stats", style=discord.ButtonStyle.green, emoji="🎲")
    async def confirm(self, i: discord.Interaction, b: discord.ui.Button):
        await finalize_character(i, self.uid, self.name, self.race, self.cls)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.edit_message(content="Cancelled. (Your permission to create a character is still valid.)", view=None)


async def finalize_character(i: discord.Interaction, uid: int, name: str, race: str, cls: str):
    if i.user.id != uid:
        return await reply(i, "That isn't yours.")
    await i.response.edit_message(content="🎲 Rolling your stats…", embed=None, view=None)
    rolled = CC.roll_all_stats()
    shown = []
    for s, v, rolls in rolled:
        shown.append(f"**{s.upper()}** {rolls} → drop lowest → **{v}**")
        await i.edit_original_response(content="🎲 Rolling your stats…\n" + "\n".join(shown))
        await asyncio.sleep(1.1)
    async with mutate() as st:
        if str(uid) not in st["join_tokens"]:
            return await i.edit_original_response(content="❌ You don't have permission to create a character right now "
                                                          "(already used?). Use `/create-char` to ask the DM.")
        st["join_tokens"].remove(str(uid))
        char = CC.create(name, race, cls, user_id=str(uid), rolled=rolled, state=st)
        init = st["turn"]["initiative"][char["id"]]
        sheet = CC.sheet_text(char)
        say(st, "char_sheets", f"🆕 **New character** for <@{uid}>:\n{sheet}")
        say(st, "play", f"🎲 **{char['name']}** joins the party and rolls **{init}** for initiative.\n"
                        f"**Turn order**\n{PT.order_text(st)}")
        st["role_sync"] = True
    await i.edit_original_response(content="✅ **Your character is ready!**\n(bonuses from your origin and class are included)\n\n"
                                           + sheet + "\n\nYour server nickname will change to your character's name.")
    qlog("dm_logs", f"🆕 <@{uid}> created **{char['name']}** (#{char['id']}, {race} {cls}).")
    await flush()


# --------------------------------------------------------------------------- /first-start
@tree.command(name="first-start", description="One-time setup: roles, channels, logs and the join message", guild=GUILD)
@app_commands.describe(
    dm="The Dungeon Master", overview="DM overview channel", char_sheets="DM char-sheets channel",
    mapping="DM mapping channel", event_planning="DM event-planning channel", dm_logs="DM dm-logs channel (player + gameplay log)",
    dev_logs="Dev logs channel (every message, edits, deletions)", server_messages="Dev server-messages channel (DM stuff + misc)",
    play="players: play channel", map_view="players: map-view channel", apply_to_play="players: apply-to-play channel",
    dm_role="existing DM role", player_role="existing player role", current_turn_role="existing current-turn role",
    not_current_turn_role="existing not-current-turn role", dev_role="optional role that may see the Dev section")
async def first_start(i: discord.Interaction, dm: discord.Member,
                      overview: Optional[discord.TextChannel] = None, char_sheets: Optional[discord.TextChannel] = None,
                      mapping: Optional[discord.TextChannel] = None, event_planning: Optional[discord.TextChannel] = None,
                      dm_logs: Optional[discord.TextChannel] = None, dev_logs: Optional[discord.TextChannel] = None,
                      server_messages: Optional[discord.TextChannel] = None, play: Optional[discord.TextChannel] = None,
                      map_view: Optional[discord.TextChannel] = None, apply_to_play: Optional[discord.TextChannel] = None,
                      dm_role: Optional[discord.Role] = None, player_role: Optional[discord.Role] = None,
                      current_turn_role: Optional[discord.Role] = None, not_current_turn_role: Optional[discord.Role] = None,
                      dev_role: Optional[discord.Role] = None):
    st0 = load_state()
    if not (i.user.guild_permissions.administrator or is_dm_user(st0, i.user.id)):
        return await reply(i, "🔒 Only a server admin (or the DM) can run /first-start.")
    await i.response.defer(ephemeral=True, thinking=True)
    g = i.guild
    everyone, me = g.default_role, g.me
    try:
        async def role(existing, name, **kw):
            return existing or await g.create_role(name=name, reason="DND bot setup", **kw)

        r_dm = await role(dm_role, "DM", colour=discord.Colour.gold(), hoist=True, mentionable=True)
        r_player = await role(player_role, "player", colour=discord.Colour.blue())
        r_cur = await role(current_turn_role, "current turn", colour=discord.Colour.green(), hoist=True)
        r_not = await role(not_current_turn_role, "not current turn", colour=discord.Colour.dark_grey())
        await dm.add_roles(r_dm, reason="DND bot setup")

        bot_ow = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                                             read_message_history=True, use_application_commands=True)
        hidden = {everyone: discord.PermissionOverwrite(view_channel=False), me: bot_ow}
        dm_cat_ow = {**hidden, r_dm: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        dev_ow = {**hidden}
        if dev_role:
            dev_ow[dev_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)

        async def make(existing, name, category, topic):
            return existing or await category.create_text_channel(name, topic=topic, reason="DND bot setup")

        dm_cat = await g.create_category("Dm", overwrites=dm_cat_ow, reason="DND bot setup") if not all(
            (overview, char_sheets, mapping, event_planning, dm_logs)) else None
        dev_cat = await g.create_category("Dev", overwrites=dev_ow, reason="DND bot setup") if not (
            dev_logs and server_messages) else None
        pl_cat = await g.create_category("players", reason="DND bot setup") if not all((play, map_view, apply_to_play)) else None

        ch = {
            "overview": await make(overview, "overview", dm_cat, "DM only - pings and overview"),
            "char_sheets": await make(char_sheets, "char-sheets", dm_cat, "Every character sheet"),
            "mapping": await make(mapping, "mapping", dm_cat, "Map previews and uploads"),
            "event_planning": await make(event_planning, "event-planning", dm_cat, "Events start here - /resume to continue"),
            "dm_logs": await make(dm_logs, "dm-logs", dm_cat, "Player + gameplay log"),
            "dev_logs": await make(dev_logs, "logs", dev_cat, "Every server message. Edited/deleted messages are marked."),
            "server_messages": await make(server_messages, "server-messages", dev_cat, "All DM stuff, what the DM does, misc"),
            "play": await make(play, "play", pl_cat, "Only the current turn can talk"),
            "map_view": await make(map_view, "map-view", pl_cat, "Discovered map"),
            "apply_to_play": await make(apply_to_play, "apply-to-play", pl_cat, "Press the button to join"),
        }
        qp = dict(send_messages=False, add_reactions=False, create_public_threads=False, create_private_threads=False,
                  send_messages_in_threads=False)
        dm_play = discord.PermissionOverwrite(view_channel=True, send_messages=True, use_application_commands=True, manage_messages=True)
        await ch["play"].set_permissions(everyone, overwrite=discord.PermissionOverwrite(use_application_commands=False, **qp))
        await ch["play"].set_permissions(r_not, overwrite=discord.PermissionOverwrite(use_application_commands=False, send_messages=False))
        await ch["play"].set_permissions(r_cur, overwrite=discord.PermissionOverwrite(send_messages=True, use_application_commands=True, view_channel=True))
        await ch["play"].set_permissions(r_dm, overwrite=dm_play)
        await ch["play"].set_permissions(me, overwrite=bot_ow)
        await ch["map_view"].set_permissions(everyone, overwrite=discord.PermissionOverwrite(use_application_commands=False, **qp))
        await ch["map_view"].set_permissions(me, overwrite=bot_ow)
        await ch["apply_to_play"].set_permissions(everyone, overwrite=discord.PermissionOverwrite(use_application_commands=True, **qp))
        await ch["apply_to_play"].set_permissions(me, overwrite=bot_ow)
    except discord.Forbidden as e:
        return await i.followup.send(f"❌ I'm missing permissions ({e.text}). Give the bot Administrator, or Manage Roles + "
                                     "Manage Channels + Manage Nicknames, and put its role at the top of the role list.", ephemeral=True)

    async with mutate() as st:
        st["setup"] = {"configured": True, "dm_id": str(dm.id),
                       "channels": {k: str(v.id) for k, v in ch.items()},
                       "roles": {"dm": str(r_dm.id), "player": str(r_player.id), "current_turn": str(r_cur.id),
                                 "not_current_turn": str(r_not.id)}}
    view = discord.ui.View(timeout=None)
    view.add_item(JoinButton())
    await ch["apply_to_play"].send(
        "# 🎲 Join the session\nPress the button and I'll DM you a character creator. "
        "Your name, origin and class can't be changed once confirmed - your stats are rolled live.\n"
        "Use **/sheet**, **/status** and **/create-char** from this channel.", view=view)
    qlog("server_messages", f"🛠️ **/first-start** by {i.user.mention}: DM is {dm.mention}. Created/linked "
                            + ", ".join(f"#{v.name}" for v in ch.values()))
    await flush()
    await i.followup.send(f"✅ Set up. DM is {dm.mention}. Channels: " + ", ".join(v.mention for v in ch.values()) +
                          "\nNext: DM, run `/create campaign` → `/apply` → `/confirm`, and import a map with `/create map`.", ephemeral=True)


# --------------------------------------------------------------------------- /create-char
@tree.command(name="create-char", description="Ask to create a character (the DM gets a request)", guild=GUILD)
@app_commands.describe(name="Character name", race="Origin", char_class="Class")
@app_commands.rename(char_class="class")
@app_commands.autocomplete(race=ac_races, char_class=ac_classes)
@configured_only()
async def create_char(i: discord.Interaction, name: Optional[str] = None, race: Optional[str] = None,
                      char_class: Optional[str] = None):
    st = load_state()
    uid = str(i.user.id)
    if uid not in st["join_tokens"]:
        dm_user = await client.fetch_user(int(st["setup"]["dm_id"]))
        v = discord.ui.View(timeout=None)
        v.add_item(ApprovalButton("allow", i.user.id))
        v.add_item(ApprovalButton("deny", i.user.id))
        text = f"{i.user.mention} wants to create a char"
        try:
            await dm_user.send(text, view=v)
        except discord.Forbidden:
            ch = channel_for(st, "overview")
            if ch:
                await ch.send(f"<@{dm_user.id}> {text}", view=v)
        qlog("server_messages", f"📨 character request from {i.user.mention} sent to the DM.")
        return await reply(i, "📨 Request sent to the DM. I'll DM you when they answer.")
    if name and race and char_class:
        return await show_confirm(i, i.user.id, name, race, char_class)
    v = CreatorView(i.user.id, name, race, char_class)
    await i.response.send_message(embed=v.embed(), view=v, ephemeral=True)


# --------------------------------------------------------------------------- player commands
async def run_turn_action(i: discord.Interaction, fn):
    """Common wrapper: hidden command, check the turn, run fn(state, char id) -> (text, ends_turn) or raise ValueError."""
    await i.response.defer(ephemeral=True)
    err = None
    async with mutate() as st:
        cid, err = guard_turn(st, i.user.id)
        if not err:
            try:
                text, ends = fn(st, cid)
            except ValueError as e:
                err = str(e)
            else:
                if ends:
                    text += end_turn_text(st)
                await finish_action(text, st)
    if err:
        return await i.followup.send(f"❌ {err}", ephemeral=True)
    await flush()
    await i.followup.send("✅ Done.", ephemeral=True)


@tree.command(name="attack", description="Attack something (your command stays hidden)", guild=GUILD)
@app_commands.describe(target="Who to attack", item="Weapon to use (default: what's in your hand)")
@app_commands.autocomplete(target=ac_targets, item=ac_my_items)
@configured_only()
async def attack_cmd(i: discord.Interaction, target: str, item: Optional[str] = None):
    def run(st, cid):
        key = find_entity(st, target)
        if not key:
            raise ValueError(f"I can't find '{target}'.")
        res = combat.attack(st, cid, key, item)
        if not res["ok"]:
            raise ValueError(res["error"])
        return res["text"], True
    await run_turn_action(i, run)


@tree.command(name="use", description="Use a consumable (ends your turn)", guild=GUILD)
@app_commands.autocomplete(item=ac_my_items)
@configured_only()
async def use_cmd(i: discord.Interaction, item: str):
    def run(st, cid):
        res = combat.use_item(st, cid, item)
        if not res["ok"]:
            raise ValueError(res["error"])
        return res["text"], True
    await run_turn_action(i, run)


@tree.command(name="equip", description="Equip something from your bag (free action)", guild=GUILD)
@app_commands.autocomplete(item=ac_my_items)
@configured_only()
async def equip_cmd(i: discord.Interaction, item: str):
    def run(st, cid):
        res = combat.equip(st, cid, item)
        if not res["ok"]:
            raise ValueError(res["error"])
        return res["text"], False
    await run_turn_action(i, run)


@tree.command(name="pass", description="End your turn without doing anything", guild=GUILD)
@configured_only()
async def pass_cmd(i: discord.Interaction):
    def run(st, cid):
        return f"💤 **{load_char(cid)['name']}** passes.", True
    await i.response.defer(ephemeral=True)
    async with mutate() as st:
        key, ctrl = PT.check(st)
        c = st["campaign"]
        if not c["started"] or c["paused"]:
            err = "Nothing is running right now."
        elif key and ctrl == "DM" and is_dm_user(st, i.user.id):
            err = None
            say(st, "play", f"💤 **{ent_get(st, key)['name']}** passes." + end_turn_text(st))
            st["role_sync"] = True
        else:
            cid, err = guard_turn(st, i.user.id)
            if not err:
                text, _ = run(st, cid)
                await finish_action(text + end_turn_text(st), st)
    if err:
        return await i.followup.send(f"❌ {err}", ephemeral=True)
    await flush()
    await i.followup.send("✅ Done.", ephemeral=True)


@tree.command(name="move", description="Walk into a connected room / hall / door (ends your turn)", guild=GUILD)
@app_commands.describe(room="Room number")
@app_commands.autocomplete(room=ac_move)
@configured_only()
async def move_cmd(i: discord.Interaction, room: int):
    def run(st, cid):
        if not st["map"].get("spawned"):
            raise ValueError("There's no map in play yet.")
        res = RR.enter_room(st, cid, room)
        if not res["ok"]:
            raise ValueError(res["error"])
        name = load_char(cid)["name"]
        r = res["room"]
        enter = f"🚪 **{name}** moves into {r['kind']} {room}."
        dm = st["setup"]["dm_id"]
        if res["need_desc"]:
            st["awaiting_room_desc"] = {"room": room, "char": cid, "event": bool(res["event"])}
            st["pending_advance"] = True
            PP.pause(st, f"waiting for the DM to describe room {room}", announce=False)
            say(st, "overview", f"<@{dm}> **{name}** entered room {room}, which has no description yet. "
                                f"Use `/room-dsc text:...` - turns are halted until you do.", ping_users=[dm])
            return enter + "\n⏳ The DM is describing it…", False
        if r["description"]:
            enter += f"\n📜 {r['description']}"
        if res["event"]:
            st["pending_advance"] = True
            say(st, "play", enter)          # post the walk-in line before the event announcement
            SE.start_event(st, room)
            return "", False
        return enter, True
    await run_turn_action(i, run)


def _my_chars(state: dict, uid, ref: Optional[str]) -> list[str]:
    if ref:
        k = find_entity(state, ref)
        if not k or is_monster(k):
            raise ValueError(f"no character '{ref}'.")
        if not (is_dm_user(state, uid) or load_bindings().get(k) == str(uid)):
            raise ValueError("That isn't your character.")
        return [k]
    mine = BC.chars_of(uid)
    if not mine:
        raise ValueError("You don't have a character yet - press the join button or use /create-char.")
    return mine


@tree.command(name="status", description="Your character's HP, effects and the turn order", guild=GUILD)
@app_commands.autocomplete(char=ac_chars)
@configured_only()
async def status_cmd(i: discord.Interaction, char: Optional[str] = None):
    st = load_state()
    lines = [combat.status_line(load_char(c)) + (f"  · room {st['positions'].get(c)}" if st["positions"].get(c) else "")
             for c in _my_chars(st, i.user.id, char)]
    lines.append(f"\n**Turn:** {PT.describe_turn(st)} ({PP.status(st)})\n{PT.order_text(st)}")
    await reply(i, "\n".join(lines))


@tree.command(name="sheet", description="Full character sheet + inventory", guild=GUILD)
@app_commands.autocomplete(char=ac_chars)
@configured_only()
async def sheet_cmd(i: discord.Interaction, char: Optional[str] = None):
    st = load_state()
    await reply(i, "\n\n".join(CC.sheet_text(load_char(c)) for c in _my_chars(st, i.user.id, char)))


# --------------------------------------------------------------------------- DM: immediate commands
@tree.command(name="room-dsc", description="DM: describe the room the party just walked into", guild=GUILD)
@dm_only()
async def room_dsc(i: discord.Interaction, text: str):
    await i.response.defer(ephemeral=True)
    async with mutate() as st:
        aw = st["awaiting_room_desc"]
        if not aw:
            return await i.followup.send("Nothing is waiting for a description. (Change one with `/edit map`.)", ephemeral=True)
        rid = aw["room"]
        RR.set_description(st, rid, text)
        PP.resume(st, announce=False)
        out = f"📜 **Room {rid}:** {text}"
        if aw.get("event"):
            SE.start_event(st, rid)
        elif st["pending_advance"]:
            st["pending_advance"] = False
            out += end_turn_text(st)
        say(st, "play", out)
        st["role_sync"] = True
    qlog("server_messages", f"📜 DM described room {rid}: {text[:300]}")
    await flush()
    await i.followup.send("✅ Described. Turns resume.", ephemeral=True)


@tree.command(name="pause", description="DM: pause the turn order", guild=GUILD)
@dm_only()
async def pause_cmd(i: discord.Interaction, reason: str = "paused by the DM"):
    async with mutate() as st:
        out = PP.pause(st, reason)
    await flush()
    await reply(i, "⏸️ " + out)


@tree.command(name="resume", description="DM: resume the turn order / end the current event", guild=GUILD)
@dm_only()
async def resume_cmd(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    async with mutate() as st:
        out = PP.resume(st)
        if st["pending_advance"]:
            st["pending_advance"] = False
            say(st, "play", end_turn_text(st).strip())
        st["role_sync"] = True
    await flush()
    await i.followup.send("▶️ " + out, ephemeral=True)


@tree.command(name="announce", description="DM: announce something to the table", guild=GUILD)
@dm_only()
async def announce_cmd(i: discord.Interaction, text: str, title: Optional[str] = None, ping: bool = False):
    async with mutate() as st:
        out = AN.announce(st, text, title, ping)
    await flush()
    await reply(i, "📢 " + out)


@tree.command(name="monster-attack", description="DM: make a monster attack (on its turn)", guild=GUILD)
@app_commands.autocomplete(monster=ac_monsters, target=ac_targets)
@dm_only()
async def monster_attack(i: discord.Interaction, monster: str, target: str):
    await i.response.defer(ephemeral=True)
    err = None
    async with mutate() as st:
        mk, tk = find_entity(st, monster), find_entity(st, target)
        key, ctrl = PT.check(st)
        if not mk or not is_monster(mk):
            err = f"no monster '{monster}'."
        elif not tk:
            err = f"can't find '{target}'."
        elif st["campaign"]["paused"] or not st["campaign"]["started"]:
            err = "The game isn't running."
        elif key != mk:
            err = f"It's not {st['monsters'][mk]['name']}'s turn - it's {PT.describe_turn(st)}."
        else:
            res = combat.attack(st, mk, tk)
            if not res["ok"]:
                err = res["error"]
            else:
                say(st, "play", res["text"] + end_turn_text(st))
                st["role_sync"] = True
    if err:
        return await i.followup.send(f"❌ {err}", ephemeral=True)
    await flush()
    await i.followup.send("✅ Done.", ephemeral=True)


@tree.command(name="peek", description="DM: see a character's/monster's item stats and effects", guild=GUILD)
@app_commands.autocomplete(target=ac_targets)
@dm_only()
async def peek_cmd(i: discord.Interaction, target: str):
    st = load_state()
    key = find_entity(st, target)
    if not key:
        raise ValueError(f"can't find '{target}'.")
    await reply(i, PEEK.peek(st, key))


@tree.command(name="chars", description="DM: list every character and who plays it", guild=GUILD)
@dm_only()
async def chars_cmd(i: discord.Interaction):
    await reply(i, BC.cli(["list"]))


@tree.command(name="export-char", description="DM: download a character as a zip (for /import-char)", guild=GUILD)
@app_commands.autocomplete(char=ac_chars)
@dm_only()
async def export_char(i: discord.Interaction, char: str):
    data = IC.export_char(char)
    await i.response.send_message(file=discord.File(io.BytesIO(data), filename=f"char_{char}.zip"), ephemeral=True)


# --------------------------------------------------------------------------- DM: staged actions (/apply -> /confirm)
def power_hint(template_ref: str, count: int) -> str:
    try:
        tpl = CMON.load_template(template_ref)
        total, avg, n = COMB.refresh()
        mp = CALC.power_of_monster(tpl) * count
        if not n:
            return f"(monster power {mp:.0f}; no players yet)"
        ratio = mp / total if total else 9
        label = "easy" if ratio < 0.5 else "fair" if ratio < 1 else "hard" if ratio < 1.5 else "deadly"
        return f"(power {mp:.0f} vs party {total:.0f} → **{label}**)"
    except Exception:
        return ""


async def queue(i: discord.Interaction, kind: str, summary: str, payload: dict):
    async with mutate() as st:
        st["pending"].append({"kind": kind, "summary": summary, "payload": payload})
        n = len(st["pending"])
    await reply(i, f"📝 Queued **#{n}**: {summary}\nNothing happens yet - `/apply` shows what will change, `/confirm` makes it real, `/discard` throws it away.")


def _num(cur: int, v: str) -> int:
    v = v.strip()
    return cur + int(v) if v[:1] in "+-" and v[1:].lstrip("-").isdigit() and len(v) > 1 else int(v)


def edit_entity(st: dict, key: str, field: str, value: str) -> str:
    obj = ent_get(st, key)
    if not obj:
        raise ValueError(f"nothing with id {key}.")
    f, v, mon = field.lower(), value.strip(), is_monster(key)
    was_dead = (not mon) and obj["hp"] <= 0
    if f == "add_effect":
        ref = parse_effect_refs(v)
        if not ref or ref[0]["id"] not in load_effects():
            raise ValueError("unknown effect - use id[:turns[:level]] like poison:3:1")
        combat.add_buff(obj, ref[0]["id"], ref[0]["turns"], ref[0]["level"])
    elif f == "remove_effect":
        obj["buffs"] = [b for b in obj["buffs"] if b["id"] != v.lower()]
    elif f in ("name", "damage", "nicname") or (mon and f == "room"):
        if f == "nicname" and mon:
            raise ValueError("monsters have no nickname.")
        if f == "damage":
            parse_dice(v)
        obj[f] = int(v) if f == "room" else v[:32] if f != "damage" else v
        if f == "name" and not mon:
            obj["nicname"] = v[:32]
    elif f in STATS and not mon:
        org, diff = obj["stats"][f]
        obj["stats"][f] = [org, diff + int(v)] if v[:1] in "+-" else [int(v), 0]
    elif f == "relationship" and not mon:
        other, _, lvl = v.partition("=")
        obj["relationships"][other.strip()] = int(lvl)
    elif f in ("give_item", "take_item") and not mon:
        if f == "give_item":
            if v.lower() not in load_items():
                raise ValueError(f"unknown item '{v}'.")
            if not combat.give_item(obj, v.lower(), st):
                raise ValueError("their bag is full.")
        else:
            loc = combat.find_in_inv(obj, v)
            if not loc:
                raise ValueError(f"they don't have '{v}'.")
            combat.remove_entry(obj, loc[0], loc[1])
    elif f in ("hp", "max_hp", "level", "xp", "hit", "evasion", "armor", "stat_mod"):
        if not mon and f in ("hit", "evasion", "armor", "stat_mod"):
            raise ValueError(f"{f} only applies to monsters.")
        obj[f] = _num(obj.get(f, 0), v)
        if f == "max_hp":
            obj[f] = max(1, obj[f])
        obj["hp"] = min(obj["hp"], obj["max_hp"])
    else:
        raise ValueError(f"can't edit '{field}'.")
    if obj.get("hp", 1) <= 0:
        combat.kill(st, key, obj)
        return f"{obj['name']} is dead."
    ent_save(st, key, obj)
    if was_dead and obj["hp"] > 0:
        BC.sync_turns(st)
    st["role_sync"] = True
    return f"{obj['name']}: {field} → {value}"


def ex_start_campaign(st, p):
    BC.sync_turns(st)
    if not any(not is_monster(k) for k in st["turn"]["order"]):
        raise ValueError("no living player characters are bound yet.")
    c = st["campaign"]
    c["started"], c["paused"], c["pause_reason"] = True, False, None
    st["turn"]["index"], st["turn"]["round"] = 0, 1
    say(st, "play", f"🎬 **{c['name']}** begins!\n**Turn order**\n{PT.order_text(st)}\n➡️ It is {PT.describe_turn(st)}.")
    st["role_sync"] = True
    return "campaign started."


def ex_create_campaign(st, p):
    fresh = default_state()
    for k in ("campaign", "turn", "monsters", "next_monster", "positions", "awaiting_room_desc", "pending_advance", "event"):
        st[k] = fresh[k]
    st["campaign"]["name"] = p["name"]
    if st["map"].get("loaded"):
        st["map"]["spawned"] = False
        rooms = load_rooms()
        for rid, r in rooms.items():
            r["marked"] = rid == st["map"]["start_room"]
        save_rooms(rooms)
    BC.sync_turns(st)
    return f"new campaign '{p['name']}' (characters kept; map reset)."


def ex_create_map(st, p):
    out = CR.commit(st)
    say(st, "mapping", f"🗺️ {out}\nRooms are numbered on the preview. Use `/spawn map` to put the party on it.",
        file=str(CR.PREVIEW_IMG) if CR.PREVIEW_IMG.exists() else None)
    return out


def ex_spawn_monster(st, p):
    keys = SMON.spawn(st, p["template"], p["count"], p.get("name"), p.get("room"))
    names = ", ".join(st["monsters"][k]["name"] for k in keys)
    say(st, "play", f"👹 **{names}** appear{'s' if len(keys) == 1 else ''}!")
    st["role_sync"] = True
    return f"spawned {names}"


def ex_spawn_map(st, p):
    return RR.activate(st)


def ex_spawn_player(st, p):
    k = SMON.spawn_from_char(st, p["char"])
    say(st, "play", f"☠️ **{st['monsters'][k]['name']}** rises again - hostile to the party!")
    st["role_sync"] = True
    return f"{st['monsters'][k]['name']} is now a monster ({k})."


def ex_edit_campaign(st, p):
    f, v = p["field"], p["value"].strip()
    if f == "name":
        st["campaign"]["name"] = v[:80]
        return f"campaign renamed to {v}."
    if f not in DEFAULT_SETTINGS:
        raise ValueError("unknown setting.")
    st["campaign"].setdefault("settings", dict(DEFAULT_SETTINGS))[f] = v.lower() in ("on", "true", "yes", "1")
    return f"{f} = {st['campaign']['settings'][f]}"


def ex_edit_monster(st, p):
    k = find_entity(st, p["monster"])
    if not k or not is_monster(k):
        raise ValueError("no such monster.")
    return edit_entity(st, k, p["field"], p["value"])


def ex_edit_player(st, p):
    return edit_entity(st, p["char"], p["field"], p["value"])


def ex_edit_map(st, p):
    return RR.edit_room(p["room"], p["field"], p["value"])


def ex_import_char(st, p):
    path = Path(p["path"])
    cid, warn = IC.import_char(path.read_bytes(), p["filename"])
    path.unlink(missing_ok=True)
    c = load_char(cid)
    say(st, "char_sheets", f"📥 **Imported** (unbound):\n{CC.sheet_text(c)}")
    return f"imported as #{cid} {c['name']}" + "".join(f" ⚠️{w}" for w in warn)


def ex_bind(st, p):
    prev = BC.bind(p["char"], p["user"])
    BC.sync_turns(st)
    return f"#{p['char']} → <@{p['user']}>" + (f" (was <@{prev}>)" if prev else "")


def ex_unbind(st, p):
    prev = BC.unbind(p["char"])
    PT.remove(st, str(p["char"]))
    BC.sync_turns(st)
    return f"#{p['char']} unbound" + (f" (was <@{prev}>)" if prev else "")


def ex_shuffle(st, p):
    new = BC.shuffle()
    BC.sync_turns(st)
    return "; ".join(f"#{c}→<@{u}>" for c, u in new.items()) or "nothing to shuffle."


def ex_create_item(st, p):
    return "created item " + CI.create_item(**p)


def ex_create_class(st, p):
    return "created class " + CCLASS.create_class(**p)


def ex_create_race(st, p):
    return "created race " + CRACE.create_race(**p)


def ex_create_effect(st, p):
    return "created effect " + CEFF.create_effect(p["id"], p["body"])


def ex_create_monster(st, p):
    return "created " + CMON.create_template(**p).name


EXEC = {k[3:]: v for k, v in globals().items() if k.startswith("ex_")}


@tree.command(name="apply", description="DM: preview everything you've queued", guild=GUILD)
@dm_only()
async def apply_cmd(i: discord.Interaction):
    st = load_state()
    if not st["pending"]:
        return await reply(i, "Nothing queued.")
    lines = [f"**{n}.** {a['summary']}" for n, a in enumerate(st["pending"], 1)]
    files = []
    if any(a["kind"] == "create_map" for a in st["pending"]) and CR.PREVIEW_IMG.exists():
        files.append(discord.File(CR.PREVIEW_IMG, filename="map_preview.png"))
    await reply(i, "**Here's what will happen when you `/confirm`:**\n" + "\n".join(lines) + "\n\n(`/discard` cancels all of it)",
                files=files)


@tree.command(name="confirm", description="DM: make everything queued actually happen", guild=GUILD)
@dm_only()
async def confirm_cmd(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    results = []
    async with mutate() as st:
        pend, st["pending"] = st["pending"], []
        for a in pend:
            snap = copy.deepcopy(st)
            try:
                out = EXEC[a["kind"]](st, a["payload"])
                results.append(f"✅ {a['summary']} — {out}")
            except Exception as e:  # noqa: BLE001 - report every kind of failure to the DM
                st.clear()
                st.update(snap)
                results.append(f"❌ {a['summary']} — {e}")
    qlog("server_messages", "✔️ **DM confirmed:**\n" + ("\n".join(results) or "(nothing was queued)"))
    await flush()
    await i.followup.send(reply_text("\n".join(results) or "Nothing queued."), ephemeral=True)


@tree.command(name="discard", description="DM: throw away everything queued", guild=GUILD)
@dm_only()
async def discard_cmd(i: discord.Interaction):
    async with mutate() as st:
        n, st["pending"] = len(st["pending"]), []
    await reply(i, f"🗑️ Discarded {n} queued action(s).")


# ---- groups: /start /create /spawn /edit   (the readme's "-campain / -monstor / -map / -player" phrases are the subcommands)
g_start = app_commands.Group(name="start", description="DM: start things")
g_create = app_commands.Group(name="create", description="DM: create things")
g_spawn = app_commands.Group(name="spawn", description="DM: spawn things")
g_edit = app_commands.Group(name="edit", description="DM: edit things")


@g_start.command(name="campaign", description="DM: start the campaign (turn order begins)")
@dm_only()
async def start_campaign(i: discord.Interaction):
    await queue(i, "start_campaign", "Start the campaign and begin the turn order", {})


@g_create.command(name="campaign", description="DM: begin a fresh campaign (characters are kept)")
@dm_only()
async def create_campaign(i: discord.Interaction, name: str):
    await queue(i, "create_campaign", f"New campaign **{name}** - clears monsters, turn order and discovered map; characters stay", {"name": name})


@g_create.command(name="monster", description="DM: create a monster template")
@app_commands.describe(hit="% chance it hits", evasion="% taken off attackers' hit chance", effects="id:turns:level,...",
                       boss="saved as bossN.dt")
@dm_only()
async def create_monster(i: discord.Interaction, name: str, hp: int, damage: str, hit: int = 60, evasion: int = 0,
                         armor: int = 0, stat_mod: int = 0, level: int = 1, effects: str = "-", boss: bool = False,
                         description: str = ""):
    await queue(i, "create_monster", f"Create {'boss' if boss else 'monster'} template **{name}** (hp {hp}, {damage}, hit {hit}%)",
                dict(name=name, hp=hp, damage=damage, hit=hit, evasion=evasion, armor=armor, stat_mod=stat_mod,
                     level=level, effects=effects, boss=boss, description=description))


@g_create.command(name="map", description="DM: import a Dungeon Scrawl map image (green square = start)")
@app_commands.describe(image="PNG export, 1x1 locked grid, one green square on the start cell",
                       floor_lum="How light a cell must be to count as floor (default 150)")
@dm_only()
async def create_map(i: discord.Interaction, image: discord.Attachment, floor_lum: int = 150):
    await i.response.defer(ephemeral=True)
    if image.size > 15_000_000:
        return await i.followup.send("❌ That image is over 15 MB.", ephemeral=True)
    try:
        info = await asyncio.to_thread(CR.stage, await image.read(), floor_lum)
    except (ValueError, OSError) as e:
        return await i.followup.send(f"❌ {e}", ephemeral=True)
    n = info["counts"]
    async with mutate() as st:
        st["pending"] = [a for a in st["pending"] if a["kind"] != "create_map"]
        st["pending"].append({"kind": "create_map", "payload": {}, "summary":
                              f"Import map: {n['room']} rooms, {n['hall']} halls, {n['door']} doors (cell {info['cell']}px) - replaces the current map"})
        k = len(st["pending"])
    await i.followup.send(f"📝 Queued **#{k}**. Found {n['room']} rooms, {n['hall']} halls, {n['door']} doors. "
                          "Numbers on the preview are the ids. `/apply` to see the full preview, `/confirm` to load it. "
                          "Wrong? Re-upload with a different `floor_lum`, or fix afterwards with `/edit map`.",
                          file=discord.File(CR.PREVIEW_IMG, filename="map_preview.png"), ephemeral=True)


@g_create.command(name="item", description="DM: add an item to tables/items.dt")
@dm_only()
async def create_item(i: discord.Interaction, id: str, name: str, type: Literal["weapon", "armor", "consumable", "misc"] = "weapon",
                      slot: Literal["-", "hand", "offhand", "head", "body", "back", "feet"] = "-", damage: str = "-",
                      hit: int = 70, stat: Literal["str", "dex", "con", "int", "wis", "cha"] = "str", armor: int = 0,
                      durability: str = "-", effects: str = "-", description: str = ""):
    await queue(i, "create_item", f"Create item **{name}** [{id}] ({type}, dmg {damage}, hit {hit}%)",
                dict(id=id, name=name, type=type, slot=slot, damage=damage, hit=hit, stat=stat, armor=armor,
                     durability=durability, effects=effects, description=description))


@g_create.command(name="race", description="DM: add an origin (race)")
@app_commands.describe(bonuses="like str:+2,con:+1")
@dm_only()
async def create_race(i: discord.Interaction, id: str, name: str, description: str = "", bonuses: str = ""):
    await queue(i, "create_race", f"Create origin **{name}** ({bonuses or 'no bonuses'})",
                dict(id=id, name=name, description=description, bonuses=bonuses))


@g_create.command(name="class", description="DM: add a class")
@app_commands.describe(bonuses="like dex:+2", base_hp="starting hp before CON", items="starting item ids, comma separated")
@dm_only()
async def create_class(i: discord.Interaction, id: str, name: str, description: str = "", bonuses: str = "",
                       base_hp: int = 8, items: str = ""):
    await queue(i, "create_class", f"Create class **{name}** (hp {base_hp}, items {items or 'none'})",
                dict(id=id, name=name, description=description, bonuses=bonuses, base_hp=base_hp, items=items))


class EffectModal(discord.ui.Modal, title="Create effect"):
    eid = discord.ui.TextInput(label="Effect id", max_length=40, placeholder="burning")
    body = discord.ui.TextInput(label="Script", style=discord.TextStyle.paragraph, max_length=1500,
                                placeholder="when.plr.turn:\n    dmg.plr(3)")

    async def on_submit(self, i: discord.Interaction):
        await queue(i, "create_effect", f"Create effect **{self.eid}**", {"id": str(self.eid), "body": str(self.body)})


@g_create.command(name="effect", description="DM: write a new buff/debuff script")
@dm_only()
async def create_effect(i: discord.Interaction):
    await i.response.send_modal(EffectModal())


@g_spawn.command(name="monster", description="DM: spawn a monster from a template")
@app_commands.autocomplete(template=ac_templates, room=ac_rooms)
@dm_only()
async def spawn_monster(i: discord.Interaction, template: str, count: int = 1, name: Optional[str] = None,
                        room: Optional[int] = None):
    await queue(i, "spawn_monster", f"Spawn {count}× **{template}** {power_hint(template, count)}",
                dict(template=template, count=count, name=name, room=room))


@g_spawn.command(name="map", description="DM: put the party on the map and show the first room")
@dm_only()
async def spawn_map(i: discord.Interaction):
    await queue(i, "spawn_map", "Spawn the map: party goes to the start room, map-view is updated", {})


@g_spawn.command(name="player", description="DM: bring a DEAD player back as a hostile monster")
@app_commands.autocomplete(char=ac_chars)
@dm_only()
async def spawn_player(i: discord.Interaction, char: str):
    c = load_char(char)
    if not c:
        raise ValueError(f"no character #{char}.")
    if c["hp"] > 0:
        raise ValueError(f"{c['name']} is still alive - only dead players can be spawned as monsters.")
    await queue(i, "spawn_player", f"Raise **{c['name']}** as a monster controlled by the DM", {"char": char})


@g_edit.command(name="campaign", description="DM: rename it or toggle optional fields")
@app_commands.describe(field="name, or an optional inventory field", value="new name, or on/off")
@dm_only()
async def edit_campaign(i: discord.Interaction, field: Literal["name", "item_level", "item_durability", "bag_type", "bag_max_slots"],
                        value: str):
    await queue(i, "edit_campaign", f"Campaign {field} → {value}", {"field": field, "value": value})


@g_edit.command(name="monster", description="DM: change a live monster")
@app_commands.describe(value="number (+3 / -3 / 10), text, or effect id:turns:level")
@app_commands.autocomplete(monster=ac_monsters)
@dm_only()
async def edit_monster(i: discord.Interaction, monster: str,
                       field: Literal["name", "hp", "max_hp", "hit", "evasion", "armor", "stat_mod", "damage", "level", "room", "add_effect", "remove_effect"],
                       value: str):
    await queue(i, "edit_monster", f"Monster {monster}: {field} → {value}", dict(monster=monster, field=field, value=value))


@g_edit.command(name="player", description="DM: change a character")
@app_commands.describe(value="number (+2 / -2 / 14; stats: +n adds an upgrade, n sets the base), text, item id, effect id:turns:level, or 'charid=level'")
@app_commands.autocomplete(char=ac_chars)
@dm_only()
async def edit_player(i: discord.Interaction, char: str,
                      field: Literal["name", "nicname", "hp", "max_hp", "level", "xp", "str", "dex", "con", "int", "wis", "cha",
                                     "add_effect", "remove_effect", "give_item", "take_item", "relationship"],
                      value: str):
    c = load_char(char)
    if not c:
        raise ValueError(f"no character #{char}.")
    await queue(i, "edit_player", f"{c['name']} (#{char}): {field} → {value}", dict(char=char, field=field, value=value))


@g_edit.command(name="map", description="DM: change a room (description, discovered, kind, important, event, monsters...)")
@app_commands.autocomplete(room=ac_rooms)
@dm_only()
async def edit_map(i: discord.Interaction, room: int,
                   field: Literal["description", "marked", "kind", "important", "event", "monsters", "loot", "notes"], value: str):
    await queue(i, "edit_map", f"Room {room}: {field} → {value}", dict(room=room, field=field, value=value))


for _g in (g_start, g_create, g_spawn, g_edit):
    tree.add_command(_g, guild=GUILD)


@tree.command(name="import-char", description="DM: import a character zip/json (made by /export-char)", guild=GUILD)
@dm_only()
async def import_char(i: discord.Interaction, file: discord.Attachment):
    if file.size > 500_000:
        raise ValueError("that file is too big for a character.")
    UPLOADS.mkdir(exist_ok=True)
    path = UPLOADS / f"{int(time.time())}_{Path(file.filename).name}"
    path.write_bytes(await file.read())
    await queue(i, "import_char", f"Import character from **{file.filename}** (arrives unbound)", {"path": str(path), "filename": file.filename})


@tree.command(name="bind-char", description="DM: give a character to a player (moves it if someone else had it)", guild=GUILD)
@app_commands.autocomplete(char=ac_chars)
@dm_only()
async def bind_char(i: discord.Interaction, char: str, user: discord.Member):
    c = load_char(char)
    if not c:
        raise ValueError(f"no character #{char}.")
    await queue(i, "bind", f"Bind **{c['name']}** (#{char}) → {user.mention}", {"char": char, "user": str(user.id)})


@tree.command(name="unbind-char", description="DM: take a character away from its player", guild=GUILD)
@app_commands.autocomplete(char=ac_chars)
@dm_only()
async def unbind_char(i: discord.Interaction, char: str):
    await queue(i, "unbind", f"Unbind character #{char}", {"char": char})


@tree.command(name="shuffle-chars", description="DM: randomly swap which player controls which character", guild=GUILD)
@dm_only()
async def shuffle_chars(i: discord.Interaction):
    await queue(i, "shuffle", "Shuffle every character between the current players", {})


# --------------------------------------------------------------------------- terminal
def console_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Type DM scripts into the terminal that runs the bot.  help | scripts | run <script> args | state | quit"""
    if not sys.stdin or not sys.stdin.isatty():
        return
    scripts = {}
    for folder in ("DM stuff", "player data"):
        for p in (ROOT / folder).rglob("*.py"):
            scripts[p.stem.lower().replace(FSLASH, "/")] = p
    print("DND bot terminal ready - type 'help'.")
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return
        try:
            parts = shlex.split(line)
        except ValueError as e:
            print("parse error:", e)
            continue
        if not parts:
            continue
        cmd = parts[0].lower()
        if cmd == "help":
            print("scripts            list runnable scripts\nrun <script> ...   run a script, e.g. run \"create item\" --id axe --name Axe\n"
                  "state              short game state\nquit               stop the bot")
        elif cmd == "scripts":
            print("\n".join(sorted(scripts)))
        elif cmd == "state":
            st = load_state()
            print(f"campaign={st['campaign']} turn={PT.check(st)} monsters={list(st['monsters'])} pending={len(st['pending'])}")
        elif cmd == "quit":
            asyncio.run_coroutine_threadsafe(client.close(), loop)
            return
        elif cmd == "run" and len(parts) > 1:
            name = parts[1].lower().replace(".py", "").replace(FSLASH, "/")
            path = scripts.get(name)
            if not path:
                print("no such script. 'scripts' lists them.")
                continue
            fut = asyncio.run_coroutine_threadsafe(_run_script(path, parts[2:]), loop)
            try:
                print(fut.result(timeout=120))
            except Exception as e:  # noqa: BLE001
                print("error:", e)
        else:
            print("unknown command - type 'help'.")


async def _run_script(path: Path, args: list[str]) -> str:
    mod = load_script(str(path.relative_to(ROOT)).replace("\\", "/"))
    async with LOCK:
        try:
            out = await asyncio.to_thread(mod.cli, args)
        except SystemExit:
            out = "(bad arguments - see the script's docstring)"
        except Exception as e:  # noqa: BLE001
            out = f"error: {e}"
    qlog("server_messages", f"⌨️ **terminal** ran `{path.name}` {' '.join(args)[:200]}\n```{str(out)[:900]}```")
    await flush()
    return out


# --------------------------------------------------------------------------- entry point
def check() -> int:
    """python main.py --check : load every data file and build the commands, without connecting."""
    print("items:", len(load_items()), "| races:", len(load_other()["race"]), "| classes:", len(load_other()["class"]),
          "| effects:", len(load_effects()), "| monster templates:", len(CMON.list_templates()))
    cmds = [c.qualified_name for c in tree.walk_commands(guild=GUILD)]
    print(f"{len(cmds)} slash commands:", ", ".join(sorted(cmds)))
    print("token set:", bool(config.BOT_TOKEN), "| guild id set:", bool(config.GUILD_ID))
    return 0


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(check())
    if not config.BOT_TOKEN or not config.GUILD_ID:
        sys.exit("Fill in BOT_TOKEN and GUILD_ID in config.py first (see SETUP.txt).")
    try:
        client.run(config.BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        sys.exit("Turn on SERVER MEMBERS INTENT and MESSAGE CONTENT INTENT for your bot in the Discord developer portal "
                 "(Bot tab ▸ Privileged Gateway Intents), then start again. See SETUP.txt.")
    except discord.LoginFailure:
        sys.exit("Discord rejected the token. Reset it in the developer portal (Bot tab) and paste the new one into config.py.")


if __name__ == "__main__":
    main()
