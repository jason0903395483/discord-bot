# === 1) ?桅? ===
$ROOT = 'D:\bot\gaid_scanner'
New-Item -ItemType Directory -Force -Path $ROOT | Out-Null
New-Item -ItemType Directory -Force -Path "$ROOT\data" | Out-Null

# === 2) requirements.txt ===
@'
discord.py>=2.4.0
python-dotenv>=1.0.1
paddleocr>=2.7.0
shapely>=2.0.4
opencv-python>=4.9.0.80
pytesseract>=0.3.10
rapidfuzz>=3.9.3
Pillow>=10.4.0
'@ | Set-Content -Encoding UTF8 "$ROOT\requirements.txt"

# === 3) .env.example ===
@'
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID=0
MENTION_BATCH=15
'@ | Set-Content -Encoding UTF8 "$ROOT\.env.example"

# === 4) .env嚗?葆?乩?????Token嚗?==
$TOKEN = os.getenv("DISCORD_TOKEN")
@"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID=0
MENTION_BATCH=15
"@ | Set-Content -Encoding UTF8 "$ROOT\.env"

# === 5) storage.py ===
@'
# -*- coding: utf-8 -*-
import json, os, threading
from typing import Dict, List

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DATA_PATH = os.path.join(DATA_DIR, "allies.json")
_lock = threading.Lock()

_DEFAULT = {"allies": [], "mapping": {}}

def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_data() -> dict:
    _ensure_dir()
    if not os.path.exists(DATA_PATH):
        save_data(_DEFAULT.copy())
        return _DEFAULT.copy()
    with _lock, open(DATA_PATH, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = _DEFAULT.copy()
    data.setdefault("allies", [])
    data.setdefault("mapping", {})
    return data

def save_data(data: dict):
    _ensure_dir()
    with _lock, open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_ally(game_id: str, discord_user_id: str | None = None):
    data = load_data()
    if game_id not in data["allies"]:
        data["allies"].append(game_id)
    if discord_user_id:
        data["mapping"][game_id] = str(discord_user_id)
    save_data(data)

def list_allies() -> tuple[list[str], dict]:
    d = load_data()
    return d["allies"], d["mapping"]

def bind(game_id: str, discord_user_id: str):
    d = load_data()
    if game_id not in d["allies"]:
        d["allies"].append(game_id)
    d["mapping"][game_id] = str(discord_user_id)
    save_data(d)
'@ | Set-Content -Encoding UTF8 "$ROOT\storage.py"

# === 6) utils.py ===
@'
# -*- coding: utf-8 -*-
from typing import Iterable, List, Tuple
from rapidfuzz import fuzz, process

def dedupe_fuzzy(names: Iterable[str], threshold: int = 92) -> List[str]:
    out: List[str] = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        if not out:
            out.append(n); continue
        best = process.extractOne(n, out, scorer=fuzz.token_set_ratio)
        if not best or best[1] < threshold:
            out.append(n)
        else:
            keep = max(n, best[0], key=len)
            out[out.index(best[0])] = keep
    return out

def fuzzy_in(name: str, whitelist: Iterable[str], threshold: int = 90) -> Tuple[bool, str, int]:
    if not whitelist:
        return False, name, 0
    best = process.extractOne(name, whitelist, scorer=fuzz.token_set_ratio)
    if best and best[1] >= threshold:
        return True, best[0], int(best[1])
    return False, name, int(best[1] if best else 0)

def chunk_lines(lines: List[str], batch: int) -> List[str]:
    out, cur, count = [], [], 0
    for ln in lines:
        cur.append(ln); count += 1
        if count >= batch:
            out.append("\n".join(cur)); cur=[]; count=0
    if cur: out.append("\n".join(cur))
    return out
'@ | Set-Content -Encoding UTF8 "$ROOT\utils.py"

# === 7) ocr.py ===
@'
# -*- coding: utf-8 -*-
import io
from typing import List
from PIL import Image
import numpy as np

_USE_PADDLE = False
try:
    from paddleocr import PaddleOCR
    _paddle = PaddleOCR(lang="ch", use_angle_cls=True, show_log=False)
    _USE_PADDLE = True
except Exception:
    _paddle = None

try:
    import cv2
except Exception:
    cv2 = None

try:
    import pytesseract
except Exception:
    pytesseract = None

def _paddle_extract(img: np.ndarray) -> List[str]:
    result = _paddle.ocr(img, cls=True)
    out: List[str] = []
    for line in result:
        for _, (txt, score) in line:
            if score < 0.55:
                continue
            t = txt.strip()
            if len(t) >= 2:
                out.append(t)
    return out

def _tesseract_extract(img: np.ndarray) -> List[str]:
    if pytesseract is None:
        return []
    if cv2 is not None:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        scale = 2.0
        gray = cv2.resize(gray, (int(w*scale), int(h*scale)))
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 31, 5)
        txt = pytesseract.image_to_string(bw, lang="chi_tra+eng", config="--oem 3 --psm 6")
    else:
        pil = Image.fromarray(img)
        txt = pytesseract.image_to_string(pil, lang="chi_tra+eng", config="--oem 3 --psm 6")
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    return lines

def extract_names(image_bytes: bytes) -> List[str]:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    npimg = np.array(img)[:, :, ::-1] if cv2 is not None else np.array(img)
    if _USE_PADDLE:
        return _paddle_extract(npimg)
    return _tesseract_extract(npimg)
'@ | Set-Content -Encoding UTF8 "$ROOT\ocr.py"

