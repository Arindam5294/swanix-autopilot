"""Narrated vertical motion-graphics video (1080x1920, 30 fps, MP4 under 50 MB). No AI images.

1. The AI writes a JSON storyboard: 5-9 scenes, each a fixed template (hook_title, stat_counter, bar_chart, line_chart,
   comparison, list_reveal, quote, cta) with its data and 1-2 sentences of narration. Bad JSON -> next model / retry;
   there is no slideshow fallback.
2. edge-tts voices each scene (en-US-AndrewNeural, then AndrewMultilingualNeural, then GuyNeural) with word timings.
3. motion.py animates every scene for exactly its narration length (count-ups, charts drawing in, kinetic captions,
   eased transitions, gradient glow background, particles), split over the CPU cores.
4. sound.py synthesizes the music bed + whoosh/pop/ding SFX; the music ducks under the voice; ffmpeg muxes it all."""
import asyncio, json, os, pathlib, re, shutil, subprocess, time
import llm, render, prompts

W, H, FPS = 1080, 1920, 30
VOICES = ["en-US-AndrewNeural", "en-US-AndrewMultilingualNeural", "en-US-GuyNeural"]
TYPES = ("hook_title", "stat_counter", "bar_chart", "line_chart", "comparison", "list_reveal", "quote", "cta")
THEMES = ("violet", "ocean", "ember", "forest", "rose")


def ensure_tools():
    if not shutil.which("ffmpeg"):
        render.apt("ffmpeg")
    render.ensure_fonts()
    render.ensure_module("edge_tts", "edge-tts")
    render.ensure_module("PIL", "pillow")
    render.ensure_module("numpy")
    render.ensure_module("cv2", "opencv-python-headless")


# ---------------- storyboard ----------------
def _s(v, n=120):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _num(v):
    if isinstance(v, (int, float)) and v == v:
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v or ""))
    if not m:
        raise ValueError("not a number")
    return float(m.group(0).replace(",", ""))


def clean_scene(sc):
    """Validate + normalise one scene. Raises ValueError when the data can't be animated."""
    t = str(sc.get("type", "")).strip().lower()
    d = sc.get("data") or {}
    if t not in TYPES:
        raise ValueError(f"unknown scene type {t!r}")
    nar = _s(sc.get("narration"), 400)
    if len(nar.split()) < 4:
        raise ValueError("narration missing")
    o = {}
    if t == "hook_title":
        o = {"title": _s(d.get("title"), 70), "kicker": _s(d.get("kicker"), 32), "emoji": _s(d.get("emoji"), 8),
             "highlight": _s(d.get("highlight"), 40)}
        if not o["title"]:
            raise ValueError("hook needs title")
    elif t == "stat_counter":
        o = {"label": _s(d.get("label"), 70), "value": _num(d.get("value")), "prefix": _s(d.get("prefix"), 3),
             "suffix": _s(d.get("suffix"), 8), "sub": _s(d.get("sub"), 110)}
        if d.get("decimals") is not None:
            o["decimals"] = max(0, min(2, int(_num(d["decimals"]))))
        if d.get("ring") is not None:
            o["ring"] = max(0.0, min(100.0, _num(d["ring"])))
    elif t == "bar_chart":
        bars = [{"label": _s(b.get("label"), 30), "value": _num(b.get("value"))} for b in (d.get("bars") or [])
                if isinstance(b, dict) and b.get("label") is not None and b.get("value") is not None][:6]
        if len(bars) < 2:
            raise ValueError("bar chart needs 2+ bars")
        o = {"title": _s(d.get("title"), 70), "unit": _s(d.get("unit"), 6), "prefix": _s(d.get("prefix"), 2), "bars": bars}
    elif t == "line_chart":
        pts = [{"x": _s(p.get("x"), 10), "y": _num(p.get("y"))} for p in (d.get("points") or [])
               if isinstance(p, dict) and p.get("y") is not None][:10]
        if len(pts) < 3:
            raise ValueError("line chart needs 3+ points")
        o = {"title": _s(d.get("title"), 70), "unit": _s(d.get("unit"), 6), "prefix": _s(d.get("prefix"), 2),
             "points": pts, "caption": _s(d.get("caption"), 80)}
    elif t == "comparison":
        def side(x):
            x = x or {}
            return {"name": _s(x.get("name"), 26), "emoji": _s(x.get("emoji"), 8),
                    "points": [_s(p, 48) for p in (x.get("points") or []) if _s(p)][:3]}
        o = {"title": _s(d.get("title"), 70), "left": side(d.get("left")), "right": side(d.get("right")),
             "verdict": _s(d.get("verdict"), 80)}
        if not (o["left"]["name"] and o["right"]["name"]):
            raise ValueError("comparison needs two names")
    elif t == "list_reveal":
        items = []
        for it in (d.get("items") or [])[:5]:
            if isinstance(it, str):
                it = {"text": it}
            if isinstance(it, dict) and _s(it.get("text")):
                items.append({"text": _s(it.get("text"), 56), "emoji": _s(it.get("emoji"), 8)})
        if len(items) < 2:
            raise ValueError("list needs 2+ items")
        o = {"title": _s(d.get("title"), 70), "items": items}
    elif t == "quote":
        o = {"text": _s(d.get("text"), 160), "by": _s(d.get("by"), 40)}
        if not o["text"]:
            raise ValueError("quote needs text")
    elif t == "cta":
        o = {"title": _s(d.get("title"), 60), "sub": _s(d.get("sub"), 34), "emoji": _s(d.get("emoji"), 8)}
        if not o["title"]:
            raise ValueError("cta needs title")
    return {"type": t, "narration": nar, "data": o}


