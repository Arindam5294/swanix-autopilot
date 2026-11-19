#!/usr/bin/env python3
"""LISTICLE MODE: fast "5 crazy things in GTA 6" Shorts (1080x1920, 30 fps, 55-68 s), end to end:
  topic (rotates, never repeats) -> sourced facts (Wikipedia GTA VI page + Rockstar + news, fetched live)
  -> script from the free AI chain (Gemini -> Groq -> OpenRouter, common/ai.py), every item tied to a quote
  -> FACT GATE (an item whose claim is not in the fetched sources = no video, exit 6)
  -> edge-tts voice (rate ~+25%), word timings, silences squeezed out
  -> montage of REAL GTA 6 gameplay from Twitch/Kick clips (find_clip.twitch_clips/kick_clips, keyless),
     cropped to fill 9:16 (never letterboxed), facecam avoided (layout.find_facecam), new cut every 0.6-1.5 s,
     no segment used twice, no shot over 3.5 s
  -> Montserrat Black Italic text: top title, green "1." + white label per item, 3-word captions at ~74%
     with keywords in neon green #39FF3C (good) / red #FF2E2E (bad)
  -> optional quiet no-vocal music bed (~7 dB under the voice) if one is configured, else none
  -> out/short.mp4 + out/metadata.json (+ out/fact_check.json, out/sources.json) for gate/send_short/upload.

Usage:
  python listicle.py [--topic "how the wanted system works in GTA 6"]     make out/short.mp4
  python listicle.py --lock                                                 mark out/metadata.json's topic+footage used (git)
Test switches: --script-file s.json (skip the LLM writer), --footage-dir DIR (local mp4s instead of Twitch/Kick),
               --skip-verifier (no LLM fact verifier, deterministic checks still run), --music FILE.
Exit codes: 0 made | 3 no usable footage | 6 script failed the fact gate / AI refusal (nothing made) | 1 other error."""
import argparse, asyncio, datetime, difflib, hashlib, html, json, os, pathlib, random, re, shutil, subprocess, sys
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
CFG = json.load(open(HERE / "config.json"))
# listicle settings live in listicle.json (no merge clash with config.json); a listicle_* key in config.json wins
if (HERE / "listicle.json").exists():
    CFG = {**json.load(open(HERE / "listicle.json")), **CFG}
OUT = HERE / "out"
WORK = pathlib.Path(os.environ.get("LISTICLE_WORK", "/tmp/gtalisticle"))
USED_F = HERE / "listicle_used.json"
FONT_DIR = HERE / "fonts"
FONT_FILE = FONT_DIR / "Montserrat-BlackItalic.ttf"
FONT_URL = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-BlackItalic.ttf"
FONT_NAME = "Montserrat Black"
W, H, FPS = 1080, 1920, 30
GREEN, RED, WHITE = "&H003CFF39&", "&H002E2EFF&", "&H00FFFFFF&"   # ASS is &HAABBGGRR: #39FF3C, #FF2E2E
C = lambda k, d: CFG.get(k, d)

DEFAULT_TOPICS = [
    "5 crazy things in GTA 6 you couldn't do in GTA 5",
    "5 realistic details in GTA 6",
    "how the wanted system works in GTA 6",
    "5 things to know about Leonida, the GTA 6 map",
    "5 things Jason and Lucia can do together in GTA 6",
    "5 ways GTA 6 makes your character feel alive",
    "5 free-roam activities in GTA 6",
    "5 ways GTA 6 makes fun of real life",
]
NUM_WORDS = ["one", "two", "three", "four", "five", "six", "seven"]
NSFW = ["racist", "slur", "n word", "f slur", "gets called"]

def log(*a): print("listicle:", *a, file=sys.stderr, flush=True)

def sh(*a, **k):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, **k)
    if r.returncode: raise RuntimeError(" ".join(map(str, a))[:200] + "\n" + r.stderr[-1500:])
    return r.stdout

def duration(p):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).strip())

def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()

# ----------------------------------------------------------------------------------------------------- used / topic
def load_used():
    """listicle_used.json from this checkout + the newest copy on GitHub (re-runs of old runs check out old copies)."""
    used = json.loads(USED_F.read_text()) if USED_F.exists() else []
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "main"], cwd=HERE, timeout=60, capture_output=True)
        r = subprocess.run(["git", "show", "FETCH_HEAD:gtaclips/listicle_used.json"], cwd=HERE, capture_output=True, text=True, timeout=30)
        if r.returncode == 0: used += [u for u in json.loads(r.stdout) if u not in used]
    except Exception as e:
        log("couldn't read newest listicle_used.json:", str(e)[:120])
    return used

def pick_topic(used):
    done = [u[6:] for u in used if u.startswith("topic:")]
    done_n = {norm(t) for t in done}
    topics = C("listicle_topics", DEFAULT_TOPICS)
    for t in topics:
        if norm(t) not in done_n: return t
    # every seed angle used: ask the AI for a fresh angle in the same style; else reuse the oldest one
    try:
        from ai import ask
        txt = ask("Suggest ONE new YouTube Shorts listicle angle about GTA 6 gameplay in the style of: "
                  + "; ".join(topics[:3]) + ". It must be different from all of these already-used angles: "
                  + "; ".join(done[-40:]) + ". It must be answerable from the Wikipedia article about Grand Theft Auto VI "
                  "(gameplay, map, characters, technology). No story spoilers, no leaks. Reply with the angle only, under 12 words.", min_len=10)
        t = txt.strip().strip('"').splitlines()[0][:90]
        if t and norm(t) not in done_n: return t
    except Exception as e:
        log("new-angle AI failed:", str(e)[:150])
    order = {norm(t): i for i, t in enumerate(done)}
    return min(topics, key=lambda t: order.get(norm(t), -1))

# ------------------------------------------------------------------------------------------------------------ sources
UA = {"User-Agent": "gtaclips-listicle/1.0 (GTA 6 fan channel bot; github.com/Arindam5294/swanix-autopilot)"}

def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def strip_html(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)\b.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

WIKI_KEEP = ("gameplay", "setting and characters", "research and design", "technology", "music")

