#!/usr/bin/env python3
"""Pick the next source clip autonomously from EITHER region (US + India) — no manual link needed.
Order: botbeat/queue.txt (links you send the Telegram bot are queued there by the Studio workflow,
the only Telegram reader) -> per-region YouTube search (regionCode, regional trending/search) ->
yt-dlp regional trending scrape as a no-API-quota fallback.

Regions are read from config['regions'] (default US,IN). For each region we search with that
regionCode + regional relevanceLanguage, and merge the pools before applying the tiers.

Rules: viral only (min_views), unused, no crypto, on-topic. Prints JSON
{url,title,desc,views,source} or nothing."""
import json, os, sys, pathlib, subprocess, datetime, urllib.request, urllib.parse, re
HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
USED_F = HERE / "used.json"
USED = set(json.loads(USED_F.read_text())) if USED_F.exists() else set()
URL_RE = re.compile(r"https?://\S+")

YT_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

# Default regions if not in config: (code, label, relevanceLanguage, fallback trending region)
DEFAULT_REGIONS = [
    {"code": "US", "label": "United States", "lang": "en"},
    {"code": "IN", "label": "India", "lang": "en"},
]
REGIONS = CFG.get("regions", DEFAULT_REGIONS)

def is_youtube(url: str) -> bool:
    return any(d in url for d in YT_DOMAINS)

def bad(text):
    t = text.lower()
    return any(w in t for w in CFG["banned_words"])

def on_topic(text):
    t = " " + text.lower() + " "
    return any(w in t for w in CFG.get("must_match", [])) and not any(w in t for w in CFG.get("skip_words", []))

def minutes(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return (int(m[1] or 0) * 60 + int(m[2] or 0) + int(m[3] or 0) / 60) if m else 999

def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "botbeat/1.0"}), timeout=30) as r:
        return json.loads(r.read())