def check_storyboard(text):
    d = llm.json_obj(text)
    if isinstance(d, list):
        d = {"scenes": d}
    raw = d.get("scenes") or []
    scenes, errs = [], []
    for sc in raw:
        try:
            scenes.append(clean_scene(sc if isinstance(sc, dict) else {}))
        except Exception as e:
            errs.append(str(e))
    if not (5 <= len(scenes) <= 9) or len(errs) > 2:
        raise llm.AIError(f"storyboard invalid: {len(scenes)} usable scenes; {'; '.join(errs[:3])}")
    words = sum(len(s["narration"].split()) for s in scenes)
    if words < 60 or words > 210:
        raise llm.AIError(f"storyboard narration length {words} words")
    theme = str(d.get("theme", "")).lower()
    return {"title": _s(d.get("title"), 90) or scenes[0]["data"].get("title", ""), "theme": theme if theme in THEMES else "violet",
            "mood": d.get("mood") if d.get("mood") in ("upbeat", "dramatic", "chill") else "upbeat", "scenes": scenes}


def write_storyboard(req, old=None, instruction=None, deadline=None):
    if instruction and old:
        p = (f"Original request: <<<{req}>>>\nCurrent video storyboard JSON:\n{json.dumps(old, ensure_ascii=False)}\n\n"
             f"Revise it for this instruction: <<<{instruction}>>>. Keep scenes that don't need changing identical. "
             "If the current JSON uses an older format (image_prompt fields), convert it fully to the scene-template "
             "format. Return the complete storyboard JSON.")
    else:
        p = f"Make a narrated vertical motion-graphics video about:\n<<<{req}>>>"
    last = None
    for attempt in range(3):     # llm.ask already walks every model twice; this retries the whole chain
        if deadline and time.time() > deadline - 360:
            break
        try:
            return llm.ask(p, system=prompts.VIDEO_SYSTEM, max_tokens=6000, check=check_storyboard,
                           temp=0.6 + 0.1 * attempt, label="video-storyboard")
        except llm.AIError as e:
            last = e
            print("   storyboard attempt failed:", str(e)[:160])
    raise last or llm.AIError("storyboard: out of time")


# ---------------- voice ----------------
async def _tts(text, mp3, v):
    import edge_tts
    words = []
    c = edge_tts.Communicate(text, v, rate="+6%", boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"t": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "w": ch["text"]})
    return words


