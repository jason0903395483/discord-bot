#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_10pct_allin_one.py

One file to:
  - monitor: CoinGecko prices & +/- threshold vs daily baseline; Telegram optional
  - paper:   paper trading with auto signals or FOLLOW_SIGNALS copy-trading; weekly P&L
  - live:    real spot trading on exchanges via CCXT (market orders); tiny size & LIVE=1 required
  - backtest:quick OHLCV backtest of the 10% baseline rule

Select mode with env MODE_MAIN = monitor | paper | live | backtest
Requires: pip install requests ccxt python-dotenv

Common env:
  THRESHOLD_PCT=10           # trigger threshold percent
  VS_CURRENCY=usd            # for monitor/paper pricing
  TELEGRAM_BOT_TOKEN=...     # optional
  TELEGRAM_CHAT_ID=...       # optional

Monitor/Paper env:
  COINS=btc,eth,sol,...      # else auto top-N by mcap (paper/monitor only)
  MAX_UNIVERSE=50
  ORDER_PCT_OF_CASH=5        # paper sizing
  START_CASH=100000
  SIGNAL_MODE=momentum       # or contrarian
  FOLLOW_SIGNALS=0           # 1 to follow signals.csv (paper only)
  LEADER_SIGNALS_PATH=signals.csv
  WEEKLY_REPORT=0            # 1 to print/save a weekly PnL json and exit (paper)

Live env:
  EXCHANGE=binance
  API_KEY=...
  API_SECRET=...
  API_PASSWORD=...           # passphrase for OKX/KuCoin if needed
  SYMBOLS=BTC/USDT,ETH/USDT  # CCXT symbols
  ORDER_USDT=10              # notional per BUY
  LIVE=0                     # set 1 to place REAL orders

Backtest env:
  EXCHANGE=binance
  SYMBOL=BTC/USDT
  TF=5m
  DAYS=60
  MODE=momentum | contrarian

Files output (depending on mode):
  signals.csv, trades.csv, prices.csv, state.json, weekly_reports/
