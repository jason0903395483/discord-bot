# -*- coding: utf-8 -*-
# app.py  —  GAID Scanner 主程式（完整可貼）
import os
import io
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv, find_dotenv

# ---------- 讀取 .env ----------
dotenv_path = find_dotenv(filename=".env", usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path=dotenv_path, override=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")

def _mask(s: str) -> str:
    return (s[:6] + "..." + s[-6:]) if len(s) >= 12 else ("***" if not s else s)

print(f"[ENV] GUILD_ID = {GUILD_ID} | TOKEN = {_mask(TOKEN)}")
if not TOKEN:
    raise SystemExit("請在 .env 設定 DISCORD_TOKEN")

# ---------- 匯入模組 ----------
from storage import add_ally, list_allies  # 資料存取
from utils import dedupe_fuzzy, fuzzy_in   # 模糊比對
from ocr import extract_names              # 圖片取名

# 嘗試載入可選的樣板儲存工具（舊/新版介面皆可相容）
def _save_template_png_fallback(_: bytes):
    return False, "此版本未提供 save_template_png"

try:
    from ocr import save_template_png as _save_template_png
except Exception:
    _save_template_png = _save_template_png_fallback

# ---------- 安全輸出工具（避免超過 2000 字） ----------
MAX_MSG = 1900  # 留一些空間給標題/換行

def split_by_chars(lines, max_chars=MAX_MSG):
    """把多行字串分包，確保每包不超過 max_chars。"""
    chunk, size = [], 0
    for ln in lines:
        ln = ln.rstrip()
        if not ln:
            continue
        if len(ln) > max_chars:
            if chunk:
                yield "\n".join(chunk); chunk, size = [], 0
            for i in range(0, len(ln), max_chars):
                yield ln[i:i+max_chars]
            continue
        add = (1 if chunk else 0) + len(ln)
        if size + add > max_chars:
            if chunk:
                yield "\n".join(chunk)
            chunk, size = [ln], len(ln)
        else:
            chunk.append(ln); size += add
    if chunk:
        yield "\n".join(chunk)

async def send_batched_text(inter: discord.Interaction, header: str, lines, mention_users=True):
    """把多行內容分批傳訊。"""
    am = discord.AllowedMentions(users=mention_users, roles=False, everyone=False)
    parts = list(split_by_chars(lines))
    if not parts:
        await inter.followup.send(header, allowed_mentions=am)
        return
    for i, body in enumerate(parts, 1):
        prefix = f"{header}（{i}/{len(parts)}）\n" if len(parts) > 1 else f"{header}\n"
        await inter.followup.send(prefix + body, allowed_mentions=am)

async def send_as_textfile(inter: discord.Interaction, filename: str, lines, note: str | None = None):
    """把大量內容改成附件傳送。"""
    buf = io.StringIO()
    if note:
        buf.write(note.rstrip() + "\n")
    for ln in lines:
        buf.write(ln.rstrip() + "\n")
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    await inter.followup.send(
        file=discord.File(data, filename),
        allowed_mentions=discord.AllowedMentions.none()
    )

# ---------- Slash 群組 ----------
class GAIDGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="gaid", description="ROM GA｜盟友ID維護與圖片掃描")

gaid = GAIDGroup()

# /gaid add <遊戲ID> [@成員]
@gaid.command(name="add", description="新增盟友ID；可同時綁定 @成員")
@app_commands.describe(game_id="遊戲中的ID文字", member="（可選）要綁定的DC成員")
async def gaid_add(inter: discord.Interaction, game_id: str, member: discord.Member | None = None):
    await inter.response.defer(thinking=True, ephemeral=True)
    add_ally(game_id.strip(), str(member.id) if member else None)
    msg = f"已加入白名單：{game_id}"
    if member:
        msg += f"，並綁定到 {member.mention}"
    await inter.followup.send(msg, ephemeral=True)

# /gaid list
@gaid.command(name="list", description="顯示白名單與已綁定對應（僅自己可見）")
async def gaid_list(inter: discord.Interaction):
    await inter.response.defer(thinking=True, ephemeral=True)
    allies, mapping = list_allies()
    if not allies:
        await inter.followup.send("目前沒有盟友ID。", ephemeral=True)
        return

    lines = []
    for a in sorted(allies):
        if a in mapping:
            uid = int(mapping[a])
            m = inter.guild.get_member(uid)
            mention = m.mention if m else f"<@{uid}>"
            lines.append(f"- {a}  {mention}")
        else:
            lines.append(f"- {a}")

    await send_batched_text(inter, "**盟友清單**", lines, mention_users=False)

