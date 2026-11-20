#!/usr/bin/env python3
"""GTA 6 SECRETS MODE: one vertical Short a day in the "GTA 6 SECRETS · PART N" series (1080x1920, 30 fps, 12-25 s).
  tip   : published GTA 6 guide articles (publisher RSS feeds + IGN wiki index + DuckDuckGo html search, keyless)
          -> Gemini extracts ONE concrete tip (money/loot spot, hidden vehicle/weapon, secret place, useful mechanic)
          -> Gemini must quote the supporting sentence; the quote has to be found verbatim in the fetched article,
             every number/keyword in the captions must be in the article, else the tip is rejected (never invented)
          -> topics already in secrets_topics.json are skipped
  clip  : keyless Twitch GraphQL top clips (EN/HI, LAST_DAY then LAST_WEEK) + Kick, ranked by how well the clip title
          matches the tip's keywords; the top few are downloaded and Gemini vision looks at 6 frames: does it SHOW the
          tip's place/item, and is it safe (no full nudity - bikini OK -, no stream alerts, no slurs on screen);
          the streamer's audio is transcribed (faster-whisper) and checked for slurs.
          Clips in used.json / listicle_used.json / secrets_used.json never come back.
  render: facecam on top when there is one (gameplay below), else full-frame 9:16; yellow "GTA 6 SECRETS · PART N"
          banner, big hook in the first 2 s, WHERE / WHAT YOU GET step captions, punch-in + white flash at the payoff,
          "FOLLOW FOR PART N+1" end card, small clip credit; original audio, no music. Montserrat Black Italic.
  out   : out/short.mp4 + out/metadata.json (+ out/tip_check.json) for secrets_gate / send_short / upload_youtube.

Usage:
  python gta_secrets.py                      make out/short.mp4 (needs GEMINI_API_KEY)
  python gta_secrets.py --lock               mark the tip + clip used, Part N posted (git)
  python gta_secrets.py --lock-footage       preview: burn the clip only (git)
Test switches (never used by the workflow):
  --no-llm        no Gemini: heuristic tip pick + title-only clip match, metadata marked test-only (can't be locked)
  --dry-run       stop after tip sourcing + clip ranking (no downloads, nothing written to state)
  --tip-file F --clip-file MP4 [--clip-url U --uploader NAME --payoff SEC --part N]   render a hand-written tip
Exit codes: 0 made | 5 no verified tip | 3 no matching clean clip | 4 (lock) part/topic already posted | 1 other error."""
import argparse, base64, datetime, difflib, html, json, os, pathlib, random, re, shutil, subprocess, sys, time
import urllib.parse, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
CFG = json.load(open(HERE / "config.json"))
if (HERE / "gta_secrets.json").exists():   # secrets settings live in gta_secrets.json; a secrets_* key in config.json wins
    CFG = {**json.load(open(HERE / "gta_secrets.json")), **CFG}
C = lambda k, d: CFG.get(k, d)
OUT = HERE / "out"
WORK = pathlib.Path(os.environ.get("SECRETS_WORK", "/tmp/gtasecrets"))
SERIES_F, TOPICS_F, USED_F = HERE / "secrets_series.json", HERE / "secrets_topics.json", HERE / "secrets_used.json"
OTHER_USED = [HERE / "used.json", HERE / "listicle_used.json"]
FONT_DIR = HERE / "fonts"
FONT_FILE = FONT_DIR / "Montserrat-BlackItalic.ttf"
W, H, FPS = 1080, 1920, 30
YELLOW = "0xFFD60A"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
NO_LLM = False

class FootageError(RuntimeError): pass
class TipError(RuntimeError): pass

def log(*a): print("secrets:", *a, file=sys.stderr, flush=True)

def sh(*a, **k):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, **k)
    if r.returncode: raise RuntimeError(" ".join(map(str, a))[:200] + "\n" + r.stderr[-1500:])
    return r.stdout

def norm(s): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()

def duration(p): return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).strip())

# ------------------------------------------------------------------------------------------------------------- state
def read_json(p, default):
    try: return json.loads(p.read_text()) if p.exists() else default
    except Exception: return default