"""
import os, sys, csv, json, math, time, datetime as dt
from pathlib import Path
from typing import Dict, List
import requests

try:
    import ccxt  # only needed for live/backtest
except Exception:
    ccxt = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------- Common helpers ----------------
HEADERS = {"Accept": "application/json", "User-Agent": "allinone-10pct/1.0"}

def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def today_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

def pct_change(cur: float, base: float) -> float:
    return 0.0 if base == 0 else (cur - base) / base * 100.0

def send_telegram(msg: str):
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    chat  = os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": msg, "parse_mode":"HTML"}, timeout=15)
    except Exception:
        pass

# ---------------- Monitor / Paper shared ----------------
VS_CCY        = os.getenv("VS_CURRENCY","usd").lower()
try:
    THRESHOLD_PCT = float(os.getenv("THRESHOLD_PCT","10"))
except ValueError:
    THRESHOLD_PCT = 10.0
COINS_ENV     = os.getenv("COINS","").strip()
try:
    MAX_UNIVERSE  = int(os.getenv("MAX_UNIVERSE","50"))
except ValueError:
    MAX_UNIVERSE = 50
SIGNAL_MODE   = os.getenv("SIGNAL_MODE","momentum").lower()

TICKER_MAP = {
    "btc":"bitcoin","xbt":"bitcoin","eth":"ethereum","sol":"solana","xrp":"ripple","bnb":"binancecoin",
    "ton":"the-open-network","toncoin":"the-open-network","ada":"cardano","doge":"dogecoin","matic":"matic-network",
    "dot":"polkadot","trx":"tron","link":"chainlink","ltc":"litecoin","avax":"avalanche-2","atom":"cosmos"
}

def resolve_universe() -> List[str]:
    if COINS_ENV:
        return [TICKER_MAP.get(c.strip().lower(), c.strip().lower()) for c in COINS_ENV.split(",") if c.strip()]
    # auto fetch top-N from CoinGecko
    try:
        per_page = min(MAX_UNIVERSE, 250)
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params={"vs_currency":VS_CCY,"order":"market_cap_desc","per_page":per_page,"page":1},
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        return [x["id"] for x in r.json() if "id" in x]
    except Exception:
        return ["bitcoin","ethereum","solana","ripple","binancecoin","the-open-network"]

def fetch_prices(ids: List[str]) -> Dict[str, float]:
    if not ids: return {}
    r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids":",".join(ids),"vs_currencies":VS_CCY},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    raw = r.json()
    return {cid: float(raw.get(cid,{}).get(VS_CCY,0) or 0) for cid in ids}

# ---------------- Monitor mode ----------------
def mode_monitor():
    STATE = Path("monitor_state.json")
    LOG   = Path("monitor_log.csv")
    if not LOG.exists():
        with LOG.open("w",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow(["ts","date","coin","price","baseline","pct_change","alerted"])
    # state
    if STATE.exists():
        try: st = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: st = {}
    else:
        st = {}
    baselines = st.setdefault("baselines", {})

    ids = resolve_universe()
    prices = fetch_prices(ids)
    if not prices:
        print("No prices."); return

    today = today_utc()
    day = baselines.setdefault(today, {})
    alerts = 0
    for cid, px in prices.items():
        base = day.get(cid)
        if base is None: day[cid] = px; base = px
        pct = pct_change(px, base)
        alerted = int(abs(pct) >= THRESHOLD_PCT)
        with LOG.open("a",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow([now_iso(), today, cid, f"{px:.8f}", f"{base:.8f}", f"{pct:.4f}", alerted])
        if alerted:
            alerts += 1
            msg = f"?? {cid} moved {pct:+.2f}% vs baseline ({base:.6f} ??{px:.6f})"
            print(msg); send_telegram(msg)
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[monitor] {len(prices)} coins checked. alerts={alerts}")

# ---------------- Paper mode ----------------
def mode_paper():
    try:
        START_CASH        = float(os.getenv("START_CASH","100000"))
    except ValueError:
        START_CASH = 100000.0
    try:
        ORDER_PCT_OF_CASH = float(os.getenv("ORDER_PCT_OF_CASH","5"))
    except ValueError:
        ORDER_PCT_OF_CASH = 5.0
    try:
        MAX_POS_PER_COIN  = int(os.getenv("MAX_POS_PER_COIN","2"))
    except ValueError:
        MAX_POS_PER_COIN = 2
    FOLLOW_SIGNALS    = os.getenv("FOLLOW_SIGNALS","0").strip()=="1"
    LEADER_PATH       = Path(os.getenv("LEADER_SIGNALS_PATH","signals.csv"))
    WEEKLY_REPORT     = os.getenv("WEEKLY_REPORT","0").strip()=="1"

    STATE = Path("paper_state.json")
    TRADES= Path("trades.csv")
    SIGNALS=Path("signals.csv")
    PRICES= Path("prices.csv")
    for p,h in [(TRADES,["trade_id","ts","coin","side","qty","price","notional","cash_after"]),
                (SIGNALS,["ts","coin","signal","price","pct_vs_baseline","mode"]),
                (PRICES,["ts","coin","price"])]:
        if not p.exists():
            with p.open("w",newline="",encoding="utf-8") as f: csv.writer(f).writerow(h)

    # state
    if STATE.exists():
        try: st=json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: st={}
    else:
        st={}
    st.setdefault("cash", START_CASH)
    st.setdefault("positions", {})
    st.setdefault("baselines", {})
    st.setdefault("trade_id", 1)
    st.setdefault("history", [])

    def get_baseline(coin, px):
        today = today_utc()
        day = st["baselines"].setdefault(today,{})
        if coin not in day: day[coin]=px
        return day[coin]

    def emit_signal(coin, sig, px, pct):
        with SIGNALS.open("a",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow([now_iso(),coin,sig,f"{px:.8f}",f"{pct:.4f}",SIGNAL_MODE])

    def place(side, coin, px):
        if side=="buy":
            cash_to_use = st["cash"] * (ORDER_PCT_OF_CASH/100.0)
            if cash_to_use<=0: return False
            qty = cash_to_use/px
            pos = st["positions"].get(coin, {"qty":0.0,"avg":0.0,"entries":0})
            if pos["entries"]>=MAX_POS_PER_COIN: return False
            new_qty = pos["qty"]+qty
            new_avg = (pos["avg"]*pos["qty"] + px*qty)/new_qty if new_qty>0 else 0.0
            pos.update({"qty":new_qty,"avg":new_avg,"entries":pos["entries"]+1})
            st["positions"][coin]=pos
            st["cash"]-=qty*px
            tid=st["trade_id"]; st["trade_id"]+=1
            with TRADES.open("a",newline="",encoding="utf-8") as f:
                csv.writer(f).writerow([tid,now_iso(),coin,"buy",f"{qty:.8f}",f"{px:.8f}",f"{qty*px:.2f}",f"{st['cash']:.2f}"])
            return True
        else:
            pos=st["positions"].get(coin); if_not = (not pos) or pos["qty"]<=0
            if if_not: return False
            qty=pos["qty"]; notional=qty*px
            # realized pnl
            realized=(px-pos["avg"])*qty
            st["history"].append({"ts":now_iso(),"coin":coin,"realized_pnl":realized})
            st["cash"]+=notional
            st["positions"].pop(coin,None)
            tid=st["trade_id"]; st["trade_id"]+=1
            with TRADES.open("a",newline="",encoding="utf-8") as f:
                csv.writer(f).writerow([tid,now_iso(),coin,"sell",f"{qty:.8f}",f"{px:.8f}",f"{notional:.2f}",f"{st['cash']:.2f}"])
            return True

    if WEEKLY_REPORT:
        # mark-to-market with last prices of our holdings
        ids=list(st["positions"].keys())
        px=fetch_prices(ids) if ids else {}
        eq=st["cash"]+sum(st["positions"][c]["qty"]*(px.get(c,st["positions"][c]["avg"])) for c in st["positions"])
        now=dt.datetime.now(dt.timezone.utc); year,week,_=now.isocalendar()
        monday=(now-dt.timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
        realized=sum(h["realized_pnl"] for h in st["history"] if dt.datetime.fromisoformat(h["ts"]).astimezone(dt.timezone.utc)>=monday)
        report={"week":f"{year}-W{week:02d}","timestamp":now_iso(),"cash":round(st["cash"],2),
                "equity":round(eq,2),"realized_week":round(realized,2),"unrealized":round(eq-st["cash"]-realized,2),
                "open_positions":{k:{"qty":round(v["qty"],8),"avg":round(v["avg"] ,8)} for k,v in st["positions"].items()}}
        Path("weekly_reports").mkdir(exist_ok=True)
        out=Path("weekly_reports")/f"{report['week']}.json"
        out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps(report,indent=2,ensure_ascii=False)); return

    # normal run
    ids = resolve_universe()
    pxs = fetch_prices(ids)
    if not pxs: print("No prices."); return
    # price snapshots
    with Path("prices.csv").open("a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        for c,p in pxs.items(): w.writerow([now_iso(),c,f"{p:.8f}"])
    # generate or follow signals
    signals=[]
    if os.getenv("FOLLOW_SIGNALS","0").strip()=="1" and LEADER_PATH.exists():
        today = today_utc()
        with LEADER_PATH.open("r",encoding="utf-8") as f:
            rd=csv.DictReader(f)
            for r in rd:
                if r["ts"].startswith(today): signals.append((r["coin"], r["signal"], float(r["price"]), float(r["pct_vs_baseline"])))
    else:
        for c, p in pxs.items():
            base = get_baseline(c, p)
            pct = pct_change(p, base)
            pos = st["positions"].get(c)
            if SIGNAL_MODE=="momentum":
                if pct>=THRESHOLD_PCT and (not pos or pos["entries"]<MAX_POS_PER_COIN): signals.append((c,"buy",p,pct))
                elif pct<=-THRESHOLD_PCT and pos: signals.append((c,"sell",p,pct))
            else:
                if pct<=-THRESHOLD_PCT and (not pos or pos["entries"]<MAX_POS_PER_COIN): signals.append((c,"buy",p,pct))
                elif pct>=THRESHOLD_PCT and pos: signals.append((c,"sell",p,pct))
    # execute
    for c, sig, p, pctv in signals:
        emit_signal(c, sig, p, pctv)
        ok = place("buy" if sig=="buy" else "sell", c, p)
        flag="?? if ok else "?哨?"
        print(f"{flag} {sig.upper()} {c} @ {p:.6f} ({pctv:+.2f}%)  cash={st['cash']:.2f}")

    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[paper] positions={len(st['positions'])} cash={st['cash']:.2f}  {now_iso()}")

# ---------------- Live mode (CCXT) ----------------
def mode_live():
    if ccxt is None:
        print("ccxt not installed. pip install ccxt"); return
    EXCHANGE_ID=os.getenv("EXCHANGE","binance")
    API_KEY=os.getenv("API_KEY",""); API_SECRET=os.getenv("API_SECRET",""); API_PASSWORD=os.getenv("API_PASSWORD","")
    SYMBOLS=[s.strip().upper() for s in os.getenv("SYMBOLS","BTC/USDT,ETH/USDT").split(",") if s.strip()]
    try:
        ORDER_USDT=float(os.getenv("ORDER_USDT","10"))
    except ValueError:
        ORDER_USDT=10.0
    LIVE=os.getenv("LIVE","0").strip()=="1"
    MODE=os.getenv("MODE","momentum").lower()
    try:
        THRESH=float(os.getenv("THRESHOLD_PCT","10"))/100.0
    except ValueError:
        THRESH=0.10
    try:
        SLEEP=int(os.getenv("SLEEP_SEC","15"))
    except ValueError:
        SLEEP=15

    def connect():
        klass=getattr(ccxt,EXCHANGE_ID); conf={"enableRateLimit":True,"apiKey":API_KEY,"secret":API_SECRET}
        if API_PASSWORD: conf["password"]=API_PASSWORD
        ex=klass(conf); ex.load_markets(); return ex
    ex=connect()
    STATE=Path("live_state.json"); LOGT=Path("trades_live.csv"); LOGS=Path("signals_live.csv")
    if not LOGT.exists():
        with LOGT.open("w",newline="",encoding="utf-8") as f: csv.writer(f).writerow(["ts","symbol","side","qty","price","notional","order_id","live"])
    if not LOGS.exists():
        with LOGS.open("w",newline="",encoding="utf-8") as f: csv.writer(f).writerow(["ts","symbol","signal","price","pct_vs_baseline","mode"])
    if STATE.exists():
        try: st=json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: st={}
    else:
        st={}
    st.setdefault("baselines", {}); st.setdefault("positions", {})

    def mid(tk):
        bid=tk.get("bid") or tk.get("last"); ask=tk.get("ask") or tk.get("last")
        return (bid+ask)/2.0 if (bid and ask) else float(tk.get("last") or 0)
    def lot(qty, symbol):
        m=ex.markets.get(symbol,{})
        step=(m.get("limits",{}).get("amount",{}) or {}).get("step")
        amin=(m.get("limits",{}).get("amount",{}) or {}).get("min")
        if step: qty=math.floor(qty/step)*step
        if amin and qty<amin: return 0.0
        return float(ex.amount_to_precision(symbol, qty))
    def order(side,symbol,qty):
        if qty<=0: return None
        if LIVE: return ex.create_order(symbol,"market",side,qty)
        else: return {"id":"dry-"+now_iso()}

    print(f"[{now_iso()}] {EXCHANGE_ID} LIVE={LIVE} MODE={MODE} THRESH={THRESH*100:.1f}% SYM={SYMBOLS}")
    while True:
        today=today_utc()
        st["baselines"].setdefault(today, {})
        for sym in SYMBOLS:
            try:
                tk=ex.fetch_ticker(sym); price=mid(tk)
                if price<=0: continue
                base=st["baselines"][today].get(sym)
                if base is None: st["baselines"][today][sym]=price; base=price
                ch=pct_change(price, base)
                pos=st["positions"].get(sym,0.0)
                signal=None
                if MODE=="momentum":
                    if ch>=THRESH*100 and pos==0: signal="buy"
                    elif ch<=-THRESH*100 and pos>0: signal="sell"
                else:
                    if ch<=-THRESH*100 and pos==0: signal="buy"
                    elif ch>=THRESH*100 and pos>0: signal="sell"
                if signal:
                    with LOGS.open("a",newline="",encoding="utf-8") as f:
                        csv.writer(f).writerow([now_iso(),sym,signal,f"{price:.8f}",f"{ch:.4f}",MODE])
                if signal=="buy":
                    qty=ORDER_USDT/price; qty=lot(qty,sym)
                    if qty<=0: continue
                    o=order("buy",sym,qty); st["positions"][sym]=st["positions"].get(sym,0.0)+qty
                    with LOGT.open("a",newline="",encoding="utf-8") as f:
                        csv.writer(f).writerow([now_iso(),sym,"buy",f"{qty:.8f}",f"{price:.8f}",f"{qty*price:.2f}",o.get("id"),int(LIVE)])
                    print(f"[BUY ] {sym} {qty:.8f} @~{price:.6f} ({ch:+.2f}% vs base {base:.6f})")
                elif signal=="sell":
                    qty=st["positions"].get(sym,0.0); qty=lot(qty,sym)
                    if qty<=0: continue
                    o=order("sell",sym,qty); st["positions"][sym]=0.0
                    with LOGT.open("a",newline="",encoding="utf-8") as f:
                        csv.writer(f).writerow([now_iso(),sym,"sell",f"{qty:.8f}",f"{price:.8f}",f"{qty*price:.2f}",o.get("id"),int(LIVE)])
                    print(f"[SELL] {sym} {qty:.8f} @~{price:.6f} ({ch:+.2f}% vs base {base:.6f})")
                STATE.write_text(json.dumps(st,indent=2,ensure_ascii=False),encoding="utf-8")
            except Exception as e:
                print(f"[live err] {sym} {e}")
        time.sleep(SLEEP)

# ---------------- Backtest mode ----------------
def mode_backtest():
    if ccxt is None:
        print("ccxt not installed. pip install ccxt"); return
    EX=os.getenv("EXCHANGE","binance")
    SYMBOL=os.getenv("SYMBOL","BTC/USDT")
    TF=os.getenv("TF","5m")
    try:
        DAYS=int(os.getenv("DAYS","60"))
    except ValueError:
        DAYS=60
    MODE=os.getenv("MODE","momentum").lower()
    try:
        THR=float(os.getenv("THRESHOLD_PCT","10"))/100.0
    except ValueError:
        THR=0.10
    ex=getattr(ccxt,EX)({"enableRateLimit":True})
    since=ex.milliseconds()-DAYS*24*60*60*1000
    data=[]
    while since < ex.milliseconds():
        batch=ex.fetch_ohlcv(SYMBOL, timeframe=TF, since=since, limit=1500)
        if not batch: break
        data.extend(batch); since=batch[-1][0]+1
        if len(batch)<1500: break
    if not data: print("no data"); return
    cash=10000.0; qty=0.0; cur=None; base=None; wins=0; losses=0
    entry=None
    for ts,o,h,l,c,v in data:
        d=dt.datetime.utcfromtimestamp(ts/1000).date()
        if d!=cur: cur=d; base=o
        up=base*(1+THR); dn=base*(1-THR)
        if MODE=="momentum":
            if qty==0 and c>=up: qty=cash/c; entry=c; cash=0
            elif qty>0 and c<=dn: cash=qty*c; wins+=1 if c>entry else 0; losses+=1 if c<=entry else 0; qty=0
        else:
            if qty==0 and c<=dn: qty=cash/c; entry=c; cash=0
            elif qty>0 and c>=up: cash=qty*c; wins+=1 if c>entry else 0; losses+=1 if c<=entry else 0; qty=0
    equity=cash + qty*(data[-1][4] if data else 0)
    pnl_pct = (equity-10000)/10000*100.0
    print({"symbol":SYMBOL,"tf":TF,"days":DAYS,"mode":MODE,"threshold_pct":THR*100,"equity":round(equity,2),"pnl_pct":round(pnl_pct,2),"wins":wins,"losses":losses,"n":len(data)})

# ---------------- main ----------------
def main():
    mode = os.getenv("MODE_MAIN","monitor").lower()
    if mode=="monitor": mode_monitor()
    elif mode=="paper": mode_paper()
    elif mode=="live": mode_live()
    elif mode=="backtest": mode_backtest()
    else: print("Set MODE_MAIN to monitor | paper | live | backtest")

if __name__=="__main__":
    main()