# /gaid set_template  (可選；用來上傳徽章樣板 PNG/JPG)
@gaid.command(name="set_template", description="上傳一張徽章樣板（只擷取徽章右側名字）")
@app_commands.describe(image="建議小一點的截圖：PNG/JPG")
async def gaid_set_template(inter: discord.Interaction, image: discord.Attachment):
    await inter.response.defer(thinking=True, ephemeral=True)
    if (not image.content_type) or (not image.content_type.startswith("image/")):
        await inter.followup.send("請上傳圖片（png/jpg）。", ephemeral=True)
        return
    data = await image.read()
    try:
        ok, path_or_msg = _save_template_png(data)  # 新版：回傳 (ok, msg)
    except TypeError:
        # 舊版可能是 (ok, path, dbg)
        res = _save_template_png(data)
        ok, path_or_msg = (res[0], res[1]) if isinstance(res, tuple) and len(res) >= 2 else (False, "未知回傳格式")
    if not ok:
        await inter.followup.send(f"儲存失敗：{path_or_msg}", ephemeral=True)
    else:
        await inter.followup.send(f"樣板已更新：{path_or_msg}\n之後用 `/gaid scan` 會更容易分辨名字。", ephemeral=True)

# /gaid scan  [image]
@gaid.command(name="scan", description="掃描一張截圖 → 偵測ID → 自動分類並 @ 綁定成員")
@app_commands.describe(image="（可選）上傳要掃描的截圖；未給則請在同訊息附上圖片")
async def gaid_scan(inter: discord.Interaction, image: discord.Attachment | None = None):
    await inter.response.defer(thinking=True)

    if image is None or (not image.content_type) or (not image.content_type.startswith("image/")):
        await inter.followup.send("請於指令附上一張圖片（png/jpg）。", allowed_mentions=discord.AllowedMentions.none())
        return

    img_bytes = await image.read()

    # 取名（先過濾長度與純數字）
    raw_names = extract_names(img_bytes)
    raw_names = [s for s in raw_names if len(s.strip()) >= 2 and not s.strip().isdigit()]
    names = dedupe_fuzzy(raw_names, threshold=92)

    if not names:
        await inter.followup.send(
            "沒有偵測到玩家名；請換更清晰的截圖，或先用 `/gaid set_template` 設定徽章樣板。",
            allowed_mentions=discord.AllowedMentions.none()
        )
        return

    allies, mapping = list_allies()
    allies_set = set(allies)

    ally_lines, outsider_lines, unknown_map = [], [], []

    for n in names:
        hit, key, score = fuzzy_in(n, allies_set, threshold=90)
        if hit:
            uid = mapping.get(key)
            if uid:
                m = inter.guild.get_member(int(uid))
                mention = m.mention if m else f"<@{uid}>"
                ally_lines.append(f"- {key}  {mention}")
            else:
                ally_lines.append(f"- {key}")
                unknown_map.append(key)
        else:
            outsider_lines.append(f"[OUT] {n}")

    # 安全輸出（不會超 2000 字）
    summary = []

    if ally_lines:
        summary.append(f"盟友命中：{len(ally_lines)}")
        await send_batched_text(inter, "**盟友**", ally_lines, mention_users=True)

    if outsider_lines:
        summary.append(f"外人/未列白名單：{len(outsider_lines)}")
        # 若內容太多，改成附件
        if len("\n".join(outsider_lines)) > 1500 or len(outsider_lines) > 20:
            await send_as_textfile(
                inter, "outsiders.txt", outsider_lines,
                note="偵測到的非白名單字串（可能含雜訊/誤判，請自行判讀）："
            )
        else:
            await send_batched_text(inter, "**外人 / 未列白名單**", outsider_lines, mention_users=False)

    if unknown_map:
        todo = "、".join(sorted(set(unknown_map))[:15])
        await inter.followup.send(
            f"尚未綁定的盟友：{todo}\n可用 `/gaid add <遊戲ID> @成員` 立即綁定。",
            allowed_mentions=discord.AllowedMentions.none()
        )

    if not (ally_lines or outsider_lines or unknown_map):
        await inter.followup.send(
            "沒有可用的偵測結果；請換更清晰的截圖試試。",
            allowed_mentions=discord.AllowedMentions.none()
        )
    else:
        await inter.followup.send(" | ".join(summary), allowed_mentions=discord.AllowedMentions.none())

# 測試用
@app_commands.command(name="ping", description="health check")
async def ping(inter: discord.Interaction):
    await inter.response.send_message("pong", ephemeral=True)

# ---------- Bot ----------
class MyBot(commands.Bot):
    async def setup_hook(self):
        self.tree.add_command(gaid)
        self.tree.add_command(ping)

        if GUILD_ID > 0:
            g = discord.Object(id=GUILD_ID)
            cmds = await self.tree.sync(guild=g)
            print(f"[SLASH] Synced to Guild({GUILD_ID}). Count: {len(cmds)}")
        else:
            cmds = await self.tree.sync()
            print(f"[SLASH] Synced GLOBAL. Count: {len(cmds)}")

    async def on_ready(self):
        print(f"[READY] Logged in as {self.user} ({self.user.id})")

intents = discord.Intents.default()
bot = MyBot(command_prefix="~no_prefix~", intents=intents)

if __name__ == "__main__":
bot.run(os.getenv("DISCORD_TOKEN"))




