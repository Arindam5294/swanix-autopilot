#!/usr/bin/env python3
"""Trend catcher for Botbeat (AI + robotics channel only).
Keeps ONLY Google Trends topics about AI/robots (config must_match, minus skip_words),
finds a YouTube clip whose TITLE is also on-topic, and queues it tagged '#trend'.
MIX MODE: if no AI trend exists, queues ONE big general trend (sports, celebs, news...) at most every
config general_trend_hours (default 12), tagged '#trend #general topic=<trend>'. Botbeat then narrates
about THAT trend (not robots) and skips slideshow-style clips. Never crashes: exits 0."""
import json, os, re, sys, urllib.parse, urllib.request, pathlib
import feedparser, time

HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
QUEUE, USED = HERE / "queue.txt", HERE / "used.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
# GLOBAL: many countries (English-speaking first so narration fits). A topic trending in more countries ranks higher.
GEOS = ["US", "IN", "GB", "CA", "AU", "NG", "ZA", "PH", "SG", "IE", "NZ", "KE", "PK", "MY", "AE", "BR", "DE", "FR", "JP", "MX"]
FEEDS = ["https://trends.google.com/trending/rss?geo=" + g for g in GEOS]

def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "replace")

def on_topic(text):
    t = (text or "").lower()
    hit = lambda w: re.search(r"(?<![a-z0-9])" + re.escape(w.strip()) + (r"(?![a-z0-9])" if len(w.strip()) <= 3 else ""), t)
    return (any(hit(w) for w in CFG.get("must_match", []) if w.strip() not in ("android", "atlas"))
            and not any(hit(w) for w in CFG.get("skip_words", []) + CFG.get("banned_words", [])))

def get_trends():
    """Global trends: merge every country's feed; rank by how many countries trend it, then by traffic."""
    seen = {}
    for f in FEEDS:
        try:
            for rank, e in enumerate(feedparser.parse(get(f)).entries):
                title = getattr(e, "title", "").strip()
                if not title or not re.search(r"[a-z]", title.lower()): continue   # skip non-Latin titles (narration is English)
                news = " ".join(getattr(e, k, "") for k in ("ht_news_item_title", "summary"))
                traffic = int(re.sub(r"\D", "", getattr(e, "ht_approx_traffic", "") or "0") or 0)
                k = title.lower()
                d = seen.setdefault(k, {"title": title, "news": "", "geos": set(), "traffic": 0, "best": 99})
                d["geos"].add(f.rsplit("=", 1)[1]); d["traffic"] += traffic; d["best"] = min(d["best"], rank)
                if len(news) > len(d["news"]): d["news"] = news
        except Exception as ex:
            print(f"Feed failed {f}: {ex}")
    ranked = sorted(seen.values(), key=lambda d: (-len(d["geos"]), -d["traffic"], d["best"]))
    for d in ranked[:8]:
        print(f"  trend: {d['title']!r} in {len(d['geos'])} countries {sorted(d['geos'])}, ~{d['traffic']}+ searches")
    return [(d["title"], d["news"]) for d in ranked]

def search_api(q):
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key: return []
    p = urllib.parse.urlencode({"part": "snippet", "q": q, "type": "video", "videoDuration": "short",
                                "maxResults": 10, "order": "relevance", "safeSearch": "strict", "key": key})
    try:
        items = json.loads(get("https://www.googleapis.com/youtube/v3/search?" + p)).get("items", [])
        return [(i["id"]["videoId"], i["snippet"].get("title", "") + " " + i["snippet"].get("description", ""))
                for i in items if i.get("id", {}).get("videoId")]
    except Exception as ex:
        print(f"YouTube API search failed: {ex}"); return []

