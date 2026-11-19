#!/usr/bin/env python3
"""Client branding + meme sound design, run after the video is made and before review/upload.

  1. Logo watermark, per run: the logo_url box on GitHub's Run workflow form (BRAND_LOGO_URL). Each client gets their own logo;
     blank = no logo. There is NO saved/default logo, so one client's logo can never land on another client's video.
     If a logo link was given and it can't be used, the run STOPS (exit 3) instead of posting a video without the client's logo.
     `python brand.py --check` only downloads + checks the link (runs first, so a bad link fails in seconds).
     BRAND_POS = top-right | top-left | top-center (default: top-right on Shorts, top-left on long videos). BRAND_SIZE = % (default 11).
  2. Sound effects like real meme edits: REAL recorded CC0 sounds from gtaclips/sfx/ (vine boom, bruh, laugh track, record scratch,
     sad trombone, bonk, air horn, crickets...), placed ON the moments: Gemini watches + listens to the finished video and plans
     the cues like an editor (punchline -> boom + laugh, fail -> sad trombone, replay -> record scratch...), every cue is snapped
     to the real audio hit, the game audio ducks under each effect, and boom/bonk/impact cues get a quick zoom punch-in.
     Long videos: a transition at every clip change + the beats the genre judge marked in each clip (out_ep/marks.json).
     No Gemini -> Whisper transcript + text AI plans them; last resort -> loudness hits. GTA_SFX=off turns effects off,
     GTA_SFX=funny|serious|soft forces a style, blank = follows the genre (no genre = no effects).
Usage: python brand.py out/short.mp4 [out/short_x.mp4 ...]     (same cue plan reused for files of the same length)"""
import base64, json, os, re, sys, pathlib, subprocess, urllib.request, urllib.error, time
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
import genre
SFX_DIR = HERE / "sfx"
class LogoError(Exception): pass

def log(*a): print("brand:", *a, file=sys.stderr, flush=True)

def sh(*a, t=900):
    r = subprocess.run(list(map(str, a)), capture_output=True, text=True, timeout=t)
    if r.returncode: raise RuntimeError(r.stderr[-500:])
    return r.stdout + r.stderr

def probe(p):
    o = sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", p)
    j = json.loads(o)
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), {})
    return int(v.get("width", 1080)), int(v.get("height", 1920)), float(j["format"].get("duration", 30)), any(s.get("codec_type") == "audio" for s in j["streams"])

# ------------------------------------------------------------------------------------------------------------ logo
def image_url(url):
    """imgbb page link (ibb.co/xxxx) -> its direct image (i.ibb.co/...). Direct links pass through."""
    url = url.strip()
    if re.match(r"https?://(www\.)?ibb\.co/", url):
        html = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8", "ignore")
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html) or re.search(r'(https://i\.ibb\.co/[^"\s<>]+)', html)
        if m: return m.group(1)
    return url

