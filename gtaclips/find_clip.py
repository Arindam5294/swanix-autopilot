#!/usr/bin/env python3
"""Pick the next source clip autonomously from EITHER region (US + India) — no manual link needed.
Order: gtaclips/queue.txt (links you send the Telegram bot are queued there by the Studio workflow,
the only Telegram reader) -> per-region YouTube search via the Data API (regionCode + regional
relevanceLanguage) -> per-region yt-dlp search as a no-API-quota fallback.

Regions are read from config['regions'] (default US,IN). Pool from all regions is merged before
the tiers are applied. Prints JSON {url,title,desc,views,source,region} or nothing."""
import json, os, sys, pathlib, subprocess, datetime, urllib.request, urllib.parse, re
HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
USED_F = HERE / "used.json"
URL_RE = re.compile(r"https?://\S+")
# SKIP_YOUTUBE=1: YouTube blocked the runner this run, so only Twitch/Kick clips are picked.
NO_YT = os.environ.get("SKIP_YOUTUBE") == "1"
EXCLUDE = {u for u in os.environ.get("EXCLUDE_URLS", "").split() if u}
ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")
def vid_id(url):
    if url and ("twitch.tv/" in url or "kick.com/" in url) and "/clips/" not in url and "clip" not in url.split("/")[2]:
        return url.rstrip("/") + "@" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H")   # a live channel can be clipped again next hour
    """Same video in any URL form (watch?v=, youtu.be, shorts/, ?si=...) -> one 11-char id."""
    m = ID_RE.search(url or "")
    if m and "youtube.com/live/" in (url or ""):   # a YouTube LIVE records NEW footage each run: one Short per 20 min of the stream
        n = datetime.datetime.now(datetime.timezone.utc); return m.group(1) + "@" + n.strftime("%Y%m%d%H") + str(n.minute // 20)
    return m.group(1) if m else (url or "").split("?")[0].rstrip("/")
def load_used():
    """used.json from this checkout PLUS the newest one on GitHub (a re-run of an old run checks out
    an old used.json, which is how the same clip got posted again and again)."""
    urls = json.loads(USED_F.read_text()) if USED_F.exists() else []
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "main"], cwd=HERE, timeout=60, capture_output=True)
        r = subprocess.run(["git", "show", "FETCH_HEAD:gtaclips/used.json"], cwd=HERE, capture_output=True, text=True, timeout=30)
        if r.returncode == 0: urls += json.loads(r.stdout)
    except Exception as e:
        print(f"find_clip: couldn't read newest used.json ({e})", file=sys.stderr)
    def _key(u):
        if "@" in u: return u                 # already a live slot key, e.g. ID@YYYYMMDDHHn
        k = vid_id(u)
        return None if "@" in k else k        # bare live link: never a permanent block (each slot is new footage)
    return {k for k in map(_key, urls) if k}
USED = load_used()

YT_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

# Default regions if not in config: (code, label, relevanceLanguage)
DEFAULT_REGIONS = [
    {"code": "US", "label": "United States", "lang": "en"},
    {"code": "IN", "label": "India", "lang": "en"},
]
REGIONS = CFG.get("regions", DEFAULT_REGIONS)
# Hindi edition: look for Indian/Hindi GTA 6 streams first (YouTube India), still allow English Twitch clips as backup.
if os.environ.get("GTA_LANG", "").strip().lower() == "hi":
    REGIONS = [{"code": "IN", "label": "India", "lang": "hi"}]
    CFG["stream_terms"] = CFG.get("hindi_stream_terms") or ["GTA 6 live hindi", "GTA 6 hindi gameplay", "GTA 6 live india"]
    CFG["twitch_languages"] = list(dict.fromkeys(["hi"] + CFG.get("twitch_languages", ["en"])))

# Track if the Data API is quota'd/rate-limited so we stop hammering it for the run.
API_QUOTA_HIT = False

def is_youtube(url: str) -> bool:
    return any(d in url for d in YT_DOMAINS)

