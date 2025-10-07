# main.py
# 隞?齒鈭?璈鈭?+ Excel ?脣漲閫??嚗憓?嚗???撖?憭???敺?蝛箏潘?

import os
import json
import re
import io
import tempfile
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
import pandas as pd
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from dateutil import parser as du_parser
from dotenv import load_dotenv

# ?????????????????????????????????????????????????????????
# 閮剖?
# ?????????????????????????????????????????????????????????
load_dotenv()
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Asia/Taipei")
GUILD_IDS = [int(x.strip()) for x in os.getenv("GUILD_IDS", "").split(",") if x.strip().isdigit()]

CONFIG_PATH = os.path.join(os.getcwd(), "config.json")
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
else:
    CONFIG = {
        "theme_color": 1489722,
        "timezone": DEFAULT_TZ,
        "i18n": {"default": "zh-TW", "fallback": "en"},
        "stickers": {"mode": "fun"},
        "reminders": {"defaults": ["-1h", "-10m"]},
        "permissions": {"delete": "admin_or_creator", "assign": "any"},
    }

THEME_COLOR = int(CONFIG.get("theme_color", 1489722))
BOT_TZ = ZoneInfo(CONFIG.get("timezone", DEFAULT_TZ))

# ?????????????????????????????????????????????????????????
# ??閫??
# ?????????????????????????????????????????????????????????
RELATIVE_PATTERN = re.compile(r"^-(\d+)([mhd])$")  # -30m / -2h / -1d

