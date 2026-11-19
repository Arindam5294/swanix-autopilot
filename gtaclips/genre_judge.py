#!/usr/bin/env python3
"""Genre judge: decides from what a clip SHOWS and SAYS (not its title) whether it really fits GTA_GENRE.

  Gemini watches + listens to a small copy of each candidate clip (video + audio inline) and scores it 0-10 for the genre,
  says what actually happens, and marks the beats (punchline, laugh, fail, impact...) with timestamps.
  No Gemini key / Gemini down -> Whisper transcript + title go to the text AI instead (stricter cap, needs real speech).

  pick(cands)      -> the best clip that truly fits (score >= GENRE_MIN_SCORE, default 7) or None. Never a silent fallback.
  annotate(clip)   -> adds beats / key moment to a clip you chose yourself (never rejects it).
  judge_file(path, meta, genre) -> the raw verdict dict (used by episode.py for each downloaded clip).

Fields added to the chosen clip: genre, genre_score, genre_why, what_happens, genre_beats [{t, type, strength}],
key_at (the best moment, used by hookfirst for the cold open + replay), said (key line, for the script)."""
import base64, json, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
import genre

WORK = pathlib.Path(os.environ.get("GENRE_WORK", "/tmp/genre_judge"))
MIN_SCORE = float(os.environ.get("GENRE_MIN_SCORE", "7"))
MAX_CHECK = int(os.environ.get("GENRE_MAX_CHECK", "10"))
BEAT_TYPES = ("punchline", "laugh", "fail", "impact", "crash", "shock", "awkward", "scream", "win", "chase", "scare", "sad")

DEFS = {
    "funny": "it makes viewers laugh: the streamer or friends laughing hard, jokes, trolling, goofy or absurd NPC/physics moments, "
             "ragdolls, ridiculous fails, awkward or chaotic moments with a comedic payoff",
    "rage": "the streamer genuinely loses it: yelling, screaming, raging at the game, slamming the desk",
    "racing": "high-speed driving: races, drifts, near misses, big jumps, vehicle crashes at speed",
    "police": "police action: cop chases, escaping a wanted level, arrests, shootouts with police",
    "chaos": "pure mayhem: explosions, rampages, huge crashes, big shootouts, destruction",
    "fails": "something goes badly wrong: crashes, deaths, missed jumps, plans backfiring, embarrassing mistakes",
    "wtf": "a bizarre or unbelievable moment that makes you say 'what?!'",
    "clutch": "a skillful play: perfect escape, insane shot, last-second save, pro-level move",
    "scary": "a genuinely scary moment: jump scare, creepy encounter, the streamer gets frightened",
    "emotional": "a heartfelt, wholesome or touching moment",
}
NOT_DEFS = {
    "funny": "tense or serious scenes, plain driving or walking around, dark/creepy moments, cutscenes, menus, the streamer just talking calmly",
}

def log(*a): print("genre_judge:", *a, file=sys.stderr, flush=True)

def definition(g):
    keys = [k for k in re.split(r"[\s,/+]+", g) if k in DEFS]
    words = [w for w in re.split(r"[\s,/+]+", g) if w and w not in DEFS]
    d = "; ".join(f"{k.upper()} = {DEFS[k]}" for k in keys)
    if words: d += ("; " if d else "") + f"it must clearly match: {' '.join(words)}"
    nots = "; ".join(NOT_DEFS[k] for k in keys if k in NOT_DEFS)
    return d, nots

def sh(*a, timeout=300):
    r = subprocess.run(list(map(str, a)), capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(r.stderr[-400:])
    return r.stdout

def duration(p):
    try: return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).strip())
    except Exception: return 0.0

