"""Swanix Alpha Bot: posts the earliest crypto news, new projects and trends
to a Telegram channel. Free sources only. Runs once per call (cron friendly)."""
import json, os, re, time, html, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
import ordnet

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@swanixcrypto")
DRY_RUN = os.environ.get("DRY_RUN") == "1" or not TOKEN
STATE_FILE = "seen.json"
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS", "8"))
UA = {"User-Agent": "Mozilla/5.0 (SwanixAlphaBot)"}

# Topics the channel covers. Edit freely.
KEYWORDS = [
    "ordinal", "inscription", "brc-20", "brc20", "runes", "bitcoin l2", "btcfi",
    "airdrop", "testnet", "mainnet", "waitlist", "points", "tge", "token launch",
    "perp dex", "perpetual", "presale", "whitelist", "mint", "nft", "zcash",
    "token launch", "new listing", "binance alpha", "hyperliquid", "restaking", "memecoin",
]

NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
}
GOOGLE_NEWS_QUERIES = [
    "ordinals OR brc-20 OR runes bitcoin",
    "crypto airdrop OR testnet OR waitlist",
    "perp dex launch",
]

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"seen": [], "llama_since": 0, "first_run": True}

def save_state(s):
    s["seen"] = s["seen"][-3000:]
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)

_KW = re.compile(r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")s?\b", re.I)

def matches(text):
    return bool(_KW.search(text))

def parse_rss(raw):
    out = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub("<[^>]+>", "", it.findtext("description") or "")
        if title and link:
            out.append((title, link, desc))
    return out

def news_items():
    items = []
    for name, url in NEWS_FEEDS.items():
        try:
            for title, link, desc in parse_rss(get(url))[:25]:
                if matches(title + " " + desc):
                    items.append(("📰", name, title, link))
        except Exception as e:
            print("feed failed", name, e)
    for q in GOOGLE_NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1d")
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            for title, link, _ in parse_rss(get(url))[:10]:
                items.append(("⚡", "Google News", title, link))
        except Exception as e:
            print("gnews failed", q, e)
    return items

def new_projects(state):
    """Brand-new protocols listed on DefiLlama, with their X handle."""
    items = []
    try:
        data = json.loads(get("https://api.llama.fi/protocols", 60))
        since = state.get("llama_since", 0) or (time.time() - 6 * 3600)
        fresh = [p for p in data if (p.get("listedAt") or 0) > since]
        fresh.sort(key=lambda p: p["listedAt"])
        for p in fresh[-10:]:
            x = f" · X: x.com/{p['twitter']}" if p.get("twitter") else ""
            chains = ", ".join((p.get("chains") or [])[:3])
            title = f"New project: {p['name']} ({p.get('category') or 'DeFi'} on {chains}){x}"
            link = p.get("url") or f"https://defillama.com/protocol/{p.get('slug','')}"
            items.append(("🆕", "DefiLlama", title, link))
        if data:
            state["llama_since"] = max([since] + [p.get("listedAt") or 0 for p in data])
    except Exception as e:
        print("llama failed", e)
    return items

def trending(state):
    """CoinGecko trending coins: post only coins newly entering the list."""
    items = []
    try:
        data = json.loads(get("https://api.coingecko.com/api/v3/search/trending"))
        for c in data.get("coins", [])[:15]:
            i = c["item"]
            key = "cg:" + i["id"] + ":" + time.strftime("%Y-%m-%d")
            title = f"Trending: {i['name']} (${i['symbol'].upper()}) · rank #{i.get('market_cap_rank') or '?'}"
            items.append(("🔥", "CoinGecko", title, f"https://www.coingecko.com/en/coins/{i['id']}", key))
    except Exception as e:
        print("coingecko failed", e)
    return items

def send(text, preview_url=None):
    if DRY_RUN:
        print("DRY:", text.replace("\n", " | "))
        return
    params = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if preview_url:
        params["link_preview_options"] = json.dumps({"url": preview_url, "prefer_large_media": True})
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=body)
    urllib.request.urlopen(req, timeout=30).read()
    time.sleep(3)  # stay well under Telegram limits

def main():
    state = load_state()
    seen = set(state["seen"])
    first = state.pop("first_run", False)
    candidates = new_projects(state) + news_items() + trending(state)
    posted = 0
    for it in candidates:
        emoji, src, title, link = it[:4]
        key = it[4] if len(it) > 4 else link
        if key in seen:
            continue
        seen.add(key); state["seen"].append(key)
        if first or posted >= MAX_POSTS_PER_RUN:
            continue  # first run only records history, so the channel isn't flooded
        msg = (f"{emoji} <b>{html.escape(title)}</b>\n"
               f"<a href=\"{html.escape(link)}\">Read more</a> · {src}\n\n#SwanixAlpha · Stay Orange 🧡")
        try:
            send(msg); posted += 1
        except Exception as e:
            print("send failed", e)
    # ord.net Ordinals market digest, twice a day (9:00 and 21:00 IST)
    if os.environ.get("ORDNET", "1") == "1":
        slot, key = ordnet.due_slot(state)
        if slot and first:   # first run only records history: mark the slot done, don't post
            state["ordnet_done"].append(key); slot = None
        if slot:
            try:
                cols, sales, rate = ordnet.fetch_market()
                text, prev = ordnet.build_post(cols, sales, rate, slot)
                if text:
                    send(text, prev)
                state["ordnet_done"].append(key)
                state["ordnet_done"] = state["ordnet_done"][-60:]
            except Exception as e:
                print("ord.net digest failed", e)
    save_state(state)
    print(f"done: {posted} posted, {len(candidates)} candidates")

if __name__ == "__main__":
    main()