def newest(path, default):
    """This checkout's copy merged with the newest copy on GitHub (re-runs of old runs check out old copies)."""
    mine = read_json(path, default)
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "main"], cwd=HERE, timeout=60, capture_output=True)
        r = subprocess.run(["git", "show", f"FETCH_HEAD:gtaclips/{path.name}"], cwd=HERE, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            theirs = json.loads(r.stdout)
            if isinstance(default, list): return mine + [x for x in theirs if x not in mine]
            if isinstance(default, dict): return {**mine, "part": max(int(mine.get("part", 0)), int(theirs.get("part", 0)))}
    except Exception as e:
        log(f"couldn't read newest {path.name}:", str(e)[:100])
    return mine

def clip_key(u):
    """One key per clip whatever URL form it was saved in (clips.twitch.tv/SLUG == twitch.tv/x/clip/SLUG)."""
    u = (u or "").strip().split("?")[0].rstrip("/")
    m = re.search(r"(?:clips\.twitch\.tv/|/clip/)([A-Za-z0-9_-]+)$", u)
    if m: return "tw:" + m.group(1)
    m = re.search(r"kick\.com/[^/]+/clips/([A-Za-z0-9_-]+)", u)
    if m: return "kick:" + m.group(1)
    return u.lower()

def used_clip_keys():
    keys = set()
    for p in OTHER_USED + [USED_F]:
        for x in newest(p, []) if p == USED_F else read_json(p, []):
            if isinstance(x, str): keys.add(clip_key(x))
    return keys

def used_topics():
    return [x[6:] for x in newest(TOPICS_F, []) if isinstance(x, str) and x.startswith("topic:")]

def next_part(): return int(newest(SERIES_F, {"part": 0}).get("part", 0)) + 1

# ------------------------------------------------------------------------------------------------------------- gemini
def gemini(parts, max_tokens=4096, temperature=0.1):
    key = os.environ.get("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY missing")
    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    model = os.environ.get("SECRETS_MODEL") or os.environ.get("GATE_MODEL", "gemini-2.5-flash")
    err = ""
    for attempt in range(3):
        try:
            rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                        json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
            d = json.loads(urllib.request.urlopen(rq, timeout=120).read())
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except Exception as e:
            err = str(e)[:120]; time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Gemini failed 3 times: {err}")

# ------------------------------------------------------------------------------------------------------------- sources
GUIDE_RE = re.compile(r"\b(where to (find|get)|locations?|how to (get|find|make|unlock|earn)|best (way|spot|place|early|money|weapon|car|vehicle|loot)|"
                      r"money|cash|loot|secret|hidden|easter egg|weapon|vehicle|car|plane|helicopter|jet|boat|bike|armou?r|"
                      r"unlock|tips?|tricks?|stash|safe|collectibles?|card locations?|airport|fast travel)\b", re.I)
SKIP_RE = re.compile(r"\b(walkthrough|ending|endings|story|spoilers?|review|reviews|layoffs?|devs?|developers?|sales?|sold|ps5 pro|fps|"
                     r"record|cast|actor|nudity|intimate|prostitut\w*|hookup|romance|glitch|exploit|dupe|duplication|leak\w*|refund|"
                     r"price|preorder|pre-order|stock|outage|parody|credits|fired|rating|controvers\w*|ask me anything|interview)\b", re.I)
GTA6_RE = re.compile(r"gta ?(6|vi)\b|grand theft auto (6|vi)\b", re.I)

def fetch(url, timeout=30, data=None):
    rq = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def domain_ok(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in C("secrets_allowed_domains", []))

def guide_title(t):
    return bool(GTA6_RE.search(t) and GUIDE_RE.search(t) and not SKIP_RE.search(t))

def feed_items(url):
    x = fetch(url)
    out = []
    for it in re.findall(r"(?s)<(?:item|entry)\b.*?</(?:item|entry)>", x):
        t = re.search(r"(?s)<title[^>]*>(.*?)</title>", it)
        l = re.search(r"(?s)<link>(.*?)</link>", it) or re.search(r'<link[^>]*href="([^"]+)"', it)
        if not t or not l: continue
        title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", t.group(1))).strip()
        link = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", l.group(1))).strip()
        out.append({"title": title, "url": link, "via": url})
    return out

def index_items(url):
    x = fetch(url)
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    out, seen = [], set()
    for href in re.findall(r'href="(/wikis/gta-6/[^"#?]+)"', x):
        if href in seen: continue
        seen.add(href)
        title = "GTA 6 " + urllib.parse.unquote(href.rsplit("/", 1)[-1]).replace("_", " ")
        out.append({"title": title, "url": base + href, "via": url})
    return out

def ddg_items(q):
    x = fetch("https://html.duckduckgo.com/html/", timeout=25, data=urllib.parse.urlencode({"q": q}).encode())
    out = []
    for href, title in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', x):
        m = re.search(r"uddg=([^&]+)", href)
        u = urllib.parse.unquote(m.group(1)) if m else href
        out.append({"title": html.unescape(re.sub(r"<[^>]+>", "", title)), "url": u, "via": "duckduckgo:" + q})
    return out

def candidate_articles():
    """Guide-looking GTA 6 articles from allowed publishers, freshest feeds first, de-duplicated."""
    items = []
    for u in C("secrets_feeds", []):
        try: got = feed_items(u); items += got; log(f"feed {u}: {len(got)} items")
        except Exception as e: log(f"feed failed {u}: {str(e)[:80]}")
    for u in C("secrets_index_pages", []):
        try: got = index_items(u); items += got; log(f"index {u}: {len(got)} pages")
        except Exception as e: log(f"index failed {u}: {str(e)[:80]}")
    qs = C("secrets_search_queries", [])
    if qs:
        rnd = random.Random(datetime.date.today().isoformat())
        for q in rnd.sample(qs, min(2, len(qs))):
            try: got = ddg_items(q); items += got; log(f"duckduckgo {q!r}: {len(got)} results")
            except Exception as e: log(f"duckduckgo failed {q!r}: {str(e)[:80]}")
    seen, out = set(), []
    for it in items:
        u = it["url"].split("#")[0]
        if u in seen or not domain_ok(u) or not guide_title(it["title"]): continue
        seen.add(u); it["url"] = u; out.append(it)
    # money/loot/hidden things first (the format that got saves), then the rest; stable shuffle per day
    rnd = random.Random("art" + datetime.date.today().isoformat()); rnd.shuffle(out)
    pri = re.compile(r"money|cash|loot|hidden|secret|where to find|location|stash|weapon|plane|helicopter|car\b|vehicle", re.I)
    out.sort(key=lambda x: (not pri.search(x["title"]), "/wikis/" in x["url"]))
    log(f"{len(out)} guide-looking GTA 6 articles from allowed publishers")
    return out

def article_text(url):
    """Readable text of the article body: headings, paragraphs and list items, in order."""
    x = fetch(url)
    x = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|aside|form)\b.*?</\1>", " ", x)
    x = re.sub(r'=\s*"[^"]*"', '=""', x)   # attribute values can hold ">" (inline JS), drop them before tag matching
    body = re.search(r"(?is)<article\b.*?</article>", x)
    seg = body.group(0) if body and len(body.group(0)) > 2000 else x
    blocks = re.findall(r"(?is)<(h[1-4]|p|li|td)\b[^>]*>(.*?)</\1>", seg)
    lines = []
    for _, b in blocks:
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", b))).strip()
        if len(t) > 25 and t not in lines: lines.append(t)
    return "\n".join(lines)

# ------------------------------------------------------------------------------------------------------------- tip
STOP = set("the a an and or of in on to for with how what why is are you your it its this that from by at be can do does did into "
           "about where find get got best way ways gta grand theft auto vi 6 game new first early free all every here there one more "
           "most top spot spots location locations guide tips tip make makes making fastest fast quick quickly ways earn need know things thing".split())
NUM_OK = {"6", "vi"}

def quote_found(q, text):
    nq, nt = norm(q), norm(text)
    return len(nq.split()) >= 6 and nq in nt