def bad(text):
    t = text.lower()
    return any(w in t for w in CFG["banned_words"])

def on_topic(text):
    t = (text or "").lower()
    hit = lambda w: re.search(r"(?<![a-z0-9])" + re.escape(w.strip()) + (r"(?![a-z0-9])" if len(w.strip()) <= 3 else ""), t)
    return (any(hit(w) for w in CFG.get("must_match", []) if w.strip() not in ("android", "atlas"))
            and not any(hit(w) for w in CFG.get("skip_words", []) + CFG.get("banned_words", [])))

def minutes(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return (int(m[1] or 0) * 60 + int(m[2] or 0) + int(m[3] or 0) / 60) if m else 999

def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gtaclips/1.0"}), timeout=30) as r:
        return json.loads(r.read())

def api_meta(url):
    """YouTube Data API metadata: never bot-blocked on GitHub (uses YOUTUBE_API_KEY, 1 quota unit)."""
    key, vid = os.environ.get("YOUTUBE_API_KEY"), vid_id(url).split("@")[0]   # /live/ ids carry an @hour tag
    if not key or not is_youtube(url) or len(vid) != 11: return None
    try:
        p = urllib.parse.urlencode({"part": "snippet,statistics,liveStreamingDetails,contentDetails", "id": vid, "key": key})
        it = (get("https://www.googleapis.com/youtube/v3/videos?" + p).get("items") or [None])[0]
    except Exception as e:
        print(f"find_clip: API lookup failed for {url}: {e}", file=sys.stderr); return None
    if not it: return None
    sn, st, ls = it["snippet"], it.get("statistics", {}), it.get("liveStreamingDetails", {})
    live = sn.get("liveBroadcastContent") == "live"
    print(f"find_clip: got metadata for {url} from the YouTube API (yt-dlp was blocked)", file=sys.stderr)
    return {"url": f"https://www.youtube.com/{'live/' if live else 'watch?v='}{vid}", "title": sn.get("title", ""), "desc": (sn.get("description") or "")[:1500],
            "views": int(st.get("viewCount", 0) or 0) or int(ls.get("concurrentViewers", 0) or 0) * 20, "uploader": sn.get("channelTitle", ""),
            "kind": "live" if live else ("stream" if ls else "video"), "duration": 0}

def meta(url):
    """yt-dlp metadata (works for X, YouTube, Instagram and more); YouTube API when GitHub is blocked."""
    cmd = ["yt-dlp", "-J", "--no-warnings", "--no-playlist", url]
    if os.path.exists("cookies.txt"): cmd[1:1] = ["--cookies", "cookies.txt"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    for px in ([os.environ.get("YTDLP_HOME_PROXY")] + [os.environ.get("YTDLP_PROXY")]):   # YouTube blocked GitHub? retry via proxy/WARP
        if r.returncode and px:
            print(f"find_clip: lookup blocked, retrying via proxy", file=sys.stderr)
            r = subprocess.run(cmd[:1] + ["--proxy", px] + cmd[1:], capture_output=True, text=True, timeout=120)
    if r.returncode:
        print("find_clip: yt-dlp said:", (r.stderr or "")[-400:], file=sys.stderr)
        return api_meta(url)
    j = json.loads(r.stdout)
    if j.get("live_status") == "is_live" and is_youtube(url) and len(vid_id(url).split("@")[0]) == 11:
        url = "https://www.youtube.com/live/" + vid_id(url).split("@")[0]   # keep the live form so the next run can clip it again
    return {"url": url, "title": j.get("title") or "", "desc": (j.get("description") or "")[:1500],
            "views": j.get("view_count") or j.get("concurrent_view_count") or 0, "uploader": j.get("uploader") or "",
            # a pasted YouTube LIVE link must be RECORDED (7 min), never downloaded (a live download never ends)
            "kind": "live" if j.get("live_status") == "is_live" else ("stream" if j.get("live_status") == "was_live" else "video"),
            "duration": j.get("duration") or 0}

def queue_links():
    q = HERE / "queue.txt"
    return [l.strip() for l in q.read_text().splitlines() if l.strip().startswith("http")] if q.exists() else []

def split_q(line):
    parts = line.split()
    return parts[0], "#trend" in parts[1:]

def q_topic(line):
    """'url #trend #general topic=Virat_Kohli' -> 'Virat Kohli' (None for AI/robot or manual links)."""
    parts = line.split()
    if "#general" not in parts: return None
    t = next((p[6:] for p in parts if p.startswith("topic=")), "")
    return t.replace("_", " ") or None

def youtube_search_region(region):
    """Search the YouTube Data API for one region (regionCode + relevanceLanguage).
    Returns list of candidate dicts with 'region' tag."""
    global API_QUOTA_HIT
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("find_clip: YOUTUBE_API_KEY missing, using yt-dlp fallback", file=sys.stderr); return []
    if API_QUOTA_HIT:
        print("find_clip: API quota eaten this run, using yt-dlp fallback", file=sys.stderr); return []
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
            if "429" in str(e):
                API_QUOTA_HIT = True
                print(f"find_clip: region {code} search hit HTTP 429 (quota) -> switching to fallback", file=sys.stderr)
                return []
            print(f"find_clip: region {code} search '{q}' failed: {e}", file=sys.stderr)
    # live streams happening right now + finished streams (replays)
    for q in CFG.get("stream_terms", []):
        for ev in ("live", "completed"):
            p = urllib.parse.urlencode({"part": "id", "q": q, "type": "video", "eventType": ev,
                                        "order": "viewCount" if ev == "completed" else "relevance",
                                        "regionCode": code, "relevanceLanguage": lang, "maxResults": 15, "key": key,
                                        **({"publishedAfter": since} if ev == "completed" else {})})
            try:
                ids += [i["id"]["videoId"] for i in get("https://www.googleapis.com/youtube/v3/search?" + p).get("items", [])]
            except Exception as e:
                print(f"find_clip: stream search '{q}' ({ev}) failed: {e}", file=sys.stderr)
    ids = list(dict.fromkeys(ids))
    out = []
    for i in range(0, len(ids), 50):
        p = urllib.parse.urlencode({"part": "snippet,statistics,contentDetails,liveStreamingDetails", "id": ",".join(ids[i:i+50]), "key": key})
        try:
            items = get("https://www.googleapis.com/youtube/v3/videos?" + p).get("items", [])
        except Exception as e:
            print(f"find_clip: region {code} videos lookup failed: {e}", file=sys.stderr); continue
        for v in items:
            views = int(v["statistics"].get("viewCount", 0))
            live = v["snippet"].get("liveBroadcastContent")
            if live == "upcoming": continue
            was_stream = "liveStreamingDetails" in v
            if live == "live":
                kind = "live"; views = max(views, int(v.get("liveStreamingDetails", {}).get("concurrentViewers", 0)) * 20)
            elif was_stream or minutes(v["contentDetails"].get("duration")) > CFG["max_clip_minutes"]:
                kind = "stream"          # long VOD / finished stream: clip its most viral moment
            else:
                kind = "video"
            out.append({"url": "https://www.youtube.com/watch?v=" + v["id"], "title": v["snippet"]["title"],
                        "desc": v["snippet"]["description"][:1500], "views": views, "kind": kind,
                        "uploader": v["snippet"]["channelTitle"], "published": v["snippet"].get("publishedAt", ""),
                        "region": code})
    print(f"find_clip: API region {code}: {len(ids)} search hits, {len(out)} usable videos", file=sys.stderr)
    return out

def youtube_dlp_search_region(region):
    """No-API-quota fallback: yt-dlp flat-playlist search scoped to a region.
    flat-playlist returns ids+titles reliably even from GitHub-hosted runners (full metadata
    lookups get bot-blocked there). Views are unknown (0); the last-resort tier therefore does
    NOT gate on views, so a clip still gets picked and posted every run.
    Returns candidate dicts tagged 'region'."""
    code = region.get("code", "US")
    lang = region.get("lang", "en")
    import random
    out = []
    for term in random.sample(CFG["search_terms"], min(3, len(CFG["search_terms"]))) + random.sample(CFG.get("stream_terms", []) or [""], 1):
      if not term: continue
      try:
          cmd = ["yt-dlp", "--flat-playlist", "-J", "--no-warnings", "--ignore-errors",
                 "--playlist-items", "1-30",
                 "--extractor-args", "youtube:player_client=default",
                 "ytsearch30:" + term]
          r = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
          if r.returncode:
              print(f"find_clip: region {code} yt-dlp search failed: {r.stderr.strip()[:200]}", file=sys.stderr)
              continue
          j = json.loads(r.stdout)
          for e in j.get("entries", []):
              vid = e.get("id")
              if not vid: continue
              out.append({"url": "https://www.youtube.com/watch?v=" + vid,
                          "title": e.get("title") or "", "desc": "",
                          "views": 0,  # flat-playlist gives no stats; last-resort handles this
                          "uploader": e.get("channel") or e.get("uploader") or "",
                          "published": "",
                          "region": code, "source": "ytdlp-search",
                          "kind": "live" if e.get("live_status") == "is_live" else
                                  ("stream" if (e.get("live_status") == "was_live" or (e.get("duration") or 0) > CFG["max_clip_minutes"] * 60) else "video"),
                          "duration": e.get("duration") or 0})
          print(f"find_clip: fallback region {code}: {len(out)} results for '{term}'", file=sys.stderr)
      except Exception as e:
          print(f"find_clip: region {code} fallback error: {e}", file=sys.stderr)
    return out

TWITCH_WEB_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch's public web client id (what yt-dlp uses); no account or 2FA needed

def twitch_gql(query):
    r = urllib.request.Request("https://gql.twitch.tv/gql", data=json.dumps({"query": query}).encode(),
                               headers={"Client-ID": TWITCH_WEB_ID, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())["data"]

def twitch_clips_keyless():
    """Same top GTA VI clips, read from Twitch's public GraphQL. Used when no TWITCH_CLIENT_ID/SECRET."""
    game = json.dumps(CFG.get("twitch_game", "Grand Theft Auto VI"))
    period = "LAST_DAY" if CFG.get("twitch_days", 3) <= 1 else "LAST_WEEK"
    try:
        d = twitch_gql('{ game(name:%s){ clips(first:50, criteria:{period:%s, sort:VIEWS_DESC}){ edges{ node{ slug url title viewCount createdAt durationSeconds language broadcaster{login displayName} } } } } }' % (game, period))
        edges = ((d.get("game") or {}).get("clips") or {}).get("edges") or []
        langs = {l.lower() for l in CFG.get("twitch_languages", ["en"])}
        out = []
        for e in edges:
            c = e["node"]; b = c.get("broadcaster") or {}
            if langs and (c.get("language") or "").lower() not in langs: continue
            out.append({"url": c.get("url") or f"https://clips.twitch.tv/{c['slug']}", "title": c["title"],
                        "desc": f"Twitch clip from {b.get('displayName','a streamer')}'s GTA 6 stream.",
                        "views": c["viewCount"] * CFG.get("twitch_view_weight", 5), "uploader": b.get("displayName", ""),
                        "kind": "twitch_clip", "published": c["createdAt"], "region": "TW", "duration": c.get("durationSeconds", 0)})
        print(f"find_clip: Twitch (keyless): {len(out)} GTA VI clips", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Twitch keyless failed:", e, file=sys.stderr); return []

def twitch_lives_keyless():
    game = json.dumps(CFG.get("twitch_game", "Grand Theft Auto VI"))
    try:
        d = twitch_gql('{ game(name:%s){ streams(first:20){ edges{ node{ title viewersCount createdAt broadcaster{login displayName} } } } } }' % game)
        edges = ((d.get("game") or {}).get("streams") or {}).get("edges") or []
        out = [{"url": "https://www.twitch.tv/" + n["broadcaster"]["login"], "title": n.get("title") or "",
                "desc": f"{n['broadcaster']['displayName']} live on Twitch playing GTA 6.", "views": n["viewersCount"] * 20,
                "uploader": n["broadcaster"]["displayName"], "kind": "live", "published": n["createdAt"], "region": "TW"}
               for n in (e["node"] for e in edges) if n.get("broadcaster") and n["viewersCount"] >= CFG.get("min_live_viewers", 2000)]
        print(f"find_clip: Twitch live (keyless): {len(out)} big GTA VI streams", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Twitch live keyless failed:", e, file=sys.stderr); return []

def twitch_clips():
    """Twitch's most-viewed GTA VI clips. Uses the official API if TWITCH_CLIENT_ID/SECRET are set, else the keyless public GraphQL."""
    cid, sec = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and sec): return twitch_clips_keyless()
    try:
        tok = json.loads(urllib.request.urlopen(urllib.request.Request("https://id.twitch.tv/oauth2/token",
              data=urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}).encode()), timeout=30).read())["access_token"]
        H = {"Client-Id": cid, "Authorization": "Bearer " + tok}
        tw = lambda path: json.loads(urllib.request.urlopen(urllib.request.Request("https://api.twitch.tv/helix/" + path, headers=H), timeout=30).read())
        game = tw("games?" + urllib.parse.urlencode({"name": CFG.get("twitch_game", "Grand Theft Auto VI")}))["data"]
        if not game: print("find_clip: Twitch game not found", file=sys.stderr); return []
        since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=CFG.get("twitch_days", 3))).strftime("%Y-%m-%dT%H:%M:%SZ")
        clips = tw("clips?" + urllib.parse.urlencode({"game_id": game[0]["id"], "started_at": since, "first": 50}))["data"]
        langs = set(CFG.get("twitch_languages", ["en"]))
        out = [{"url": c["url"], "title": c["title"], "desc": f"Twitch clip from {c['broadcaster_name']}'s GTA 6 stream.",
                "views": c["view_count"] * CFG.get("twitch_view_weight", 5), "uploader": c["broadcaster_name"], "kind": "twitch_clip",
                "published": c["created_at"], "region": "TW", "duration": c.get("duration", 0)}
               for c in clips if (not langs or c.get("language") in langs)]
        print(f"find_clip: Twitch: {len(out)} GTA VI clips", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Twitch failed:", e, file=sys.stderr); return []

def twitch_lives():
    """GTA VI streams live on Twitch right now, biggest first (recorded + clipped at their loudest moment)."""
    cid, sec = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and sec): return twitch_lives_keyless()
    try:
        tok = json.loads(urllib.request.urlopen(urllib.request.Request("https://id.twitch.tv/oauth2/token",
              data=urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}).encode()), timeout=30).read())["access_token"]
        H = {"Client-Id": cid, "Authorization": "Bearer " + tok}
        tw = lambda path: json.loads(urllib.request.urlopen(urllib.request.Request("https://api.twitch.tv/helix/" + path, headers=H), timeout=30).read())
        g = tw("games?" + urllib.parse.urlencode({"name": CFG.get("twitch_game", "Grand Theft Auto VI")}))["data"]
        if not g: return []
        st = tw("streams?" + urllib.parse.urlencode({"game_id": g[0]["id"], "first": 20}))["data"]
        out = [{"url": "https://www.twitch.tv/" + x["user_login"], "title": x["title"], "desc": f"{x['user_name']} live on Twitch playing GTA 6.",
                "views": x["viewer_count"] * 20, "uploader": x["user_name"], "kind": "live", "published": x["started_at"], "region": "TW"}
               for x in st if x["viewer_count"] >= CFG.get("min_live_viewers", 2000)]
        print(f"find_clip: Twitch live: {len(out)} big GTA VI streams", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Twitch live failed:", e, file=sys.stderr); return []

def kick_lives():
    """GTA VI streams live on Kick (official public API; needs KICK_CLIENT_ID + KICK_CLIENT_SECRET from kick.com/settings/developer)."""
    cid, sec = os.environ.get("KICK_CLIENT_ID"), os.environ.get("KICK_CLIENT_SECRET")
    if not (cid and sec): return []
    try:
        tok = json.loads(urllib.request.urlopen(urllib.request.Request("https://id.kick.com/oauth/token",
              data=urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}).encode(),
              headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30).read())["access_token"]
        H = {"Authorization": "Bearer " + tok, "Accept": "application/json"}
        k = lambda path: json.loads(urllib.request.urlopen(urllib.request.Request("https://api.kick.com/public/v1/" + path, headers=H), timeout=30).read())
        cats = [c for c in k("categories?" + urllib.parse.urlencode({"q": "Grand Theft Auto VI"})).get("data", [])
                if re.search(r"grand theft auto vi\b|gta ?(6|vi)\b", c.get("name", ""), re.I)]
        if not cats: print("find_clip: Kick category not found", file=sys.stderr); return []
        st = k("livestreams?" + urllib.parse.urlencode({"category_id": cats[0]["id"], "sort": "viewer_count", "limit": 20})).get("data", [])
        out = [{"url": "https://kick.com/" + x["slug"], "title": x.get("stream_title", ""), "desc": f"{x['slug']} live on Kick playing GTA 6.",
                "views": int(x.get("viewer_count", 0)) * 20, "uploader": x["slug"], "kind": "live", "published": x.get("started_at", ""), "region": "KICK"}
               for x in st if int(x.get("viewer_count", 0)) >= CFG.get("min_live_viewers", 2000)]
        print(f"find_clip: Kick live: {len(out)} big GTA VI streams", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Kick live failed:", e, file=sys.stderr); return []

def kick_clips():
    """Kick's most-viewed GTA VI clips (public site endpoint, no key; Cloudflare may block it, then it's skipped)."""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "25", "-A", "Mozilla/5.0", "-H", "Accept: application/json",
                            "https://kick.com/api/v2/categories/" + CFG.get("kick_slug", "grand-theft-auto-vi") + "/clips?sort=view&time=day"],
                           capture_output=True, text=True, timeout=40)
        clips = json.loads(r.stdout).get("clips", [])
        out = [{"url": f"https://kick.com/{c['channel']['slug']}/clips/{c['id']}", "title": c.get("title", ""),
                "desc": f"Kick clip from {c['channel'].get('username', c['channel']['slug'])}'s GTA 6 stream.",
                "views": int(c.get("view_count") or c.get("views") or 0) * CFG.get("twitch_view_weight", 5),
                "uploader": c["channel"].get("username", c["channel"]["slug"]), "kind": "twitch_clip",
                "published": c.get("created_at", ""), "region": "KICK"} for c in clips]
        print(f"find_clip: Kick clips: {len(out)}", file=sys.stderr); return out
    except Exception as e:
        print("find_clip: Kick clips skipped:", str(e)[:80], file=sys.stderr); return []