# === 8) app.py ===
@'
# -*- coding: utf-8 -*-
import os, asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from storage import add_ally, list_allies, bind
from ocr import extract_names
from utils import dedupe_fuzzy, fuzzy_in, chunk_lines

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
MENTION_BATCH = int(os.getenv("MENTION_BATCH", "15"))

if not TOKEN:
    raise SystemExit("隢 .env 閮剖? DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="~no_prefix~", intents=intents)

class GAIDGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="gaid", description="ROM GA嚚??D蝬剛風??????蝪∪???")

gaid = GAIDGroup()

@gaid.command(name="add", description="?啣???ID嚗??蝬? @?")
@app_commands.describe(game_id="?銝剔?ID??", member="嚗?賂?閬?摰?DC?")
async def gaid_add(inter: discord.Interaction, game_id: str, member: discord.Member | None = None):
    await inter.response.defer(thinking=True, ephemeral=True)
    add_ally(game_id.strip(), str(member.id) if member else None)
    msg = f"??撌脣??亦?嚗?*{game_id}**"
    if member:
        msg += f"嚗蒂蝬???{member.mention}"
    await inter.followup.send(msg, ephemeral=True)

@gaid.command(name="list", description="憿舐內?賢??株?撌脩?摰???)
async def gaid_list(inter: discord.Interaction):
    await inter.response.defer(thinking=True, ephemeral=True)
    allies, mapping = list_allies()
    if not allies:
        await inter.followup.send("嚗?????D嚗?, ephemeral=True); return
    lines = []
    for a in sorted(allies):
        if a in mapping:
            uid = int(mapping[a])
            m = inter.guild.get_member(uid)
            mention = m.mention if m else f"<@{uid}>"
            lines.append(f"??**{a}** ??{mention}")
        else:
            lines.append(f"?恬? **{a}**")
    chunks = chunk_lines(lines, 25)
    for idx, c in enumerate(chunks, 1):
        await inter.followup.send(f"**??皜嚗idx}/{len(chunks)}嚗?*\n{c}", ephemeral=True)

async def _resolve_image_bytes(inter: discord.Interaction, image: discord.Attachment | None) -> bytes | None:
    if image:
        return await image.read()
    return None

@gaid.command(name="scan", description="???? ???菜葫ID ???芸???銝?@ 蝬??")
@app_commands.describe(image="嚗?賂?銝閬????芸?嚗蝯血?隢???舫?銝???)
async def gaid_scan(inter: discord.Interaction, image: discord.Attachment | None = None):
    await inter.response.defer(thinking=True)
    img_bytes = await _resolve_image_bytes(inter, image)
    if img_bytes is None:
        await inter.followup.send("隢?誘?????辣嚗???/gaid scan ?銝剜?靘?image嚗?, ephemeral=True)
        return

    raw = extract_names(img_bytes)
    raw = [s for s in raw if len(s.strip()) >= 2 and not s.strip().isdigit()]
    names = dedupe_fuzzy(raw, threshold=92)

    allies, mapping = list_allies()
    allies_set = set(allies)

    ally_lines, outsider_lines, unknown_map = [], [], []

    for n in names:
        hit, key, score = fuzzy_in(n, allies_set, threshold=90)
        if hit:
            uid = mapping.get(key)
            if uid:
                m = inter.guild.get_member(int(uid))
                mention = (m.mention if m else f"<@{uid}>")
                ally_lines.append(f"??**{key}** {mention}")
            else:
                ally_lines.append(f"??**{key}**")
                unknown_map.append(key)
        else:
            outsider_lines.append(f"??{n}")

    blocks = []
    if ally_lines:
        blocks.append("**??**\n" + "\n".join(ally_lines))
    if outsider_lines:
        blocks.append("**憭犖 / ?芸??賢???*\n" + "\n".join(outsider_lines))
    if unknown_map:
        todo = "??.join(sorted(set(unknown_map))[:10])
        blocks.append(f"?妝 撠蝬?????{todo}\n?∴? ?舐 `/gaid add <?ID> @?` 蝡鋆?")

    text = "\n\n".join(blocks) if blocks else "嚗?菜葫?啣?冽?摮?隢?銝撘菜??唳??"
    lines = text.splitlines()
    chunks = chunk_lines(lines, MENTION_BATCH)
    for part in chunks:
        await inter.followup.send(part)

bot.tree.add_command(gaid)

@bot.event
async def on_ready():
    try:
        if GUILD_ID > 0:
            g = discord.Object(id=GUILD_ID)
            await bot.tree.sync(guild=g)
            print(f"??Slash ?誘撌脣?甇亙 Guild({GUILD_ID})")
        else:
            cmds = await bot.tree.sync()
            print(f"???典? Slash ?誘?郊摰?嚗len(cmds)} ??)
    except Exception as e:
        print(f"?? ?誘?郊憭望?嚗e}")
    print(f"?? Logged in as {bot.user} ({bot.user.id})")

if __name__ == "__main__":
bot.run(os.getenv("DISCORD_TOKEN"))
'@ | Set-Content -Encoding UTF8 "$ROOT\app.py"

# === 9) README.md ===
@'
# GAID Scanner (ROM GA ??ID????

## ?誘
- `/gaid add <?ID> [@?]`嚗??亦?嚗??蝬? @??
- `/gaid list`嚗????摰????撌勗閬???
- `/gaid scan [image]`嚗?????????????憭犖??銝西??@ 撌脩?摰?

## 摰?
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 撌脩?單撱箇?嚗?湔蝺刻摩