def tip_faults(tip, text, used):
    f = []
    for k in ("title", "keywords", "where", "what", "hook", "quote", "summary"):
        if not tip.get(k): f.append(f"missing {k}")
    if f: return f
    if not quote_found(tip["quote"], text): f.append("supporting quote is not in the article text (verbatim check failed)")
    nt = norm(text)
    kws = [k for k in tip["keywords"] if norm(k)]
    found = [k for k in kws if norm(k) in nt]
    if not found: f.append("none of the place/item keywords appear in the article")
    if found and not any(norm(k) in norm(tip["quote"]) or any(w in norm(tip["quote"]).split() for w in norm(k).split() if w not in STOP)
                         for k in found):
        f.append("the quote does not mention the tip's place/item")
    copy = " ".join(str(tip[k]) for k in ("title", "where", "what", "hook", "summary"))
    for num in re.findall(r"\$?\d[\d,.]*[kKmM%]?", copy):
        n_ = norm(num)
        if n_ in NUM_OK: continue
        if not re.search(r"(?<![a-z0-9])" + re.escape(n_) + r"(?![a-z0-9])", nt): f.append(f"number '{num}' is not in the article")
    if SKIP_RE.search(copy): f.append("tip touches a banned subject (spoiler/glitch/exploit/nudity...)")
    bad = [w for w in CFG.get("banned_words", []) if re.search(r"\b" + re.escape(w) + r"\b", copy, re.I)]
    if bad: f.append("banned words: " + ", ".join(bad))
    for u in used:
        if difflib.SequenceMatcher(None, norm(u), norm(tip["title"])).ratio() > 0.8:
            f.append(f"topic already done: {u!r}"); break
    if len(tip["hook"].split()) > 7: f.append("hook longer than 7 words")
    return f

TIP_PROMPT = """You pick ONE GTA 6 tip for a 20-second YouTube Short in a "GTA 6 SECRETS" series, from the guide article below.
ARTICLE: {title}
URL: {url}
TEXT:
{text}

Pick ONE concrete, useful tip a player can act on right away and that can be SHOWN in gameplay footage: a money/loot spot,
a hidden vehicle or weapon, a secret place, or a useful mechanic. Prefer something visual (a place, a vehicle, a weapon).
HARD RULES: use ONLY what this article says. Never invent places, names, numbers, prices, controls or steps.
No story spoilers, no glitches/exploits, no cheats that lock progress, no nudity/sex topics.
Do NOT pick any of these topics, they were already posted: {used}

Reply ONLY JSON:
{{"ok": true|false (false if the article has no such tip),
 "title": "the tip in under 45 characters, e.g. 'Free plane at the airport'",
 "yt_title": "YouTube title under 55 characters that contains 'GTA 6', honest, no emoji",
 "keywords": ["2-5 short place/item words that a streamer's clip title or the screen would show, e.g. 'airport', 'plane'"],
 "hook": "3-6 word on-screen hook for the first 2 seconds, e.g. 'FREE PLANE ON DAY ONE'",
 "where": "WHERE caption, max 8 words, the place/how to get there, from the article",
 "what": "WHAT YOU GET caption, max 8 words, from the article",
 "summary": "the tip in 1-2 short sentences for the description, from the article",
 "share_cta": "ALL CAPS share line starting 'SEND THIS TO', max 9 words, who needs this tip, e.g. 'SEND THIS TO THE FRIEND WHO ALWAYS NEEDS MONEY'",
 "quote": "the sentence from the article that proves the tip, copied EXACTLY character for character (8-40 words)"}}"""

def llm_tip(art, text, used):
    return gemini([{"text": TIP_PROMPT.format(title=art["title"], url=art["url"], text=text[:15000], used="; ".join(used[-60:]) or "none")}])

LOC_RE = re.compile(r"(?i)\b(you can find|is located|are located|head (to|over to)|found (at|in|on|near)|spawns?|located (at|in|on|near)|go to|make your way to)\b")

def heuristic_tip(art, text, used):
    """--no-llm TEST ONLY: first sentence that reads like a location; keywords = its capitalised names."""
    for s in re.split(r"(?<=[.!?])\s+|\n", text):
        s = s.strip()
        if not (8 <= len(s.split()) <= 40 and LOC_RE.search(s)): continue
        caps = [w for w in re.findall(r"\b[A-Z][a-zA-Z'-]{2,}(?:\s[A-Z][a-zA-Z'-]{2,})?", s)
                if norm(w) not in STOP and not GTA6_RE.search(w) and norm(w) not in {"you", "the", "this", "once", "when", "head"}]
        kws = list(dict.fromkeys(caps))[:4] or [w for w in norm(art["title"]).split() if w not in STOP][:3]
        if not kws: continue
        t = re.sub(r"(?i)^gta ?(6|vi):?\s*", "", art["title"])[:45]
        return {"ok": True, "title": t, "yt_title": ("GTA 6: " + t)[:55], "keywords": kws, "hook": " ".join(t.upper().split()[:5]),
                "where": " ".join(s.split()[:8]), "what": " ".join(kws)[:40], "summary": s, "quote": s}
    return {"ok": False}

def find_tips(used):
    """Yield verified tips one by one (caller tries footage for each)."""
    tried = 0
    for art in candidate_articles()[:C("secrets_max_articles", 10)]:
        if tried >= C("secrets_max_tips", 4): return
        try:
            text = article_text(art["url"])
        except Exception as e:
            log(f"article fetch failed {art['url']}: {str(e)[:80]}"); continue
        if len(text) < 600 or not GTA6_RE.search(text):
            log(f"article too thin / not GTA 6: {art['url']}"); continue
        try:
            tip = heuristic_tip(art, text, used) if NO_LLM else llm_tip(art, text, used)
        except Exception as e:
            log(f"tip extraction failed {art['url']}: {str(e)[:120]}"); continue
        if not tip.get("ok"):
            log(f"no usable tip in {art['url']}"); continue
        tip["keywords"] = [str(k) for k in tip.get("keywords", []) if str(k).strip()][:5]
        faults = tip_faults(tip, text, used)
        tried += 1
        if faults:
            log(f"tip REJECTED ({art['url']}): {'; '.join(faults)}"); continue
        tip.update({"source_url": art["url"], "source_title": art["title"], "found_via": art["via"],
                    "verified": "quote found verbatim in article" + (" (no-llm heuristic, TEST ONLY)" if NO_LLM else "")})
        log(f"tip OK: {tip['title']!r} keywords={tip['keywords']} from {art['url']}")
        yield tip