def download(url, dst):
    """Small copy for judging (<=720p). Same proxy routes as make_short.py."""
    base = ["yt-dlp", "-q", "--no-playlist", "--no-part", "-f", "b[height<=720]/bv*[height<=720]+ba/b", "--merge-output-format", "mp4", "-o", str(dst)]
    routes = ([os.environ["YTDLP_HOME_PROXY"]] if os.environ.get("YTDLP_HOME_PROXY") else []) + [None] \
             + ([os.environ["YTDLP_PROXY"]] if os.environ.get("YTDLP_PROXY") else [])
    for px in routes:
        try:
            sh(*(base + (["--proxy", px] if px else []) + [url]), timeout=240)
            if dst.exists() and dst.stat().st_size > 100000: return dst
        except Exception as e:
            log(f"download failed ({px or 'direct'}) {url}: {str(e).strip().splitlines()[-1][:120] if str(e).strip() else e}")
    return None

def proxy(src, dst, max_s=120):
    """360p 10 fps copy with the audio kept, small enough to send inline to Gemini (< ~8 MB)."""
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-t", str(max_s), "-vf", "scale=-2:360,fps=10", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "33", "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "32000", "-movflags", "+faststart", dst)
    return dst

def gemini(parts, max_tokens=2048):
    key = os.environ.get("GEMINI_API_KEY")
    if not key: raise RuntimeError("no GEMINI_API_KEY")
    models = [m for m in dict.fromkeys([os.environ.get("GENRE_MODEL"), os.environ.get("GATE_MODEL"), "gemini-2.5-flash", "gemini-2.0-flash"]) if m]
    err = ""
    for attempt in range(4):
        model = models[min(attempt // 2, len(models) - 1)]
        cfg = {"temperature": 0, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
        if model.startswith("gemini-2.5"): cfg["thinkingConfig"] = {"thinkingBudget": 1024}
        try:
            rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                        json.dumps({"contents": [{"parts": parts}], "generationConfig": cfg}).encode(),
                                        {"Content-Type": "application/json", "x-goog-api-key": key})
            d = json.loads(urllib.request.urlopen(rq, timeout=180).read())
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
            return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except urllib.error.HTTPError as e:
            err = f"{model} HTTP {e.code}: {e.read()[:200]!r}"
            time.sleep(20 if e.code == 429 else 4)
        except Exception as e:
            err = f"{model}: {str(e)[:160]}"; time.sleep(4)
    raise RuntimeError(f"Gemini failed: {err}")

JUDGE_Q = """You are a strict editor for a GTA 6 YouTube Shorts channel. Watch AND listen to this whole clip
(a Twitch/Kick/YouTube stream clip; title "{title}", by {who}). The title can be wrong: judge ONLY what you see and hear.

GENRE WE NEED: "{genre}". It fits when {defn}.{nots}

Score 0-10: 10 = a perfect, laugh-out-loud / unmistakable example of the genre; 7 = clearly fits, a viewer would agree;
4 = loosely related; 0 = not this genre at all. Most random clips are 0-4. Be harsh: only 7+ gets posted.

beats: the moments a meme editor would hit with a sound effect, seconds from the start of THIS clip (0 to {dur:.0f}),
type one of {types}, strength 1-3 (3 = the big one). best_t = the single best moment (the payoff), in seconds.
safe = false if it shows full nudity, a sexual act, gore, a big stream alert/donation/sub overlay, a slur, or is not GTA 6 footage.
Swimwear/shirtless/webcam is fine.

Reply ONLY JSON: {{"score": 0-10, "what_happens": "one plain sentence", "why": "why it fits or not", "said": "the key line spoken (or empty)",
"best_t": seconds, "beats": [{{"t": seconds, "type": "...", "strength": 1-3}}], "safe": true|false}}"""

def _clean(v, dur):
    try: v["score"] = max(0.0, min(10.0, float(v.get("score", 0))))
    except Exception: v["score"] = 0.0
    beats = []
    for b in v.get("beats") or []:
        try:
            t = float(b.get("t")); typ = str(b.get("type", "impact")).lower().strip()
            if 0 <= t <= (dur or 9999): beats.append({"t": round(t, 2), "type": typ if typ in BEAT_TYPES else "impact",
                                                       "strength": max(1, min(3, int(b.get("strength", 2))))})
        except Exception: pass
    v["beats"] = sorted(beats, key=lambda b: b["t"])
    try:
        bt = float(v.get("best_t"))
        v["best_t"] = round(bt, 2) if 0 < bt <= (dur or 9999) else None
    except Exception: v["best_t"] = None
    if v["best_t"] is None and beats: v["best_t"] = max(beats, key=lambda b: b["strength"])["t"]
    return v

def judge_gemini(path, meta, g):
    dur = duration(path)
    WORK.mkdir(parents=True, exist_ok=True)
    small = proxy(path, WORK / (path.stem + "_judge.mp4"))
    if small.stat().st_size > 15_000_000: raise RuntimeError("clip too big to judge inline")
    defn, nots = definition(g)
    q = JUDGE_Q.format(title=(meta.get("title") or "")[:150].replace('"', "'"), who=meta.get("uploader") or "a streamer", genre=g,
                       defn=defn, nots=(f" It does NOT fit when it is mostly: {nots}." if nots else ""), dur=dur, types="|".join(BEAT_TYPES))
    v = gemini([{"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(small.read_bytes()).decode()}}, {"text": q}])
    v = _clean(v, dur); v["by"] = "gemini-video"; v["dur"] = dur
    return v

def judge_text(path, meta, g):
    """No vision: Whisper transcript + title -> text AI. Needs real speech; capped at 8 because it cannot see."""
    import captions
    from ai import ask
    ws = captions.words(path, model="tiny.en")
    if len(ws) < 8: raise RuntimeError("too little speech to judge without video")
    dur = duration(path)
    lines = " ".join(f"[{s:.1f}] {t}" for s, _, t in ws)[:3500]
    defn, nots = definition(g)
    out = ask(f"""Judge this GTA 6 stream clip for the genre "{g}" ({defn}{'; NOT: ' + nots if nots else ''}) from its transcript only.
Title (may be wrong): {meta.get('title','')}. Transcript with second marks: {lines}
Be harsh. Reply ONLY JSON: {{"score": 0-10, "what_happens": "...", "why": "...", "said": "key line", "best_t": seconds,
"beats": [{{"t": seconds, "type": "{'|'.join(BEAT_TYPES)}", "strength": 1-3}}], "safe": true}}""", min_len=20)
    v = _clean(json.loads(re.search(r"\{.*\}", out, re.S).group(0)), dur)
    v["score"] = min(v["score"], 8.0); v["by"] = "transcript"; v["dur"] = dur
    return v

def judge_file(path, meta, g=None):
    g = g or genre.name() or "most engaging"
    path = pathlib.Path(path)
    try:
        return judge_gemini(path, meta, g)
    except Exception as e:
        log("video judge unavailable:", str(e)[:200])
    try:
        return judge_text(path, meta, g)
    except Exception as e:
        log("transcript judge unavailable:", str(e)[:200])
    return None

def _apply(m, v, g):
    m.update({"genre": g, "genre_score": v["score"], "genre_why": v.get("why", "")[:300], "what_happens": v.get("what_happens", "")[:300],
              "genre_beats": v["beats"][:24], "genre_judge": v.get("by", "")})
    if v.get("best_t") is not None and not m.get("key_at"): m["key_at"] = v["best_t"]
    said = (v.get("said") or "").strip()
    if said and not m.get("said"): m["said"] = said[:400]
    if v.get("what_happens"):   # the script writer gets what really happens, so the hook matches the clip
        m["desc"] = f"What actually happens in the clip: {v['what_happens']}\n" + (m.get("desc") or "")
    return m

def judgeable(m):
    d = m.get("duration") or 0
    return m.get("kind") != "live" and (m.get("kind") == "twitch_clip" or (0 < d <= 120))

def pick(cands, g=None):
    """cands sorted best-first. Returns the clip that truly fits, or None (the caller must NOT fall back to a random clip)."""
    g = g or genre.name()
    if not g: return cands[0] if cands else None
    rx = genre.pattern(g)
    cands = [m for m in cands if judgeable(m)]
    titled = [m for m in cands if rx and rx.search(genre.text_of(m))]
    order = titled + [m for m in cands if m not in titled]
    order = order[:MAX_CHECK]
    log(f"genre '{g}': judging {len(order)} clips by what they show ({len(titled)} have genre words in the title)")
    if not order: return None
    WORK.mkdir(parents=True, exist_ok=True)
    def dl(ix_m):
        i, m = ix_m
        return i, download(m["url"], WORK / f"c{i}.mp4")
    best, report = None, []
    with ThreadPoolExecutor(3) as ex:
        for i, p in ex.map(dl, list(enumerate(order))):
            m = order[i]
            if not p: report.append({"url": m["url"], "result": "download failed"}); continue
            v = judge_file(p, m, g)
            if not v: report.append({"url": m["url"], "result": "could not judge"}); continue
            ok = v["score"] >= MIN_SCORE and v.get("safe", True) is not False
            log(f"{'FITS' if ok else 'no  '} {v['score']:.0f}/10 {m.get('uploader','')}: {(v.get('what_happens') or '')[:110]}")
            report.append({"url": m["url"], "title": m.get("title", ""), "score": v["score"], "safe": v.get("safe"), "what": v.get("what_happens", ""), "why": v.get("why", "")})
            if ok and (not best or v["score"] > best[1]["score"]): best = (m, v)
            if best and best[1]["score"] >= 9: break
    try:
        pathlib.Path(HERE / "out").mkdir(exist_ok=True)
        (HERE / "out" / "genre_report.json").write_text(json.dumps({"genre": g, "min_score": MIN_SCORE, "checked": report}, indent=1))
    except Exception: pass
    if not best:
        log(f"no clip truly fits '{g}' (needs {MIN_SCORE:.0f}+/10)."); return None
    return _apply(dict(best[0]), best[1], g)

def annotate(m, g=None):
    """A clip you chose yourself: never rejected, just gets beats + the key moment for the edit and the sound effects."""
    g = g or genre.name()
    if not g or not judgeable(m): return m
    WORK.mkdir(parents=True, exist_ok=True)
    p = download(m["url"], WORK / "manual.mp4")
    v = judge_file(p, m, g) if p else None
    if v:
        log(f"your clip scores {v['score']:.0f}/10 for '{g}': {(v.get('what_happens') or '')[:120]}")
        _apply(m, v, g)
    return m

def tell_no_fit():
    """Telegram message when strict genre mode found nothing: what the judge saw in the top clips."""
    g = genre.name()
    try: rep = json.loads((HERE / "out" / "genre_report.json").read_text())
    except Exception: rep = {"checked": []}
    seen = [r for r in rep.get("checked", []) if "score" in r]
    lines = "\n".join(f"- {r['score']:.0f}/10: {(r.get('what') or r.get('title') or '')[:90]}" for r in sorted(seen, key=lambda r: -r["score"])[:4])
    msg = (f"GTA VI Clips: no clip really fit '{g}' this run, so nothing was made (no wrong video sent). "
           f"The judge watched {len(seen)} clips, a clip needs {MIN_SCORE:.0f}+/10." + (f"\nBest it saw:\n{lines}" if lines else "") +
           "\nTry again in a few hours, or paste a clip link in the url box.")
    from notify import telegram
    telegram(msg)

if __name__ == "__main__" and sys.argv[1:2] == ["--tell-no-fit"]:
    tell_no_fit(); sys.exit(0)

if __name__ == "__main__":   # test: python genre_judge.py clip.mp4 funny
    print(json.dumps(judge_file(sys.argv[1], {"title": sys.argv[3] if len(sys.argv) > 3 else ""}, sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
