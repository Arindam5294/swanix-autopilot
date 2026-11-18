#!/usr/bin/env python3
"""Playbook steps 2-3, weekly:
1) Find active English YouTube channels posting GTA 6 videos (YouTube Data API, ~700 quota units a run).
2) Pull each channel's recent uploads, keep the videos that beat that channel's median views by OUTLIER_X or more
   -> outliers.csv.  channels.json keeps the channel list; add ids to "blocked" to drop one.
3) AI studies the outlier titles/thumbnails/lengths and writes THUMBNAILS.md (patterns every title,
   thumbnail and episode follows). Telegram tells you what changed since last week.
Key: GTA_YOUTUBE_API_KEY (from the GTA Cloud project) or YOUTUBE_API_KEY."""
import csv, json, os, pathlib, statistics, sys, urllib.parse, urllib.request, datetime, re
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
KEY = os.environ.get("GTA_YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_API_KEY")
CH_F, CSV_F, PAT_F = HERE / "channels.json", HERE / "outliers.csv", HERE / "THUMBNAILS.md"
OUTLIER_X, MIN_VIEWS, MAX_CH = 3.0, 30000, 40
QUERIES = ["GTA 6", "GTA 6 gameplay", "GTA 6 secrets", "GTA 6 map", "GTA 6 tips"]

def api(path, **q):
    q["key"] = KEY
    return json.loads(urllib.request.urlopen("https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(q), timeout=40).read())

def iso_secs(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    return sum(int(x or 0) * k for x, k in zip(m.groups(), (86400, 3600, 60, 1))) if m else 0

def find_channels(known):
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = dict(known)
    for q in QUERIES:
        try:
            for it in api("search", part="snippet", q=q, type="video", order="viewCount", publishedAfter=since,
                          relevanceLanguage="en", maxResults=50)["items"]:
                seen.setdefault(it["snippet"]["channelId"], it["snippet"]["channelTitle"])
        except Exception as e:
            print("outliers: search failed:", e, file=sys.stderr)
    return seen

def uploads(cids):
    rows = []
    for i in range(0, len(cids), 50):
        for c in api("channels", part="contentDetails,statistics,snippet", id=",".join(cids[i:i+50]))["items"]:
            rows.append((c["id"], c["snippet"]["title"], c["contentDetails"]["relatedPlaylists"]["uploads"],
                         int(c["statistics"].get("subscriberCount", 0) or 0)))
    return rows

def main():
    if not KEY: print("outliers: no GTA_YOUTUBE_API_KEY/YOUTUBE_API_KEY"); return
    chans = json.loads(CH_F.read_text()) if CH_F.exists() else {"channels": {}, "blocked": []}
    found = find_channels(chans["channels"])
    found = {k: v for k, v in found.items() if k not in chans["blocked"]}
    new_ch = [v for k, v in found.items() if k not in chans["channels"]]
    chans["channels"] = found
    out = []
    for cid, name, pl, subs in uploads(list(found)[:MAX_CH * 2])[:MAX_CH]:
        try:
            ids = [i["contentDetails"]["videoId"] for i in api("playlistItems", part="contentDetails", playlistId=pl, maxResults=30)["items"]]
            vids = api("videos", part="statistics,snippet,contentDetails", id=",".join(ids))["items"]
        except Exception as e:
            print("outliers:", name, e, file=sys.stderr); continue
        views = [int(v["statistics"].get("viewCount", 0)) for v in vids]
        if len(views) < 5: continue
        med = statistics.median(views) or 1
        for v, n in zip(vids, views):
            t = v["snippet"]["title"]
            if n >= MIN_VIEWS and n >= OUTLIER_X * med and re.search(r"gta ?(6|vi)|vice city|leonida", t, re.I):
                secs = iso_secs(v["contentDetails"]["duration"])
                out.append({"found": datetime.date.today().isoformat(), "channel": name, "subs": subs, "title": t, "views": n,
                            "channel_median": int(med), "x_median": round(n / med, 1), "seconds": secs,
                            "format": "short" if secs <= 60 else "long", "published": v["snippet"]["publishedAt"][:10],
                            "url": f"https://youtu.be/{v['id']}", "thumbnail": v["snippet"]["thumbnails"].get("high", {}).get("url", "")})
    out.sort(key=lambda r: -r["x_median"])
    old = set()
    if CSV_F.exists():
        old = {r["url"] for r in csv.DictReader(CSV_F.open())}
    if out:
        with CSV_F.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    CH_F.write_text(json.dumps(chans, indent=1))
    fresh = [r for r in out if r["url"] not in old]
    write_patterns(out)
    msg = (f"📈 GTA weekly outliers: {len(out)} videos beat their channel's usual views by {OUTLIER_X:.0f}x+ "
           f"({len(fresh)} new this week, {len(found)} channels tracked).\n\n"
           + "\n".join(f"{r['x_median']}x · {r['views']:,} views · {r['channel']}: {r['title'][:70]}\n{r['url']}" for r in fresh[:8])
           + (f"\n\nNew channels: {', '.join(new_ch[:15])}" if new_ch else "")
           + "\n\nTo drop a channel, add its id to channels.json → blocked.")
    print(msg)
    try:
        from notify import telegram; telegram(msg)
    except Exception: pass

def write_patterns(rows):
    if not rows: return
    from ai import ask
    longs = [r["seconds"] for r in rows if r["format"] == "long"]; shorts = [r["seconds"] for r in rows if r["format"] == "short"]
    data = "\n".join(f"{r['x_median']}x | {r['format']} {r['seconds']}s | {r['title']}" for r in rows[:60])
    txt = ask(f"""These GTA 6 YouTube videos beat their channel's median views by a lot (ratio | format length | title):
{data}
Write a SHORT markdown playbook called THUMBNAILS.md for a GTA 6 clips channel:
## Title patterns (6-8 fill-in-the-blank templates that repeat above, with one real example each)
## Thumbnail rules (what the big 1-3 words say, colors, faces/arrows/circles, what's in the picture)
## Words that win (10-15 words/phrases)
## Lengths (shorts median {int(statistics.median(shorts)) if shorts else 'n/a'}s, long median {int(statistics.median(longs)) if longs else 'n/a'}s - say what length to copy)
## Avoid (what flopped or looks spammy)
Only claim patterns you can see in the data.""", min_len=300)
    PAT_F.write_text(txt.strip() + f"\n\n_Updated {datetime.date.today()} from {len(rows)} outliers._\n")

if __name__ == "__main__":
    main()