# ------------------------------------------------------------------------------------------------------------- footage
NSFW = ["nudist", "naked", "nude", "racist", "slur", "n word", "f slur", "gets called", "boobs", "nsfw", "strip club"]
SLUR_RE = re.compile(r"\b(n[i1]gg(a|er|ers|as|uh)|f[a@]gg?(ot|ots|s)?|retard(ed|s)?|tr[a@]nn(y|ies)|k[i1]ke|ch[i1]nk|sp[i1]c|wetback|paki)\b", re.I)
SYN = {"plane": ["airplane", "jet", "plane", "flying", "fly", "airport"], "helicopter": ["heli", "chopper", "helicopter"],
       "money": ["money", "cash", "rich", "bank", "atm", "robbery", "rob"], "car": ["car", "cars", "supercar", "vehicle"],
       "boat": ["boat", "yacht", "jetski"], "weapon": ["gun", "weapon", "rpg", "sniper", "minigun"], "bike": ["bike", "motorcycle", "dirtbike"]}

def kw_tokens(tip):
    toks = set()
    for k in tip["keywords"] + [tip["title"]]:
        for w in norm(k).split():
            if w in STOP or len(w) < 3: continue
            toks.add(w)
            for base, syn in SYN.items():
                if w == base or w in syn: toks |= set(syn)
    return toks

def twitch_pool():
    import find_clip
    game = json.dumps(find_clip.CFG.get("twitch_game", "Grand Theft Auto VI"))
    langs = [l.upper() for l in C("secrets_languages", ["EN", "HI"])]
    out, seen = [], set()
    for period in C("secrets_periods", ["LAST_DAY", "LAST_WEEK"]):
        try:
            d = find_clip.twitch_gql('{ game(name:%s){ clips(first:100, criteria:{period:%s, sort:VIEWS_DESC, languages:[%s]}){ edges{ node{ slug url title viewCount createdAt durationSeconds language broadcaster{login displayName} } } } } }'
                                     % (game, period, ",".join(langs)))
            n = 0
            for e in ((d.get("game") or {}).get("clips") or {}).get("edges") or []:
                c = e["node"]; b = c.get("broadcaster") or {}
                url = c.get("url") or f"https://clips.twitch.tv/{c['slug']}"
                if url in seen: continue
                seen.add(url); n += 1
                out.append({"url": url, "title": c.get("title", ""), "views": c.get("viewCount", 0), "uploader": b.get("displayName", ""),
                            "login": b.get("login", ""), "duration": c.get("durationSeconds", 0), "lang": (c.get("language") or "").lower(),
                            "credit": f"twitch.tv/{b.get('login', '')}", "period": period})
            log(f"twitch {period}: {n} clips")
        except Exception as e:
            log(f"twitch {period} failed:", str(e)[:120])
    return out

def kick_pool():
    import find_clip
    out = []
    for c in find_clip.kick_clips():
        login = c["url"].split("kick.com/")[1].split("/")[0]
        out.append({"url": c["url"], "title": c.get("title", ""), "views": c.get("views", 0), "uploader": c.get("uploader", ""),
                    "login": login, "duration": c.get("duration", 0), "lang": "", "credit": f"kick.com/{login}", "period": "KICK"})
    return out

def ranked_clips(tip, used_keys):
    import find_clip
    toks = kw_tokens(tip)
    pool = twitch_pool() + kick_pool()
    good, seen = [], set()
    for c in pool:
        k = clip_key(c["url"])
        t = c["title"].lower()
        if k in seen or k in used_keys or find_clip.bad(t) or any(w in t for w in NSFW) or SLUR_RE.search(t): continue
        if c.get("duration") and c["duration"] < C("secrets_min_clip_seconds", 12): continue
        seen.add(k)
        words = set(norm(c["title"]).split())
        c["score"] = len(words & toks) + (0.5 if c["period"] == "LAST_DAY" else 0)
        c["matched"] = sorted(words & toks)
        good.append(c)
    good.sort(key=lambda c: (-len(c["matched"]), -c["score"], -c.get("views", 0)))
    matched = [c for c in good if c["matched"]][:C("secrets_max_downloads", 5)]
    unmatched = [c for c in good if not c["matched"]][:C("secrets_unmatched_tries", 2)]
    log(f"clips: {len(pool)} in pool, {len(good)} unused + clean, {len([c for c in good if c['matched']])} title-match {sorted(toks)}")
    return matched + unmatched

def download(c, dst):
    import listicle   # same yt-dlp routes/proxy secrets as the listicle
    return listicle.download_clip(c["url"], dst)

def frame_jpg(path, t, width=480, rect=None):
    vf = (f"crop={rect[2]}:{rect[3]}:{rect[0]}:{rect[1]}," if rect else "") + f"scale={width}:-2"
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(path), "-frames:v", "1", "-vf", vf,
                           "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"], capture_output=True).stdout

VISION_Q = """These are 6 frames (numbered 0-5, in time order, at {times} seconds) from a Twitch/Kick GTA 6 stream clip.
We want to use it in a Short about this GTA 6 tip:
  TIP: {title}
  WHERE: {where}
  WHAT YOU GET: {what}
  KEY PLACE/ITEMS: {kw}
1) shows_tip: does the gameplay clearly SHOW this tip's place or item (e.g. the plane/airport, the weapon, the stash spot)?
   Be strict: generic driving or shooting that is not the tip's place/item is false.
2) best_frame: the frame number that shows it best (the payoff moment).
3) safe: NUDITY RULE: swimwear, bikinis, lingerie, thongs, underwear and shirtless characters are FINE. Full nudity is NOT fine:
   visible bare genitals, bare female nipples/breasts, fully naked characters, even small or in the background.
   safe=false if ANY frame shows full nudity, a sexual act, gore, a stream alert/overlay (sub/donation/follower alerts,
   gifted subs, big chat box), a slur or hateful text on screen, or is not GTA 6 video game footage.
   A streamer's webcam in a corner is fine.
Reply ONLY JSON: {{"shows_tip": true|false, "best_frame": 0-5, "safe": true|false, "why": "short reason"}}"""

def vision_check(path, dur, tip):
    times = [dur * (i + 0.5) / 6 for i in range(6)]
    if NO_LLM:
        return {"shows_tip": True, "best_frame": 3, "safe": None, "why": "not checked (--no-llm test)", "times": times}
    parts = [{"text": VISION_Q.format(times=", ".join(f"{t:.1f}" for t in times), title=tip["title"], where=tip["where"],
                                      what=tip["what"], kw=", ".join(tip["keywords"]))}]
    for t in times:
        b = frame_jpg(path, t)
        if b: parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b).decode()}})
    v = gemini(parts, max_tokens=1024, temperature=0)
    v["times"] = times
    return v

