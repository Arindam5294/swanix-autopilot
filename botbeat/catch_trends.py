#!/usr/bin/env python3
"""Trend catcher for Botbeat (AI + robotics channel only).
Keeps ONLY Google Trends topics about AI/robots (config must_match, minus skip_words),
finds a YouTube clip whose TITLE is also on-topic, and queues it tagged '#trend'.
Off-topic trends (sports, celebs, elections...) are ignored. Never crashes: exits 0."""
import json, os, re, sys, urllib.parse, urllib.request, pathlib
import feedparser

HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
QUEUE, USED = HERE / "queue.txt", HERE / "used.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
FEEDS = ["https://trends.google.com/trending/rss?geo=US", "https://trends.google.com/trending/rss?geo=IN"]

def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "replace")

def on_topic(text):
    t = (text or "").lower()
    hit = lambda w: re.search(r"(?<![a-z0-9])" + re.escape(w.strip()) + (r"(?![a-z0-9])" if len(w.strip()) <= 3 else ""), t)
    return (any(hit(w) for w in CFG.get("must_match", []) if w.strip() not in ("android", "atlas"))
            and not any(hit(w) for w in CFG.get("skip_words", []) + CFG.get("banned_words", [])))

def get_trends():
    out = []
    for f in FEEDS:
        try:
            for e in feedparser.parse(get(f)).entries:
                title = getattr(e, "title", "")
                news = " ".join(getattr(e, k, "") for k in ("ht_news_item_title", "summary"))
                if title: out.append((title, news))
        except Exception as ex:
            print(f"Feed failed {f}: {ex}")
    return out

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

def main():
    trends = get_trends()
    niche = [(t, n) for t, n in trends if on_topic(t) or on_topic(n)]
    print(f"{len(trends)} trends today, {len(niche)} about AI/robots: {[t for t, _ in niche][:10]}")
    if not niche:
        print("No AI/robot trend today. Nothing queued (Botbeat uses its own robot search instead).")
        return 0
    used = set(json.loads(USED.read_text())) if USED.exists() else set()
    queued = {l.split()[0] for l in QUEUE.read_text().splitlines() if l.strip()} if QUEUE.exists() else set()
    for trend, _ in niche[:10]:
        for vid, text in (search_api(trend) or search_scrape(trend)):
            link = "https://www.youtube.com/watch?v=" + vid
            if vid in used or link in used or link in queued: continue
            if not on_topic(text):
                print(f"  skip off-topic clip: {text[:80]!r}"); continue
            with open(QUEUE, "a") as f: f.write(link + " #trend\n")
            print(f"Queued for trend '{trend}': {link} | {text[:80]}")
            return 0
    print("Found AI trends but no on-topic clip. Nothing queued.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