def meta(url):
    """yt-dlp metadata (works for X, YouTube, Instagram and more)."""
    cmd = ["yt-dlp", "-J", "--no-warnings", "--no-playlist", url]
    if os.path.exists("cookies.txt"): cmd[1:1] = ["--cookies", "cookies.txt"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode: return None
    j = json.loads(r.stdout)
    return {"url": url, "title": j.get("title") or "", "desc": (j.get("description") or "")[:1500],
            "views": j.get("view_count") or 0, "uploader": j.get("uploader") or ""}

def queue_links():
    q = HERE / "queue.txt"
    return [l.strip() for l in q.read_text().splitlines() if l.strip().startswith("http")] if q.exists() else []

def youtube_search_region(region):
    """Search the YouTube Data API for one region (regionCode + relevanceLanguage).
    Uses the configured search terms; newest-viral first. Returns list of candidate dicts."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("find_clip: YOUTUBE_API_KEY missing", file=sys.stderr); return []
    code = region.get("code", "US")
    lang = region.get("lang", "en")
    days = max(CFG["max_age_days"], CFG.get("pool_days", 180))
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in CFG["search_terms"]:
        p = urllib.parse.urlencode({"part": "id", "q": q, "type": "video", "order": "viewCount",
                                    "publishedAfter": since, "regionCode": code,
                                    "relevanceLanguage": lang, "safeSearch": "strict",
                                    "maxResults": 25, "key": key})
        try:
            ids += [i["id"]["videoId"] for i in get("https://www.googleapis.com/youtube/v3/search?" + p).get("items", [])]
        except Exception as e:
            print(f"find_clip: region {code} search '{q}' failed: {e}", file=sys.stderr)
    ids = list(dict.fromkeys(ids))
    out = []
    for i in range(0, len(ids), 50):
        p = urllib.parse.urlencode({"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i+50]), "key": key})
        try:
            items = get("https://www.googleapis.com/youtube/v3/videos?" + p).get("items", [])
        except Exception as e:
            print(f"find_clip: region {code} videos lookup failed: {e}", file=sys.stderr); continue
        for v in items:
            views = int(v["statistics"].get("viewCount", 0))
            if v["snippet"].get("liveBroadcastContent") != "none": continue
            if minutes(v["contentDetails"].get("duration")) > CFG["max_clip_minutes"]: continue
            out.append({"url": "https://www.youtube.com/watch?v=" + v["id"], "title": v["snippet"]["title"],
                        "desc": v["snippet"]["description"][:1500], "views": views,
                        "uploader": v["snippet"]["channelTitle"], "published": v["snippet"].get("publishedAt", ""),
                        "region": code})
    print(f"find_clip: region {code}: {len(ids)} search hits, {len(out)} usable videos", file=sys.stderr)
    return out

def youtube_trending_region(region):
    """Fallback with no API quota: scrape that region's YouTube trending feed via yt-dlp.
    Returns list of candidate dicts (views may be 0 for some feeds; we keep them but let
    the tier sort prioritise the API hits with views)."""
    code = region.get("code", "US")
    try:
        cmd = ["yt-dlp", "--flat-playlist", "-J", "--no-warnings",
               "https://www.youtube.com/feed/trending", "--playlist-items", "1-30"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode:
            print(f"find_clip: region {code} trending scrape failed", file=sys.stderr); return []
        j = json.loads(r.stdout)
        out = []
        for e in j.get("entries", []):
            vid = e.get("id")
            if not vid: continue
            out.append({"url": "https://www.youtube.com/watch?v=" + vid,
                        "title": e.get("title") or "", "desc": "",
                        "views": e.get("view_count") or 0,
                        "uploader": e.get("channel") or e.get("uploader") or "",
                        "published": e.get("timestamp") and datetime.datetime.utcfromtimestamp(e["timestamp"]).strftime("%Y-%m-%dT%H:%M:%SZ") or "",
                        "region": code, "source": "trending"})
        print(f"find_clip: region {code}: {len(out)} trending entries", file=sys.stderr)
        return out
    except Exception as e:
        print(f"find_clip: region {code} trending scrape error: {e}", file=sys.stderr)
        return []

def age_days(m):
    try:
        t = datetime.datetime.strptime(m["published"][:19], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        days = (datetime.datetime.now(datetime.timezone.utc) - t).days
        if m.get("source") == "trending" and not m["published"]:
            return 0  # trending = fresh by definition
        return days
    except Exception:
        return 9999

def main():
    manual = queue_links()                   # links you send the bot land here (studio/run.py queues them)
    for url in manual:                       # links you chose yourself skip the view bar
        if url in USED: continue
        if not is_youtube(url):
            print(f"find_clip: skipping non-YouTube URL: {url}", file=sys.stderr)
            continue
        m = meta(url) or {"url": url, "title": "", "desc": "", "views": 0, "uploader": ""}
        m["source"] = "manual"; print(json.dumps(m)); return

    # Multi-region discovery: merge pools from every configured region.
    pool = []
    api_hits = False
    for region in REGIONS:
        hits = youtube_search_region(region)
        if hits: api_hits = True
        pool += hits
        if not hits or len(hits) < 3:     # thin region pool -> top up with trending scrape
            pool += youtube_trending_region(region)

    # Dedupe by url, keep highest views version.
    by_url = {}
    for m in pool:
        cur = by_url.get(m["url"])
        if not cur or m["views"] > cur["views"]:
            by_url[m["url"]] = m
    pool = list(by_url.values())

    # Apply the same filters as before, across all regions.
    filtered = [m for m in pool
                if m["url"] not in USED and m["views"] >= CFG["min_views"]
                and not bad(m["title"] + m["desc"]) and on_topic(m["title"])]
    trusted = {c.lower() for c in CFG.get("trusted_channels", [])}
    is_trusted = lambda m: m["uploader"].lower() in trusted
    fresh = CFG["max_age_days"]
    tiers = [("trusted-fresh", lambda m: is_trusted(m) and age_days(m) <= fresh),
             ("trusted-older", is_trusted)]
    if CFG.get("allow_untrusted_fallback", True) or not CFG.get("trusted_only"):
        tiers.append(("any-fresh", lambda m: age_days(m) <= fresh))
    for name, ok in tiers:
        hits = sorted([m for m in filtered if ok(m)], key=lambda x: -x["views"])
        print(f"find_clip: tier {name}: {len(hits)} candidates", file=sys.stderr)
        if hits:
            m = hits[0]
            reg = m.get("region", "?")
            m["source"] = f"youtube-search:{name}:region-{reg}"
            del m["region"]
            print(json.dumps(m)); return
    print("find_clip: nothing new passed the filters (US + India)", file=sys.stderr)

if __name__ == "__main__":
    main()