def speech_faults(path):
    """Streamer audio is kept, so transcribe it (faster-whisper) and refuse slurs."""
    try:
        import captions
        ws = captions.words(path)
    except Exception as e:
        return None, f"speech check unavailable ({str(e)[:60]})"
    txt = " ".join(t for _, _, t in ws)
    m = SLUR_RE.search(txt) or next((re.search(r"\b" + re.escape(w) + r"\b", txt, re.I) for w in CFG.get("banned_words", [])
                                     if re.search(r"\b" + re.escape(w) + r"\b", txt, re.I)), None)
    return (f"slur/banned word in speech: '{m.group(0)}'" if m else ""), f"checked ({len(ws)} words)"

def find_footage(tip, used_keys):
    import layout
    for i, c in enumerate(ranked_clips(tip, used_keys)):
        p = download(c, WORK / f"clip{i}.mp4")
        if not p: continue
        try:
            d = duration(p)
            if d < C("secrets_min_clip_seconds", 12): log(f"too short ({d:.0f}s): {c['url']}"); continue
            v = vision_check(p, d, tip)
            if v.get("safe") is False: log(f"UNSAFE ({v.get('why','')[:80]}): {c['url']}"); continue
            if not v.get("shows_tip"): log(f"doesn't show the tip ({v.get('why','')[:80]}): {c['url']}"); continue
            bad, speech = speech_faults(p)
            if bad: log(f"{bad}: {c['url']}"); continue
            bf = min(5, max(0, int(v.get("best_frame", 3))))
            c.update({"path": p, "dur": d, "payoff": v["times"][bf], "vision": v,
                      "visual_safety": "checked: safe + shows tip" if v.get("safe") is True else "unchecked (--no-llm test)",
                      "speech": speech, "cam": layout.find_facecam(p)})
            log(f"clip OK: {c['uploader']} {c['url']} payoff ~{c['payoff']:.1f}s cam={c['cam']} ({v.get('why','')[:80]})")
            return c
        except Exception as e:
            log(f"clip vet failed {c['url']}: {str(e)[:150]}")
    raise FootageError(f"no clean clip that shows '{tip['title']}'")

# ------------------------------------------------------------------------------------------------------------- render
_fonts = {}
def text_w(s, size):
    from PIL import ImageFont
    if size not in _fonts: _fonts[size] = ImageFont.truetype(str(FONT_FILE), size)
    return _fonts[size].getlength(s)

def wrap(s, size, maxw=960, max_lines=2, minsize=44):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and text_w(cur + " " + w, size) > maxw: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    if (len(lines) > max_lines or any(text_w(l, size) > maxw for l in lines)) and size > minsize:
        return wrap(s, size - 4, maxw, max_lines, minsize)
    return lines, size

def split2(s):
    w = s.split()
    if len(w) < 3: return [s]
    best = min(range(1, len(w)), key=lambda i: abs(len(" ".join(w[:i])) - len(" ".join(w[i:]))))
    return [" ".join(w[:best]), " ".join(w[best:])]

def pick_window(dur, payoff):
    lo, hi, tgt = C("secrets_min_seconds", 12), C("secrets_max_seconds", 25), C("secrets_target_seconds", 20)
    L = min(dur - 0.2, tgt)
    if L < lo: raise FootageError(f"clip is only {dur:.1f}s")
    L = min(L, hi)
    end_card, punch = C("secrets_endcard_seconds", 2.5), C("secrets_punch_seconds", 1.5)
    # payoff ~60% in, and always before the end card with the punch-in done
    s = payoff - 0.6 * L
    s = max(0.0, min(s, dur - 0.2 - L))
    if payoff + punch > s + L - end_card: s = max(0.0, min(dur - 0.2 - L, payoff + punch + end_card - L))
    return round(s, 2), round(s + L, 2)

def cam_layout(w, h, cam):
    """(cam_rect for 1080x700, game_rect for 1080x1220): a 1080:700 cut inside the webcam box, gameplay away from the cam."""
    cx, cy, cw, ch = cam
    ar = 1080 / 700
    bh = ch * 0.98; bw = bh * ar                      # fill the panel from INSIDE the webcam box (no gameplay bleeding in)
    if bw > cw * 0.98: bw = cw * 0.98; bh = bw / ar
    bx = min(max(0, cx + cw / 2 - bw / 2), w - bw); by = min(max(0, cy + ch / 2 - bh / 2), h - bh)
    gh = h; gw = gh * 1080 / 1220
    if gw > w: gw = w; gh = gw * 1220 / 1080
    gx = (w - gw) / 2
    if gx < cx + cw and gx + gw > cx:   # overlaps the cam: slide to the far side
        gx = min(w - gw, cx + cw + 4) if cx + cw / 2 < w / 2 else max(0, cx - gw - 4)
    e = lambda v: int(v) // 2 * 2
    return (e(bx), e(by), e(bw), e(bh)), (e(gx), 0, e(gw), e(gh))

def full_layout(w, h):
    import listicle
    r = listicle.crop_rect(w, h, None)
    if not r: raise FootageError("no 9:16 crop")
    return r

def textfile(name, s):
    p = WORK / f"txt_{name}.txt"; p.write_text(s.replace("\r", "")); return p

def dt(name, text, y, size, color, t0, t1, bw=10, x="(w-text_w)/2"):
    p = textfile(name, text)
    return (f"drawtext=fontfile={FONT_FILE}:textfile={p}:expansion=none:fontsize={size}:fontcolor={color}:borderw={bw}:bordercolor=black:"
            f"shadowx=0:shadowy=6:shadowcolor=black@0.6:x={x}:y={y}:enable='between(t,{t0:.2f},{t1:.2f})'")

def secrets_cta(tip):
    c = re.sub(r"\s+", " ", str(tip.get("share_cta") or "")).strip().upper().rstrip(".!")
    return c if c.startswith("SEND THIS TO ") and len(c) <= 60 and len(c.split()) <= 12 else "SEND THIS TO YOUR GTA 6 SQUAD"


