import os
from dotenv import load_dotenv

load_dotenv()
# -*- coding: utf-8 -*-
import re
from dotenv import load_dotenv
import json
from dotenv import load_dotenv
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple, List

import discord
from dotenv import load_dotenv
from discord import app_commands
from discord.ext import commands, tasks

# ========= 雿?閮剖?嚗?撌脩憸券嚗?=========
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1351806583457316874
MY_GUILD = discord.Object(id=GUILD_ID)

# ???嗾????
EARLY_MINUTES = 3

# /k ??/killat 敺??????蝡蝳嚗誑??挾???批??澆?暺???舐?亦???
ANTI_DUP_GRACE_SEC = 180

# ?身?”嚗望?嚗?蝔望??殷?
DEFAULT_BOSSES = {
    120: ["02", "03"],
    180: ["05", "06", "08", "10"],
    240: ["12", "14", "70-2F"],
    300: ["17", "18"],
    360: ["19", "21", "80-3F"],
    480: ["22", "26", "29", "j70-2F"],
    600: ["30", "31", "32", "40"],
    720: ["B3", "33", "34", "37", "j80-3F"],
    840: ["41"],
}

# ========= LOG =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("boss-bot")

# ========= 鞈?????=========
DATA_FILE = Path("records.json")

# records["08"] = {
#   "period": 180,
#   "last_kill": datetime|None,
#   "user": "?餉???,
#   "killed_by": "??隡?,
#   "channel": int|None,
#   "reminded": bool,
#   "carded": bool,
#   "card_channel_id": int|None,
#   "card_msg_id": int|None,
#   "manual_set_at": str(iso) | None,   # /k ??/killat ?身摰????斗?犖撌亥身摰?
# }
records: dict[str, dict] = {}


def save_records() -> None:
    data = {}
    for k, v in records.items():
        vv = dict(v)
        if isinstance(vv.get("last_kill"), datetime):
            vv["last_kill"] = vv["last_kill"].isoformat()
        data[k] = vv
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("? 撌脣神??records.json嚗?s 蝑?", len(records))


