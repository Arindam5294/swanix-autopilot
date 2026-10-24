#!/usr/bin/env python3
"""Pick the next source clip. Order: botbeat/queue.txt (links you send the Telegram bot are queued there by
the Studio workflow, the only Telegram reader) -> YouTube search.
Rules: viral only (min_views), unused, no crypto. Prints JSON {url,title,desc,views,source} or nothing."""
import json, os, sys, pathlib, subprocess, datetime, urllib.request, urllib.parse, re
HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
USED_F = HERE / "used.json"
USED = set(json.loads(USED_F.read_text())) if USED_F.exists() else set()
URL_RE = re.compile(r"https?://\S+")

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

def youtube_search():
    """One wide search (pool_days, default 180), newest-viral first; tiers are applied later in main()."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("find_clip: YOUTUBE_API_KEY missing", file=sys.stderr); return []
    days = max(CFG["max_age_days"], CFG.get("pool_days", 180))
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in CFG["search_terms"]:
        p = urllib.parse.urlencode({"part": "id", "q": q, "type": "video", "order": "viewCount",
                                    "publishedAfter": since, "relevanceLanguage": "en", "safeSearch": "strict", "maxResults": 25, "key": key})
        try:
            ids += [i["id"]["videoId"] for i in get("https://www.googleapis.com/youtube/v3/search?" + p).get("items", [])]
        except Exception as e:
            print(f"find_clip: search '{q}' failed: {e}", file=sys.stderr)
    ids = list(dict.fromkeys(ids))
    out = []
    for i in range(0, len(ids), 50):
        p = urllib.parse.urlencode({"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i+50]), "key": key})
        try:
            items = get("https://www.googleapis.com/youtube/v3/videos?" + p).get("items", [])
        except Exception as e:
            print(f"find_clip: videos lookup failed: {e}", file=sys.stderr); continue
        for v in items:
            views = int(v["statistics"].get("viewCount", 0))
            if v["snippet"].get("liveBroadcastContent") != "none": continue
            if minutes(v["contentDetails"].get("duration")) > CFG["max_clip_minutes"]: continue
            out.append({"url": "https://www.youtube.com/watch?v=" + v["id"], "title": v["snippet"]["title"],
                        "desc": v["snippet"]["description"][:1500], "views": views,
                        "uploader": v["snippet"]["channelTitle"], "published": v["snippet"].get("publishedAt", "")})
    print(f"find_clip: {len(ids)} search hits, {len(out)} usable videos", file=sys.stderr)
    return out

def age_days(m):
    try:
        t = datetime.datetime.strptime(m["published"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - t).days
    except Exception:
        return 9999

def main():
    manual = queue_links()                   # links you send the bot land here (studio/run.py queues them)
    for url in manual:                       # links you chose yourself skip the view bar
        if url in USED: continue
        m = meta(url) or {"url": url, "title": "", "desc": "", "views": 0, "uploader": ""}
        m["source"] = "manual"; print(json.dumps(m)); return
    pool = [m for m in youtube_search()
            if m["url"] not in USED and m["views"] >= CFG["min_views"] and not bad(m["title"] + m["desc"]) and on_topic(m["title"])]
    trusted = {c.lower() for c in CFG.get("trusted_channels", [])}
    is_trusted = lambda m: m["uploader"].lower() in trusted
    fresh = CFG["max_age_days"]
    # Tiers: trusted+fresh -> trusted+older (pool_days) -> any channel+fresh (only if allow_untrusted_fallback).
    # trusted_only alone dried the pool up after 4 clips (Oct 20 2026), so the run silently made nothing.
    tiers = [("trusted-fresh", lambda m: is_trusted(m) and age_days(m) <= fresh),
             ("trusted-older", is_trusted)]
    if CFG.get("allow_untrusted_fallback", True) or not CFG.get("trusted_only"):
        tiers.append(("any-fresh", lambda m: age_days(m) <= fresh))
    for name, ok in tiers:
        hits = sorted([m for m in pool if ok(m)], key=lambda x: -x["views"])
        print(f"find_clip: tier {name}: {len(hits)} candidates", file=sys.stderr)
        if hits:
            m = hits[0]; m["source"] = "youtube-search:" + name; print(json.dumps(m)); return
    print("find_clip: nothing new passed the filters", file=sys.stderr)

if __name__ == "__main__":
    main()