def tts(text, mp3):
    saved = {}
    try:
        for v in VOICES:
            for i in range(3):
                try:
                    w = asyncio.run(_tts(text, mp3, v))
                    if os.path.getsize(mp3) > 1000:
                        return w
                except Exception as e:
                    print(f"   tts retry ({v}):", str(e)[:120])
                    if "login" in str(e) or "proxy" in str(e).lower():   # sandbox proxies confuse aiohttp
                        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
                            if k in os.environ:
                                saved[k] = os.environ.pop(k)
                    else:
                        time.sleep(2 + 3 * i)
        raise RuntimeError("text-to-speech failed")
    finally:
        os.environ.update(saved)


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def punctuate(words, text):
    """edge-tts word boundaries drop punctuation; recover it from the narration so captions break at sentences."""
    pos = 0
    low = text.lower()
    for w in words:
        k = low.find(w["w"].lower(), pos)
        if k < 0:
            continue
        pos = k + len(w["w"])
        nxt = text[pos:pos + 2].strip()[:1]
        w["p"] = nxt if nxt in ".,?!;:" else ""
    return words


def caption_groups(words, t0, end):
    """Up to 3 words per caption, broken at punctuation; absolute times."""
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or w.get("p") or w["w"].endswith((".", ",", "?", "!", ";", ":")):
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    out = []
    for k, g in enumerate(groups):
        a = t0 + g[0]["t"]
        b = t0 + (groups[k + 1][0]["t"] if k + 1 < len(groups) else min(end - t0 - 0.05, g[-1]["t"] + g[-1]["d"] + 0.6))
        out.append({"t0": a, "t1": b, "words": [{"w": re.sub(r"[\"“”]", "", x["w"]), "t": t0 + x["t"], "d": x["d"]} for x in g]})
    return out


# ---------------- main ----------------
def prepare(sb, work):
    """Voice every scene, time it, plan the reveals/SFX and write the final audio mix. Returns the mix path."""
    import motion, sound
    t, caps, events, voice_parts = 0.0, [], [], []
    for i, sc in enumerate(sb["scenes"]):
        mp3 = work / f"v{i}.mp3"
        words = tts(sc["narration"], mp3)
        lead = 0.35 if i == 0 else 0.2              # breath before the first line, short gap between scenes
        D = max(2.6, lead + dur(mp3) + 0.45)
        sc["_t0"], sc["_D"] = t, D
        local = punctuate([{"t": w["t"] + lead, "d": w["d"], "w": w["w"]} for w in words], sc["narration"])
        events += [(t + et, k) for et, k in motion.plan(sc, D, local)]
        caps += caption_groups(local, t, t + D)
        wav = work / f"v{i}.wav"
        render.sh(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-af", f"adelay={int(lead*1000)}:all=1,apad,atrim=0:{D:.3f}",
                   "-ar", str(sound.SR), "-ac", "2", str(wav)])
        voice_parts.append(wav)
        t += D
    total = t
    sb["_caps"], sb["_total"], sb["_theme"] = caps, total, motion.THEMES[sb.get("theme", "violet")]
    import numpy as np
    voice = np.concatenate([sound.read_wav(p) for p in voice_parts])
    mix = work / "mix.wav"
    sound.mix(voice, sound.music(total, [s["_t0"] for s in sb["scenes"]], sb.get("mood", "upbeat")), sound.sfx(events, total), mix)
    return mix


def build(sb, work, out):
    """Voice, time, render and mux a cleaned storyboard. Returns (out, seconds)."""
    import motion
    mix = prepare(sb, work)
    total = sb["_total"]
    t0 = time.time()
    for crf in (22, 27, 31):
        sb["_crf"] = crf
        silent = work / "silent.mp4"
        motion.render(sb, silent)
        render.sh(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(mix), "-map", "0:v", "-map", "1:a",
                   "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "160k",
                   "-shortest", "-movflags", "+faststart", str(out)], timeout=900)
        if out.stat().st_size < 48 * 1024 * 1024:
            break
    print(f"   rendered {total:.1f}s video in {time.time()-t0:.0f}s ({out.stat().st_size/1e6:.1f} MB)")
    return out, total


def make_video(req, old=None, instruction=None, deadline=None):
    ensure_tools()
    work = render.WORK / f"video-{int(time.time())}"
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    sb = write_storyboard(req, old, instruction, deadline)
    keep = json.loads(json.dumps(sb))                 # clean copy (no timing fields) for later edits
    out = render.WORK / "out" / f"video-{int(time.time())}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out, total = build(sb, work, out)
    return {"files": [("video", out, sb.get("title", ""))], "source": {"script": keep}, "seconds": round(total, 1)}
