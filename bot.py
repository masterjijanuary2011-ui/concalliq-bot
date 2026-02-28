"""
ConcallIQ — AI-Powered Telegram Bot
Smart Stock Intelligence for Indian Retail Investors
Uses Claude AI as brain — thinks like an agent, simple like a bot
"""

import os, re, json, time, sqlite3, requests, threading, schedule
from datetime import datetime
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
DB_PATH        = "stockiq.db"

DEFAULT_WATCHLIST = [
    "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK",
    "SBIN","WIPRO","TATASTEEL","SUNPHARMA","MARUTI"
]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS concalls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, quarter TEXT, date TEXT,
            transcript TEXT, analysis TEXT, sentiment TEXT, fetched_at TEXT,
            UNIQUE(symbol, quarter)
        );
        CREATE TABLE IF NOT EXISTS user_watchlist (
            chat_id TEXT, symbol TEXT,
            PRIMARY KEY(chat_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT, symbol TEXT, message TEXT, sent_at TEXT
        );
    """)
    conn.commit()
    conn.close()

def get_watchlist(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol FROM user_watchlist WHERE chat_id=?", (str(chat_id),))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows] if rows else DEFAULT_WATCHLIST

def add_stock(chat_id, symbol):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO user_watchlist VALUES (?,?)", (str(chat_id), symbol))
    conn.commit(); conn.close()

def remove_stock(chat_id, symbol):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM user_watchlist WHERE chat_id=? AND symbol=?", (str(chat_id), symbol))
    conn.commit(); conn.close()

def send(chat_id, text):
    if not TELEGRAM_TOKEN:
        print(f"MSG→{chat_id}: {text[:150]}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except: pass

def typing(chat_id):
    if not TELEGRAM_TOKEN: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
                      json={"chat_id": chat_id, "action": "typing"}, timeout=5)
    except: pass

def ai(prompt, max_tokens=900):
    if not ANTHROPIC_KEY:
        return "AI unavailable. Add ANTHROPIC_API_KEY."
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system": "You are an expert equity analyst for Indian stock markets (NSE/BSE). Give crisp, actionable analysis in simple language. Use Markdown formatting with * for bold. Keep responses under 400 words.",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        return f"AI error {r.status_code}"
    except Exception as e:
        return f"AI error: {e}"

def fetch_nse_concalls(symbol):
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        time.sleep(1)
        r = s.get(
            f"https://www.nseindia.com/api/corp-info?symbol={symbol}&corpType=announcements&market=equities",
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"},
            timeout=10
        )
        data = r.json().get("data", [])
        return [a for a in data if any(k in a.get("subject","").lower()
                for k in ["concall","conference call","earnings call","transcript","investor meet"])]
    except:
        return []

def fetch_screener(symbol):
    try:
        r = requests.get(f"https://www.screener.in/company/{symbol}/consolidated/",
                         headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        name = soup.find("h1")
        name = name.text.strip() if name else symbol
        ratios = {}
        for li in soup.find_all("li", class_="flex flex-space-between")[:12]:
            spans = li.find_all("span")
            if len(spans) >= 2:
                ratios[spans[0].text.strip()] = spans[-1].text.strip()
        tables = soup.find_all("table")
        tbl_text = ""
        for t in tables[:2]:
            tbl_text += t.get_text(separator="|") + "\n"
        return {"name": name, "ratios": ratios, "table": tbl_text[:2000]}
    except:
        return {"name": symbol, "ratios": {}, "table": ""}

def fetch_shareholding(symbol):
    try:
        r = requests.get(f"https://www.screener.in/company/{symbol}/",
                         headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        sec = soup.find("section", id="shareholding")
        return sec.get_text(separator="\n")[:1500] if sec else ""
    except:
        return ""

def fetch_bulk_deals():
    try:
        r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w",
                         headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        return r.json().get("Table", [])[:15]
    except:
        return []

def detect_quarter(text, date_str=""):
    m = re.search(r'Q([1-4])\s*(?:FY)?\s*(\d{2,4})', text.upper())
    if m:
        yr = m.group(2)
        if len(yr)==2: yr="20"+yr
        return f"Q{m.group(1)}FY{yr[-2:]}"
    if date_str:
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            mo, yr = dt.month, dt.year
            if mo in [4,5,6]: return f"Q1FY{str(yr+1)[-2:]}"
            elif mo in [7,8,9]: return f"Q2FY{str(yr+1)[-2:]}"
            elif mo in [10,11,12]: return f"Q3FY{str(yr+1)[-2:]}"
            else: return f"Q4FY{str(yr)[-2:]}"
        except: pass
    return "Latest"

def h_start(cid, name):
    send(cid, f"""🚀 *Welcome to ConcallIQ!*
_AI Stock Intelligence for Indian Investors_

Namaste {name}! Main aapka personal equity analyst hoon.

*📞 Concall:* `/concall RELIANCE`
*📊 Results:* `/results TCS`
*👥 Shareholding:* `/holding HDFCBANK`
*💰 Bulk Deals:* `/deals`
*🔍 Full Analysis:* `/analyse SUNPHARMA`
*🤖 Kuch bhi poocho:* `/ask INFY guidance positive hai?`
*🌅 Morning Digest:* `/morning`
*📋 Watchlist:* `/watchlist`
*➕ Add Stock:* `/add BAJFINANCE`""")

def h_concall(cid, symbol):
    if not symbol:
        send(cid, "Example: `/concall RELIANCE`"); return
    typing(cid)
    send(cid, f"🔍 *{symbol}* ka concall fetch ho raha hai...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT analysis, quarter FROM concalls WHERE symbol=? ORDER BY rowid DESC LIMIT 1", (symbol,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        send(cid, row[0]); return
    anns = fetch_nse_concalls(symbol)
    if anns:
        latest = anns[0]
        qtr = detect_quarter(latest["subject"], latest.get("an_dt",""))
        typing(cid)
        prompt = f"Analyse concall for {symbol} {qtr}: {latest['subject']}. Give bullish/neutral/bearish verdict with key points in Hindi-English mix."
        analysis = ai(prompt)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO concalls VALUES (NULL,?,?,?,?,?,?,?)",
                     (symbol, qtr, latest.get("an_dt",""), latest["subject"], analysis, "AI", datetime.now().isoformat()))
        conn.commit(); conn.close()
        send(cid, analysis)
    else:
        data = fetch_screener(symbol)
        prompt = f"Company: {data['name']} ({symbol})\nRatios: {data['ratios']}\nData: {data['table'][:1000]}\nGive investment analysis."
        send(cid, ai(prompt))

def h_results(cid, symbol):
    if not symbol:
        send(cid, "Example: `/results HDFCBANK`"); return
    typing(cid)
    data = fetch_screener(symbol)
    prompt = f"Quarterly results analysis for {symbol}:\n{data['table']}\nRatios: {data['ratios']}\nGive verdict."
    send(cid, ai(prompt))

def h_holding(cid, symbol):
    if not symbol:
        send(cid, "Example: `/holding SBIN`"); return
    typing(cid)
    holding = fetch_shareholding(symbol)
    prompt = f"Shareholding analysis for {symbol}:\n{holding if holding else 'No data'}\nWhat does it mean for retail investor?"
    send(cid, ai(prompt))

def h_deals(cid, symbol=None):
    typing(cid)
    deals = fetch_bulk_deals()
    if symbol:
        deals = [d for d in deals if symbol in str(d).upper()]
    if not deals:
        send(cid, "Aaj koi bulk deals nahi hain."); return
    prompt = f"Bulk deals analysis:\n{json.dumps(deals[:8])}\nKey highlights for retail investor?"
    send(cid, ai(prompt))

def h_ask(cid, symbol, question):
    if not symbol or not question:
        send(cid, "Format: `/ask SYMBOL question`"); return
    typing(cid)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT analysis FROM concalls WHERE symbol=? ORDER BY rowid DESC LIMIT 1", (symbol,))
    row = c.fetchone()
    conn.close()
    context = row[0] if row else ""
    prompt = f"Stock: {symbol}\nContext: {context[:1000]}\nQuestion: {question}\nSimple answer max 200 words."
    send(cid, f"🤖 *{symbol}*\n\n{ai(prompt)}")

def h_analyse(cid, symbol):
    if not symbol:
        send(cid, "Example: `/analyse TATASTEEL`"); return
    typing(cid)
    send(cid, f"🔄 *{symbol}* full analysis ho raha hai...")
    data = fetch_screener(symbol)
    holding = fetch_shareholding(symbol)
    prompt = f"Complete investment analysis for {symbol}:\nRatios: {data['ratios']}\nFinancials: {data['table'][:1000]}\nHolding: {holding[:300]}\nGive POSITIVE/NEUTRAL/CAUTIOUS verdict with reasons."
    send(cid, ai(prompt, max_tokens=1000))

def h_morning(cid):
    typing(cid)
    wl = get_watchlist(cid)
    prompt = f"Morning digest for Indian stocks: {wl[:8]}\nDate: {datetime.now().strftime('%d %b %Y')}\nGive brief bullish/bearish/neutral summary for each."
    send(cid, ai(prompt))

def h_watchlist(cid):
    wl = get_watchlist(cid)
    msg = "📋 *Aapki Watchlist:*\n\n"
    for sym in wl:
        msg += f"⚪ *{sym}*\n"
    msg += f"\n_Total: {len(wl)} stocks_\n\n➕ /add SYMBOL | ➖ /remove SYMBOL"
    send(cid, msg)

def h_help(cid):
    send(cid, """📱 *ConcallIQ — Sabhi Commands*
`/concall SYMBOL` — Concall analysis
`/results SYMBOL` — Quarterly results
`/holding SYMBOL` — Shareholding
`/deals` — Bulk/block deals
`/analyse SYMBOL` — Full analysis
`/ask SYMBOL sawaal` — AI se poocho
`/morning` — Daily digest
`/watchlist` — Watchlist dekhna
`/add SYMBOL` — Stock add karo
`/remove SYMBOL` — Stock hatao""")

def route(update):
    msg   = update.get("message", {})
    text  = msg.get("text", "").strip()
    cid   = msg.get("chat", {}).get("id")
    name  = msg.get("from", {}).get("first_name", "")
    if not text or not cid: return
    parts = text.split(maxsplit=2)
    cmd   = parts[0].lower().split("@")[0]
    a1    = parts[1].upper().strip() if len(parts) > 1 else ""
    a2    = parts[2].strip() if len(parts) > 2 else ""
    dispatch = {
        "/start": lambda: h_start(cid, name),
        "/help": lambda: h_help(cid),
        "/concall": lambda: h_concall(cid, a1),
        "/cc": lambda: h_concall(cid, a1),
        "/results": lambda: h_results(cid, a1),
        "/holding": lambda: h_holding(cid, a1),
        "/deals": lambda: h_deals(cid, a1 or None),
        "/ask": lambda: h_ask(cid, a1, a2),
        "/analyse": lambda: h_analyse(cid, a1),
        "/morning": lambda: h_morning(cid),
        "/watchlist": lambda: h_watchlist(cid),
        "/add": lambda: (add_stock(cid, a1), send(cid, f"✅ *{a1}* added!")),
        "/remove": lambda: (remove_stock(cid, a1), send(cid, f"✅ *{a1}* removed.")),
    }
    fn = dispatch.get(cmd)
    if fn:
        fn()
    elif not text.startswith("/") and len(text) > 5:
        typing(cid)
        send(cid, f"🤖 {ai(f'Indian stock market question: {text}. Simple answer.')}")
    else:
        send(cid, "❓ /help type karein.")

def main():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_BOT_TOKEN set nahi hai!")
        return
    print("🤖 ConcallIQ Bot start ho raha hai...")
    init_db()
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35
            )
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                threading.Thread(target=route, args=(upd,), daemon=True).start()
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
Ab ye karo:
Code copy karo ✅
GitHub pe jaao → concalliq-bot repository
Add file → Create new file
Naam likho: bot.py
Code paste karo
Commit changes dabao
Karo! 😊