def logo():
    """The client logo for THIS run, from BRAND_LOGO_URL only. None = no logo asked for. LogoError = link given but unusable."""
    if os.environ.get("BRAND_LOGO", "").lower() in ("off", "0", "false", "no"): return None
    url = os.environ.get("BRAND_LOGO_URL", "").strip()
    if not url: return None
    out = pathlib.Path("/tmp/brand_logo.png")
    if out.exists() and out.with_suffix(".src").exists() and out.with_suffix(".src").read_text() == url: return out   # already fetched this run
    try:
        raw = urllib.request.urlopen(urllib.request.Request(image_url(url), headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    except Exception as e:
        raise LogoError(f"could not download the logo link ({str(e)[:150]})")
    try:
        import io; from PIL import Image
        im = Image.open(io.BytesIO(raw)); im.load(); im = im.convert("RGBA")
    except Exception:
        raise LogoError("the logo link is not an image (for imgbb, use the ibb.co/... link or the i.ibb.co/... direct link)")
    if min(im.size) < 32: raise LogoError(f"the logo is too small ({im.size[0]}x{im.size[1]})")
    bb = im.getbbox()   # trim empty transparent borders so the logo isn't tiny inside its canvas
    if bb and bb != (0, 0) + im.size: im = im.crop(bb)
    im.thumbnail((800, 800)); im.save(out); out.with_suffix(".src").write_text(url)
    return out

# ------------------------------------------------------------------------------------------------------------ audio analysis
def levels(p, step=0.05):
    """RMS loudness (dB) every `step` seconds."""
    n = int(8000 * step)
    o = sh("ffmpeg", "-hide_banner", "-nostats", "-i", p, "-vn", "-af",
           f"aresample=8000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-", t=1800)
    return [float(x) if "inf" not in x else -90.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", o)]

def rises(lv, step=0.05):
    """[(t, jump_dB)] sudden loudness jumps (where a hit / a scream / a laugh starts), strongest first, >= 0.6 s apart."""
    out = []
    for i in range(8, len(lv) - 2):
        base = sorted(lv[i - 8:i])[3]; jump = (lv[i] + lv[i + 1]) / 2 - base
        if jump > 5: out.append((round(i * step, 2), round(jump, 1)))
    out.sort(key=lambda x: -x[1]); keep = []
    for t, j in out:
        if all(abs(t - k) >= 0.6 for k, _ in keep): keep.append((t, j))
    return keep

def snap(t, rs, win=0.45):
    near = [r for r in rs if abs(r[0] - t) <= win]
    return max(near, key=lambda r: r[1])[0] if near else t

# ------------------------------------------------------------------------------------------------------------ sound pack
def pack():
    f = SFX_DIR / "sfx.json"
    return json.loads(f.read_text()) if f.exists() else {}

# Sounds per GENRE: what to play on each beat type, in rotation (variety, never the same sound twice in a row).
B = lambda **k: k
BEAT_SOUNDS = {
    "funny":   B(punchline=["vine_boom", "ba_dum_tss", "vine_boom", "bruh"], laugh=["laugh_crowd", "laugh_small", "laugh_man"],
                 fail=["sad_trombone", "retro_lose", "oof", "cartoon_fall", "wrong_buzzer"], impact=["bonk", "metal_pipe", "vine_boom", "boing"],
                 crash=["metal_pipe", "bonk", "oof"], shock=["oh_my_god", "crowd_gasp", "dun_dun_dun"], awkward=["crickets", "huh", "bruh", "clown_horn"],
                 scream=["oh_no", "vine_boom"], win=["tada", "airhorn", "anime_wow"], chase=["whoosh", "boing"], scare=["huh", "dun_dun_dun"], sad=["sad_trombone"]),
    "fails":   B(punchline=["vine_boom", "bruh"], laugh=["laugh_crowd", "laugh_man"], fail=["sad_trombone", "game_over", "retro_lose", "oof", "cartoon_fall", "wrong_buzzer"],
                 impact=["bonk", "punch", "metal_pipe"], crash=["car_crash", "metal_pipe", "oof"], shock=["oh_no", "crowd_gasp"], awkward=["crickets", "bruh"],
                 scream=["oh_no"], win=["tada"], chase=["whoosh"], scare=["oh_no"], sad=["sad_trombone"]),
    "wtf":     B(punchline=["huh", "vine_boom", "record_scratch"], laugh=["laugh_man"], fail=["bruh", "wrong_buzzer"], impact=["vine_boom", "metal_pipe"],
                 crash=["vine_boom"], shock=["dun_dun_dun", "oh_my_god", "crowd_gasp"], awkward=["huh", "crickets", "glitch"], scream=["oh_no"],
                 win=["anime_wow"], chase=["whoosh"], scare=["dun_dun_dun", "huh"], sad=["bruh"]),
    "rage":    B(punchline=["vine_boom"], laugh=["laugh_man"], fail=["wrong_buzzer", "bruh", "sad_trombone"], impact=["desk_slam", "punch", "glass_break"],
                 crash=["glass_break", "metal_pipe"], shock=["braam", "vine_boom"], awkward=["bruh", "crickets"], scream=["desk_slam", "vine_boom", "punch"],
                 win=["airhorn"], chase=["whoosh"], scare=["braam"], sad=["sad_trombone"]),
    "racing":  B(punchline=["engine_rev", "bass_drop"], laugh=["laugh_man"], fail=["car_crash", "oof"], impact=["car_crash", "boom"], crash=["car_crash", "glass_break", "boom"],
                 shock=["crowd_gasp", "braam"], awkward=["car_horn"], scream=["tire_screech"], win=["crowd_cheer", "airhorn"],
                 chase=["tire_screech", "engine_rev", "turbo_blowoff", "car_peel_off"], scare=["tire_screech"], sad=["dun_dun_dun"]),
    "police":  B(punchline=["siren_whoop", "boom"], laugh=["laugh_man"], fail=["siren_whoop", "wrong_buzzer"], impact=["gunshot", "boom", "punch"],
                 crash=["car_crash", "glass_break"], shock=["police_radio", "dun_dun_dun"], awkward=["siren_whoop"], scream=["siren_whoop"],
                 win=["crowd_cheer", "airhorn"], chase=["siren_whoop", "police_siren", "tire_screech", "helicopter"], scare=["police_radio"], sad=["dun_dun_dun"]),
    "chaos":   B(punchline=["boom", "explosion"], laugh=["laugh_man"], fail=["oof", "explosion"], impact=["explosion", "boom", "shotgun", "gunshot"],
                 crash=["car_crash", "glass_break", "explosion"], shock=["braam", "crowd_gasp"], awkward=["car_horn"], scream=["braam"],
                 win=["airhorn", "crowd_cheer"], chase=["tire_screech", "whoosh"], scare=["braam"], sad=["dun_dun_dun"]),
    "clutch":  B(punchline=["bass_drop", "hitmarker"], laugh=["crowd_cheer"], fail=["oof"], impact=["hitmarker", "gunshot", "boom"], crash=["boom"],
                 shock=["crowd_gasp", "braam"], awkward=["bruh"], scream=["airhorn"], win=["airhorn", "crowd_cheer", "bass_drop", "hitmarker"],
                 chase=["riser", "whoosh"], scare=["braam"], sad=["dun_dun_dun"]),
    "scary":   B(punchline=["horror_sting"], laugh=["laugh_man"], fail=["horror_sting"], impact=["jumpscare", "boom"], crash=["boom"],
                 shock=["horror_sting", "dun_dun_dun"], awkward=["heartbeat"], scream=["jumpscare"], win=["crowd_gasp"], chase=["heartbeat", "riser_noise"],
                 scare=["jumpscare", "horror_sting"], sad=["sad_violin"]),
    "emotional": B(punchline=["whoosh_soft"], laugh=["applause"], fail=["sad_violin"], impact=["whoosh"], crash=["whoosh"], shock=["crowd_gasp"],
                 awkward=["whoosh_soft"], scream=["whoosh"], win=["applause", "tada"], chase=["whoosh"], scare=["whoosh"], sad=["sad_violin"]),
}
TRANSITION = {"funny": ["whoosh", "record_scratch", "whoosh_soft", "scratch_quick"], "fails": ["record_scratch", "whoosh"], "wtf": ["record_scratch", "glitch", "whoosh"],
              "rage": ["whoosh", "scratch_quick"], "racing": ["car_pass", "whoosh"], "police": ["whoosh", "siren_whoop"], "chaos": ["whoosh", "glitch"],
              "clutch": ["whoosh", "riser"], "scary": ["glitch", "whoosh_soft"], "emotional": ["whoosh_soft"]}
OLD_STYLE = {"funny": "funny", "serious": "chaos", "soft": "emotional"}

def style():
    """Which genre's sound set: GTA_SFX (any genre name, or funny/serious/soft, or off) wins, else the genre box. Blank genre = no effects."""
    forced = (os.environ.get("GTA_SFX") or "").strip().lower()
    if forced in ("off", "none", "0", "no", "false"): return ""
    if forced in BEAT_SOUNDS: return forced
    if forced in OLD_STYLE: return OLD_STYLE[forced]
    g = genre.name()
    for k in re.split(r"[\s,/+]+", g):
        if k in BEAT_SOUNDS: return k
    m = genre.mood()   # typed-in words / blank: closest style (only used for the fallback plan; the AI picks from ALL sounds)
    return OLD_STYLE.get(m, "") or "chaos"

def menu(md):
    """ALL sounds in the pack (any genre can use any sound when it fits the frame); this genre's usual sounds listed first."""
    P = pack(); names = []
    for lst in list(BEAT_SOUNDS.get(md, {}).values()) + [TRANSITION.get(md, [])]:
        names += [n for n in lst if n in P and n not in names]
    names += [n for n in P if n not in names]
    return {n: P[n] for n in names}

# ------------------------------------------------------------------------------------------------------------ cue planning
PLAN_Q = """You are the sound designer for a viral GTA 6 YouTube Short ({dur:.1f} s, genre: {genre}).
Watch AND listen to the finished video frame by frame. Add sound effects exactly like top gaming Shorts editors do: each one lands
ON the exact frame where something happens (the crash, the shot, the jump, the cop arriving, the fail, the reaction) and MATCHES what
is on screen at that frame. Few, never random.

Pick from ALL the sounds below, whatever fits the frame best, from any category. The genre only sets the overall tone:
a car crash gets car_crash even in a funny Short, a police car showing up gets siren_whoop, a gunshot on screen gets gunshot,
a drift gets tire_screech, a fall gets cartoon_fall or oof, a scary moment gets jumpscare, a clean kill gets hitmarker,
and the funny punchline still gets vine_boom / bruh.

Available sounds (use ONLY these names):
{menu}

Structure of these Shorts: 0-1.2 s cold open (the key moment), then the clip, then a 0.5x REPLAY of the key moment with a
"REPLAY" tag, then an end card "SEND THIS TO ...". A whoosh / record scratch on the replay start works well.

Rules: 3 to 4 cues, never more. One signature sound on the single best moment (the payoff); the others only where something clearly happens on screen. Nothing over the streamer talking unless it is the payoff. A laugh track only right AFTER a funny punchline/fail.
Voice sounds (bruh, huh, oh_no, oh_my_god, oof) never on top of the streamer talking: put them in a gap. At least 1.0 s
between cues. Nothing before 0.15 s. Times to 0.1 s, on the frame the thing happens. If nothing happens, no sound.
Reply ONLY JSON: {{"cues": [{{"t": seconds, "sound": "name", "why": "what is on screen there"}}]}}"""

def _gemini(parts):
    key = os.environ.get("GEMINI_API_KEY")
    if not key: raise RuntimeError("no GEMINI_API_KEY")
    models = [m for m in dict.fromkeys([os.environ.get("SFX_MODEL"), os.environ.get("GATE_MODEL"), "gemini-2.5-flash", "gemini-2.0-flash"]) if m]
    err = ""
    for attempt in range(4):
        model = models[min(attempt // 2, len(models) - 1)]
        cfg = {"temperature": 0.2, "maxOutputTokens": 2048, "responseMimeType": "application/json"}
        if model.startswith("gemini-2.5"): cfg["thinkingConfig"] = {"thinkingBudget": 1024}
        try:
            rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                        json.dumps({"contents": [{"parts": parts}], "generationConfig": cfg}).encode(),
                                        {"Content-Type": "application/json", "x-goog-api-key": key})
            d = json.loads(urllib.request.urlopen(rq, timeout=180).read())
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
            return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except urllib.error.HTTPError as e:
            err = f"{model} HTTP {e.code}"; time.sleep(20 if e.code == 429 else 4)
        except Exception as e:
            err = f"{model}: {str(e)[:150]}"; time.sleep(4)
    raise RuntimeError(f"Gemini failed: {err}")

def menu_text(md):
    return "\n".join(f"- {n}: {s['desc']} ({s['len']:.1f} s)" for n, s in menu(md).items())

def plan_gemini(src, dur, md):
    small = pathlib.Path("/tmp/brand_plan.mp4")
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-vf", "scale=-2:480,fps=12", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
       "-c:a", "aac", "-b:a", "64k", "-ac", "1", small)
    v = _gemini([{"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(small.read_bytes()).decode()}},
                 {"text": PLAN_Q.format(dur=dur, genre=genre.name() or "best moments", md=md, menu=menu_text(md))}])
    return [(float(c["t"]), str(c["sound"]).strip(), str(c.get("why", ""))[:80]) for c in v.get("cues", []) if "t" in c and "sound" in c]

AI_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "NVIDIA_API_KEY", "CEREBRAS_API_KEY")

def plan_text(src, dur, md, rs):
    if not any(os.environ.get(k) for k in AI_KEYS): raise RuntimeError("no AI keys")
    import captions
    from ai import ask
    ws = captions.words(src, model="tiny.en")
    said = " ".join(f"[{s:.1f}] {w}" for s, _, w in ws)[:2500] or "(no speech)"
    hits = ", ".join(f"{t:.1f}s (+{j:.0f} dB)" for t, j in sorted(rs[:10]))
    out = ask(PLAN_Q.format(dur=dur, genre=genre.name() or "best moments", md=md, menu=menu_text(md)).replace("Watch AND listen to the finished video.",
              f"You can't see it, but here is what is said (second marks): {said}\nSudden loud moments: {hits}.") , min_len=20)
    v = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
    return [(float(c["t"]), str(c["sound"]).strip(), str(c.get("why", ""))[:80]) for c in v.get("cues", []) if "t" in c and "sound" in c]

def plan_heuristic(dur, md, rs):
    body = [r for r in rs if 1.4 < r[0] < dur - 2.0]
    bs = BEAT_SOUNDS[md]
    cues = []
    if body:
        t = body[0][0]; cues.append((t, bs["punchline"][0], "biggest hit"))
        if md == "funny": cues.append((t + 0.9, bs["laugh"][0], "laugh after the hit"))
    for k, (t, j) in enumerate(body[1:3]): cues.append((t, bs["impact"][(k + 1) % len(bs["impact"])], "loud hit"))
    cues.append((max(0.2, dur - 1.6), TRANSITION[md][0], "end card"))
    return cues

def validate(cues, dur, md, rs, max_n=6, gap=1.0):
    """Known sounds only, inside the video, snapped to the real hit, spaced out, voice sounds kept off busy speech."""
    P = pack(); ok = []
    for t, name, why in sorted(cues):
        if name not in P:
            alt = next((n for n in P if n.replace("_", " ") in name.replace("_", " ").lower() or name.lower() in n), None)
            if not alt: log(f"unknown sound '{name}' skipped"); continue
            name = alt
        s = P[name]
        if s["kind"] in ("hit", "voice", "fail", "shock", "joke", "cartoon"): t = snap(t, rs)
        if s["kind"] == "riser": t = max(t, s["len"])       # a riser ENDS on the moment
        if not (0.15 <= t <= dur - 0.3): continue
        if any(abs(t - o[0]) < gap for o in ok): continue
        ok.append((round(t, 2), name, why))
        if len(ok) >= max_n: break
    return ok

def plan_short(src, dur, md):
    try: rs = rises(levels(src))
    except Exception: rs = []
    for fn, label in ((lambda: plan_gemini(src, dur, md), "Gemini watched it"), (lambda: plan_text(src, dur, md, rs), "transcript + AI")):
        try:
            c = validate(fn(), dur, md, rs, max_n=4)
            if len(c) >= 2: log(f"cue plan ({label}): " + "; ".join(f"{n}@{t:.1f}s ({w})" for t, n, w in c)); return c
        except Exception as e:
            log(f"{label} plan failed: {str(e)[:160]}")
    c = validate(plan_heuristic(dur, md, rs), dur, md, rs)
    log("cue plan (loudness only): " + "; ".join(f"{n}@{t:.1f}s" for t, n, _ in c)); return c

SEG_Q = PLAN_Q.replace("viral GTA 6 YouTube Short", "segment of a long GTA 6 compilation video").replace(
    """Structure of these Shorts: 0-1.2 s cold open (the key moment), then the clip, then a 0.5x REPLAY of the key moment with a
"REPLAY" tag, then an end card "SEND THIS TO ...". A whoosh / record scratch on the replay start works well.""", "This is ONE clip of the compilation.").replace(
    "Rules: 3 to 4 cues, never more.", "Rules: 1 to 4 cues (about one per 8 s at most).")

def plan_long_ai(src, dur, md, marks):
    if not os.environ.get("GEMINI_API_KEY"): return None
    edges = sorted(set([0.0] + [float(m) for m in marks if 0.5 < float(m) < dur - 1] + [dur]))
    segs = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 2]
    if not segs or len(segs) > 30: return None
    cues, ok = [], 0
    for a, b in segs:
        cut = pathlib.Path("/tmp/brand_seg.mp4")
        sh("ffmpeg", "-v", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{b-a:.2f}", "-i", src, "-vf", "scale=-2:360,fps=10", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "33", "-c:a", "aac", "-b:a", "48k", "-ac", "1", cut)
        try:
            v = _gemini([{"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(cut.read_bytes()).decode()}},
                         {"text": SEG_Q.format(dur=b - a, genre=genre.name() or "best moments", md=md, menu=menu_text(md))}])
            got = [(a + float(c["t"]), str(c["sound"]).strip(), str(c.get("why", ""))[:80]) for c in v.get("cues", []) if "t" in c and "sound" in c]
            cues += [c for c in got if a + 0.3 < c[0] < b - 0.3][:4]; ok += 1
        except Exception as e:
            log(f"segment {a:.0f}-{b:.0f}s: AI sound plan failed ({str(e)[:80]})")
        time.sleep(4)
    return cues if ok >= max(1, len(segs) // 2) else None

def plan_long(src, dur, md, marks_file):
    """Episode: a transition at each clip change + the judged beats (max one effect per ~6 s, strongest first)."""
    try: rs = rises(levels(src))
    except Exception: rs = []
    j = json.load(open(marks_file)) if marks_file and pathlib.Path(marks_file).exists() else {}
    marks = j if isinstance(j, list) else j.get("marks", [])
    beats = [] if isinstance(j, list) else j.get("beats", [])
    tr, bs, cues, used = TRANSITION[md], BEAT_SOUNDS[md], [], {}
    for k, m in enumerate(sorted(float(x) for x in marks)):
        if m > 0.5: cues.append((m - 0.12, tr[k % len(tr)], "next clip"))
    ai = None
    try: ai = plan_long_ai(src, dur, md, marks)
    except Exception as e: log(f"AI long plan failed: {str(e)[:120]}")
    if ai:
        c = validate(cues + ai, dur, md, rs, max_n=400, gap=1.0)
        log(f"long video: {len(c)} sound cues, picked per clip by AI watching the frames"); return c
    rot = {}
    for b in sorted(beats, key=lambda b: (-b.get("strength", 2), b["t"])):
        if b.get("strength", 2) < 2: continue
        if any(abs(b["t"] - c[0]) < 5.0 for c in cues if c[2] != "next clip") or any(abs(b["t"] - c[0]) < 1.2 for c in cues): continue
        opts = [o for o in bs.get(b.get("type", "impact"), bs["impact"]) if o in pack()] or bs["impact"]; i = rot.get(b.get("type"), 0); rot[b.get("type")] = i + 1
        cues.append((b["t"], opts[i % len(opts)], b.get("type", "")))
        if b.get("type") in ("punchline", "fail") and md == "funny" and b.get("strength", 2) == 3:
            cues.append((b["t"] + 1.0, bs["laugh"][i % len(bs["laugh"])], "laugh"))
    if not beats:   # no judged beats: the biggest loudness hit inside each clip
        ends = sorted(float(x) for x in marks)[1:] + [dur]
        for k, (m0, m1) in enumerate(zip(sorted(float(x) for x in marks), ends)):
            h = [r for r in rs if m0 + 1.5 < r[0] < m1 - 1.0 and r[1] > 9]
            if h: cues.append((h[0][0], bs["impact"][k % len(bs["impact"])], "hit"))
    c = validate(cues, dur, md, rs, max_n=400, gap=1.0)
    log(f"long video: {len(c)} sound cues ({len(marks)} clip changes, {len(beats)} judged beats)"); return c

# ------------------------------------------------------------------------------------------------------------ render
def zoom_expr(punches, amp=0.13):
    if not punches: return None
    terms = [f"if(between(t,{t:.3f},{t+0.07:.3f}),(t-{t:.3f})/0.07,if(between(t,{t+0.07:.3f},{t+0.5:.3f}),1-(t-{t+0.07:.3f})/0.43,0))" for t in punches]
    return f"(1+{amp}*min(1,{'+'.join(terms)}))"

def program_lufs(p):
    try: return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", sh("ffmpeg", "-hide_banner", "-nostats", "-i", p, "-vn", "-af", "ebur128", "-f", "null", "-", t=1800))[-1])
    except Exception: return -14.0

def build(src, dst, cues=None, long_video=None):
    w, h, dur, has_a = probe(src)
    lg = logo()
    md = style()
    if md and not pack(): log("sound pack missing (gtaclips/sfx), no effects"); md = ""
    if md and not has_a: md = ""
    if cues is None and md:
        lv = long_video if long_video is not None else (w > h and dur > 120)
        cues = plan_long(src, dur, md, os.environ.get("BRAND_MARKS", "")) if lv else plan_short(src, dur, md)
    cues = cues or []
    if not lg and not cues: log("no logo and no sound effects for this run, nothing to do"); return False, cues
    P = pack(); ins, fc = ["-i", src], ""
    # video: zoom punch on boom/bonk/impact cues (Shorts only), then the logo on top (the logo never zooms)
    vin = "[0:v]"
    punches = [t for t, n, _ in cues if P.get(n, {}).get("punch")] if h > w else []
    z = zoom_expr(punches)
    if z:
        fc += (f"{vin}scale=w='trunc(iw*{z}/2)*2':h=-2:eval=frame:flags=bicubic,crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,setsar=1[vz];")
        vin = "[vz]"
    vmap = None
    if lg:
        pct = float(os.environ.get("BRAND_SIZE", "11")) / 100
        lw = int(min(w, h) * pct) // 2 * 2; m = int(min(w, h) * 0.03)
        pos = os.environ.get("BRAND_POS") or ("top-right" if h > w else "top-left")
        x = {"top-left": str(m), "top-center": "(W-w)/2"}.get(pos, f"W-w-{m}")
        ins += ["-loop", "1", "-i", lg]
        fc += f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.85[lg];{vin}[lg]overlay={x}:{m}:shortest=1[v];"
        vmap = "[v]"
    elif z:
        vmap = "[vz]"
    amap = "0:a?"
    if cues:
        n0 = 2 if lg else 1; labels = []
        # every sound is ~-12 LUFS at its loudest; lift it to ~4 LU over this video's own loudness so it cuts through on a phone
        lift = max(-6.0, min(8.0, program_lufs(src) + 1.0 + 12.0))
        for k, (t, name, _) in enumerate(cues):
            s = P[name]
            start = t - s["len"] if s["kind"] == "riser" else t - 0.02
            start = max(0.0, start); ms = int(start * 1000)
            ins += ["-i", SFX_DIR / s["file"]]
            g = s.get("gain_db", 0) + lift + float(os.environ.get("SFX_GAIN_DB", "0"))
            fc += f"[{n0+k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={g:.1f}dB,adelay={ms}|{ms}[s{k}];"
            labels.append(f"[s{k}]")
        fc += (f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,apad[bus];[bus]asplit=2[fx][key];"
               f"[0:a]aresample=48000,aformat=channel_layouts=stereo[game];"
               f"[game][key]sidechaincompress=threshold=0.015:ratio=4:attack=5:release=350:makeup=1[duck];"
               f"[duck][fx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.93:attack=2:release=60[a];")
        amap = "[a]"
    if fc.endswith(";"): fc = fc[:-1]
    cmd = ["ffmpeg", "-v", "error", "-y", *ins]
    if fc: cmd += ["-filter_complex", fc]
    cmd += ["-map", vmap or "0:v", "-map", amap, "-t", f"{dur:.3f}"]
    cmd += (["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"] if vmap else ["-c:v", "copy"])
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", dst]
    sh(*cmd, t=2400)
    log(f"{'logo ' if lg else ''}{('+ ' + str(len(cues)) + ' ' + md + ' sound effects') if cues else ''}"
        f"{' + ' + str(len(punches)) + ' zoom punches' if z else ''} -> {pathlib.Path(src).name}")
    return True, cues

def main(files):
    bad, plan = False, None
    for f in files:
        p = pathlib.Path(f)
        if not p.exists(): continue
        tmp = p.with_name(p.stem + "_brand.mp4")
        try:
            dur = probe(p)[2]
            reuse = plan[1] if plan and abs(plan[0] - dur) < 0.35 else None   # short_x.mp4 = same timeline as short.mp4
            ok, cues = build(p, tmp, cues=reuse)
            if ok: tmp.replace(p)
            plan = plan or (dur, cues)
            try:
                (p.parent / "sfx_cues.json").write_text(json.dumps([{"t": t, "sound": n, "why": w} for t, n, w in (plan[1] or [])], indent=1))
            except Exception: pass
        except Exception as e:
            tmp.unlink(missing_ok=True)
            logo_asked = os.environ.get("BRAND_LOGO_URL", "").strip() and os.environ.get("BRAND_LOGO", "").lower() not in ("off", "0", "false", "no")
            if logo_asked:
                if not isinstance(e, LogoError) and os.environ.get("GTA_SFX", "").lower() != "off":
                    log(f"failed with sound effects ({str(e)[:200]}), retrying logo only")
                    os.environ["GTA_SFX"] = "off"
                    try:
                        if build(p, tmp, cues=[])[0]: tmp.replace(p)
                        continue
                    except Exception as e2: e = e2; tmp.unlink(missing_ok=True)
                print(f"::error::Client logo could not be added to {p.name}: {str(e)[:300]}. Stopping so it never posts without the logo.")
                bad = True
            else:
                log(f"skipped for {p.name}, original kept: {str(e)[:300]}")
    return 3 if bad else 0

if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:   # fail fast on a bad logo link, before the video is made
        try:
            lg = logo()
            from PIL import Image
            print("brand: client logo OK" + (f" {Image.open(lg).size}" if lg else " (none for this run)"))
        except LogoError as e:
            print(f"::error::Client logo problem: {e}. Nothing was made or posted. Fix the logo_url box and run again."); sys.exit(3)
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