def render_hookfirst(clip, tip, part, dst):
    """Hook-first structure (hookfirst.py) with the series dressing: cold open on the payoff + hook, facecam body with the
    PART banner and WHERE / WHAT YOU GET captions, 0.5x REPLAY of the payoff, end card 'SEND THIS TO ...' + 'FOLLOW FOR PART N+1'."""
    import hookfirst, listicle
    path = clip["path"]
    w, h = listicle.probe_wh(path)
    if clip.get("key_at") is not None:
        key, why = float(clip["key_at"]), "manual key_at"
    else:   # Gemini vision payoff (6 coarse frames) refined to the loudness/motion peak within 2 s of it
        key, why = hookfirst.key_moment(path, near=float(clip["payoff"]), spread=2.0)
    log(f"hook-first key moment {key}s ({why})")
    cam = clip.get("cam") if w > h else None
    banner = f"GTA 6 SECRETS · PART {part}"
    cta = secrets_cta(tip)

    def extra(tl):
        b0, e0 = tl["body"][0], tl["end"][0]
        ko = tl["key_out"] if tl.get("key_out") is not None else (b0 + tl["body"][1]) / 2
        bsz = 62; bwid = text_w(banner, bsz); by = 48
        out = [f"drawbox=x={int((W - bwid) / 2 - 34)}:y={by - 16}:w={int(bwid + 68)}:h={bsz + 40}:color={YELLOW}@1:t=fill:enable='between(t,{b0:.2f},{e0:.2f})'",
               dt("banner", banner, by + 2, bsz, "black", b0, e0, bw=0).replace("shadowy=6", "shadowy=0")]
        cap_y = 1330 if tl["cam"] else 1240
        for name, label, body, t0, t1 in (("where", "WHERE", tip["where"], b0, ko), ("what", "WHAT YOU GET", tip["what"], ko, e0)):
            if t1 - t0 < 0.4: continue
            out.append(dt(f"{name}L", label, cap_y, 56, YELLOW, t0, t1, bw=8))
            lines, sz = wrap(body.upper(), 84, 960, 2)
            for j, ln in enumerate(lines):
                out.append(dt(f"{name}{j}", ln, cap_y + 74 + j * int(sz * 1.15), sz, "white", t0, t1))
        return out

    info = hookfirst.render(path, dst, hook=tip["hook"], cta=cta, credit=hookfirst.credit_for(clip) if clip.get("url") else
                            f"clip: {clip.get('credit') or clip.get('uploader') or 'streamer'}", key=key, cam=cam,
                            total=max(12, min(20, C("secrets_target_seconds", 20))), work=WORK, extra=extra,
                            end_sub=f"FOLLOW FOR PART {part + 1}", preset="medium")
    P, tl = info["plan"], info["timeline"]
    return {"start": P["body"][0], "end": P["body"][1], "seconds": info["seconds"], "payoff_at": round(tl["body"][0] + key - P["body"][0], 2),
            "layout": info["layout"], "structure": "hookfirst", "key_at": key, "key_why": why, "share_cta": cta}