def load_records() -> None:
    if not DATA_FILE.exists():
        log.info("擐活?瑁?嚗ecords.json 銝??剁?撠銋??芸?撱箇???)
        return
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for k, v in raw.items():
            d = dict(v)
            if isinstance(d.get("last_kill"), str):
                try:
                    d["last_kill"] = datetime.fromisoformat(d["last_kill"])
                except Exception:
                    d["last_kill"] = None
            records[k] = d
        log.info("? 撌脰???records.json嚗?s 蝑?", len(records))
    except Exception:
        log.exception("霈??records.json 憭望?嚗?敹賜??)


# ========= 撠極??=========
def boss_label(name: str) -> str:
    return f"{name} BOSS"


def safe_period(p: Optional[int], fallback: int = 120) -> int:
    try:
        p = int(p or fallback)
        if p <= 0:
            return fallback
        return p
    except Exception:
        return fallback


def ensure_boss(boss: str, period_hint: Optional[int] = None) -> str:
    b = boss.strip()
    if b not in records:
        # ?岫敺?閮剔?銵冽皜祇望?
        p = period_hint
        if p is None:
            for pp, names in DEFAULT_BOSSES.items():
                if b in names:
                    p = pp
                    break
        if p is None:
            p = 120
        records[b] = {"period": int(p), "last_kill": None, "channel": None}
    return b


def progress_bar(elapsed: timedelta, total_minutes: int, width: int = 16) -> str:
    total = max(1, total_minutes * 60)
    e = max(0, min(total, int(elapsed.total_seconds())))
    filled = int(round(e / total * width))
    filled = max(0, min(width, filled))
    return "?? * filled + "?? * (width - filled)


def pretty_compact(td: timedelta) -> str:
    """蝺??拚???嚗?撠?59??/ 12??/ 0??""
    total = int(td.total_seconds())
    if total <= 0:
        return "0??
    m, _ = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}撠?{m:02d}?? if h else f"{m}??


def status_of(
    period: int,
    last: Optional[datetime],
    now: datetime,
) -> Tuple[str, str, Optional[datetime], Optional[int], Optional[int]]:
    """
    ? (??泵?? ?拚???, respawn, miss_times, minutes_over)
    ????? ?芰閮??? ?圈?嚚? 15 ?嚚??銝哨??? ?舫?
    """
    if not last:
        return "??", "?芰閮?, None, None, None

    respawn = last + timedelta(minutes=period)
    remain = respawn - now
    if remain.total_seconds() <= 0:
        over_secs = int(-remain.total_seconds())
        period_secs = period * 60
        miss_times = over_secs // period_secs  # 瘥遛銝?望???+1
        if miss_times >= 1:
            return "??", f"撌脤?miss_times}", respawn, miss_times, over_secs // 60
        else:
            return "??", "0??, respawn, 0, 0
    elif remain.total_seconds() <= 15 * 60:
        return "??, pretty_compact(remain), respawn, 0, None
    else:
        return "?妣", pretty_compact(remain), respawn, 0, None


def chunk_text_blocks(lines: List[str], max_len: int = 950) -> List[str]:
    """??銵?摮?????<= max_len ?挾?踝??冽?瑟??挾??""
    blocks, cur = [], ""
    for ln in lines:
        add = (ln + "\n")
        if len(cur) + len(add) > max_len:
            blocks.append(cur.rstrip("\n"))
            cur = add
        else:
            cur += add
    if cur:
        blocks.append(cur.rstrip("\n"))
    return blocks


def fmt_m_d(dt: datetime) -> str:
    if dt is None:
        return "--/--"
    # Windows 銋?函??澆?
    return dt.strftime("%m-%d")


def fmt_h_m(dt: datetime) -> str:
    if dt is None:
        return "--:--"
    return dt.strftime("%H:%M")


# ========= ?∠??? =========
def build_boss_card(
    boss: str,
    rec: dict,
    now: Optional[datetime] = None,
    *,
    state_override: Optional[str] = None,
    color_override: Optional[discord.Color] = None,
    footer_text: Optional[str] = None,
) -> discord.Embed:
    now = now or datetime.now()
    period = safe_period(rec.get("period", 120))
    last: Optional[datetime] = rec.get("last_kill")

    # ????脣漲
    state_line = "?? **撠蝝??*"
    color = discord.Color.greyple()
    bar_text = None
    respawn = None
    remain_text = "??

    if last:
        respawn = last + timedelta(minutes=period)
        remain = respawn - now
        elapsed = now - last
        bar_text = progress_bar(elapsed, period, 18)

        sym, remain_text_calc, _, miss_times, _minutes_over = status_of(period, last, now)
        if sym == "??":
            state_line = "?? **撌脣????**"
            color = discord.Color.red()
            remain_text = "0??
        elif sym == "??:
            state_line = "??**?喳???嚗?5 ?嚗?*"
            color = discord.Color.gold()
            remain_text = remain_text_calc
        elif sym == "?妣":
            state_line = "?妣 **?銝?*"
            color = discord.Color.blurple()
            remain_text = remain_text_calc
        elif sym == "??":
            state_line = f"?? **撌脤??*"
            color = discord.Color.orange()
            remain_text = remain_text_calc

    if state_override:
        state_line = state_override
    if color_override:
        color = color_override

    desc = f"{state_line}\n\n**?望?**嚗period} ??"
    if bar_text:
        desc += f"\n**?脣漲**嚗{bar_text}`"

    e = discord.Embed(title=f"? {boss_label(boss)}", description=desc, color=color)

    if last and respawn:
        e.add_field(
            name="?妤 銝活?捏",
            value=f"?交?嚗fmt_m_d(last)}\n??嚗fmt_h_m(last)}",
            inline=False,
        )
        e.add_field(
            name="??????",
            value=f"?交?嚗fmt_m_d(respawn)}\n??嚗fmt_h_m(respawn)}",
            inline=False,
        )

        e.add_field(name="???拚?", value=remain_text, inline=False)

    if footer_text:
        e.set_footer(text=footer_text)
    return e


# ========= 鈭???View嚗?湧?蝳嚗?=========
class BossKillView(discord.ui.View):
    def __init__(self, boss: str, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.boss = boss
        # 銝?萇??典?冽????隤斗?嚗?
        for child in self.children:
            child.disabled = disabled

    @discord.ui.button(label="?? 閮???", style=discord.ButtonStyle.danger)
    async def btn_kill(self, interaction: discord.Interaction, _: discord.ui.Button):
        b = ensure_boss(self.boss)
        now = datetime.now()
        rec = records[b]
        rec["last_kill"] = now
        rec["killed_by"] = interaction.user.display_name
        # 皜???
        rec.pop("reminded", None)
        rec.pop("carded", None)
        rec.pop("card_channel_id", None)
        rec.pop("card_msg_id", None)
        save_records()

        e = build_boss_card(
            b,
            rec,
            now,
            state_override="?? **撌脫?畾?*",
            color_override=discord.Color.green(),
            footer_text=f"??{interaction.user.display_name} 暺?",
        )
        # 暺?敺?駁????????銴?
        self.clear_items()
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="?咱 瘝", style=discord.ButtonStyle.secondary)
    async def btn_no(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message("撌脣??梧??郭瘝嚗?霈蝝????, ephemeral=True)


# ========= COG =========
class BossCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_task.start()

    def cog_unload(self):
        self.check_task.cancel()

    # --------- 頛嚗?撌脤?暺??????----------
    async def _disable_existing_card(self, boss: str):
        rec = records.get(boss, {})
        chan_id = rec.get("card_channel_id")
        msg_id = rec.get("card_msg_id")
        if not chan_id or not msg_id:
            return
        try:
            channel = self.bot.get_channel(chan_id) or await self.bot.fetch_channel(chan_id)
            msg = await channel.fetch_message(msg_id)
            await msg.edit(view=BossKillView(boss, disabled=True))
            # 銋皜閮??踹?銋???閰?
            rec.pop("card_channel_id", None)
            rec.pop("card_msg_id", None)
            save_records()
        except Exception as e:
            log.warning("?⊥??湔?????%s", e)

    # ?瑼Ｘ嚗???/ ?圈??∠?
    @tasks.loop(seconds=60)
    async def check_task(self):
        now = datetime.now()
        for name, rec in list(records.items()):
            if not rec.get("last_kill") or not rec.get("period"):
                continue

            kill = rec["last_kill"]
            period = safe_period(rec["period"])
            respawn = kill + timedelta(minutes=period)
            remind_time = respawn - timedelta(minutes=EARLY_MINUTES)

            chan_id = rec.get("channel")
            if not chan_id:
                continue
            channel = self.bot.get_channel(chan_id) or await self.bot.fetch_channel(chan_id)
            if not channel:
                continue

            # 3 ???嚗銝甈∴?
            if not rec.get("reminded") and now >= remind_time and now < respawn:
                rec["reminded"] = True
                e = build_boss_card(
                    name,
                    rec,
                    now,
                    state_override=f"??**?喳???嚗EARLY_MINUTES} ?嚗?*",
                )
                try:
                    await channel.send(embed=e)
                    save_records()
                except Exception:
                    log.exception("3 ?????憭望?")

            # ?圈??∠?嚗銝甈∴?
            if not rec.get("carded") and now >= respawn:
                rec["carded"] = True

                # 憒???鈭箏極??/k ??/killat 閮剖?嚗挾???批停?湔蝳??嚗??脤?銴?
                disable_view = False
                if rec.get("manual_set_at"):
                    try:
                        set_at = datetime.fromisoformat(rec["manual_set_at"])
                        if (now - set_at).total_seconds() <= ANTI_DUP_GRACE_SEC:
                            disable_view = True
                    except Exception:
                        pass

                e = build_boss_card(name, rec, now, state_override="?? **撌脣????**")
                try:
                    msg = await channel.send(embed=e, view=BossKillView(name, disabled=disable_view))
                    # 閮??撐?∠?嚗?敺?/k ??/killat ???????
                    rec["card_channel_id"] = msg.channel.id
                    rec["card_msg_id"] = msg.id
                    save_records()
                except Exception:
                    log.exception("?圈??∠??憭望?")

    @check_task.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ?梁嚗?撘?Embed
    async def _send_embeds(self, interaction: discord.Interaction, embeds: list[discord.Embed]):
        if not embeds:
            if interaction.response.is_done():
                await interaction.followup.send("嚗?????", ephemeral=True)
            else:
                await interaction.response.send_message("嚗?????", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embeds[0])
            for e in embeds[1:]:
                await interaction.followup.send(embed=e)
        else:
            for e in embeds:
                await interaction.followup.send(embed=e)

    # ===== 蝞∠?嚗憓?閮剖?/?芷/皜征 =====
    @app_commands.command(name="add", description="?啣? BOSS 銝西身摰望?嚗???")
    @app_commands.describe(boss="BOSS 隞??", period="?望?嚗???")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_(self, interaction: discord.Interaction, boss: str, period: int):
        b = ensure_boss(boss, period)
        records[b]["period"] = int(period)
        save_records()
        await interaction.response.send_message(f"??撌脫憓?{boss_label(b)}嚗望? {period} ????, ephemeral=True)

    @app_commands.command(name="set", description="閮剖? BOSS ?望?嚗???")
    @app_commands.describe(boss="BOSS 隞??", period="?望?嚗???")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_(self, interaction: discord.Interaction, boss: str, period: int):
        b = ensure_boss(boss)
        records[b]["period"] = int(period)
        save_records()
        await interaction.response.send_message(f"??撌脫??{boss_label(b)} ?望???{period} ????, ephemeral=True)

    @app_commands.command(name="del", description="?芷 BOSS 蝝??)
    @app_commands.describe(boss="BOSS 隞??")
    @app_commands.checks.has_permissions(administrator=True)
    async def del_(self, interaction: discord.Interaction, boss: str):
        b = boss.strip()
        if b in records:
            records.pop(b)
            save_records()
            await interaction.response.send_message(f"?? 撌脣??{boss_label(b)} ????閮剖???, ephemeral=True)
        else:
            await interaction.response.send_message("?曆??啗府 BOSS??, ephemeral=True)

    @app_commands.command(name="clear", description="皜征???畾箇???靽??望?嚗?)
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_(self, interaction: discord.Interaction):
        for _, rec in records.items():
            rec["last_kill"] = None
            rec.pop("reminded", None)
            rec.pop("carded", None)
            rec.pop("card_channel_id", None)
            rec.pop("card_msg_id", None)
        save_records()
        await interaction.response.send_message("?完 撌脫?蝛箸???畾箇???靽??望?閮剖?嚗?, ephemeral=True)

    # ===== ?嚗閮?/ ???? / ?亥岷 =====
    @app_commands.command(name="k", description="?餉? BOSS ?捏嚗?冽???")
    @app_commands.describe(boss="BOSS 隞??")
    async def k_(self, interaction: discord.Interaction, boss: str):
        b = ensure_boss(boss)
        now = datetime.now()
        rec = records[b]
        rec["last_kill"] = now
        rec["channel"] = interaction.channel.id
        rec["user"] = interaction.user.display_name
        rec["manual_set_at"] = now.isoformat()  # 璅?鈭箏極閮剖???
        # 皜???
        rec.pop("reminded", None)
        rec.pop("carded", None)
        # 憒??????????
        await self._disable_existing_card(b)
        save_records()

        e = build_boss_card(b, rec, now, footer_text=f"?餉?嚗interaction.user.display_name}")
        log.info("?? /k by %s in #%s -> %s", interaction.user, interaction.channel, b)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="killat", description="??隞予 HHMM ?箸?畾箸???靽格??嚗?)
    @app_commands.describe(boss="BOSS 隞??", time_hhmm="靘? 2130")
    async def killat_(self, interaction: discord.Interaction, boss: str, time_hhmm: str):
        b = ensure_boss(boss)
        now = datetime.now()
        try:
            t = datetime.strptime(time_hhmm, "%H%M")
            kill_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        except ValueError:
            await interaction.response.send_message("?????澆??航炊嚗???HHMM嚗?憒?2340嚗?, ephemeral=True)
            return

        rec = records[b]
        rec["last_kill"] = kill_time
        rec["channel"] = interaction.channel.id
        rec["user"] = interaction.user.display_name
        rec["manual_set_at"] = now.isoformat()
        # 皜???
        rec.pop("reminded", None)
        rec.pop("carded", None)
        # 憒??????????
        await self._disable_existing_card(b)
        save_records()

        e = build_boss_card(b, rec, now, footer_text=f"靽格??嚗interaction.user.display_name}")
        log.info("?? /killat by %s -> %s %s", interaction.user, b, time_hhmm)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="when", description="?亥岷 BOSS ????嚗??嚗?)
    @app_commands.describe(boss="BOSS 隞??嚗?憒?08??2?3嚗?)
    async def when_(self, interaction: discord.Interaction, boss: str):
        b = ensure_boss(boss)
        rec = records[b]
        if not rec.get("last_kill"):
            await interaction.response.send_message(f"??{boss_label(b)} 撠??畾箇???, ephemeral=True)
            return
        now = datetime.now()
        e = build_boss_card(
            b,
            rec,
            now,
            footer_text=f"?亥岷嚗interaction.user.display_name} 嚚??湔嚗now.strftime('%Y-%m-%d %H:%M:%S')}",
        )
        await interaction.response.send_message(embed=e)

    # ===== /all嚗銵?蝑??餈????嚗????????皝擗擃蝮?=====
    @app_commands.command(name="all", description="?撌脩閮?BOSS嚗?餈????嚗?銵?蝑???)
    @app_commands.describe(limit="憿舐內蝑嚗?閮?10嚗???40嚗?)
    async def all_list_(self, interaction: discord.Interaction, limit: Optional[int] = 10):
        now = datetime.now()
        limit = max(1, min(int(limit or 10), 40))

        IND1 = "  "         # 蝚砌???銵葬??
        SEP  = " 嚚?"

        # ???舫＊蝷箇? BOSS
        items = []
        for b, rec in records.items():
            last = rec.get("last_kill")
            if not last:
                continue
            period = safe_period(rec.get("period"))
            sym, _, respawn, miss_times, _ = status_of(period, last, now)
            if not respawn:
                continue
            left_sec = max(0, int((respawn - now).total_seconds()))
            items.append((b, period, last, respawn, sym, miss_times, left_sec))

        # 靘擗??貊撠憭??圈? / 敹怠鈭??
        items.sort(key=lambda x: x[6])

        lines: list[str] = []
        shown = 0
        for b, period, last, respawn, sym, miss_times, _ in items:
            # 蝚砌?銵?閬＊蝷箇??擗?璅?
            if sym == "??" and miss_times and miss_times >= 1:
                tail = f"撌脤?miss_times}"
            elif sym == "??":
                tail = "0??
            else:
                tail = pretty_compact(respawn - now)

            # 銝?銝蝑?
            lines.append(f"??{boss_label(b)}嚗tail}嚗?)
            lines.append(f"{IND1}銝活嚗fmt_m_d(last)}{SEP}{fmt_h_m(last)}")
            lines.append(f"{IND1}??嚗fmt_m_d(respawn)}{SEP}{fmt_h_m(respawn)}")
            lines.append("")  # 蝛箄???

            shown += 1
            if shown >= limit:
                break

        if not lines:
            await interaction.response.send_message("嚗???歇?餉???BOSS嚗?)
            return

        text = "```" + "\n".join(lines).rstrip() + "```"
        e = discord.Embed(
            title="?? 撌脩閮?BOSS ??嚗?餈??????",
            description=text
        )
        e.set_footer(text=f"??嚗len(items)} 蝑?憿舐內??{min(limit, len(items))} 蝑?嚗蝙??/all limit:20 ?舫＊蝷箸憭?)
        await interaction.response.send_message(embed=e)

    # ===== /cards嚗?∪?蝯??殷??券 BOSS嚗?芰閮?嚗???????擃蝮?=====
    @app_commands.command(name="cards", description="?券 BOSS ??蝯??殷??桀??瘣嚗?)
    async def cards_(self, interaction: discord.Interaction):
        now = datetime.now()
        # 蝣箔??身????
        for p, names in DEFAULT_BOSSES.items():
            for n in names:
                ensure_boss(n, p)

        IND = "  "          # 蝚砌???銵葬??
        SEP = " 嚚?"

        grouped: dict[int, list[str]] = {}
        unlogged: list[str] = []

        for b in sorted(records.keys()):
            rec = records[b]
            period = safe_period(rec.get("period"))
            last = rec.get("last_kill")
            sym, _, respawn, miss_times, _ = status_of(period, last, now)

            if not last or not respawn:
                unlogged.append(f"{IND}??{boss_label(b)}??芰閮?)
                continue

            # ?祈??抒????璅?
            if sym == "??" and miss_times and miss_times >= 1:
                tail = f"撌脤?miss_times}"
            elif sym == "??":
                tail = "0??
            else:
                tail = pretty_compact(respawn - now)

            # 銝?銝蝑?蝚砌?銵＊蝷箏擗???蝚砌?銵?甈∴?蝚砌?銵???
            line1 = f"??{boss_label(b)}嚗tail}嚗?
            line2 = f"{IND}銝活嚗fmt_m_d(last)}{SEP}{fmt_h_m(last)}"
            line3 = f"{IND}??嚗fmt_m_d(respawn)}{SEP}{fmt_h_m(respawn)}"
            grouped.setdefault(period, []).extend([line1, line2, line3, ""])

        lines: list[str] = ["?? BOSS 蝮質”嚗?蝯??殷?", ""]
        for period in sorted(grouped.keys()):
            lines.append(f"??{period} ?望?")
            lines.append("```")
            lines.extend(grouped[period])
            lines.append("```")
        if unlogged:
            lines.append("??撠?餉?")
            lines.append("```")
            lines.extend(unlogged)
            lines.append("```")

        text = "\n".join(lines)
        if len(text) <= 3900:
            await interaction.response.send_message(embed=discord.Embed(
                title="?? BOSS 蝮質”嚗?⊥??桃?嚗?, description=text
            ))
        else:
            parts = chunk_text_blocks(lines, max_len=900)
            e = discord.Embed(title="?? BOSS 蝮質”嚗?⊥??桃?嚗?)
            for i, part in enumerate(parts, 1):
                e.add_field(name=f"蝚?{i} 畾?, value="\n".join(part.splitlines()), inline=False)
            await interaction.response.send_message(embed=e)

    # ===== 敺??臬??伐?鞎澆???舫????ID嚗?=====
    @app_commands.command(name="import_msg", description="敺?????臬?伐?鞎潸??舫???D嚗?)
    @app_commands.describe(message_link="閮????? ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def import_msg_(self, interaction: discord.Interaction, message_link: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            msg_id = None
            m = re.search(r"/(\d{17,20})$", message_link.strip())
            if m:
                msg_id = int(m.group(1))
            elif message_link.strip().isdigit():
                msg_id = int(message_link.strip())
            else:
                await interaction.followup.send("???閬票銝??舫??????ID??, ephemeral=True)
                return

            msg = await interaction.channel.fetch_message(msg_id)

            # 閰西?閫???撌梯撓?箇??澆?嚗?甈∴?MM-DD 嚚?HH:MM??
            text = ""
            if msg.embeds:
                emb = msg.embeds[0]
                text = (emb.description or "") + "\n" + "\n".join(f.value or "" for f in emb.fields)
            else:
                text = msg.content or ""

            # ?曉 BOSS 隞??嚗?憒?5 BOSS?????05 BOSS??
            m_boss = re.search(r"(\d{2}|[A-Za-z0-9\-]+)\s*BOSS", text)
            m_last = re.search(r"銝活[:嚗\s*(\d{2})-(\d{2}).*?(\d{2}):(\d{2})", text)

            if not (m_boss and m_last):
                await interaction.followup.send("??閫??憭望?嚗銝 BOSS ?迂??甈⊥?????, ephemeral=True)
                return

            boss = m_boss.group(1)
            month, day, hh, mm = map(int, m_last.groups())
            now = datetime.now()
            last_dt = now.replace(month=month, day=day, hour=hh, minute=mm, second=0, microsecond=0)

            b = ensure_boss(boss)
            rec = records[b]
            rec["last_kill"] = last_dt
            rec["channel"] = interaction.channel.id
            rec["user"] = interaction.user.display_name
            rec["manual_set_at"] = now.isoformat()
            rec.pop("reminded", None)
            rec.pop("carded", None)
            await self._disable_existing_card(b)
            save_records()

            await interaction.followup.send(f"??撌脣??{boss_label(b)}嚗?甈?{fmt_m_d(last_dt)} {fmt_h_m(last_dt)}", ephemeral=True)

        except Exception as e:
            log.exception("import_msg 憭望?")
            await interaction.followup.send(f"??霈???臬仃???航撌脣?斗??⊥???嚗e}", ephemeral=True)

    # ===== ?誘?郊 =====
    @app_commands.command(name="sync", description="??郊?砌撩?嚗???蝞∠??∴?")
    @app_commands.guilds(MY_GUILD)
    async def sync_cmd(self, interaction: discord.Interaction):
        app_owner = (await interaction.client.application_info()).owner
        is_owner = interaction.user.id == app_owner.id
        is_admin = getattr(interaction.user.guild_permissions, "administrator", False)
        if not (is_owner or is_admin):
            await interaction.response.send_message("???閬???蝞∠??～?, ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            synced = await interaction.client.tree.sync(guild=MY_GUILD)
            await interaction.followup.send(
                f"??撌脣?甇亙?砌撩?嚗len(synced)} ??隞歹?"
                + ", ".join(sorted(c.name for c in synced)),
                ephemeral=True,
            )
            log.info("?妣 ?? /sync 摰?")
        except Exception:
            log.exception("?? /sync 憭望?")
            try:
                await interaction.followup.send("???郊憭望?嚗??蜓?批??, ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="syncfix", description="皜?典?畾蔣銝血??郊?砌撩?嚗???蝞∠??∴?")
    @app_commands.guilds(MY_GUILD)
    async def syncfix_cmd(self, interaction: discord.Interaction):
        app_owner = (await interaction.client.application_info()).owner
        is_owner = interaction.user.id == app_owner.id
        is_admin = getattr(interaction.user.guild_permissions, "administrator", False)
        if not (is_owner or is_admin):
            await interaction.response.send_message("???閬???蝞∠??～?, ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.client.tree.sync(guild=None)  # ???典?
            synced = await interaction.client.tree.sync(guild=MY_GUILD)
            await interaction.followup.send(
                f"??撌脫??文?蒂?郊?唳隡箸??剁?{len(synced)} ??隞歹?"
                + ", ".join(sorted(c.name for c in synced)),
                ephemeral=True,
            )
            log.info("SYNCFIX done")
        except Exception:
            log.exception("SYNCFIX 憭望?")
            try:
                await interaction.followup.send("???郊憭望?嚗??蜓?批??, ephemeral=True)
            except Exception:
                pass

    # ?典? app command ?航炊?嚗?????
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("?誘?航炊嚗?s", error)
        try:
            msg = f"???誘?瑁??航炊嚗{type(error).__name__}`嚗error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


# ========= BOT =========
class BossBot(commands.Bot):
    async def setup_hook(self):
        await self.add_cog(BossCog(self))

        # ????閮剔?
        for p, names in DEFAULT_BOSSES.items():
            for n in names:
                ensure_boss(n, p)
        load_records()
        save_records()

        try:
            self.tree.copy_global_to(guild=MY_GUILD)
            synced = await self.tree.sync(guild=MY_GUILD)
            log.info("?? ???郊摰?嚗 %s ??Slash ?誘嚗?s", len(synced), ", ".join(sorted(c.name for c in synced)))
        except Exception:
            log.exception("???郊憭望?")


intents = discord.Intents.default()
intents.message_content = True
bot = BossBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("??撌脩?伐?%s (id=%s)", bot.user, bot.user.id)


# ?????誘??郊嚗???
@bot.command(name="sync")
@commands.is_owner()
async def legacy_sync(ctx: commands.Context):
    try:
        bot.tree.copy_global_to(guild=MY_GUILD)
        res = await bot.tree.sync(guild=MY_GUILD)
        await ctx.send(f"??撌脣?甇?{len(res)} ??隞文?砌撩?嚗', '.join(sorted(c.name for c in res))}")
    except Exception as e:
        await ctx.send(f"???郊憭望?嚗e}")


if __name__ == "__main__":
bot.run(os.getenv("DISCORD_TOKEN"))







