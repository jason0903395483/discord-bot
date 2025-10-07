# js_helper_bot.py
import os
import datetime
import asyncio
import discord
from discord.ext import tasks
from googleapiclient.discovery import build
from dotenv import load_dotenv

# ========== ?啣?霈 ==========
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
if not DISCORD_CHANNEL_ID:
    raise RuntimeError("隢 .env 閮剖? DISCORD_CHANNEL_ID")
DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID)

# ========== ?摮?==========
SEARCH_QUERIES = [
    "憭?蝘餃極?啗?",
    "?啁?極?輻?",
    "憭??極甈?",
    "憭??唾?瘚??湔",
    "????憭??極 瘜?",
    "site:mol.gov.tw 憭??極",
    "site:wda.gov.tw ?閮勗",
]

# ========== Discord Client ==========
intents = discord.Intents.default()
intents.guilds = True  # ?Ⅱ?? guilds 敹怠?
client = discord.Client(intents=intents)

# ========== Google ?芾??? ==========
def google_search(query: str, api_key: str, cse_id: str, num: int = 5):
    service = build("customsearch", "v1", developerKey=api_key)
    res = service.cse().list(q=query, cx=cse_id, num=num).execute()
    return res.get("items", []) or []

# ?梁嚗祕?閰Ｚ??
async def post_update_once(channel: discord.abc.Messageable):
    embed = discord.Embed(
        title="憭??賊??啗???閬?- 瘥?湔",
        description=f"隞乩??臭???Google ?????啗?閮??湔??嚗datetime.date.today()}嚗?",
        color=discord.Color.blue(),
    )

    try:
        all_results = []
        seen_links = set()  # ?駁?

        for query in SEARCH_QUERIES:
            items = google_search(query, GOOGLE_API_KEY, GOOGLE_CSE_ID, num=3)
            for it in items:
                link = it.get("link")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                all_results.append(it)

        if not all_results:
            await channel.send("隞予瘝??曉?賊???撠???)
            return

        for it in all_results:
            title = it.get("title")
            link = it.get("link")
            snippet = it.get("snippet") or ""
            if title and link:
                embed.add_field(name=title, value=f"[?梯??游?]({link})\n{snippet}", inline=False)

        await channel.send(embed=embed)
        print("[Info] 撌脣???駁??潔??湔??)

    except discord.Forbidden:
        print("[Error] 璈鈭箏閰脤???????閬??喲??胯??仿??嚗?)
    except Exception as e:
        print(f"[Error] ?潮?唳??潛?靘?嚗e}")

async def get_target_channel():
    """?芸??典翰???駁?嚗?銝???API 鋆?甈～?""
    ch = client.get_channel(DISCORD_CHANNEL_ID)
    if ch is not None:
        return ch
    try:
        ch = await client.fetch_channel(DISCORD_CHANNEL_ID)
        return ch
    except discord.NotFound:
        print(f"[Error] ?曆??圈??ID: {DISCORD_CHANNEL_ID}嚗?蝣箄???*???駁?**??ID??)
    except discord.Forbidden:
        print("[Error] ??仃??璈鈭箇甈??亦?閰脤??)
    except Exception as e:
        print(f"[Error] ????憭?{e}")
    return None

# ========== 鈭辣 ==========
@client.event
async def on_ready():
    print(f"璈鈭箏歇銝?嚗client.user}")

    # ??瘥予 09:00嚗????
    if not daily_9am_update.is_running():
        daily_9am_update.start()
        print("??嚗???09:00嚗????撠?雿??啗?閮?)

    # ??敺??喟銝甈∴??嫣噶雿Ⅱ隤?
    await asyncio.sleep(2)  # 蝯血翰??暺???
    ch = await get_target_channel()
    if ch:
        print("[Info] ??敺??喟雿?甈∩誑皜祈岫...")
        await post_update_once(ch)

# ========== 瘥予 09:00嚗??隞餃? ==========
TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))
RUN_TIME_9_TAIPEI = datetime.time(hour=9, minute=0, tzinfo=TAIPEI_TZ)

@tasks.loop(time=RUN_TIME_9_TAIPEI)
async def daily_9am_update():
    print("[Info] 閫貊瘥 09:00 ?湔隞餃?...")
    ch = await get_target_channel()
    if not ch:
        return
    await post_update_once(ch)

# ========== ?亙 ==========
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("蝻箏? DISCORD_TOKEN")
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        raise RuntimeError("蝻箏? GOOGLE_API_KEY ??GOOGLE_CSE_ID")
client.run(os.getenv("DISCORD_TOKEN"))