def wiki_source(title="Grand_Theft_Auto_VI"):
    d = json.loads(fetch("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "prop": "extracts", "explaintext": 1, "format": "json", "redirects": 1, "titles": title})))
    text = list(d["query"]["pages"].values())[0].get("extract", "")
    parts = re.split(r"\n(==+ ?[^=\n]+ ?==+)\n", "\n" + text)
    keep = [parts[0].strip()[:1200]]                       # lead (release facts)
    for i in range(1, len(parts) - 1, 2):
        head = parts[i].strip("= ").strip().lower()
        if any(head.startswith(k) for k in WIKI_KEEP) and "leak" not in head:
            keep.append(f"{head.title()}: " + parts[i + 1].strip())
    return {"url": "https://en.wikipedia.org/wiki/" + title, "title": "Wikipedia: Grand Theft Auto VI",
            "text": "\n".join(k for k in keep if k)}

def news_source(topic):
    """Google News RSS headlines + snippets for the topic (fact hints from real outlets; snippets only)."""
    q = "GTA 6 " + " ".join(w for w in norm(topic).split() if w not in STOP and len(w) > 2)
    x = fetch("https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    items = re.findall(r"(?s)<item>.*?<title>(.*?)</title>.*?<description>(.*?)</description>", x)[:12]
    lines = [strip_html(html.unescape(t)) + ". " + strip_html(html.unescape(d))[:300] for t, d in items]
    return {"url": "https://news.google.com/search?q=" + urllib.parse.quote(q), "title": "News headlines: " + q, "text": "\n".join(lines)}

def page_source(url):
    t = strip_html(fetch(url))
    if len(t) < 400 or not re.search(r"grand theft auto vi|gta ?(6|vi)\b", t, re.I): raise RuntimeError("no usable text")
    return {"url": url, "title": url, "text": t[:12000]}

STOP = set("the a an and or of in on to for with how what why is are you your it its this that from by at be can do does "
           "did in into about crazy couldn wouldn didn things thing things couldn't couldnt could not new ways way 5 6 gta".split())

def relevant(text, topic, limit):
    """Keep the paragraphs that best match the topic, in their original order, up to `limit` characters."""
    kw = {w for w in norm(topic).split() if w not in STOP and len(w) > 2}
    syn = {"wanted": ["police", "cops", "crime", "chase", "criminal", "law"], "realistic": ["detail", "real", "weight", "social"],
           "map": ["leonida", "vice city", "keys", "grassrivers", "world", "larger"], "together": ["jason", "lucia", "relationship", "split"]}
    for k, v in syn.items():
        if k in kw: kw |= set(v)
    paras = [p.strip() for p in re.split(r"\n+|(?<=[.!?]) (?=[A-Z])", text) if len(p.strip()) > 40]
    scored = sorted(range(len(paras)), key=lambda i: -sum(norm(paras[i]).count(k) for k in kw))
    pick, n = set(), 0
    for i in scored:
        if n + len(paras[i]) > limit: continue
        pick.add(i); n += len(paras[i])
    return "\n".join(paras[i] for i in sorted(pick))

def gather_sources(topic):
    srcs = []
    jobs = [("wikipedia", wiki_source)] + [(u, (lambda u=u: page_source(u))) for u in C("listicle_source_urls", [
        "https://www.rockstargames.com/VI", "https://www.rockstargames.com/newswire"])] + [("news", lambda: news_source(topic))]
    for name, fn in jobs:
        try:
            s = fn()
            if len(s["text"]) > 200: srcs.append(s); log(f"source ok: {name} ({len(s['text'])} chars)")
            else: log(f"source empty: {name}")
        except Exception as e:
            log(f"source failed: {name}: {str(e)[:120]}")
    if not srcs or not any("wikipedia" in s["url"] for s in srcs):
        raise RuntimeError("no fact source could be fetched (Wikipedia down?) - refusing to write an unsourced script")
    budget = C("listicle_source_chars", 14000)
    for i, s in enumerate(srcs):
        s["id"] = f"S{i+1}"
        s["full"] = s["text"]
        lim = int(budget * 0.65) if "wikipedia" in s["url"] else int(budget * 0.35 / max(1, len(srcs) - 1))
        s["text"] = relevant(s["text"], topic, lim) if len(s["text"]) > lim else s["text"]
    return srcs

# ------------------------------------------------------------------------------------------------------------- script
REFUSAL = re.compile(r"no source (was )?provided|cannot be generated|can(no|')t (generate|create|write|help)|unable to (generate|create|write)|"
                     r"please (provide|paste|share)|as an ai\b|i('m| am) sorry|insufficient (information|details)|placeholder|lorem", re.I)

def script_prompt(topic, srcs, n, feedback=""):
    src = "\n\n".join(f"[{s['id']}] {s['title']} ({s['url']})\n{s['text']}" for s in srcs)
    lo, hi = C("listicle_min_words", 250), C("listicle_max_words", 290)
    return f"""You write the voiceover script for a fast, punchy YouTube Short about GTA 6 (a fan channel, not Rockstar).
TOPIC / ANGLE: {topic}

HARD RULE - FACTS: state ONLY facts that are written in the SOURCES below. Never invent features, mechanics, places,
names, numbers, prices, dates, quotes or comparisons. If the sources do not say GTA 5 lacked something, do NOT claim
GTA 5 lacked it: say what GTA 6 does instead. If the angle cannot be filled with {n} sourced items, adapt the angle
(e.g. "5 things you can do in GTA 6") rather than invent. No story spoilers (no plot twists, endings, deaths), no leaks,
no release-date talk, no "play early", no prices.

SOURCES (the only facts you may use):
{src}

FORMAT (spoken, {lo}-{hi} words in total, so the Short is 55-68 seconds):
- "hook": the first spoken sentence, 8-12 words, said in about 3 seconds, the curiosity promise, e.g.
  "Here's five crazy things in GTA 6 you couldn't do in GTA 5." No greeting, no channel name, no "welcome back".
- "screen_title": the same promise for the top of the screen, ALL CAPS, max 30 characters, starts with "{n} ",
  e.g. "{n} THINGS GTA 6 CHANGED".
- "items": exactly {n} objects, best item LAST (save the wow for the end). Each:
   "label": 1-3 word name for the thing, Title Case (spoken as "Number one, <label>." - do not write that part yourself),
   "body": 42-52 words, spoken right after the label: the claim in one short sentence, then a concrete example told like
           a tiny story in second person ("Say you..." / "You're driving..." - the scenario may only illustrate the
           sourced mechanic, adding no new mechanic or detail), then one sentence on what it changes for the player.
           Short sentences, gamer energy, no filler, no emojis, no hashtags, no "number X" inside the body.
   "source": the [S#] id the claim comes from,
   "evidence": a VERBATIM quote (8-30 words) copied exactly from that source that proves the claim,
   "green": 1-3 single words from the body to flash neon green (exciting/good words),
   "red": 0-2 single words from the body to flash red (danger/bad words like "police", "lose", "jail").
- "outro": optional final line, max 8 words, a question to the viewer (e.g. "Which one are you trying first?"), or "".
- "title": YouTube title under 70 characters, contains "GTA 6", max one emoji, honest, ends with " #shorts".
- "description": 2 short sentences about the video (no links).
- "tags": 15 search tags (gta 6, gta vi, gta 6 gameplay, ...).
{feedback}
Reply with ONLY valid JSON: {{"hook":..., "screen_title":..., "items":[...], "outro":..., "title":..., "description":..., "tags":[...]}}"""

def spoken_items(s):
    return [f"Number {NUM_WORDS[i]}, {it['label'].strip().rstrip('.')}. {it['body'].strip()}" for i, it in enumerate(s["items"])]

def full_text(s):
    return " ".join([s["hook"].strip()] + spoken_items(s) + ([s["outro"].strip()] if s.get("outro", "").strip() else []))

def shape_faults(s, n):
    f = []
    for k in ("hook", "screen_title", "items", "title"):
        if not s.get(k): f.append(f"missing {k}")
    if f: return f
    if len(s["items"]) != n: f.append(f"needs exactly {n} items, got {len(s['items'])}")
    words = len(full_text(s).split())
    lo, hi = C("listicle_min_words", 250), C("listicle_max_words", 290)
    if not lo - 10 <= words <= hi + 10: f.append(f"total is {words} words, must be {lo}-{hi}")
    if len(s["hook"].split()) > 14: f.append("hook is longer than 12 words")
    if re.search(r"\b(welcome|subscribe|hey guys|what's up|channel)\b", s["hook"], re.I): f.append("hook has a greeting/channel mention")
    for i, it in enumerate(s["items"]):
        b = len(str(it.get("body", "")).split())
        if not 34 <= b <= 60: f.append(f"item {i+1} body is {b} words, must be 42-52")
        if not 1 <= len(str(it.get("label", "")).split()) <= 4: f.append(f"item {i+1} label must be 1-3 words")
    blob = json.dumps(s, ensure_ascii=False)
    m = REFUSAL.search(blob)
    if m: f.append(f"AI refusal/meta text: '{m.group(0)}'")
    bad = [w for w in CFG.get("banned_words", []) if re.search(r"\b" + re.escape(w) + r"\b", full_text(s) + " " + s["title"], re.I)]
    if bad: f.append("banned words: " + ", ".join(bad))
    return f

# ---------------------------------------------------------------------------------------------------------- fact gate
ALLOWED_NAMES = {"gta", "grand", "theft", "auto", "rockstar", "jason", "lucia", "leonida", "vice", "city", "i", "number", "you",
                 "say", "imagine", "now", "here", "so", "and", "but", "then", "one", "two", "three", "four", "five", "vi", "v",
                 "ok", "okay", "yes", "no", "boom", "nope", "wait", "plus", "also", "or", "the", "a", "an", "it", "that", "this"}

def evidence_found(ev, text):
    e, t = norm(ev), norm(text)
    if len(e.split()) < 5: return False
    if e in t: return True
    sm = difflib.SequenceMatcher(None, t, e, autojunk=False)
    m = sm.find_longest_match(0, len(t), 0, len(e))
    if m.size >= 0.8 * len(e): return True
    # near-verbatim (a word changed): most of the quote's 4-word runs appear in the source
    ws = e.split(); grams = [" ".join(ws[i:i+4]) for i in range(len(ws) - 3)]
    return bool(grams) and sum(g in t for g in grams) >= 0.75 * len(grams)

def deterministic_faults(s, srcs):
    allt = norm(" ".join(x["full"] for x in srcs))
    by = {x["id"]: x for x in srcs}
    f = []
    for i, it in enumerate(s["items"]):
        tag = f"item {i+1} ({it.get('label','')})"
        src = by.get(str(it.get("source", "")).strip("[] "))
        if not src: f.append(f"{tag}: cites no fetched source ({it.get('source')!r})"); continue
        if not evidence_found(it.get("evidence", ""), src["full"]):
            f.append(f"{tag}: its evidence quote is not in {src['id']} ({src['url']})")
        body = f"{it['label']} {it['body']}"
        for num in re.findall(r"\b\d[\d,.]*%?", body):
            n_ = norm(num)
            if n_ in ("5", "6") and re.search(r"gta\s*" + re.escape(num), body, re.I): continue
            if not re.search(r"\b" + re.escape(n_) + r"\b", allt): f.append(f"{tag}: number '{num}' is not in any source")
        for mult in re.findall(r"\b(twice|double|triple|(?:two|three|four|five|ten) times)\b", body, re.I):
            if norm(mult) not in allt: f.append(f"{tag}: '{mult}' is not in any source")
        # capitalised names in the middle of a sentence (places, brands, people) must exist in the sources
        for sent in re.split(r"(?<=[.!?])\s+", it["body"]):
            for w in re.findall(r"(?<!^)(?<=\s)([A-Z][a-zA-Z'’-]{2,})", sent):
                if w.lower().strip("'’s") not in ALLOWED_NAMES and norm(w).replace(" s", "") not in allt:
                    f.append(f"{tag}: name '{w}' is not in any source")
    return f

def llm_verify(s, srcs):
    """A fresh AI checker that never saw the writer's prompt: flags any claim the sources don't support."""
    from ai import ask
    src = "\n\n".join(f"[{x['id']}] {x['url']}\n{x['full'][:9000]}" for x in srcs)
    items = "\n".join(f"{i+1}. {t}" for i, t in enumerate([s["hook"]] + spoken_items(s)))
    txt = ask(f"""You are a strict fact checker. You did not write this script and must not trust it.
SOURCES:
{src}

SCRIPT LINES (line 1 is the hook; lines 2+ are the list items):
{items}

For EVERY line, list each factual claim about GTA 6 (or GTA 5) that the SOURCES do not support. A second-person example
scenario ("Say you're driving...") is fine ONLY if it illustrates a sourced mechanic and adds no new mechanic, place,
name, number or comparison. "GTA 6 has X but GTA 5 didn't" needs the sources to support that X is new or was missing.
Reply ONLY JSON: {{"unsupported": [{{"line": <n>, "claim": "<the unsupported claim>"}}]}} (empty list if all supported).""", min_len=10)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m: raise RuntimeError("verifier gave no JSON")
    return [f"line {u.get('line')}: {u.get('claim')}" for u in json.loads(m.group(0)).get("unsupported", [])]

def fact_gate(s, srcs, skip_verifier=False):
    faults = deterministic_faults(s, srcs)
    verifier = "skipped"
    if not faults and not skip_verifier:
        try:
            faults += ["unsupported " + x for x in llm_verify(s, srcs)]; verifier = "llm"
        except Exception as e:
            verifier = f"unavailable ({str(e)[:80]})"
            if C("listicle_require_verifier", False): faults.append("LLM fact verifier unavailable and listicle_require_verifier is on")
    return {"pass": not faults, "faults": faults, "verifier": verifier,
            "items": [{"label": it["label"], "source": it.get("source"), "evidence": it.get("evidence")} for it in s["items"]]}

def write_script(topic, srcs, n, script_file=None, skip_verifier=False):
    if script_file:
        s = json.loads(pathlib.Path(script_file).read_text())
        f = shape_faults(s, n)
        fc = fact_gate(s, srcs, skip_verifier)
        return s, fc, f
    from ai import ask
    feedback, last = "", None
    for attempt in range(3):
        try:
            txt = ask(script_prompt(topic, srcs, n, feedback) + "\nAnswer with valid JSON only.", min_len=600)
            s = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except Exception as e:
            feedback = f"\nYOUR LAST ANSWER WAS NOT VALID JSON ({str(e)[:80]}). Reply with JSON only."; last = (None, None, [str(e)]); continue
        f = shape_faults(s, n)
        fc = fact_gate(s, srcs, skip_verifier) if not f else {"pass": False, "faults": [], "verifier": "not run"}
        last = (s, fc, f)
        if not f and fc["pass"]: return s, fc, f
        feedback = ("\nYOUR LAST SCRIPT WAS REJECTED. Fix ALL of these and keep everything else:\n- "
                    + "\n- ".join((f + fc["faults"])[:12]) + "\nPrevious script:\n" + json.dumps(s, ensure_ascii=False)[:6000])
        log(f"script attempt {attempt+1} rejected:", "; ".join((f + fc['faults'])[:6])[:500])
    return last

# --------------------------------------------------------------------------------------------------------------- voice
def clean_for_tts(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,!?'%$-]", " ", t)).strip()

async def _edge(text, mp3, voice, rate):
    import edge_tts
    words = []
    c = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio": f.write(ch["data"])
            elif ch["type"] == "WordBoundary": words.append({"t": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "w": ch["text"]})
    return words

def tts(text, mp3, rate, fallback=True):
    """edge-tts with WordBoundary timings; the main voice gets 5 tries (the free endpoint drops requests now and then),
    then backup voices, then gTTS + faster-whisper timings. fallback=False: return None instead of switching voice."""
    import time
    text = clean_for_tts(text)
    main = C("listicle_voice", CFG.get("voice", "en-US-AndrewMultilingualNeural"))
    plan = [(main, 5)] + ([("en-US-GuyNeural", 2), ("en-US-ChristopherNeural", 2)] if fallback else [])
    for voice, tries in plan:
        for attempt in range(tries):
            try:
                words = asyncio.run(asyncio.wait_for(_edge(text, mp3, voice, rate), C("listicle_tts_timeout", 120)))
                if os.path.getsize(mp3) > 1000:
                    if not words: words = whisper_words(mp3, text)
                    log(f"voice: edge-tts {voice} rate {rate}, {len(words)} word timings"); return words
            except Exception as e:
                log(f"tts failed: {voice} try {attempt+1}: {type(e).__name__} {str(e)[:80]}")
            time.sleep(3 + 4 * attempt)
    if not fallback: return None
    from gtts import gTTS
    gTTS(text, lang="en", tld="com").save(mp3)
    log("voice: gTTS backup (timings from faster-whisper)")
    return whisper_words(mp3, text)

def whisper_words(mp3, text):
    try:
        import captions
        ws = captions.words(mp3)            # faster-whisper word timings (same helper the clip Shorts use)
        if ws: return [{"t": a, "d": max(0.05, b - a), "w": t} for a, b, t in ws]
    except Exception as e:
        log("whisper timings failed:", str(e)[:100])
    ws = text.split(); d = duration(mp3); step = d / max(1, len(ws))
    return [{"t": i * step, "d": step, "w": w} for i, w in enumerate(ws)]

def tighten(mp3, words, wav, keep=0.11, lead=0.04, tail=0.25):
    """Cut every pause longer than `keep` down to `keep` (no dead air), remap word timings. Returns (words, length)."""
    import numpy as np
    sr = 24000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.float32)
    keepiv, out_words, pos, removed = [], [], 0.0, 0.0
    start = max(0.0, words[0]["t"] - lead) if words else 0.0
    cur = start
    for i, w in enumerate(words):
        if i:
            pe = words[i - 1]["t"] + words[i - 1]["d"]
            gap = w["t"] - pe
            if gap > keep:
                cut_a, cut_b = pe + keep / 2, w["t"] - keep / 2
                keepiv.append((cur, cut_a)); removed += cut_b - cut_a; cur = cut_b
        out_words.append({"t": w["t"] - start - removed, "d": w["d"], "w": w["w"]})
    end = min(len(a) / sr, (words[-1]["t"] + words[-1]["d"] + tail) if words else len(a) / sr)
    keepiv.append((cur, end))
    fade = int(0.004 * sr)
    pieces = []
    for x, y in keepiv:
        seg = a[int(x * sr):int(y * sr)].copy()
        if len(seg) > 2 * fade:
            seg[:fade] *= np.linspace(0, 1, fade); seg[-fade:] *= np.linspace(1, 0, fade)
        pieces.append(seg)
    b = np.concatenate(pieces) if pieces else a
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-", str(wav)], input=b.tobytes(), check=True)
    return out_words, len(b) / sr

def make_voice(text):
    lo, hi = C("listicle_min_seconds", 55), C("listicle_max_seconds", 66)
    rate = C("listicle_rate", "+25%")
    mp3, wav = WORK / "vo.mp3", WORK / "vo.wav"
    words = tts(text, mp3, rate)
    words, ln = tighten(mp3, words, wav)
    log(f"voice: {ln:.1f}s after removing pauses ({len(text.split())} words, rate {rate})")
    if ln > hi:   # too slow: re-voice faster (edge-tts sounds better than time-stretching)
        r0 = int(re.sub(r"[^\d-]", "", rate) or 0)
        need = int(round((100 + r0) * ln / (hi - 1) - 100)) + 1
        new_rate = f"+{min(need, 60)}%"
        w2 = tts(text, WORK / "vo_fast.mp3", new_rate, fallback=False)
        if w2:
            rate = new_rate; shutil.copy(WORK / "vo_fast.mp3", mp3); words, ln = tighten(mp3, w2, wav)
            log(f"voice: re-voiced at {rate}: {ln:.1f}s")
        else: log("voice: faster re-voice failed, time-stretching instead")
    if ln > hi + 0.5:   # last resort: pitch-safe time stretch
        f = min(1.25, ln / hi)
        sh("ffmpeg", "-v", "error", "-y", "-i", wav, "-af", f"atempo={f:.4f}", WORK / "vo2.wav"); (WORK / "vo2.wav").replace(wav)
        words = [{"t": w["t"] / f, "d": w["d"] / f, "w": w["w"]} for w in words]; ln = duration(wav)
        log(f"voice: stretched x{f:.3f} -> {ln:.1f}s")
    if ln < lo - 4: log(f"WARNING: voice is only {ln:.1f}s (target {lo}-{hi}s)")
    return words, ln, rate

def align(tokens, words):
    """Give each script token (with item/colour info) the time of the matching TTS word."""
    a = [norm(t["w"]).replace(" ", "") for t in tokens]
    b = [norm(w["w"]).replace(" ", "") for w in words]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    times = [None] * len(tokens)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1): times[i1 + k] = (words[j1 + k]["t"], words[j1 + k]["d"])
        elif tag == "replace" and j2 > j1:
            t0 = words[j1]["t"]; t1 = words[j2 - 1]["t"] + words[j2 - 1]["d"]
            for k in range(i2 - i1):
                st = t0 + (t1 - t0) * k / (i2 - i1); times[i1 + k] = (st, (t1 - t0) / (i2 - i1))
    # fill tokens TTS skipped (punctuation-only etc.) by interpolation
    for i in range(len(times)):
        if times[i] is None:
            prev = next((times[j] for j in range(i - 1, -1, -1) if times[j]), (0.0, 0.2))
            times[i] = (prev[0] + prev[1], 0.15)
    for t, (st, d) in zip(tokens, times): t["t"], t["d"] = st, d
    return tokens

def tokens_for(s):
    toks = []
    def add(text, item, kind, green=(), red=()):
        g = {norm(x) for x in green}; r = {norm(x) for x in red}
        for w in clean_for_tts(text).split():
            n_ = norm(w)
            col = GREEN if n_ in g else RED if n_ in r else WHITE
            toks.append({"w": w, "item": item, "kind": kind, "col": col})
    add(s["hook"], -1, "hook")
    for i, (it, sp) in enumerate(zip(s["items"], spoken_items(s))):
        add(sp, i, "item", it.get("green", []), it.get("red", []))
        # number and label words in the caption are green too (they echo the header)
        lab_n = len(f"Number {NUM_WORDS[i]}, {it['label'].strip().rstrip('.')}.".split())
        for t in toks[-len(clean_for_tts(sp).split()):][:lab_n]: t["col"] = GREEN
    if s.get("outro", "").strip(): add(s["outro"], len(s["items"]), "outro")
    return toks

# ------------------------------------------------------------------------------------------------------------ footage
def footage_candidates(used):
    import find_clip   # keyless Twitch GraphQL + Kick public clips (find_clip's behaviour is unchanged)
    c = find_clip.twitch_clips() + find_clip.kick_clips()
    seen, out = set(), []
    skip = NSFW + [w.lower() for w in C("listicle_skip_words", [])]
    for x in c:
        t = (x.get("title", "") + " " + x.get("desc", "")).lower()
        if x["url"] in seen or find_clip.bad(t) or any(w in t for w in skip): continue
        if x.get("duration") and x["duration"] < C("listicle_min_clip_seconds", 15): continue
        seen.add(x["url"]); out.append(x)
    fresh = [x for x in out if x["url"] not in used]
    rest = [x for x in out if x["url"] in used]
    rnd = random.Random(datetime.date.today().isoformat())
    rnd.shuffle(fresh)
    # Twitch first (language-filtered, real titles), Kick after; big clips first inside each, the rest shuffled
    fresh.sort(key=lambda x: ("kick.com" in x["url"], -x.get("views", 0) if x.get("views", 0) > 50000 else 0))
    # one clip per streamer first (variety), then the others
    first, later, who = [], [], set()
    for x in fresh + rest:
        (later if x.get("uploader") in who else first).append(x); who.add(x.get("uploader"))
    return first + later

def download_clip(url, dst):
    base = ["yt-dlp", "-q", "--no-playlist", "--no-part", "-f", "b[height<=1080][height>=480]/bv*[height<=1080]+ba/b", "--merge-output-format", "mp4", "-o", str(dst)]
    routes = ([os.environ["YTDLP_HOME_PROXY"]] if os.environ.get("YTDLP_HOME_PROXY") else []) + [None] \
             + ([os.environ["YTDLP_PROXY"]] if os.environ.get("YTDLP_PROXY") else [])   # same proxy secrets as make_short.py
    for px in routes:
        try:
            sh(*(base + (["--proxy", px] if px else []) + [url]), timeout=300)
            if dst.exists() and dst.stat().st_size > 200000: return dst
        except Exception as e:
            log(f"download failed ({px or 'direct'}): {url}: {str(e).splitlines()[-1][:120]}")
    return None

def probe_wh(p):
    w, h = sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", p).strip().split(",")[:2]
    return int(w), int(h)

def crop_rect(w, h, cam):
    """Largest 9:16 rectangle that fills the frame (no bars) and does not touch the facecam box."""
    def ok(x, y, cw, ch):
        if not cam: return True
        cx, cy, cwid, chei = cam; pad = 0.02 * w
        return x + cw <= cx - pad or x >= cx + cwid + pad or y + ch <= cy - pad or y >= cy + chei + pad
    cut_b = C("listicle_crop_bottom", 0.12) if w > h else 0.0   # bottom band of 16:9 games = subtitles/HUD, keep it out
    for scale in (1.0, 0.92, 0.85, 0.78, 0.7, 0.62):
        ch = int(h * min(scale, 1 - cut_b)) // 2 * 2; cw = int(ch * 9 / 16) // 2 * 2
        if cw > w: cw = w // 2 * 2; ch = int(cw * 16 / 9) // 2 * 2
        ymax = int(h * (1 - cut_b)) - ch
        ys = sorted({max(0, ymax // 2), max(0, ymax), 0}, key=lambda y: abs(y - max(0, ymax) * 0.6))
        xs = sorted(range(0, w - cw + 1, max(2, (w - cw) // 40 or 2)), key=lambda x: abs(x - (w - cw) / 2))
        for y in ys:
            for x in xs:
                if ok(x, y, cw, ch): return (x // 2 * 2, y // 2 * 2, cw, ch)
    return None

def analyse(path, rect):
    """Sample the crop at 4 fps: brightness/detail/motion per sample, a big-face check (just-chatting cam), scene cuts."""
    import numpy as np
    x, y, cw, ch = rect; sw, shh = 108, 192
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"crop={cw}:{ch}:{x}:{y},fps=4,scale={sw}:{shh},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, shh, sw).astype(np.float32) if raw else np.zeros((0, shh, sw), np.float32)
    if len(fr) < 8: return None
    mean, std = fr.mean(axis=(1, 2)), fr.std(axis=(1, 2))
    motion = np.r_[0, np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))]
    sharp = (np.abs(np.diff(fr, axis=2)).mean(axis=(1, 2)) + np.abs(np.diff(fr, axis=1)).mean(axis=(1, 2))) / 2
    band = max(2, int(shh * 0.05))
    top, mid = fr[:, :band, :].mean(axis=(1, 2)), fr[:, band * 3:-band * 3, :].mean(axis=(1, 2))
    bars = (top < 10) & (fr[:, :band, :].std(axis=(1, 2)) < 4) & (mid > 25)   # letterboxed cutscene = no (spoilers + bars)
    good = (mean > 18) & (std > 12) & (mean < 235) & (sharp > C("listicle_min_sharpness", 2.0)) & ~bars
    cuts = [i / 4 for i in range(1, len(fr)) if motion[i] > 38]
    face = 0
    try:
        import cv2
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(str(path)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1; k = 0
        for j in range(6):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (j + 0.5) / 6)); okk, f = cap.read()
            if not okk: continue
            g = cv2.cvtColor(f[y:y + ch, x:x + cw], cv2.COLOR_BGR2GRAY); k += 1
            if len(casc.detectMultiScale(g, 1.1, 7, minSize=(int(cw * 0.22), int(cw * 0.22)))): face += 1
        cap.release(); face = face / max(1, k)
    except Exception: pass
    return {"good": good.tolist(), "motion": motion.tolist(), "sharp": sharp.tolist(), "cuts": cuts, "face": face, "n": len(fr)}

def prepare_footage(need, used, footage_dir=None):
    """Download + vet clips until there is ~2.5x the montage length of usable, facecam-free gameplay."""
    import layout
    clips, have = [], 0.0
    target = need * C("listicle_footage_factor", 2.5)
    if footage_dir:
        cands = [{"url": str(p), "uploader": p.stem, "local": p} for p in sorted(pathlib.Path(footage_dir).glob("*.mp4"))]
    else:
        cands = footage_candidates(used)
    log(f"footage: {len(cands)} candidate clips, need ~{target:.0f}s usable")
    for i, c in enumerate(cands[:C("listicle_max_downloads", 14)]):
        if have >= target and len(clips) >= C("listicle_min_clips", 4): break
        p = c.get("local") or download_clip(c["url"], WORK / f"clip{i:02d}.mp4")
        if not p: continue
        try:
            w, h = probe_wh(p); d = duration(p)
            cam = layout.find_facecam(p) if w > h else None
            rect = crop_rect(w, h, cam)
            if not rect: log(f"footage: no facecam-free 9:16 crop in {c['url']}"); continue
            if rect[3] < 480: log(f"footage: crop too small ({rect}) in {c['url']}"); continue
            a = analyse(p, rect)
            if not a: continue
            if a["face"] >= 0.34: log(f"footage: skipped (a face fills the crop = cam footage, not gameplay): {c['url']}"); continue
            ok, why = vision_ok(p, rect, d)
            if not ok: log(f"footage: skipped by vision check ({why[:80]}): {c['url']}"); continue
            usable = sum(a["good"]) / 4
            if usable < 6: log(f"footage: skipped (mostly dark/blank): {c['url']}"); continue
            clips.append({**c, "path": p, "w": w, "h": h, "dur": d, "cam": cam, "rect": rect, "a": a, "used": []})
            have += usable
            log(f"footage: + {c.get('uploader','?')} {d:.0f}s, crop {rect}{' (facecam avoided)' if cam else ''}, usable {usable:.0f}s")
        except Exception as e:
            log(f"footage: failed to vet {c['url']}: {str(e)[:150]}")
    if not clips or have < need * 1.05:
        raise FootageError(f"only {have:.0f}s usable gameplay for a {need:.0f}s montage")
    return clips

class FootageError(RuntimeError): pass

def vision_ok(path, rect, dur):
    """Gemini vision look at 4 frames of the crop (only when GEMINI_API_KEY is set): real GTA 6 gameplay, YouTube-safe,
    no streamer cam filling the shot. Clip titles like 'wtf' say nothing, so look at the pixels."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not C("listicle_vision_check", True): return True, "not checked"
    import base64
    x, y, cw, ch = rect; parts = []
    for i in range(4):
        b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur * (i + 0.5) / 4:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"crop={cw}:{ch}:{x}:{y},scale=270:-2", "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True).stdout
        if b: parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b).decode()}})
    q = ("In-game nudity and adult scenes are FINE, never reject for them. These are 4 frames of B-roll for a YouTube Short about GTA 6 gameplay. Answer ONLY JSON "
         '{"ok": true|false, "why": "..."}. ok=false if ANY frame shows: gore, a real person/webcam filling the shot, a menu/loading/black screen, or something that is not video game footage.')
    try:
        body = {"contents": [{"parts": [{"text": q}] + parts}], "generationConfig": {"temperature": 0, "maxOutputTokens": 200}}
        rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('GATE_MODEL', 'gemini-2.5-flash')}:generateContent",
                                    json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        d = json.loads(urllib.request.urlopen(rq, timeout=90).read())
        txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        v = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        return bool(v.get("ok")), v.get("why", "")
    except Exception as e:
        return True, f"vision check failed ({str(e)[:60]}), kept"

def plan_cuts(total, item_starts, rnd):
    """Cut lengths 0.6-1.5 s (faster in the 3-second hook), a hard cut on every new item, none over 3.5 s."""
    lo, hi = C("listicle_cut_min", 0.6), C("listicle_cut_max", 1.5)
    marks = sorted({0.0, *[t for t in item_starts if 0.5 < t < total - 0.5], total})
    cuts = []
    for a, b in zip(marks, marks[1:]):
        t, seg = a, []
        while b - t > 1e-6:
            d = rnd.uniform(0.55, 0.85) if t < 3.0 else rnd.uniform(lo, hi)
            if b - (t + d) < lo: d = b - t if b - t <= 3.5 else (b - t) / 2
            seg.append(d); t += d
        cuts += seg
    return cuts

def pick_segments(clips, cuts, rnd):
    """Each cut gets a fresh, never-reused stretch of gameplay, rotating across clips (prefers motion + scene cuts)."""
    segs, ci = [], rnd.randrange(len(clips))
    for k, d in enumerate(cuts):
        for tries in range(len(clips)):
            c = clips[(ci + tries) % len(clips)]; a = c["a"]
            best, bs = None, -1
            starts = [(t, True) for t in a["cuts"]] + [(i / 4, False) for i in range(0, a["n"], 2)]
            for st, is_cut in starts:
                st = max(0.25, st + 0.05); en = st + d
                if en > c["dur"] - 0.25: continue
                if any(st < u1 + 0.1 and en > u0 - 0.1 for u0, u1 in c["used"]): continue
                idx = range(int(st * 4), min(a["n"], int(en * 4) + 1))
                if not idx or not all(a["good"][i] for i in idx): continue
                sc = sum(a["motion"][i] for i in idx) / len(idx) + (8 if is_cut else 0) + rnd.random() * 6
                if k < 3: sc += 2 * sum(a["sharp"][i] for i in idx) / len(idx)   # opening shots: crispest action
                if sc > bs: best, bs = st, sc
            if best is not None:
                c["used"].append((best, best + d))
                segs.append({"clip": c, "start": best, "dur": d, "zoom": rnd.choice([1.0, 1.0, 1.08, 1.15])})
                ci = (ci + tries + 1) % len(clips); break
        else:
            raise FootageError(f"ran out of unused gameplay at cut {k+1}/{len(cuts)}")
    return segs

SAFETY_Q = ("In-game nudity and adult scenes are FINE, never flag them. You check B-roll frames for a YouTube Short about GTA 6. Frames are numbered from 0 in the order given. "
            "List every frame that shows ANY of: gore, a real person or webcam, a menu/loading/black screen, a big on-screen title card or chapter text, "
            "cinematic black bars, or something that is not video game footage. Reply ONLY JSON: {\"bad\": [frame numbers]}.")

def _frame_b64(path, t, rect):
    import base64
    x, y, cw, ch = rect
    b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", f"crop={cw}:{ch}:{x}:{y},scale=216:-2",
                        "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"], capture_output=True).stdout
    return base64.b64encode(b).decode() if b else None

def unsafe_segments(segs):
    """Gemini vision looks at the start + middle frame of EVERY planned cut (batches of 20 cuts). Returns the bad indices,
    or None when it couldn't check (no GEMINI_API_KEY / API error)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not C("listicle_vision_check", True): return None
    bad = set()
    for b0 in range(0, len(segs), 20):
        batch = list(range(b0, min(len(segs), b0 + 20)))
        imgs, owner = [], []
        for i in batch:
            sg = segs[i]
            for t in (sg["start"] + 0.05, sg["start"] + sg["dur"] / 2):
                d = _frame_b64(sg["clip"]["path"], t, sg["clip"]["rect"])
                if d: imgs.append({"inline_data": {"mime_type": "image/jpeg", "data": d}}); owner.append(i)
        try:
            body = {"contents": [{"parts": [{"text": SAFETY_Q}] + imgs}], "generationConfig": {"temperature": 0, "maxOutputTokens": 400}}
            rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('GATE_MODEL', 'gemini-2.5-flash')}:generateContent",
                                        json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
            d = json.loads(urllib.request.urlopen(rq, timeout=120).read())
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            for k in json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("bad", []):
                if 0 <= int(k) < len(owner): bad.add(owner[int(k)])
        except Exception as e:
            log("visual safety check failed:", str(e)[:120]); return None
    return sorted(bad)

def safe_segments(clips, segs, rnd):
    """Replace every cut the vision check flags (its stretch stays marked used, so it can never come back)."""
    for rnd_i in range(3):
        bad = unsafe_segments(segs)
        if bad is None: return segs, "unchecked (no GEMINI_API_KEY or API error)"
        if not bad: return segs, "checked: all cuts safe" + (" after replacing flagged cuts" if rnd_i else "")
        log(f"visual safety: {len(bad)} cuts flagged (cam/title card...), replacing them")
        for c in clips:   # a clip with 3+ flagged cuts is dropped entirely
            if sum(segs[i]["clip"] is c for i in bad) >= 3: c["a"]["good"] = [False] * c["a"]["n"]
        for i in bad:
            segs[i] = pick_segments([c for c in clips if any(c["a"]["good"])], [segs[i]["dur"]], rnd)[0]
    return segs, "FLAGGED cuts remained after 3 replacement rounds"

def render_montage(segs, total):
    d_ = WORK / "segs"; shutil.rmtree(d_, ignore_errors=True); d_.mkdir(parents=True)
    t_acc, frames_done, jobs = 0.0, 0, []
    for i, s in enumerate(segs):
        t_acc += s["dur"]; nf = max(1, round(t_acc * FPS) - frames_done); frames_done += nf
        x, y, cw, ch = s["clip"]["rect"]; z = s["zoom"]
        vf = f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos"
        if z > 1.0: vf += f",scale={int(W*z)//2*2}:{int(H*z)//2*2},crop={W}:{H}"
        vf += f",fps={FPS},setsar=1,format=yuv420p"
        jobs.append((i, s, nf, vf))
    def run(j):
        i, s, nf, vf = j
        sh("ffmpeg", "-v", "error", "-y", "-ss", f"{s['start']:.3f}", "-i", s["clip"]["path"], "-vf", vf, "-frames:v", nf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-r", FPS, "-video_track_timescale", "15360", d_ / f"s{i:03d}.mp4", timeout=300)
    with ThreadPoolExecutor(max(2, min(6, os.cpu_count() or 2))) as ex: list(ex.map(run, jobs))
    lst = d_ / "list.txt"; lst.write_text("".join(f"file 's{i:03d}.mp4'\n" for i in range(len(segs))))
    sh("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", WORK / "montage.mp4", timeout=600)
    return WORK / "montage.mp4"

# ------------------------------------------------------------------------------------------------------------ overlay
def ensure_font():
    if not FONT_FILE.exists():
        FONT_DIR.mkdir(exist_ok=True)
        try: urllib.request.urlretrieve(FONT_URL, FONT_FILE)
        except Exception as e: log("font download failed, libass will fall back:", e)
    return FONT_FILE.exists()

_fonts = {}
def text_w(s, size):
    from PIL import ImageFont
    if size not in _fonts: _fonts[size] = ImageFont.truetype(str(FONT_FILE), size)
    return _fonts[size].getlength(s)

def fit(s, size, maxw, minsize=40):
    while size > minsize and text_w(s, size) > maxw: size -= 4
    return size

def wrap_lines(s, size, maxw, max_lines=3):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and text_w(cur + " " + w, size) > maxw: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    if cur: lines.append(cur)
    if len(lines) > max_lines: return wrap_lines(s, size - 6, maxw, max_lines)
    return lines, size

def ts(t):
    t = max(0.0, t); cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

def esc(s): return s.replace("{", "(").replace("}", ")").replace("\\", "/")

def build_ass(s, toks, item_spans, total):
    cap_y = int(H * C("listicle_caption_y", 0.74))
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,{FONT_NAME},118,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,1,0,0,100,100,0,0,1,10,4,5,60,60,0,1\n"
            f"Style: Head,{FONT_NAME},108,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,1,0,0,100,100,0,0,1,11,5,8,60,60,0,1\n"
            f"Style: Title,{FONT_NAME},56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,1,0,0,100,100,0,0,1,7,3,8,60,60,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    maxw = 900
    first_item = item_spans[0][0] if item_spans else total
    # 1) HOOK: the promise, big, top block (digits in green), held until item 1 starts
    st = s["screen_title"].upper().strip()
    lines, size = wrap_lines(st, 112, maxw, 3)
    txt = r"\N".join(re.sub(r"\b(\d+)\b", lambda m: "{\\c" + GREEN + "}" + m.group(1) + "{\\c" + WHITE + "}", esc(l)) for l in lines)
    ev.append(f"Dialogue: 2,{ts(0)},{ts(first_item)},Head,,0,0,0,,{{\\pos(540,230)\\fs{size}\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}}{txt}")
    # 2) during the items: small title line on top + green "1." + white label held for the whole item
    tsize = fit(st, 56, maxw)
    if item_spans:
        ev.append(f"Dialogue: 1,{ts(first_item)},{ts(total)},Title,,0,0,0,,{{\\pos(540,175)\\fs{tsize}}}{esc(st)}")
    for i, (a, b) in enumerate(item_spans):
        label = s["items"][i]["label"].upper().strip().rstrip(".")
        lines, size = wrap_lines(f"{i+1}. {label}", 116, maxw, 2)
        body = r"\N".join(esc(l) for l in lines)
        body = body.replace(f"{i+1}.", "{\\c" + GREEN + "}" + f"{i+1}." + "{\\c" + WHITE + "}", 1)
        ev.append(f"Dialogue: 2,{ts(a)},{ts(b)},Head,,0,0,0,,{{\\pos(540,270)\\fs{size}\\fscx70\\fscy70\\t(0,110,\\fscx106\\fscy106)\\t(110,180,\\fscx100\\fscy100)}}{body}")
    # 3) captions: max 3 words per line at ~74% height, keywords coloured, pop-in
    chunks, cur = [], []
    for t in toks:
        if cur and (len(cur) == 3 or t["item"] != cur[-1]["item"] or len(" ".join(x["w"] for x in cur + [t])) > 20):
            chunks.append(cur); cur = []
        cur.append(t)
        if re.search(r"[.!?,;:]$", t["w"]): chunks.append(cur); cur = []
    if cur: chunks.append(cur)
    for k, ch in enumerate(chunks):
        a = ch[0]["t"]; b = chunks[k + 1][0]["t"] if k + 1 < len(chunks) else min(total, ch[-1]["t"] + ch[-1]["d"] + 0.4)
        b = min(b, a + 2.2)
        if b - a < 0.06: continue
        words = [re.sub(r"[^\w'%$-]", "", x["w"]).upper() for x in ch]
        plain = " ".join(w for w in words if w)
        if not plain: continue
        size = fit(plain, 118, maxw - 40, 60)
        parts = "".join(("{\\c" + x["col"] + "}" if x["col"] != WHITE else "{\\c" + WHITE + "}") + esc(w) + " "
                        for x, w in zip(ch, words) if w).strip()
        ev.append(f"Dialogue: 3,{ts(a)},{ts(b)},Cap,,0,0,0,,{{\\pos(540,{cap_y})\\fs{size}\\fscx80\\fscy80\\t(0,80,\\fscx108\\fscy108)\\t(80,140,\\fscx100\\fscy100)}}{parts}")
    return head + "\n".join(ev) + "\n"

# ---------------------------------------------------------------------------------------------------------- music/mix
def pick_music(arg):
    if arg: return pathlib.Path(arg) if pathlib.Path(arg).exists() else None
    files = sorted((HERE / C("listicle_music_dir", "music")).glob("*.mp3")) + sorted((HERE / C("listicle_music_dir", "music")).glob("*.m4a"))
    for u in C("listicle_music_urls", []):
        try:
            p = WORK / ("bed_" + hashlib.md5(u.encode()).hexdigest()[:8] + ".mp3"); urllib.request.urlretrieve(u, p); files.append(p)
        except Exception as e: log("music download failed:", str(e)[:80])
    return random.choice(files) if files else None

def final_mix(montage, wav, ass, total, music, dst):
    db = C("listicle_music_db_under_voice", 7)
    fc = f"[0:v]subtitles={ass}:fontsdir={FONT_DIR}[v];[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,apad,atrim=0:{total:.3f}[vo];"
    ins = ["-i", montage, "-i", wav]
    if music:
        ins += ["-stream_loop", "-1", "-i", music]
        fc += (f"[2:a]atrim=0:{total:.3f},loudnorm=I={-14 - db}:TP=-6:LRA=11,afade=t=in:d=0.4,afade=t=out:st={max(0, total-1.2):.2f}:d=1.2[mu];"
               f"[vo][mu]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[a]")
    else:
        fc += "[vo]alimiter=limit=0.95[a]"
    sh("ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", "-r", FPS,
       "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-maxrate", "4500k", "-bufsize", "9000k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
       "-movflags", "+faststart", dst, timeout=1800)

# --------------------------------------------------------------------------------------------------------------- main
def lock():
    m = json.loads((OUT / "metadata.json").read_text())
    used = load_used()
    key = "topic:" + m["topic"]
    if key in used:
        log(f"topic already posted by another run: {m['topic']!r}"); sys.exit(4)
    f = json.loads(USED_F.read_text()) if USED_F.exists() else []
    f += [key] + [u for u in m.get("footage", []) if u not in f]
    USED_F.write_text(json.dumps(f, indent=0))
    r = subprocess.run([sys.executable, str(HERE.parent / "common" / "gitsave.py"), "gtaclips: listicle lock " + m["topic"][:50],
                        "--union", "gtaclips/listicle_used.json"], cwd=HERE.parent)
    sys.exit(0 if r.returncode == 0 else 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default=os.environ.get("TOPIC", ""))
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--script-file"); ap.add_argument("--footage-dir"); ap.add_argument("--music")
    ap.add_argument("--no-music", action="store_true"); ap.add_argument("--skip-verifier", action="store_true")
    ap.add_argument("--seed", default="")
    a = ap.parse_args()
    if a.lock: return lock()
    shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True); OUT.mkdir(exist_ok=True)
    for f in ("short.mp4", "short_x.mp4", "metadata.json", "gate.json", "youtube_link.txt", "upload_route.txt", "upload_notes.txt", "fact_check.json"):
        (OUT / f).unlink(missing_ok=True)
    used = load_used()
    topic = a.topic.strip() or pick_topic(used)
    n = C("listicle_items", 5)
    log(f"topic: {topic!r}")

    # 1) facts first: nothing is written that isn't in these fetched pages
    srcs = gather_sources(topic)
    json.dump([{k: x[k] for k in ("id", "url", "title", "text")} for x in srcs], open(OUT / "sources.json", "w"), indent=1)

    # 2) script + fact gate (no video if any claim is unsourced)
    s, fc, shape = write_script(topic, srcs, n, a.script_file, a.skip_verifier)
    json.dump({**(fc or {}), "shape_faults": shape}, open(OUT / "fact_check.json", "w"), indent=1)
    if s is None or shape or not fc or not fc["pass"]:
        log("FACT/SCRIPT GATE: rejected, no video made:", "; ".join((shape or []) + ((fc or {}).get("faults") or []))[:800])
        sys.exit(6)
    log(f"script ok: {len(full_text(s).split())} words, fact gate passed (verifier: {fc['verifier']})")

    # 3) voice + word timings
    words, vo_len, rate = make_voice(full_text(s))
    toks = align(tokens_for(s), words)
    total = round(vo_len + 0.35, 2)
    starts = [next(t["t"] for t in toks if t["item"] == i and t["kind"] == "item") for i in range(n)]
    ends = starts[1:] + [total]
    item_spans = [(st, en) for st, en in zip(starts, ends)]
    log(f"timeline: {total:.1f}s, hook {starts[0]:.1f}s, items at " + ", ".join(f"{x:.1f}" for x in starts))

    # 4) footage montage
    rnd = random.Random(a.seed or (topic + datetime.date.today().isoformat()))
    cuts = plan_cuts(total, starts, rnd)
    try:
        clips = prepare_footage(sum(cuts), used, a.footage_dir)
        segs = pick_segments(clips, cuts, rnd)
        segs, safety = safe_segments(clips, segs, rnd)
        log("visual safety:", safety)
    except FootageError as e:
        log("NO FOOTAGE:", e); sys.exit(3)
    montage = render_montage(segs, total)
    log(f"montage: {len(segs)} cuts from {len({id(x['clip']) for x in segs})} clips, avg {total/len(segs):.2f}s, longest {max(cuts):.2f}s")

    # 5) text + audio -> final
    ensure_font()
    ass = WORK / "overlay.ass"; ass.write_text(build_ass(s, toks, item_spans, total))
    music = None if a.no_music else pick_music(a.music)
    log("music bed:", music.name if music else "none (no free bed configured)")
    final_mix(montage, WORK / "vo.wav", ass, total, music, WORK / "final.mp4")   # render on local disk, then copy
    shutil.copy(WORK / "final.mp4", OUT / "short.mp4")

    # 6) metadata for gate / send_short / upload_youtube
    used_clips = []
    for x in segs:
        if x["clip"]["url"] not in [c["url"] for c in used_clips]: used_clips.append(x["clip"])
    credits = "\n".join(f"🎥 {c.get('uploader') or 'streamer'}: {c['url']}" for c in used_clips if not c.get("local"))
    tags = [t for t in s.get("tags", []) if isinstance(t, str)][:15] or ["gta 6", "gta vi", "gta 6 gameplay"]
    title = s["title"].strip()
    if "#shorts" not in title.lower(): title = title[:88] + " #shorts"
    desc = (s.get("description", "").strip() + "\n\nFacts from: " + ", ".join(dict.fromkeys(x["url"] for x in srcs if x["id"] in {i.get("source") for i in s["items"]}))
            + "\nGameplay footage (all credit to the streamers):\n" + (credits or "own/local footage")
            + "\n\nFan page. Not affiliated with Rockstar Games.\n#GTA6 #GTAVI #GTA6Gameplay #ViceCity #Leonida #Shorts")
    meta = {"mode": "listicle", "topic": topic, "title": title, "description": desc, "tags": tags,
            "hook": s["screen_title"], "cta": s.get("outro", ""), "script": s, "voiceover": full_text(s), "voice_rate": rate,
            "seconds": round(total, 1), "source_url": next((x["url"] for x in srcs if "wikipedia" in x["url"]), srcs[0]["url"]),
            "sources": [x["url"] for x in srcs], "footage": [c["url"] for c in used_clips], "uploader": "",
            "cuts": len(segs), "visual_safety": safety, "music": music.name if music else "", "fact_check": fc, "layout": "fullframe-listicle"}
    json.dump(meta, open(OUT / "metadata.json", "w"), indent=1)
    shutil.copy(ass, OUT / "overlay.ass")
    print("made", OUT / "short.mp4", round(total, 1), "s")

if __name__ == "__main__":
    main()