def age_days(m):
    if m.get("source") == "ytdlp-search" and not m["published"]:
        return 0  # search results assumed fresh
    try:
        t = datetime.datetime.strptime(m["published"][:19], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - t).days
    except Exception:
        return 9999

def score(m):
    """Playbook step 4: rank by views PER HOUR (what's blowing up now, not what's old and big), x3 for a streamer
    with an active paid clipping campaign (campaigns.json)."""
    try:
        p = (m.get("published") or "").replace("Z", "+00:00")
        hrs = max(1.0, (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(p)).total_seconds() / 3600)
    except Exception:
        hrs = max(1.0, min(age_days(m), 60) * 24)
    s = m["views"] / hrs
    try:
        import campaign
        if any(k in (m.get("uploader", "") + " " + m.get("title", "")).lower() for k in campaign.boost_names()): s *= 3
    except Exception: pass
    return s

def main():
    manual = queue_links()                   # links you send the bot land here (studio/run.py queues them)
    mu = os.environ.get("MANUAL_URL", "").strip()
    if mu:   # the link you typed in THIS run wins: old queued links (e.g. Carry) wait for a scheduled run
        manual = [l for l in manual if vid_id(l.split()[0]).split("@")[0] == vid_id(mu).split("@")[0]] or [mu]
    for line in manual:                      # links you chose yourself skip the view bar
        url, auto = split_q(line)
        if vid_id(url) in USED or url in EXCLUDE:
            print(f"find_clip: {url} was already used (this slot), skipped", file=sys.stderr); continue
        if NO_YT and is_youtube(url): continue
        if any(d in url for d in ("x.com/", "twitter.com/")) and "/status/" in url:
            url = url.split("?")[0].replace("twitter.com/", "x.com/")   # X post: yt-dlp downloads it, no YouTube bot wall
        elif not is_youtube(url) and "twitch.tv" not in url and "kick.com" not in url:
            print(f"find_clip: skipping non-YouTube URL: {url}", file=sys.stderr)
            continue
        m = meta(url) or {"url": url, "title": "", "desc": "", "views": 0, "uploader": ""}
        if not (m.get("title") or "").strip():
            # No title/description = nothing true to narrate, and the topic filter can't run. Never pass it on
            # (Oct 30: an empty-metadata clip became a Short of the LLM saying "no source provided").
            print(f"find_clip: no metadata for {url} (YouTube blocked the lookup), skipped this run", file=sys.stderr); continue
        topic = q_topic(line)
        if topic:   # mix mode: a general trend; Botbeat narrates THIS topic, not robots
            if bad(m["title"] + m["desc"]):
                print(f"find_clip: general trend clip banned, skipped: {m['title'][:80]!r}", file=sys.stderr); continue
            m.update({"source": "trend-general", "mode": "general", "topic": topic}); print(json.dumps(m)); return
        if auto and m["title"] and not (on_topic(m["title"]) and not bad(m["title"] + m["desc"])):
            print(f"find_clip: trend clip off-topic, skipped: {m['title'][:80]!r}", file=sys.stderr); continue
        m["source"] = "trend" if auto else "manual"
        try:   # GTA_GENRE: your own link is never rejected, but the judge marks its beats/key moment for the edit + sound effects
            import genre, genre_judge
            if genre.name(): m = genre_judge.annotate(m)
        except Exception as e: print("genre: could not judge your clip:", str(e)[:150], file=sys.stderr)
        print(json.dumps(m)); return

    if os.environ.get("MANUAL_URL", "").strip():
        # You asked for ONE specific link. If it couldn't be used, stop here (Telegram says so) instead of posting something random.
        print("find_clip: your link couldn't be used this run, so nothing was auto-picked in its place", file=sys.stderr); return

    # Multi-region discovery: merge pools from every configured region.
    pool = []
    for region in ([] if NO_YT else REGIONS):
        hits = youtube_search_region(region)
        if not hits:                       # API miss -> use no-API fallback for this region
            hits = youtube_dlp_search_region(region)
        pool += hits

    pool += twitch_clips() + twitch_lives() + kick_lives() + kick_clips()
    if not CFG.get("allow_live", True): pool = [m for m in pool if m.get("kind") != "live"]
    pool = [m for m in pool if m["url"] not in EXCLUDE and not (NO_YT and is_youtube(m["url"]))]
    NOT_GAMEPLAY = re.compile(r"\b(ama|q&a|review|reviewer|podcast|interview|news|explained|analysis|discussion|talk|tier list|reacts? to trailer|watch ?party|sales|price|everything we know|countdown)\b", re.I)
    NOT_GAMEPLAY_CH = {c.lower() for c in CFG.get("non_gameplay_channels", ["IGN", "GameSpot", "Kotaku", "Polygon", "GameRant", "Eurogamer", "The Game Theorists", "Gamer Guides"])}
    before = len(pool)
    pool = [m for m in pool if not NOT_GAMEPLAY.search(m.get("title", "")) and (m.get("uploader") or "").lower() not in NOT_GAMEPLAY_CH]
    print(f"find_clip: real-gameplay filter dropped {before - len(pool)} talk/news/review videos", file=sys.stderr)
    # Before launch (NZ midnight 19 Nov 2026 = 18 Nov 11:00 UTC) nobody is playing GTA 6: live "GTA VI" streams are waiting/donation streams, not footage.
    if datetime.datetime.now(datetime.timezone.utc) < datetime.datetime(2026, 11, 18, 11, tzinfo=datetime.timezone.utc):
        pool = [m for m in pool if not (m.get("kind") == "live" and m.get("region") in ("TW", "KICK"))]
    if NO_YT: print(f"find_clip: YouTube blocked -> Twitch/Kick only, {len(pool)} in pool", file=sys.stderr)

    # Dedupe by url, keep highest views version.
    by_url = {}
    for m in pool:
        cur = by_url.get(m["url"])
        if not cur or m["views"] > cur["views"]:
            by_url[m["url"]] = m
    pool = list(by_url.values())
    try:
        import genre
        g = genre.name()
    except Exception: g = ""
    if g:   # GTA_GENRE: STRICT. The judge watches + listens to the top clips; only a clip that really fits is used, else nothing is made.
        import genre_judge
        cands = sorted([m for m in pool if m.get("kind") != "live" and vid_id(m["url"]) not in USED and m["url"] not in EXCLUDE
                        and not bad(m["title"] + " " + m.get("desc", "")) and m["views"] >= CFG["min_views"] / 3
                        and (on_topic(m["title"]) or m.get("region") in ("TW", "KICK"))], key=lambda x: -score(x))
        best = genre_judge.pick(cands, g)
        if not best:
            print(f"no clip truly fits genre '{g}' this run", file=sys.stderr); sys.exit(3)
        best["source"] = "trend"; print(json.dumps(best)); return

    # Apply the same filters as before, across all regions.
    filtered = [m for m in pool
                if vid_id(m["url"]) not in USED and m["views"] >= CFG["min_views"]
                and not bad(m["title"] + m["desc"]) and (on_topic(m["title"]) or m.get("region") in ("TW", "KICK"))]
    trusted = {c.lower() for c in CFG.get("trusted_channels", []) + CFG.get("trusted_streamers", [])}
    is_trusted = lambda m: m["uploader"].lower() in trusted
    fresh = CFG["max_age_days"]
    tiers = [("trusted-fresh", lambda m: is_trusted(m) and age_days(m) <= fresh),
             ("trusted-older", is_trusted)]
    if CFG.get("allow_untrusted_fallback", True) or not CFG.get("trusted_only"):
        tiers.append(("any-fresh", lambda m: age_days(m) <= fresh))
    for name, ok in tiers:
        hits = sorted([m for m in filtered if ok(m)], key=lambda x: -score(x))
        print(f"find_clip: tier {name}: {len(hits)} candidates", file=sys.stderr)
        if hits:
            m = hits[0]
            reg = m.pop("region", "?")
            m["source"] = f"youtube-search:{name}:region-{reg}"
            print(json.dumps(m)); return

    # LAST RESORT: still nothing passed the strict filters (e.g. API quota + flat-playlist fallback
    # gives no view counts). Grab the first non-banned, not-already-used clip from the pool.
    # This guarantees the bot posts SOMETHING every scheduled run instead of going silent.
    fallback = [m for m in pool
                if vid_id(m["url"]) not in USED and not bad(m["title"] + m["desc"]) and (on_topic(m["title"]) or m.get("region") in ("TW", "KICK"))]
    print(f"find_clip: last-resort tier: {len(fallback)} candidates", file=sys.stderr)
    if fallback:
        m = max(fallback, key=lambda x: x.get("views", 0))
        reg = m.pop("region", "?")
        m["source"] = f"youtube-search:last-resort:region-{reg}"
        print(json.dumps(m)); return

    print(f"find_clip: nothing new passed the filters (US + India) — {len(pool)} pool entries, {len(filtered)} filtered", file=sys.stderr)

if __name__ == "__main__":
    main()
