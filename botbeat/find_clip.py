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
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key: return []
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=CFG["max_age_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in CFG["search_terms"]:
        p = urllib.parse.urlencode({"part": "id", "q": q, "type": "video", "order": "viewCount",
                                    "publishedAfter": since, "relevanceLanguage": "en", "safeSearch": "strict", "maxResults": 10, "key": key})
        ids += [i["id"]["videoId"] for i in get("https://www.googleapis.com/youtube/v3/search?" + p).get("items", [])]
    out = []
    for i in range(0, len(ids), 50):
        p = urllib.parse.urlencode({"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i+50]), "key": key})
        for v in get("https://www.googleapis.com/youtube/v3/videos?" + p).get("items", []):
            views = int(v["statistics"].get("viewCount", 0))
            if v["snippet"].get("liveBroadcastContent") != "none": continue
            if minutes(v["contentDetails"].get("duration")) > CFG["max_clip_minutes"]: continue
            out.append({"url": "https://www.youtube.com/watch?v=" + v["id"], "title": v["snippet"]["title"],
                        "desc": v["snippet"]["description"][:1500], "views": views,
                        "uploader": v["snippet"]["channelTitle"]})
    trusted = {c.lower() for c in CFG.get("trusted_channels", [])}
    seen, uniq = set(), []
    for m in out:
        if m["url"] not in seen: seen.add(m["url"]); uniq.append(m)
    uniq.sort(key=lambda x: (x["uploader"].lower() not in trusted, -x["views"]))  # real sources first
    if CFG.get("trusted_only"): uniq = [m for m in uniq if m["uploader"].lower() in trusted]
    return uniq

def main():
    manual = queue_links()                   # links you send the bot land here (studio/run.py queues them)
    for url in manual:                       # links you chose yourself skip the view bar
        if url in USED: continue
        m = meta(url) or {"url": url, "title": "", "desc": "", "views": 0, "uploader": ""}
        m["source"] = "manual"; print(json.dumps(m)); return
    for m in youtube_search():
        if m["url"] in USED or m["views"] < CFG["min_views"] or bad(m["title"] + m["desc"]) or not on_topic(m["title"]): continue
        m["source"] = "youtube-search"; print(json.dumps(m)); return

if __name__ == "__main__":
    main()
