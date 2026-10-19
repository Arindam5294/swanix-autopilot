"""ord.net Ordinals market digest for Swanix.
Reads ord.net's public page data (no API key) and builds an eye-catching
Telegram post: floors of top collections, top movers, hottest sales, discounts.
"""
import json, html, random, urllib.request, datetime

UA = {"User-Agent": "Mozilla/5.0 (SwanixAlphaBot)"}
SOURCE = "https://ord.net/__data.json"
SITE = "https://ord.net"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def _get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def _unflatten(arr):
    """Decode SvelteKit 'devalue' flattened data."""
    cache = {}
    def h(i):
        if not isinstance(i, int) or i < 0:
            return None
        if i in cache:
            return cache[i]
        v = arr[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in ("Date", "Set", "Map", "BigInt", "RegExp"):
                cache[i] = v[1] if len(v) > 1 else None
                return cache[i]
            out = []; cache[i] = out
            out.extend(h(x) for x in v)
            return out
        if isinstance(v, dict):
            out = {}; cache[i] = out
            for k, x in v.items():
                out[k] = h(x)
            return out
        cache[i] = v
        return v
    return h(0)

def fetch_market():
    d = _get_json(SOURCE)
    page = {}
    for n in d.get("nodes", []):
        if n and n.get("type") == "data":
            x = _unflatten(n["data"])
            if isinstance(x, dict) and "collections" in x:
                page = x
    cols = {}
    for key in ("collections", "popularCollections"):
        for c in page.get(key) or []:
            if c and c.get("slug") and c.get("floor"):
                cols[c["slug"]] = c
    sales = [s for s in (page.get("recentSales") or []) if s and s.get("price")]
    btc_usd = None
    for s in sales:
        if s.get("priceUsd") and s.get("price"):
            btc_usd = s["priceUsd"] / s["price"]; break
    return list(cols.values()), sales, btc_usd

def _btc(v):
    if v is None:
        return "–"
    return f"{v:.4f}".rstrip("0").rstrip(".") + " ₿" if v >= 0.001 else f"{v * 1e8:,.0f} sats"

def _usd(v, rate):
    return f" (${v * rate:,.0f})" if rate and v else ""

def _pct(v):
    return f"{'+' if v > 0 else ''}{v:.1f}%"

def _link(c):
    return f'<a href="{SITE}{html.escape(c.get("href") or "/collection/" + c["slug"])}">{html.escape(c["name"])}</a>'

def build_post(cols, sales, rate, slot="morning"):
    if not cols:
        return None, None
    by_vol = sorted(cols, key=lambda c: -(c.get("volume7d") or c.get("volume") or 0))
    top = by_vol[:6]
    active = [c for c in cols if (c.get("volume7d") or 0) > 0 or (c.get("sales24h") or 0) > 0]
    movers = sorted([c for c in active if c.get("change24h")], key=lambda c: -abs(c["change24h"]))
    pumps = [c for c in movers if c["change24h"] > 0][:3]
    dumps = [c for c in movers if c["change24h"] < 0][:3]

    # Discounts: floor well below its 7d/30d level, or floor sitting near the best offer.
    disc = []
    for c in active:
        ch = min(c.get("change7d") or 0, c.get("change30d") or 0)
        if ch <= -10:
            disc.append((ch, c, f"floor {_pct(ch)} vs {'7d' if (c.get('change7d') or 0) <= (c.get('change30d') or 0) else '30d'}"))
        elif c.get("topOffer") and c["floor"] and c["topOffer"] / c["floor"] >= 0.9:
            gap = (1 - c["topOffer"] / c["floor"]) * 100
            disc.append((-50 + gap, c, f"floor just {gap:.0f}% above top bid"))
    disc.sort(key=lambda x: x[0])
    disc = disc[:3]

    hot = []
    for c in cols:
        s = c.get("topSale24h")
        if s and s.get("price"):
            hot.append((s["price"], c, s))
    hot.sort(key=lambda x: -x[0])
    hot = hot[:3]
    busiest = sorted(cols, key=lambda c: -(c.get("sales24h") or 0))[:1]

    # Headline: pick from what the data says
    lead = pumps[0] if pumps else None
    bleed = dumps[0] if dumps else None
    options = []
    if lead and lead["change24h"] >= 15:
        options += [f"🚀 {lead['name']} RIPS {_pct(lead['change24h'])}: Ordinals are waking up",
                    f"🔥 {lead['name']} floor on fire, {_pct(lead['change24h'])} in 24h"]
    if bleed and bleed["change24h"] <= -10:
        options += [f"🩸 Blood on the blocks: {bleed['name']} {_pct(bleed['change24h'])}. Dip or trap?",
                    f"🧨 {bleed['name']} floor cracks {_pct(bleed['change24h'])}: bargain hunters circle"]
    if hot:
        options += [f"🐋 Whale alert: a {hot[0][1]['name']} just went for {_btc(hot[0][0])}"]
    if disc:
        options += [f"💸 Discount season: {disc[0][1]['name']} {disc[0][2]}"]
    if not options:
        options = ["🧡 Ordinals Market Pulse: calm before the storm?"]
    headline = random.choice(options)

    now = datetime.datetime.now(IST)
    tag = "☀️ Morning Alpha" if slot == "morning" else "🌙 Night Watch"
    L = [f"<b>{html.escape(headline)}</b>", f"<i>{tag} · Ordinals floor report · {now:%d %b %Y}</i>", ""]

    L.append("👑 <b>Top collections · floor</b>")
    for c in top:
        ch = c.get("change24h") or 0
        arrow = "🟢" if ch > 0 else "🔴" if ch < 0 else "⚪"
        L.append(f"{arrow} {_link(c)}: {_btc(c['floor'])}{_usd(c['floor'], rate)} · {_pct(ch)}")
    if pumps or dumps:
        L += ["", "📈 <b>Top movers (24h)</b>"]
        for c in pumps:
            L.append(f"🚀 {_link(c)} {_pct(c['change24h'])} → {_btc(c['floor'])}")
        for c in dumps:
            L.append(f"📉 {_link(c)} {_pct(c['change24h'])} → {_btc(c['floor'])}")
    if hot:
        L += ["", "🐋 <b>Biggest sales (24h)</b>"]
        for price, c, s in hot:
            title = html.escape(s.get("title") or c["name"])
            L.append(f"💰 <a href=\"{SITE}{html.escape(s.get('href', ''))}\">{title}</a>: {_btc(price)}{_usd(price, rate)}")
        if busiest and (busiest[0].get("sales24h") or 0) > 1:
            b = busiest[0]
            L.append(f"⚡ Most traded: {_link(b)} with {b['sales24h']} sales")
    if disc:
        L += ["", "💸 <b>On discount</b>"]
        for _, c, why in disc:
            L.append(f"🏷️ {_link(c)}: {_btc(c['floor'])}, {why}")
    L += ["", "Not financial advice. DYOR.", "#Ordinals #SwanixAlpha · Stay Orange 🧡"]
    text = "\n".join(L)
    preview = f"{SITE}{(lead or top[0]).get('href') or ''}"
    return text, preview

def due_slot(state, hours=(9, 21)):
    """Return 'morning'/'evening' when a digest is due (IST), else None."""
    now = datetime.datetime.now(IST)
    done = state.setdefault("ordnet_done", [])
    for h, name in zip(hours, ("morning", "evening")):
        key = f"{now:%Y-%m-%d}-{name}"
        if now.hour >= h and key not in done:
            # don't fire a stale morning post late at night
            if name == "morning" and now.hour >= hours[1]:
                done.append(key); continue
            return name, key
    return None, None

if __name__ == "__main__":
    cols, sales, rate = fetch_market()
    text, prev = build_post(cols, sales, rate)
    print(text); print("preview:", prev)