def parse_dt(text: str, tz: ZoneInfo) -> datetime:
    dt = du_parser.parse(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt

def parse_reminders(remind_raw: Optional[str], when_dt: datetime) -> List[datetime]:
    if not remind_raw:
        remind_raw = ",".join(CONFIG.get("reminders", {}).get("defaults", [])) or "-1h,-10m"
    points: List[datetime] = []
    for token in [x.strip() for x in remind_raw.split(",") if x.strip()]:
        m = RELATIVE_PATTERN.match(token)
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            delta = timedelta(minutes=val) if unit == "m" else timedelta(hours=val) if unit == "h" else timedelta(days=val)
            points.append(when_dt - delta)
        else:
            try:
                points.append(parse_dt(token, when_dt.tzinfo))
            except Exception:
                continue
    clean: List[datetime] = []
    now = datetime.now(tz=when_dt.tzinfo)
    for p in points:
        if p >= when_dt:
            p = when_dt - timedelta(minutes=5)
        if p > now:
            clean.append(p)
    clean = sorted(list({int(p.timestamp()): p for p in clean}.values()))
    return clean

# ?????????????????????????????????????????????????????????
# DB
# ?????????????????????????????????????????????????????????
DB_PATH = os.path.join(os.getcwd(), "todos.sqlite3")
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER,
  channel_id INTEGER,
  message_id INTEGER,
  creator_id INTEGER,
  content TEXT,
  place TEXT,
  when_ts INTEGER,
  tz TEXT,
  reminders_json TEXT,
  assignees_json TEXT,
  status TEXT,
  done_at_ts INTEGER,
  note TEXT,
  priority TEXT,
  tags_json TEXT,
  created_at_ts INTEGER,
  updated_at_ts INTEGER
);
"""

# ?????????????????????????????????????????????????????????
# Bot ????
# ?????????????????????????????????????????????????????????
intents = discord.Intents.default()
intents.message_content = False  # 銝?閬?閮?批捆
bot = commands.Bot(command_prefix="!", intents=intents)

scheduler = AsyncIOScheduler(timezone=BOT_TZ)
# 瘜冽?嚗?閬?ㄐ start()嚗??on_ready() ??嚗??event loop ?航炊

# ?????????????????????????????????????????????????????????
# UI ??嚗nooze / Done嚗?
# ?????????????????????????????????????????????????????????
class TodoView(View):
    def __init__(self, todo_id: int, *, timeout: Optional[float] = 600):
        super().__init__(timeout=timeout)
        self.todo_id = todo_id
        self.add_item(Button(label="+10m Snooze", custom_id=f"snooze:10:{todo_id}"))
        self.add_item(Button(label="+30m Snooze", custom_id=f"snooze:30:{todo_id}"))
        self.add_item(Button(label="+1h Snooze", custom_id=f"snooze:60:{todo_id}"))
        self.add_item(Button(label="??璅?摰?", style=discord.ButtonStyle.success, custom_id=f"done:{todo_id}"))

@bot.event
async def on_interaction(inter: discord.Interaction):
    if inter.type == discord.InteractionType.component and inter.data:
        cid = inter.data.get("custom_id", "")
        if cid.startswith("snooze:"):
            _, mins, tid = cid.split(":")
            await handle_snooze(inter, int(tid), int(mins))
        elif cid.startswith("done:"):
            _, tid = cid.split(":")
            await handle_done(inter, int(tid))

async def handle_snooze(inter: discord.Interaction, todo_id: int, minutes: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT when_ts, tz, reminders_json, status FROM todos WHERE id=?", (todo_id,))
        row = await cur.fetchone(); await cur.close()
        if not row:
            return await inter.response.send_message("?曆??唳迨隞餃???, ephemeral=True)
        if row[3] != "open":
            return await inter.response.send_message("甇支遙?歇蝯?????, ephemeral=True)
        when_ts, tz_str, reminders_json = row[0], row[1], row[2]
        tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
        when_dt = datetime.fromtimestamp(when_ts, tz)
        reminders = json.loads(reminders_json or "[]")
        new_time = datetime.now(tz=tz) + timedelta(minutes=minutes)
        if int(new_time.timestamp()) >= int(when_dt.timestamp()):
            return await inter.response.send_message("撱嗅?敺歇頞??唳???嚗??湔???脰??楊頛臭遙??, ephemeral=True)
        reminders.append(int(new_time.timestamp()))
        reminders = sorted(list({x for x in reminders if x > int(datetime.now(tz=tz).timestamp())}))
        await db.execute("UPDATE todos SET reminders_json=?, updated_at_ts=? WHERE id=?", (json.dumps(reminders), int(datetime.now(tz=tz).timestamp()), todo_id))
        await db.commit()
        schedule_reminders_for(todo_id, when_dt, [datetime.fromtimestamp(x, tz) for x in reminders])
    await inter.response.send_message(f"撌脣辣敺???+{minutes} ????, ephemeral=True)

async def handle_done(inter: discord.Interaction, todo_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status, tz FROM todos WHERE id=?", (todo_id,))
        row = await cur.fetchone(); await cur.close()
        if not row:
            return await inter.response.send_message("?曆??唳迨隞餃???, ephemeral=True)
        if row[0] != "open":
            return await inter.response.send_message("甇支遙?歇????, ephemeral=True)
        tz = ZoneInfo(row[1] or CONFIG.get("timezone", DEFAULT_TZ))
        now_ts = int(datetime.now(tz=tz).timestamp())
        await db.execute("UPDATE todos SET status='done', done_at_ts=?, updated_at_ts=? WHERE id=?", (now_ts, now_ts, todo_id))
        await db.commit()
    await inter.response.send_message("??撌脫?閮???, ephemeral=True)

# ?????????????????????????????????????????????????????????
# Embed / Scheduler
# ?????????????????????????????????????????????????????????
def build_todo_embed(todo: Dict[str, Any]) -> discord.Embed:
    e = discord.Embed(title=f"??儭? 隞?齒鈭? #{todo['id']}", color=THEME_COLOR)
    e.add_field(name="?? ?批捆", value=todo["content"], inline=False)
    if todo.get("place"):
        e.add_field(name="?? ?圈?", value=todo["place"], inline=False)
    tz = ZoneInfo(todo.get("tz") or CONFIG.get("timezone", DEFAULT_TZ))
    when_dt = datetime.fromtimestamp(todo["when_ts"], tz)
    e.add_field(name="?? ?唳?", value=when_dt.strftime("%Y-%m-%d %H:%M %Z"))
    if todo.get("reminders"):
        rem_text = ", ".join(datetime.fromtimestamp(r, tz).strftime("%m/%d %H:%M") for r in todo["reminders"]) or "??
        e.add_field(name="????", value=rem_text)
    if todo.get("assignees"):
        e.add_field(name="? ?晷", value=", ".join(f"<@{uid}>" for uid in todo["assignees"]))
    if todo.get("priority"):
        e.add_field(name="?? ?芸?摨?, value=todo["priority"].upper())
    if todo.get("tags"):
        e.add_field(name="?儭?璅惜", value=" ".join(f"#{t}" for t in todo["tags"]))
    e.set_footer(text="??/todo add 撱箇? ???臭誑??/todo edit 蝺刻摩")
    return e

JOB_PREFIX = "todo:"

def schedule_reminders_for(todo_id: int, when_dt: datetime, reminders: List[datetime]):
    # 皜??Ｘ? job
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{JOB_PREFIX}{todo_id}:"):
            job.remove()
    # 摰???
    for rdt in reminders:
        if rdt > datetime.now(tz=when_dt.tzinfo):
            job_id = f"{JOB_PREFIX}{todo_id}:REM:{int(rdt.timestamp())}"
            scheduler.add_job(reminder_job, DateTrigger(run_date=rdt), id=job_id, args=[todo_id, int(rdt.timestamp())])
    # 摰??芣迫
    if when_dt > datetime.now(tz=when_dt.tzinfo):
        job_id = f"{JOB_PREFIX}{todo_id}:DUE:{int(when_dt.timestamp())}"
        scheduler.add_job(due_job, DateTrigger(run_date=when_dt), id=job_id, args=[todo_id])

async def reminder_job(todo_id: int, run_ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT guild_id, channel_id, content, place, when_ts, tz, assignees_json, status FROM todos WHERE id=?", (todo_id,))
        row = await cur.fetchone(); await cur.close()
        if not row: return
        guild_id, channel_id, content, place, when_ts, tz_str, assignees_json, status = row
        if status != "open": return
        tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
        when_dt = datetime.fromtimestamp(when_ts, tz)
        assignees = json.loads(assignees_json or "[]")
        ch = bot.get_channel(channel_id)
        if not ch: return
        mentions = " ".join(f"<@{uid}>" for uid in assignees) or ""
        todo = {"id": todo_id, "content": content, "place": place, "when_ts": when_ts, "tz": tz_str, "assignees": assignees, "reminders": [run_ts]}
        embed = build_todo_embed(todo)
        view = TodoView(todo_id)
        mins = max(0, int((when_dt - datetime.now(tz=tz)).total_seconds() // 60))
        await ch.send(content=f"????嚗?{todo_id}嚗?{mentions}\n?? {mins} ???唳???, embed=embed, view=view)

async def due_job(todo_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT guild_id, channel_id, content, place, when_ts, tz, assignees_json, status FROM todos WHERE id=?", (todo_id,))
        row = await cur.fetchone(); await cur.close()
        if not row: return
        guild_id, channel_id, content, place, when_ts, tz_str, assignees_json, status = row
        if status != "open": return
        tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
        assignees = json.loads(assignees_json or "[]")
        ch = bot.get_channel(channel_id)
        if not ch: return
        mentions = " ".join(f"<@{uid}>" for uid in assignees) or ""
        todo = {"id": todo_id, "content": content, "place": place, "when_ts": when_ts, "tz": tz_str, "assignees": assignees, "reminders": []}
        embed = build_todo_embed(todo)
        view = TodoView(todo_id)
        await ch.send(content=f"???芣迫嚗?{todo_id}嚗?{mentions}\n?曉??嚗?, embed=embed, view=view)

async def reschedule_open_todos():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, when_ts, tz, reminders_json, status FROM todos WHERE status='open'")
        rows = await cur.fetchall(); await cur.close()
    for tid, when_ts, tz_str, reminders_json, status in rows:
        tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
        when_dt = datetime.fromtimestamp(when_ts, tz)
        reminders = [datetime.fromtimestamp(x, tz) for x in json.loads(reminders_json or "[]")]
        reminders = [r for r in reminders if r > datetime.now(tz=tz)]
        schedule_reminders_for(tid, when_dt, reminders)

# ?????????????????????????????????????????????????????????
# Slash ?誘蝢斤?嚗?todo
# ?????????????????????????????????????????????????????????
class TodoGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="todo", description="敺齒鈭?")

    @app_commands.command(name="add", description="?啣?隞?齒鈭?")
    @app_commands.describe(
        content="鈭??批捆",
        when="?唳???嚗?嚗?025-12-31 14:30 ??'?予 3pm'嚗?,
        remind="??嚗?30m,-1h ??蝯???嚗???嚗?閮?-1h,-10m嚗?,
        where="?圈?嚗???嚗?憿舐內??Embed 銝哨?",
        assignees="@?嚗鞎潔?憭?mention嚗?圾??",
        priority="?芸?摨佗?low|normal|high|urgent",
        tags="璅惜嚗誑????"
    )
    async def add(self, inter: discord.Interaction,
                  content: str,
                  when: str,
                  remind: Optional[str] = None,
                  where: Optional[str] = None,
                  assignees: Optional[str] = None,
                  priority: Optional[str] = None,
                  tags: Optional[str] = None):
        await inter.response.defer(ephemeral=False, thinking=True)
        tz = BOT_TZ
        when_dt = parse_dt(when, tz)
        reminders = parse_reminders(remind, when_dt)
        # assignees
        assignee_ids: List[int] = []
        if assignees:
            for m in re.findall(r"<@!?(\d+)>", assignees):
                try: assignee_ids.append(int(m))
                except: pass
            assignee_ids = list(dict.fromkeys(assignee_ids))
        tag_list = [t.strip().lstrip('#') for t in (tags.split(',') if tags else []) if t.strip()]
        now_ts = int(datetime.now(tz=tz).timestamp())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(CREATE_TABLE_SQL)
            cur = await db.execute(
                """
                INSERT INTO todos (guild_id, channel_id, message_id, creator_id, content, place, when_ts, tz, reminders_json, assignees_json, status, priority, tags_json, created_at_ts, updated_at_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    inter.guild_id, inter.channel_id, 0, inter.user.id, content, where or "",
                    int(when_dt.timestamp()), str(tz.key), json.dumps([int(r.timestamp()) for r in reminders]), json.dumps(assignee_ids),
                    (priority or "normal"), json.dumps(tag_list), now_ts, now_ts
                )
            )
            await db.commit()
            tid = cur.lastrowid
        schedule_reminders_for(tid, when_dt, reminders)
        todo = {
            "id": tid, "content": content, "place": where,
            "when_ts": int(when_dt.timestamp()), "tz": str(tz.key),
            "assignees": assignee_ids,
            "reminders": [int(r.timestamp()) for r in reminders],
            "priority": (priority or "normal"),
            "tags": tag_list
        }
        embed = build_todo_embed(todo)
        view = TodoView(tid)
        msg = await inter.channel.send(embed=embed, view=view)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE todos SET message_id=?, updated_at_ts=? WHERE id=?", (msg.id, int(datetime.now(tz=tz).timestamp()), tid))
            await db.commit()
        await inter.followup.send(f"??撌脣遣蝡誨颲虫???`#{tid}`??)

    @app_commands.command(name="list", description="?隞?齒鈭?")
    @app_commands.describe(scope="mine|channel|server", status="open|done|all", tag="?舫嚗?摰?蝐?)
    async def list(self, inter: discord.Interaction, scope: Optional[str] = "mine", status: Optional[str] = "open", tag: Optional[str] = None):
        await inter.response.defer(ephemeral=True, thinking=True)
        q = "SELECT id, content, place, when_ts, tz, assignees_json, status FROM todos WHERE 1=1"
        args: List[Any] = []
        if scope == "mine":
            q += " AND creator_id=?"; args.append(inter.user.id)
        elif scope == "channel":
            q += " AND channel_id=?"; args.append(inter.channel_id)
        elif scope == "server":
            q += " AND guild_id=?"; args.append(inter.guild_id)
        if status in ("open", "done"):
            q += " AND status=?"; args.append(status)
        if tag:
            q += " AND (tags_json LIKE ? )"; args.append(f"%{tag}%")
        q += " ORDER BY when_ts ASC LIMIT 20"
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(q, tuple(args))
            rows = await cur.fetchall(); await cur.close()
        if not rows:
            return await inter.followup.send("瘝?蝚血?璇辣????)
        lines = ["雿?鈭?嚗?]
        for tid, content, place, when_ts, tz_str, assignees_json, st in rows:
            tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
            when_s = datetime.fromtimestamp(when_ts, tz).strftime("%m/%d %H:%M")
            where_s = f"嚗place}" if place else ""
            lines.append(f"#{tid} {content} ??{when_s} {where_s} [{st}]")
        await inter.followup.send("\n".join(lines))

    @app_commands.command(name="done", description="璅?摰?")
    async def done(self, inter: discord.Interaction, id: int, note: Optional[str] = None):
        await inter.response.defer(ephemeral=True)
        tz = BOT_TZ
        now_ts = int(datetime.now(tz=tz).timestamp())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE todos SET status='done', note=?, done_at_ts=?, updated_at_ts=? WHERE id=?", (note or "", now_ts, now_ts, id))
            await db.commit()
        await inter.followup.send(f"??撌脣???#{id}")

    @app_commands.command(name="delete", description="?芷鈭?嚗遣蝡?蝞∠??∴?")
    async def delete(self, inter: discord.Interaction, id: int):
        await inter.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT creator_id FROM todos WHERE id=?", (id,))
            row = await cur.fetchone(); await cur.close()
        if not row:
            return await inter.followup.send("?曆??唳迨鈭???)
        is_admin = inter.user.guild_permissions.manage_guild
        if (row[0] != inter.user.id) and (not is_admin):
            return await inter.followup.send("雿???斗迨鈭?????)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM todos WHERE id=?", (id,))
            await db.commit()
        for job in scheduler.get_jobs():
            if job.id.startswith(f"{JOB_PREFIX}{id}:"):
                job.remove()
        await inter.followup.send(f"??儭?撌脣??#{id}")

    @app_commands.command(name="edit", description="蝺刻摩鈭?嚗遙銝甈?嚗?)
    async def edit(self, inter: discord.Interaction, id: int, content: Optional[str] = None, when: Optional[str] = None, remind: Optional[str] = None, where: Optional[str] = None, assignees: Optional[str] = None, priority: Optional[str] = None, tags: Optional[str] = None):
        await inter.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT content, place, when_ts, tz, reminders_json, assignees_json, priority, tags_json FROM todos WHERE id=?", (id,))
            row = await cur.fetchone(); await cur.close()
        if not row:
            return await inter.followup.send("?曆??唳迨鈭???)
        old_content, old_place, old_when_ts, tz_str, old_rem_json, old_ass_json, old_pri, old_tags_json = row
        tz = ZoneInfo(tz_str or CONFIG.get("timezone", DEFAULT_TZ))
        new_content = content or old_content
        new_place = where if where is not None else old_place
        when_dt = parse_dt(when, tz) if when else datetime.fromtimestamp(old_when_ts, tz)
        if remind is not None:
            reminders = parse_reminders(remind, when_dt)
            reminders_ts = [int(r.timestamp()) for r in reminders]
        else:
            reminders_ts = json.loads(old_rem_json or "[]")
            reminders = [datetime.fromtimestamp(x, tz) for x in reminders_ts]
        if assignees is not None:
            ids: List[int] = []
            for m in re.findall(r"<@!?(\d+)>", assignees):
                try: ids.append(int(m))
                except: pass
            assignee_ids = list(dict.fromkeys(ids))
        else:
            assignee_ids = json.loads(old_ass_json or "[]")
        tag_list = [t.strip().lstrip('#') for t in (tags.split(',') if tags else json.loads(old_tags_json or "[]")) if t]
        pri = priority or old_pri or "normal"
        now_ts = int(datetime.now(tz=tz).timestamp())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE todos SET content=?, place=?, when_ts=?, reminders_json=?, assignees_json=?, priority=?, tags_json=?, updated_at_ts=? WHERE id=?",
                (new_content, new_place or "", int(when_dt.timestamp()), json.dumps(reminders_ts), json.dumps(assignee_ids), pri, json.dumps(tag_list), now_ts, id),
            )
            await db.commit()
        schedule_reminders_for(id, when_dt, reminders)
        await inter.followup.send(f"?? 撌脫??#{id}")

    @app_commands.command(name="remind", description="蝡閫貊??嚗葫閰衣嚗?)
    async def remind(self, inter: discord.Interaction, id: int):
        await inter.response.defer(ephemeral=True)
        await reminder_job(id, int(datetime.now(tz=BOT_TZ).timestamp()))
        await inter.followup.send("撌脰孛?潔?甈⊥???)

# ?????????????????????????????????????????????????????????
# Excel ?敺脣漲閫??嚗憓?嚗???撖?憭???敺?蝛箏潘?
# ?????????????????????????????????????????????????????????
import re as _re

_STAGE_ORDER = ["瘙?", "?⊿???, "?????詨)", "????VISA)", "隤?(颲虫???", "撖?憭?]

def _norm(s: str) -> str:
    s = "" if s is None else str(s)
    s = s.replace("嚗?, "(").replace("嚗?, ")")
    s = _re.sub(r"\s+", "", s)
    return s

def _any_contains(col_pair, key_norm: str) -> bool:
    a, b = col_pair
    return _norm(a) == key_norm or _norm(b) == key_norm

def _find_first_idx(cols, key: str):
    keyn = _norm(key)
    for i, c in enumerate(cols):
        if _any_contains(c, keyn):
            return i
    return None

def parse_last_progress(file_path: str,
                        sheet_name: str = "?亙???,
                        roc_date: bool = True) -> pd.DataFrame:
    """
    - header=[0,1]嚗撅方”?哨?dtype=str嚗???摮葡嚗?13/xx/xx / OK / X嚗?
    - 靘???撖?憭?畾蛛???畾菜?敺???蝛箏潘??湧??敺????潛?畾萸??箝?敺脣漲??
    - ????蝔晞腦撣?隞嗚?憒??剁?
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=[0, 1], dtype=str)
    cols = [(str(a) if a is not None else "", str(b) if b is not None else "") for a, b in df.columns.to_list()]

    idx_firm = _find_first_idx(cols, "撱??迂")
    idx_city = _find_first_idx(cols, "蝮??")
    idx_case = _find_first_idx(cols, "獢辣")
    if idx_firm is None:
        return pd.DataFrame(columns=["撱?", "蝮??", "獢辣", "?敺脣漲"])

    stage_pos = {}
    for s in _STAGE_ORDER:
        i = _find_first_idx(cols, s)
        if i is not None:
            stage_pos[s] = i

    ordered_stages = [s for s in _STAGE_ORDER if s in stage_pos]
    stage_ranges = []
    for si, s in enumerate(ordered_stages):
        start = stage_pos[s]
        end = stage_pos[ordered_stages[si + 1]] if si + 1 < len(ordered_stages) else len(cols)
        stage_ranges.append((s, start, end))

    rows = []
    for _, row in df.iterrows():
        firm = str(row.iloc[idx_firm]).strip() if pd.notna(row.iloc[idx_firm]) else ""
        if not firm or firm == "撱??迂":
            continue
        city = str(row.iloc[idx_city]).strip() if (idx_city is not None and pd.notna(row.iloc[idx_city])) else ""
        case = str(row.iloc[idx_case]).strip() if (idx_case is not None and pd.notna(row.iloc[idx_case])) else ""

        last_stage = None
        last_value = None
        for stage, start, end in stage_ranges:
            stage_val = None
            for j in range(start, end):
                v = row.iloc[j]
                if pd.notna(v) and str(v).strip() != "":
                    stage_val = str(v).strip()
            if stage_val:
                last_stage = stage
                last_value = stage_val

        if last_stage and last_value:
            rows.append({
                "撱?": firm,
                "蝮??": city,
                "獢辣": case,
                "?敺脣漲": f"{last_stage} > {last_value}",
            })

    return pd.DataFrame(rows)

def build_progress_embed(title: str, df: pd.DataFrame) -> discord.Embed:
    e = discord.Embed(title=f"?? {title}", color=THEME_COLOR)
    if df.empty:
        e.description = "瘝?閫???唬遙雿脣漲??
        return e
    parts = []
    for _, r in df.head(20).iterrows():
        firm = r.get("撱?", "")
        city = r.get("蝮??", "")
        case = r.get("獢辣", "")
        status = r.get("?敺脣漲", "")
        head = f"**{firm}嚗city}嚚case}嚗?*" if city or case else f"**{firm}**"
        parts.append(f"{head}\n?∴? {status}")
    e.description = "\n\n".join(parts)
    if len(df) > 20:
        e.set_footer(text=f"??閬賢? 20 蝑???{len(df)} 蝑??渲??歇?? CSV??)
    else:
        e.set_footer(text=f"??{len(df)} 蝑?)
    return e

class ProgressGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="progress", description="獢辣?敺脣漲嚗xcel 銝閫??嚗?)

    @app_commands.command(name="import", description="銝 Excel嚗圾??撱???敺脣漲??)
    @app_commands.describe(
        file="銝 Excel 瑼?.xlsx嚗?,
        sheet="撌乩?銵典?蝔梧??身嚗憓?嚗?,
        datefmt="?交??澆?嚗oc=瘞??so=镼踹?嚗????璅??甇文??詨敹賜嚗?,
    )
    async def import_(self,
                      inter: discord.Interaction,
                      file: discord.Attachment,
                      sheet: Optional[str] = "?亙???,
                      datefmt: Optional[str] = "roc"):
        await inter.response.defer(thinking=True)
        with tempfile.TemporaryDirectory() as td:
            tmp_path = os.path.join(td, file.filename)
            await file.save(tmp_path)
            try:
                df = parse_last_progress(tmp_path, sheet_name=sheet or "?亙???, roc_date=(str(datefmt).lower() != "iso"))
            except Exception as e:
                return await inter.followup.send(f"閫??憭望?嚗e}")
            title = f"{sheet or '?亙???} - ?敺脣漲"
            embed = build_progress_embed(title, df)
            csv_bytes = io.BytesIO()
            df.to_csv(csv_bytes, index=False, encoding="utf-8-sig")
            csv_bytes.seek(0)
            csv_file = discord.File(fp=csv_bytes, filename=f"{(sheet or '?亙???)}_?敺脣漲.csv")
            await inter.followup.send(embed=embed, file=csv_file)

# ?????????????????????????????????????????????????????????
# /help 嚗翰?牧??
# ?????????????????????????????????????????????????????????
async def help_command(inter: discord.Interaction):
    e = discord.Embed(title="?? ?誘隤芣?嚗翰??嚗?, color=THEME_COLOR)
    e.add_field(
        name="/todo",
        value=(
            "`/todo add content:<??> when:<??> [remind:-10m,-1h] [where:<?圈?>] [assignees:@鈭?..] [priority:high] [tags:????]`\n"
            "`/todo list scope:mine|channel|server status:open|done|all [tag:摮`\n"
            "`/todo edit id:<蝺刻?> [content/when/remind/where/assignees/priority/tags]`\n"
            "`/todo done id:<蝺刻?>`   `/todo delete id:<蝺刻?>`   `/todo remind id:<蝺刻?>`"
        ),
        inline=False
    )
    e.add_field(
        name="/progress",
        value="`/progress import file:<Excel> [sheet:?亙??` ??閫????敺脣漲????喲?蝛箏潘???,
        inline=False
    )
    e.set_footer(text="???舐?芰隤?隞予 20:30嚗?憭?3pm嚗?025-12-31 14:30嚗????-10m, -1h 蝑?)
    await inter.response.send_message(embed=e, ephemeral=True)

# ?????????????????????????????????????????????????????????
# on_ready嚗???APScheduler ??閮餃??誘 ???郊
# ?????????????????????????????????????????????????????????
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

    # ?? APScheduler嚗?隞嗉艘?歇??????
    try:
        if not scheduler.running:
            scheduler.start()
    except Exception as e:
        print(f"Scheduler start error: {e}")

    # 蝣箔?鞈?銵典???
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()

    # ??????
    await reschedule_open_todos()

    # 皜征?暹?璅對??踹???閮餃?
    try:
        bot.tree.clear_commands(guild=None)
    except Exception:
        pass
    try:
        for g in bot.guilds:
            bot.tree.clear_commands(guild=g)
    except Exception:
        pass

    # ?典?閮餃?嚗??湛?
    try:
        bot.tree.add_command(TodoGroup())
    except Exception as e:
        print(f"add_command(global Todo) error: {e}")
    try:
        bot.tree.add_command(ProgressGroup())
    except Exception as e:
        print(f"add_command(global Progress) error: {e}")
    try:
        bot.tree.add_command(app_commands.Command(name="help", description="憿舐內?舐?誘??靘?, callback=help_command))
    except Exception as e:
        print(f"add_command(global help) error: {e}")

    # ??guild 閮餃?嚗??喳閬?
    for g in bot.guilds:
        try:
            bot.tree.add_command(TodoGroup(), guild=g)
        except Exception as e:
            print(f"add_command(Todo guild={g.id}) error: {e}")
        try:
            bot.tree.add_command(ProgressGroup(), guild=g)
        except Exception as e:
            print(f"add_command(Progress guild={g.id}) error: {e}")
        try:
            bot.tree.add_command(app_commands.Command(name="help", description="憿舐內?舐?誘??靘?, callback=help_command), guild=g)
        except Exception as e:
            print(f"add_command(help guild={g.id}) error: {e}")

    # ?? GUILD_IDS ?芸??郊
    synced_any = False
    if GUILD_IDS:
        for gid in GUILD_IDS:
            try:
                guild_obj = discord.Object(id=gid)
                await bot.tree.sync(guild=guild_obj)
                cmds = await bot.tree.fetch_commands(guild=guild_obj)
                print(f"Synced to guild {gid}; commands = {[c.name for c in cmds]}")
                synced_any = True
            except Exception as e:
                print(f"Sync to guild {gid} failed: {e}")

    # 撌脣???guild ???郊
    for g in bot.guilds:
        try:
            await bot.tree.sync(guild=g)
            cmds = await bot.tree.fetch_commands(guild=g)
            print(f"Synced to joined guild {g.id} ({g.name}); commands = {[c.name for c in cmds]}")
            synced_any = True
        except Exception as e:
            print(f"Sync to joined guild {g.id} failed: {e}")

    # ?典??郊嚗?敺??湛?
    if not synced_any:
        try:
            await bot.tree.sync()
            cmds = await bot.tree.fetch_commands()
            print(f"Synced globally; commands = {[c.name for c in cmds]}")
        except Exception as e:
            print(f"Global sync error: {e}")

# ?????????????????????????????????????????????????????????
# ?脣暺?
# ?????????????????????????????????????????????????????????
def main():
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("隢 .env 閮剖? DISCORD_TOKEN")
bot.run(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    main()