def search_scrape(q):
    try:
        html = get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q + " shorts"))
        ids = list(dict.fromkeys(re.findall(r"watch\?v=([\w-]{11})", html)))[:8]
    except Exception as ex:
        print(f"YouTube page search failed: {ex}"); return []
    out = []
    for vid in ids:
        try:
            t = json.loads(get("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + vid)).get("title", "")
            out.append((vid, t))
        except Exception:
            pass
    return out

STATE = HERE / "trend_state.json"
STOP = {"the", "and", "for", "with", "news", "live", "today", "2026", "vs", "from", "this", "that", "what", "who"}

def keywords(trend):
    return [w for w in re.findall(r"[a-z0-9]+", trend.lower()) if len(w) >= 3 and w not in STOP]

def relevant(trend, text):
    """Clip title must name the trend (all its key words, or at least 2 of them for long trends)."""
    t, ks = (text or "").lower(), keywords(trend)
    if not ks: return False
    hits = sum(1 for k in ks if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", t))
    return hits >= min(len(ks), 2)

def general_trend(trends, used, queued):
    """One big non-AI trend, with a real clip about it. Respects the 12h spacing."""
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    gap = CFG.get("general_trend_hours", 12) * 3600
    if not CFG.get("general_trends", True):
        print("General trends are off (config general_trends=false)."); return 0
    if time.time() - st.get("last_general", 0) < gap:
        print("A general trend was queued less than %dh ago; skipping." % (gap // 3600)); return 0
    if QUEUE.exists() and "#general" in QUEUE.read_text():
        print("A general trend is still waiting in the queue; skipping."); return 0
    bad = CFG.get("banned_words", []) + CFG.get("general_skip_words", [])
    for trend, _ in trends[:25]:
        if any(b in trend.lower() for b in bad): continue
        for vid, text in (search_api(trend) or search_scrape(trend)):
            link = "https://www.youtube.com/watch?v=" + vid
            if vid in used or link in queued: continue
            if not relevant(trend, text) or any(b in text.lower() for b in bad + ["slideshow", "photos", "images", "status", "lyrics", "edit", "fan made", "ai generated"]):
                continue
            tag = re.sub(r"\s+", "_", trend.strip())[:60]
            with open(QUEUE, "a") as f: f.write(f"{link} #trend #general topic={tag}\n")
            st["last_general"] = time.time(); st["last_topic"] = trend; STATE.write_text(json.dumps(st))
            print(f"Queued GENERAL trend '{trend}': {link} | {text[:80]}")
            return 0
    print("No general trend had a clean clip. Nothing queued.")
    return 0

def main():
    trends = get_trends()
    niche = [(t, n) for t, n in trends if on_topic(t) or on_topic(n)]
    print(f"{len(trends)} trends today, {len(niche)} about AI/robots: {[t for t, _ in niche][:10]}")
    idf = lambda u: (re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", u or "") or [None, u])[1]
    used = {idf(u) for u in (json.loads(USED.read_text()) if USED.exists() else [])}
    used |= {idf(l.split()[0]) for l in (QUEUE.read_text().splitlines() if QUEUE.exists() else []) if l.strip().startswith("http")}
    queued = {l.split()[0] for l in QUEUE.read_text().splitlines() if l.strip()} if QUEUE.exists() else set()
    if not niche:
        print("No AI/robot trend right now; trying one general trend (mix mode).")
        return general_trend(trends, used, queued)
    for trend, _ in niche[:10]:
        for vid, text in (search_api(trend) or search_scrape(trend)):
            link = "https://www.youtube.com/watch?v=" + vid
            if vid in used or link in used or link in queued: continue
            if not on_topic(text):
                print(f"  skip off-topic clip: {text[:80]!r}"); continue
            with open(QUEUE, "a") as f: f.write(link + " #trend\n")
            print(f"Queued for trend '{trend}': {link} | {text[:80]}")
            return 0
    print("Found AI trends but no on-topic clip; trying one general trend (mix mode).")
    return general_trend(trends, used, queued)

if __name__ == "__main__":
    sys.exit(main())
