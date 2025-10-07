import os
from dotenv import load_dotenv
import asyncio
from dotenv import load_dotenv
import logging
from dotenv import load_dotenv
import re
from dotenv import load_dotenv
import json
from dotenv import load_dotenv
import time
from dotenv import load_dotenv
import httpx
from dotenv import load_dotenv
import discord
from dotenv import load_dotenv
from collections import OrderedDict, defaultdict
from urllib.parse import quote_plus
from discord.ext import commands

load_dotenv()

# ========= ?箸閮剖? =========
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    intents=intents
)

# ========= 隤?撠 =========
LANG_MAP = {
    "EN": "en", "ENG": "en", "ENGLISH": "en",
    "CN": "zh-CN", "ZH": "zh-CN", "ZH-CN": "zh-CN", "CHINESE": "zh-CN",
    "TW": "zh-TW", "ZH-TW": "zh-TW", "TC": "zh-TW", "TRAD": "zh-TW",
}
def norm_lang(s: str) -> str | None:
    if not s:
        return None
    return LANG_MAP.get(s.strip().upper().replace("_", "-"))

# ========= ??皜?/?斗 =========
MENTION_RE = re.compile(r"<@!?\d+>")
CHANNEL_RE = re.compile(r"<#\d+>")
EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
CODEBLOCK_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`([^`]*)`")

def clean_discord_text(text: str) -> str:
    text = CODEBLOCK_RE.sub("", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = MENTION_RE.sub("", text)
    text = CHANNEL_RE.sub("", text)
    text = EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()

def has_meaningful_content(text: str) -> bool:
    # ?芰?銝?貉?銝剜??嚗摨?0 閬?∪摰?
    meaningful = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text)
    return len(meaningful) > 0

def detect_source_lang(text: str) -> str:
    return "zh-CN" if re.search(r"[\u4e00-\u9fff]", text) else "en"

# ========= HTTP Client嚗eep-Alive嚗? http2 ??????蝝?=========
CLIENT_TIMEOUT = httpx.Timeout(connect=1.0, read=2.5, write=2.5, pool=2.5)
client: httpx.AsyncClient | None = None
use_http2 = True

# ?典?憭隢?銝衣?嚗???云憭?????嚗?
EXTERNAL_CONCURRENCY = 8
ext_sema = asyncio.Semaphore(EXTERNAL_CONCURRENCY)

# ========= 蝡舫? =========
LIBRE_ENDPOINTS = [
    "https://libretranslate.de",
    "https://translate.astian.org",
    "https://lt.vern.cc",
]
LINGVA_ENDPOINTS = [
    "https://lingva.ml",
    "https://lingva.garudalinux.org",
]

# ========= LRU 敹怠?嚗 TTL嚗?========
class TTLCache:
    def __init__(self, cap=1000, ttl=300):
        self.cap = cap
        self.ttl = ttl
        self.data = OrderedDict()  # key -> (value, expire_ts)

    def get(self, key):
        item = self.data.get(key)
        if not item:
            return None
        val, exp = item
        if exp < time.time():
            self.data.pop(key, None)
            return None
        self.data.move_to_end(key)
        return val

    def set(self, key, val):
        self.data[key] = (val, time.time() + self.ttl)
        self.data.move_to_end(key)
        if len(self.data) > self.cap:
            self.data.popitem(last=False)

cache = TTLCache(cap=1000, ttl=300)

# ========= ?岫撌亙嚗??詨?? + ??嚗?========
async def with_retry(coro_factory, retries=2, base_delay=0.25, max_delay=1.5):
    """
    coro_factory: 銝??澆??lambda嚗???coroutine嚗?甈⊿?閰衣???瘙?
    """
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except Exception:
            attempt += 1
            if attempt > retries:
                return None
            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + (0.05 * attempt)
            await asyncio.sleep(delay)

# ========= 靘??祕雿?=========
async def libre_once(base: str, text: str, target: str) -> str | None:
    assert client is not None
    payload = {"q": text, "source": "auto", "target": target, "format": "text"}
    async with ext_sema:
        r = await client.post(f"{base}/translate", json=payload)
    if r.status_code == 200:
        out = r.json().get("translatedText", "")
        if out and out.strip():
            return out.strip()
    return None

async def libre_task(base: str, text: str, target: str) -> str | None:
    return await with_retry(lambda: libre_once(base, text, target), retries=1)

async def translate_via_libre_race(text: str, target: str) -> str | None:
    tasks = [asyncio.create_task(libre_task(b, text, target)) for b in LIBRE_ENDPOINTS]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    result = None
    for d in done:
        try:
            result = d.result()
        except Exception:
            result = None
    for p in pending:
        p.cancel()
    return result

async def mymemory_once(text: str, target: str) -> str | None:
    assert client is not None
    source = detect_source_lang(text)
    url = f"https://api.mymemory.translated.net/get?q={quote_plus(text)}&langpair={source}|{quote_plus(target)}"
    async with ext_sema:
        r = await client.get(url)
    if r.status_code == 200:
        out = (r.json().get("responseData") or {}).get("translatedText", "")
        if out and out.strip():
            return out.strip()
    return None

async def translate_via_mymemory(text: str, target: str) -> str | None:
    return await with_retry(lambda: mymemory_once(text, target), retries=1)

async def lingva_once(base: str, text: str, target: str) -> str | None:
    assert client is not None
    tgt = {"zh-CN": "zh", "zh-TW": "zh-TW", "en": "en"}[target]
    q = quote_plus(text)
    async with ext_sema:
        r = await client.get(f"{base}/api/v1/auto/{tgt}/{q}")
    if r.status_code == 200:
        data = r.json()
        out = data.get("translation") or data.get("translatedText") or ""
        if out and out.strip():
            return out.strip()
    return None

async def translate_via_lingva(text: str, target: str) -> str | None:
    async def _one(b):  # 撣園?閰?
        return await with_retry(lambda: lingva_once(b, text, target), retries=1)
    tasks = [asyncio.create_task(_one(b)) for b in LINGVA_ENDPOINTS]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    result = None
    for d in done:
        try:
            result = d.result()
        except Exception:
            result = None
    for p in pending:
        p.cancel()
    return result

# ========= 蝧餉陌銝餅?蝔?=========
async def translate_text(text: str, target: str) -> str:
    text = clean_discord_text(text)
    if not has_meaningful_content(text):
        return "嚗?頛詨閬蕃霅舐???嚗?
    if target not in {"en", "zh-CN", "zh-TW"}:
        return "嚗??舀?璅?閮嚗?

    key = f"{target}::{text}"
    cached = cache.get(key)
    if cached:
        return cached

    racers = [
        asyncio.create_task(translate_via_libre_race(text, target)),
        asyncio.create_task(translate_via_mymemory(text, target)),
        asyncio.create_task(translate_via_lingva(text, target)),
    ]
    try:
        while racers:
            done, pending = await asyncio.wait(racers, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    res = d.result()
                except Exception:
                    res = None
                if res:
                    for p in pending:
                        p.cancel()
                    cache.set(key, res)
                    return res
            racers = list(pending)
    finally:
        for t in racers:
            t.cancel()

    return "?? ?桀?蝧餉陌???急?銝?剁?隢?敺?閰艾?

# ========= 瘥?雿輻??摨??極雿????脫銵?=========
USER_QUEUE_LIMIT = 8          # 瘥?雿輻??憭???8 蝑?
USER_IDLE_EXIT = 60           # 60 蝘瘝撌乩?撠梢???worker

user_queues: dict[int, asyncio.Queue] = {}
user_workers: dict[int, asyncio.Task] = {}
warn_cooldown: dict[int, float] = defaultdict(float)  # ??皛踵????瑕

async def user_worker(uid: int):
    """瘥?雿輻??璇?瘞渡?嚗?????嚗????憭?瘙?""
    q = user_queues[uid]
    try:
        while True:
            try:
                job = await asyncio.wait_for(q.get(), timeout=USER_IDLE_EXIT)
            except asyncio.TimeoutError:
                break  # 憭芯?瘝撌乩?嚗?蝺?
            ctx_or_channel, target, text = job
            try:
                out = await translate_text(text, target)
                # ctx_or_channel ?航??ctx ??channel
                if hasattr(ctx_or_channel, "send"):
                    await ctx_or_channel.send(out)
                else:
                    await ctx_or_channel.send(out)
            finally:
                q.task_done()
    finally:
        # 皜?
        user_queues.pop(uid, None)
        user_workers.pop(uid, None)

def enqueue_job(user_id: int, dest, target: str, text: str):
    """?曉??嚗皛蹂?嚗?蝷箸銝暺?""
    q = user_queues.get(user_id)
    if q is None:
        q = asyncio.Queue(maxsize=USER_QUEUE_LIMIT)
        user_queues[user_id] = q
        user_workers[user_id] = asyncio.create_task(user_worker(user_id))

    if q.full():
        now = time.time()
        if now - warn_cooldown[user_id] > 5:
            warn_cooldown[user_id] = now
            # 銝?靘????
            try:
                asyncio.create_task(dest.send("??雿撓?亙云敹怠嚚??冽?????蝔?銝銝???))
            except Exception:
                pass
        return False

    q.put_nowait((dest, target, text))
    return True

# ========= ??閫貊嚗O <LANG> <text> =========
PATTERN = re.compile(r"^\s*TO\s+([A-Za-z\-]+)\s+(.+)$", re.IGNORECASE)

@bot.event
async def on_ready():
    global client, use_http2
    if client is None:
        try:
            client = httpx.AsyncClient(http2=True, timeout=CLIENT_TIMEOUT,
                                       headers={"User-Agent": "FastTransBot/1.1"})
            use_http2 = True
        except Exception:
            client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT,
                                       headers={"User-Agent": "FastTransBot/1.1"})
            use_http2 = False
    print(f"??Ready as {bot.user} | http2={use_http2} | guilds = {[g.name for g in bot.guilds]}")

@bot.event
async def on_close():
    global client
    if client:
        await client.aclose()
        client = None

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    content = message.content.strip()

    m = PATTERN.match(content)
    if m:
        lang_raw, txt = m.group(1), m.group(2)
        to_code = norm_lang(lang_raw)
        if not to_code:
            await message.channel.send("??隤?隞?Ⅳ??湛?EN / CN(蝪∩葉) / TW(蝜葉)")
            return
        enqueue_job(message.author.id, message.channel, to_code, txt)
        return

    await bot.process_commands(message)

# ========= ?誘 =========
@bot.command(help="撱園皜祈岫")
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency*1000)} ms")

@bot.command(name="en", help="!en <銝剜?> ??頧??)
@commands.cooldown(5, 6, commands.BucketType.user)  # 6 蝘?憭?5 甈?
async def en_cmd(ctx, *, text: str):
    enqueue_job(ctx.author.id, ctx, "en", text)

@bot.command(name="cn", help="!cn <?望?/蝜葉> ??頧陛銝?)
@commands.cooldown(5, 6, commands.BucketType.user)
async def cn_cmd(ctx, *, text: str):
    enqueue_job(ctx.author.id, ctx, "zh-CN", text)

@bot.command(name="tw", help="!tw <?望?/蝪∩葉> ??頧?銝?)
@commands.cooldown(5, 6, commands.BucketType.user)
async def tw_cmd(ctx, *, text: str):
    enqueue_job(ctx.author.id, ctx, "zh-TW", text)

# ========= ?? =========
bot.run(os.getenv("DISCORD_TOKEN"))