def render_narrated(clip, tip, part, dst):
    """Narrated Secrets: the verified tip spoken by an AI voice (Hindi by default) over the clip's gameplay. Facecam, chat and
    subtitles cropped out, streamer audio muted, one caption card per line, PART pill, small clip credit."""
    import narrate
    lang = os.environ.get("GTA_NARRATE_LANG") or C("narrate_lang", "hi")
    lines, lang = narrate.secret_lines(tip, part, lang, gemini if os.environ.get("GEMINI_API_KEY") and not NO_LLM else None)
    credit = f"clip: {clip.get('credit') or clip.get('uploader') or 'streamer'}"
    start = max(0.0, float(clip.get("payoff", 0)) - 4.0)
    info = narrate.render(clip["path"], lines, dst, WORK / "narr", lang=lang, rate=C("narrate_rate", "+22%"),
                          pill=f"GTA 6 SECRETS · PART {part}", credit=credit, cam=clip.get("cam"), start=start,
                          game_db=C("narrate_game_db", None), max_seconds=C("secrets_max_seconds", 32) - 0.6)
    (WORK / "narration.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1))
    return {"start": start, "end": start + info["seconds"], "seconds": info["seconds"], "payoff_at": 0.0, "layout": info["layout"],
            "structure": "narrated", "voiceover": True, "voice": info["voice"], "narration": [l["say"] for l in lines]}


def render(clip, tip, part, dst):
    WORK.mkdir(parents=True, exist_ok=True)
    import listicle, hookfirst
    listicle.FONT_DIR, listicle.FONT_FILE = FONT_DIR, FONT_FILE
    listicle.ensure_font()
    import narrate
    if narrate.enabled(CFG):   # AI voiceover over clean gameplay (transformed = original content); "narrate": false / GTA_NARRATE=0 = off
        try:
            return render_narrated(clip, tip, part, dst)
        except Exception as e:
            log("narrated render failed, using the hook-first edit:", str(e)[-400:])
    if hookfirst.enabled(CFG):   # shared hook-first edit (config/gta_secrets.json "hookfirst": false or GTA_HOOKFIRST=0 = old render)
        return render_hookfirst(clip, tip, part, dst)
    path = clip["path"]; dur = clip.get("dur") or duration(path)
    w, h = listicle.probe_wh(path)
    s, e = pick_window(dur, clip["payoff"])
    L = e - s
    p0 = min(max(s + 2.2, clip["payoff"] - 0.3), e - C("secrets_endcard_seconds", 2.5) - 0.6)
    p1 = min(p0 + C("secrets_punch_seconds", 1.5), e - 0.3)
    cam = clip.get("cam") if w > h else None
    segs = [(s, p0, False), (p0, p1, True), (p1, e, False)]
    gh_out = 1220 if cam else H
    if cam: cam_r, game_r = cam_layout(w, h, cam)
    else: game_r = full_layout(w, h)
    fc = []
    for i, (a, b, punch) in enumerate(segs):
        nf = max(1, int(round((b - a) * FPS)))
        fc.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS,fps={FPS},split=2[a{i}][b{i}];")
        if cam:
            x, y, cw, ch = cam_r
            fc.append(f"[a{i}]crop={cw}:{ch}:{x}:{y},scale=1080:700:flags=lanczos,eq=saturation=1.1,setsar=1[cam{i}];")
        else:
            fc.append(f"[a{i}]nullsink;")
        x, y, gw, gh = game_r
        g = f"[b{i}]crop={gw}:{gh}:{x}:{y},scale=1080:{gh_out}:flags=lanczos,eq=contrast=1.06:saturation=1.15"
        if punch:
            g += (f",zoompan=z='min(1+0.28*on/12,1.28)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x{gh_out}:fps={FPS}"
                  f",fade=t=in:st=0:d=0.18:color=white")
        g += f",trim=end_frame={nf},setsar=1[g{i}];"
        fc.append(g)
        fc.append(f"[cam{i}][g{i}]vstack[v{i}];" if cam else f"[g{i}]null[v{i}];")
        fc.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,aresample=48000[au{i}];")
    fc.append("".join(f"[v{i}][au{i}]" for i in range(3)) + "concat=n=3:v=1:a=1[vc][ac];")
    # ---- text (output timeline: 0..L)
    hook_t = C("secrets_hook_seconds", 2.0)
    ec0 = L - C("secrets_endcard_seconds", 2.5)
    po = p0 - s
    banner = f"GTA 6 SECRETS · PART {part}"
    bsz = 62; bwid = text_w(banner, bsz)
    by = 48
    txt = [f"drawbox=x={int((W - bwid) / 2 - 34)}:y={by - 16}:w={int(bwid + 68)}:h={bsz + 40}:color={YELLOW}@1:t=fill:enable='lt(t,{ec0:.2f})'",
           dt("banner", banner, by + 2, bsz, "black", 0, ec0, bw=0).replace("shadowy=6", "shadowy=0")]
    hook_y = 712 if cam else 700
    hl = split2(tip["hook"].upper())
    for j, line in enumerate(hl[:2]):
        lines, sz = wrap(line, 104 if j else 84, 980, 1)
        txt.append(dt(f"hook{j}", lines[0], hook_y + (0 if j == 0 else 96), sz, "white" if j == 0 else YELLOW, 0, hook_t))
    cap_y = 1330 if cam else 1240
    for name, label, body, t0, t1 in (("where", "WHERE", tip["where"], hook_t, po + 0.05), ("what", "WHAT YOU GET", tip["what"], po + 0.05, ec0)):
        if t1 - t0 < 0.4: continue
        txt.append(dt(f"{name}L", label, cap_y, 56, YELLOW, t0, t1, bw=8))
        lines, sz = wrap(body.upper(), 84, 960, 2)
        for j, ln in enumerate(lines):
            txt.append(dt(f"{name}{j}", ln, cap_y + 74 + j * int(sz * 1.15), sz, "white", t0, t1))
    txt.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.55:t=fill:enable='gte(t,{ec0:.2f})'")
    txt.append(dt("ec1", "FOLLOW FOR", 760, 96, "white", ec0, L + 1))
    txt.append(dt("ec2", f"PART {part + 1}", 880, 170, YELLOW, ec0, L + 1, bw=12))
    credit = f"clip: {clip.get('credit') or clip.get('uploader') or 'streamer'}"
    txt.append(dt("credit", credit, 640 if cam else 160, 34, "white@0.9", 0, L + 1, bw=4, x="36"))
    fc.append("[vc]" + ",".join(txt) + ",format=yuv420p[vo];[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    (WORK / "filter.txt").write_text("".join(fc))
    sh("ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex_script", WORK / "filter.txt", "-map", "[vo]", "-map", "[ao]",
       "-t", f"{L:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high",
       "-r", FPS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", dst, timeout=1800)
    return {"start": s, "end": e, "seconds": round(L, 2), "payoff_at": round(po, 2), "layout": "facecam-top" if cam else "fullframe"}

# ------------------------------------------------------------------------------------------------------------- copy
def make_copy(tip, clip, part):
    t = re.sub(r"\s+", " ", (tip.get("yt_title") or f"GTA 6 Secret: {tip['title']}")).strip()
    if "gta 6" not in t.lower() and "gta vi" not in t.lower(): t = "GTA 6: " + t
    while len(t) > 59: t = t.rsplit(" ", 1)[0].rstrip(":,-–")
    title = t + " #Shorts"
    who = clip.get("uploader") or "the streamer"
    clip_url = clip.get("url", "")
    share = ""
    try:
        import hookfirst
        if hookfirst.enabled(CFG): share = hookfirst.cta_sentence(secrets_cta(tip)) + "\n"
    except Exception:
        share = ""
    desc = (f"{tip['summary'].strip()}\n\n{share}GTA 6 Secrets · Part {part}. Follow for Part {part + 1} 🔔\n\n"
            f"🎥 Clip: {who} {clip_url} (all credit to the streamer)\n📖 Guide source: {tip['source_url']}\n\n"
            "Fan page. Not affiliated with Rockstar Games.\n#GTA6 #GTA6Secrets #GTAVI #GTA6Tips #Shorts")
    kw = [k.lower() for k in tip["keywords"]][:4]
    tags = list(dict.fromkeys(["gta 6", "gta vi", "gta 6 secrets", "gta 6 tips", "gta 6 guide", "gta 6 hidden", "gta 6 money",
                               "gta 6 gameplay", "grand theft auto 6", "vice city", "leonida"] + [f"gta 6 {k}" for k in kw]))[:15]
    hashtags = "#GTA6 #GTA6Secrets #GTAVI #GTA6Tips #GTA6Gameplay #GrandTheftAuto6 #ViceCity #Leonida #GTA #Gaming #GamingTips #Reels"
    ig = (f"GTA 6 SECRETS · PART {part}: {tip['title']}\n\n{tip['summary'].strip()}\n\n"
          f"📍 WHERE: {tip['where']}\n🎁 WHAT YOU GET: {tip['what']}\n\n🎥 Clip: {who} ({clip.get('credit') or clip_url}), all credit to the streamer\n"
          f"📖 Source: {tip['source_url']}\nFan page. Not affiliated with Rockstar Games.\n\n{hashtags}\n\n"
          f"{share}Save this for later 🔖 What should Part {part + 1} be?")
    return title, desc, tags, ig[:2200]

# ------------------------------------------------------------------------------------------------------------- lock
def gitsave(msg, *args):
    return subprocess.run([sys.executable, str(HERE.parent / "common" / "gitsave.py"), msg, *args], cwd=HERE.parent).returncode

def lock_footage():
    m = json.loads((OUT / "metadata.json").read_text())
    f = read_json(USED_F, [])
    f += [u for u in m.get("footage", []) if u not in f]
    USED_F.write_text(json.dumps(f, indent=0))
    sys.exit(0 if gitsave("gtaclips: secrets preview footage used", "--union", "gtaclips/secrets_used.json") == 0 else 1)

def lock():
    m = json.loads((OUT / "metadata.json").read_text())
    if m.get("test_only"): log("refusing to lock a --no-llm / hand-made test video"); sys.exit(4)
    topics = newest(TOPICS_F, [])
    key = "topic:" + m["topic"]
    posted = int(newest(SERIES_F, {"part": 0}).get("part", 0))
    if key in topics: log(f"topic already posted: {m['topic']!r}"); sys.exit(4)
    if m["part"] <= posted: log(f"Part {m['part']} was already posted (series is at {posted}); re-run to make Part {posted + 1}"); sys.exit(4)
    t = read_json(TOPICS_F, []); t += [x for x in (key, "url:" + m["guide_url"]) if x not in t]
    TOPICS_F.write_text(json.dumps(t, indent=0))
    f = read_json(USED_F, []); f += [u for u in m.get("footage", []) if u not in f]
    USED_F.write_text(json.dumps(f, indent=0))
    SERIES_F.write_text(json.dumps({"part": m["part"], "last_title": m["title"], "last_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}, indent=1))
    rc = gitsave(f"gtaclips: secrets Part {m['part']} {m['topic'][:40]}", "--union", "gtaclips/secrets_topics.json", "gtaclips/secrets_used.json",
                 "--mirror", "gtaclips/secrets_series.json")
    sys.exit(0 if rc == 0 else 1)

# ------------------------------------------------------------------------------------------------------------- main
def write_outputs(tip, clip, part, info, test_only=False):
    title, desc, tags, ig = make_copy(tip, clip, part)
    meta = {"mode": "secrets", "part": part, "topic": tip["title"], "title": title, "description": desc, "tags": tags,
            "ig_caption": ig, "hook": tip["hook"], "tip": tip, "guide_url": tip["source_url"],
            "source_url": clip.get("url", ""), "sources": [tip["source_url"]], "footage": [clip.get("url", "")] if clip.get("url") else [],
            "uploader": clip.get("uploader", ""), "clip_title": clip.get("title", ""), "seconds": info["seconds"], "window": info,
            "visual_safety": clip.get("visual_safety", "unchecked"), "speech_check": clip.get("speech", ""),
            "vision": {k: v for k, v in (clip.get("vision") or {}).items() if k != "times"}, "layout": info["layout"], "test_only": test_only,
            "structure": info.get("structure", "classic"), "key_at": info.get("key_at"), "share_cta": info.get("share_cta", "")}
    json.dump(meta, open(OUT / "metadata.json", "w"), indent=1, ensure_ascii=False)
    json.dump({"pass": not test_only and "verbatim" in tip.get("verified", ""), "verified": tip.get("verified", ""),
               "quote": tip.get("quote"), "source_url": tip["source_url"]}, open(OUT / "tip_check.json", "w"), indent=1, ensure_ascii=False)
    return meta

def main():
    global NO_LLM
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true"); ap.add_argument("--lock-footage", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="TEST ONLY: no Gemini (heuristic tip, unchecked footage)")
    ap.add_argument("--dry-run", action="store_true", help="tip sourcing + clip ranking only")
    ap.add_argument("--tip-file"); ap.add_argument("--clip-file"); ap.add_argument("--clip-url", default="")
    ap.add_argument("--uploader", default=""); ap.add_argument("--payoff", type=float); ap.add_argument("--part", type=int)
    a = ap.parse_args()
    if a.lock: return lock()
    if a.lock_footage: return lock_footage()
    NO_LLM = a.no_llm
    shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True); OUT.mkdir(exist_ok=True)
    for f in ("short.mp4", "short_x.mp4", "metadata.json", "gate.json", "youtube_link.txt", "upload_route.txt", "upload_notes.txt",
              "tip_check.json", "thumb.jpg"):
        (OUT / f).unlink(missing_ok=True)

    if a.tip_file and a.clip_file:   # hand-written tip + local clip: layout test, never postable
        import layout
        tip = json.loads(pathlib.Path(a.tip_file).read_text())
        tip.setdefault("verified", "hand-written test tip"); tip.setdefault("source_url", "local-test")
        p = pathlib.Path(a.clip_file); d = duration(p)
        login = re.search(r"twitch\.tv/([^/]+)/clip", a.clip_url)
        clip = {"path": p, "dur": d, "payoff": a.payoff if a.payoff is not None else d * 0.6, "url": a.clip_url,
                "uploader": a.uploader or p.stem, "credit": f"twitch.tv/{login.group(1)}" if login else (a.uploader or p.stem),
                "cam": layout.find_facecam(p), "visual_safety": "unchecked (local test)"}
        part = a.part or next_part()
        info = render(clip, tip, part, WORK / "final.mp4"); shutil.copy(WORK / "final.mp4", OUT / "short.mp4")
        write_outputs(tip, clip, part, info, test_only=True)
        print("made (TEST)", OUT / "short.mp4", info); return

    if not NO_LLM and not os.environ.get("GEMINI_API_KEY"):
        log("GEMINI_API_KEY is missing. Add it under repo Settings > Secrets > Actions; tips and footage can't be verified without it.")
        sys.exit(1)
    used = used_topics(); used_keys = used_clip_keys()
    part = next_part()
    log(f"next: PART {part}; {len(used)} topics done, {len(used_keys)} clips already used")
    reasons = []
    for tip in find_tips(used):
        if a.dry_run:
            cands = ranked_clips(tip, used_keys)
            print(json.dumps({"tip": tip, "clips": [{k: c.get(k) for k in ("url", "title", "uploader", "matched", "views", "duration")} for c in cands]},
                             indent=1, ensure_ascii=False))
            return
        try:
            clip = find_footage(tip, used_keys)
        except FootageError as e:
            reasons.append(str(e)); log("NO FOOTAGE:", e); continue
        info = render(clip, tip, part, WORK / "final.mp4")
        shutil.copy(WORK / "final.mp4", OUT / "short.mp4")
        meta = write_outputs(tip, clip, part, info, test_only=NO_LLM)
        print("made", OUT / "short.mp4", meta["seconds"], "s", "PART", part, meta["title"]); return
    (OUT / "no_secret.txt").write_text("; ".join(reasons) or "no guide tip passed the verbatim-quote check")
    if reasons:
        log("no matching clean clip for any verified tip:", "; ".join(reasons)); sys.exit(3)
    log("no verified tip today (every candidate failed the quote/number checks or no guide articles were reachable)"); sys.exit(5)

if __name__ == "__main__":
    main()
