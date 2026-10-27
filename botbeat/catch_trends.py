#!/usr/bin/env python3
"""Daily trend catcher: grabs today's top Google Trends topic, finds a matching YouTube Short,
and queues it in botbeat/queue.txt. The main Botbeat workflow picks it up from the queue and
makes + uploads the Short (it has ffmpeg, voice and all the YouTube secrets).
Never crashes the run: if no trend or no clip is found it just logs and exits 0."""
import json, os, re, sys, urllib.parse, urllib.request, pathlib
import feedparser

HERE = pathlib.Path(__file__).resolve().parent
QUEUE = HERE / "queue.txt"
USED = HERE / "used.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
# Google retired /trends/trendingsearches/daily/rss in 2024; /trending/rss is the live feed.
FEEDS = [
    "https://trends.google.com/trending/rss?geo=US",
    "https://trends.google.com/trending/rss?geo=IN",
]

def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "replace")

def get_trends():
    out = []
    for f in FEEDS:
        try:
            out += [e.title for e in feedparser.parse(get(f)).entries if getattr(e, "title", "")]
        except Exception as e:
            print(f"Feed failed {f}: {e}")
    return out

def search_api(q):
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return []
    p = urllib.parse.urlencode({"part": "id", "q": q, "type": "video", "videoDuration": "short",
                                "maxResults": 5, "order": "relevance", "key": key})
    try:
        data = json.loads(get("https://www.googleapis.com/youtube/v3/search?" + p))
        return [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
    except Exception as e:
        print(f"YouTube API search failed: {e}")
        return []

def search_scrape(q):
    try:
        html = get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q + " shorts"))
        return list(dict.fromkeys(re.findall(r"watch\?v=([\w-]{11})", html)))
    except Exception as e:
        print(f"YouTube page search failed: {e}")
        return []

def main():
    print("Catching today's top trends...")
    trends = get_trends()
    if not trends:
        print("No trends returned today. Skipping (not an error).")
        return 0
    used = set(json.loads(USED.read_text())) if USED.exists() else set()
    queued = set(QUEUE.read_text().split()) if QUEUE.exists() else set()
    for trend in trends[:10]:
        print(f"Trend: {trend}")
        for vid in (search_api(trend) or search_scrape(trend)):
            link = "https://www.youtube.com/watch?v=" + vid
            if vid in used or link in used or link in queued:
                continue
            with open(QUEUE, "a") as f:
                f.write(link + "\n")
            print(f"Queued viral clip: {link}")
            return 0
    print("No new clip found for today's trends. Skipping (not an error).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
