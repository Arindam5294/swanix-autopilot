#!/usr/bin/env python3
"""NARRATED render: AI voiceover over clean gameplay, the format that IG/YouTube treat as transformed (original) content.
  - the streamer's facecam, chat and subtitles are cropped out (gameplay only, full 9:16)
  - the streamer's audio is MUTED (only the AI voice; optional quiet game bed with "narrate_game_db")
  - one big caption card per spoken line (label + big words + small line), series pill on top, small clip credit
  - voice: edge-tts (Hindi hi-IN-MadhurNeural / English en-US-AndrewMultilingualNeural), retries + backup voices, gTTS last
Used by gta_secrets.py (Secrets: script from the verified tip) and make_short.py (stream moments: script from s["vo"]).
Config (config.json / gta_secrets.json): "narrate": true, "narrate_lang": "hi"|"en", "narrate_rate": "+22%",
"narrate_moments": true, "narrate_game_db": null (muted) or e.g. -26.  Env GTA_NARRATE=0 turns it off.

Local test:  python narrate.py --clip some.mp4 --lines lines.json --out test.mp4 [--lang hi]
  lines.json = [{"say": "...", "label": "STEP 1", "big": "SAMUNDAR MEIN TAIRO", "small": "..."}, ...]"""
import asyncio, json, os, pathlib, re, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
W, H, FPS = 1080, 1920, 30
FONT = HERE / "fonts" / "Montserrat-BlackItalic.ttf"
VOICES = {"hi": ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"],
          "en": ["en-US-AndrewMultilingualNeural", "en-US-GuyNeural", "en-US-ChristopherNeural"]}
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


def log(*a): print("narrate:", *a, file=sys.stderr, flush=True)


def sh(*a, timeout=900):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(f"{a[0]} failed: {r.stderr[-600:]}")
    return r.stdout


def enabled(cfg, key="narrate"):
    if os.environ.get("GTA_NARRATE") == "0": return False
    if os.environ.get("GTA_NARRATE") == "1": return True
    return bool(cfg.get(key, True))


def duration(p): return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).strip())


def probe_wh(p):
    w, h = sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", p).strip().split(",")[:2]
    return int(w), int(h)


# ------------------------------------------------------------------------------------------------------------- voice
def clean_say(t):
    t = re.sub(r"[#*_`\"“”]|https?://\S+", " ", str(t))
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


async def _edge(text, mp3, voice, rate):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(mp3)


def tts_line(text, mp3, lang="hi", rate="+22%"):
    """One spoken line -> mp3. edge-tts (3 tries per voice, backup voices), gTTS last so a run is never lost."""
    text = clean_say(text)
    saved = {k: os.environ.pop(k) for k in PROXY_VARS if k in os.environ}   # a proxy with ':' in the login breaks aiohttp
    try:
        for voice in VOICES.get(lang, VOICES["en"]):
            for attempt in range(3):
                try:
                    asyncio.run(asyncio.wait_for(_edge(text, str(mp3), voice, rate), 90))
                    if pathlib.Path(mp3).exists() and pathlib.Path(mp3).stat().st_size > 2000: return voice
                except Exception as e:
                    log(f"tts {voice} try {attempt + 1}: {type(e).__name__} {str(e)[:80]}"); time.sleep(2 + 3 * attempt)
    finally:
        os.environ.update(saved)
    from gtts import gTTS
    gTTS(text, lang="hi" if lang == "hi" else "en").save(str(mp3))
    return "gTTS"


# ------------------------------------------------------------------------------------------------------------- captions
def _font(size):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT), size)


def _wrap(d, text, font, maxw):
    lines = [""]
    for w in text.split():
        t = (lines[-1] + " " + w).strip()
        if d.textlength(t, font=font) <= maxw or not lines[-1]: lines[-1] = t
        else: lines.append(w)
    return lines


def latin(s): return not re.search(r"[^\x00-\u024F\u2000-\u206F$₹€£%]", s or "")


def caption_png(path, pill, label, big, small, credit):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    if pill and latin(pill):   # series pill on top
        f = _font(46); tw = d.textlength(pill, font=f)
        d.rounded_rectangle(((W - tw) / 2 - 34, 130, (W + tw) / 2 + 34, 214), 42, fill=(255, 214, 10, 255))
        d.text((W / 2, 172), pill, font=f, fill=(10, 10, 10), anchor="mm")
    y = 760
    if label and latin(label):
        f = _font(52); tw = d.textlength(label, font=f)
        d.rounded_rectangle(((W - tw) / 2 - 26, y - 40, (W + tw) / 2 + 26, y + 40), 18, fill=(0, 0, 0, 205))
        d.text((W / 2, y), label, font=f, fill=(255, 214, 10), anchor="mm"); y += 100
    if big and latin(big):
        size = 112
        while size > 70:
            f = _font(size); lines = _wrap(d, big.upper(), f, 980)
            if len(lines) <= 3: break
            size -= 8
        for ln in lines[:3]:
            d.text((W / 2, y), ln, font=f, fill="white", anchor="mt", stroke_width=10, stroke_fill="black"); y += int(size * 1.12)
    if small and latin(small):
        f = _font(44)
        for ln in _wrap(d, small, f, 960)[:2]:
            d.text((W / 2, y + 22), ln, font=f, fill="white", anchor="mt", stroke_width=6, stroke_fill="black"); y += 56
    if credit:
        d.text((36, 250), credit, font=_font(32), fill=(255, 255, 255, 230), stroke_width=4, stroke_fill="black")
    img.save(path)


# ------------------------------------------------------------------------------------------------------------- crop
def game_crop(w, h, cam):
    """Largest 9:16 gameplay window away from the facecam; top 4% (chat/alerts) and bottom 12% (subtitles/HUD) cut."""
    top, bot = (int(h * 0.04), int(h * 0.12)) if w > h else (0, 0)
    ch = (h - top - bot) // 2 * 2; cw = int(ch * 9 / 16) // 2 * 2
    if cw > w: cw = w // 2 * 2; ch = int(cw * 16 / 9) // 2 * 2
    x = (w - cw) // 2
    if cam:
        cx, cy, cwid, chei = cam
        if x < cx + cwid and x + cw > cx and top < cy + chei:   # overlaps the cam: slide to the far side
            x = min(w - cw, cx + cwid + 8) if cx + cwid / 2 < w / 2 else max(0, cx - cw - 8)
            if x < cx + cwid and x + cw > cx:                     # still overlaps (cam in the middle): start below it
                top = min(h - ch, cy + chei + 8); ch = min(ch, h - top) // 2 * 2; cw = int(ch * 9 / 16) // 2 * 2
    return x // 2 * 2, top, cw, ch


# ------------------------------------------------------------------------------------------------------------- render
def render(src, lines, dst, work, lang="hi", rate="+22%", pill="GTA 6 SECRETS", credit="", cam=None, start=0.0,
           game_db=None, max_seconds=40):
    """lines: [{"say", "label", "big", "small"}]. Returns info dict (seconds, timeline, voice)."""
    work = pathlib.Path(work); work.mkdir(parents=True, exist_ok=True)
    lines = [l for l in lines if clean_say(l.get("say", ""))]
    if not lines: raise RuntimeError("narrate: no lines to say")
    gap, t, starts, durs, voice = 0.18, 0.12, [], [], ""
    for i, l in enumerate(lines):
        mp3 = work / f"nv{i}.mp3"
        voice = tts_line(l["say"], mp3, lang, rate)
        d = duration(mp3); starts.append(t); durs.append(d); t += d + gap
        if t > max_seconds: log(f"script runs past {max_seconds}s, dropping the rest"); lines = lines[:i + 1]; break
    total = round(t + 0.35, 2)
    for i, l in enumerate(lines):
        caption_png(work / f"cap{i}.png", pill, l.get("label", ""), l.get("big", ""), l.get("small", ""), credit)
    w, h = probe_wh(src)
    x, y, cw, ch = game_crop(w, h, cam if w > h else None)
    sdur = duration(src); start = max(0.0, min(start, sdur - 3))
    inputs = ["-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(src)]   # loops a short clip under a long voice
    for i in range(len(lines)): inputs += ["-i", str(work / f"cap{i}.png")]
    for i in range(len(lines)): inputs += ["-i", str(work / f"nv{i}.mp3")]
    n = len(lines)
    fc = (f"[0:v]fps={FPS},crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1,unsharp=5:5:0.5,"
          f"eq=brightness=-0.07:contrast=1.05:saturation=1.12,"
          f"zoompan=z='min(1+0.0005*on,1.10)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}[b0];")
    last = "b0"
    for i in range(n):
        e = starts[i + 1] if i + 1 < n else total + 1
        fc += f"[{last}][{i + 1}:v]overlay=0:0:enable='between(t,{starts[i]:.2f},{e:.2f})'[b{i + 1}];"; last = f"b{i + 1}"
    for i in range(n):
        ms = int(starts[i] * 1000)
        fc += f"[{1 + n + i}:a]aresample=48000,adelay={ms}|{ms},apad[a{i}];"
    mix = "".join(f"[a{i}]" for i in range(n))
    has_audio = "audio" in sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", src)
    if game_db is not None and has_audio:   # quiet, muffled game bed (speech band cut) under the narrator
        fc += f"[0:a]aresample=48000,lowpass=f=300,volume={game_db}dB[g];"; mix += "[g]"; n_in = n + 1
    else:
        n_in = n
    fc += f"{mix}amix=inputs={n_in}:normalize=0,atrim=0:{total},loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    (work / "narrate_filter.txt").write_text(fc)
    sh("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", work / "narrate_filter.txt", "-map", f"[{last}]", "-map", "[a]",
       "-t", f"{total:.2f}", "-r", FPS, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", dst, timeout=1800)
    log(f"made {dst}: {total:.1f}s, {len(lines)} lines, voice {voice}, crop {cw}x{ch}+{x}+{y}, streamer audio muted")
    return {"seconds": total, "starts": [round(s, 2) for s in starts], "voice": voice, "lang": lang, "lines": lines,
            "layout": "narrated-gameplay", "structure": "narrated", "voiceover": True}


# ------------------------------------------------------------------------------------------------------------- scripts
def _nums(s): return set(re.findall(r"\d[\d,.]*", s or ""))


def secret_lines(tip, part, lang="hi", gemini=None):
    """Narration for a verified Secrets tip. Uses ONLY the tip's own words (summary/quote/where/what); every number spoken or
    shown must be in them. Gemini writes it when available; otherwise a template (English) built from the tip fields."""
    facts = " ".join(str(tip.get(k, "")) for k in ("summary", "quote", "where", "what", "hook", "title"))
    if gemini:
        lang_rule = ("'say' in natural spoken HINDI written in Devanagari (English game names like GTA 6, jet ski, Vice City stay in "
                     "English letters), casual like a desi gaming YouTuber" if lang == "hi" else "'say' in punchy spoken English")
        prompt = f"""Write the voiceover for a 22-30 second vertical GTA 6 tip video (fan page, part {part} of the "GTA 6 SECRETS" series).
Use ONLY these facts, add nothing else (no new numbers, places, items or claims):
{json.dumps({k: tip.get(k, '') for k in ('title', 'hook', 'where', 'what', 'summary', 'quote')}, ensure_ascii=False)}
Return JSON: {{"lines": [{{"say": "...", "label": "...", "big": "...", "small": "..."}}]}} with 5 to 7 lines:
line 1 = the hook (what you get, why it's crazy), then the steps in order, then the reward, last line = "follow for part {part + 1}" style CTA.
{lang_rule}. Each 'say' max 16 words.
'label' (e.g. "STEP 1", "REWARD", "FOLLOW FOR"), 'big' (2-4 words) and 'small' (max 7 words) are on-screen text in Hinglish or English
written ONLY in Latin letters (no Devanagari), big = the key words of that line."""
        try:
            out = gemini([{"text": prompt}], max_tokens=2048, temperature=0.4)
            ls = [l for l in out.get("lines", []) if isinstance(l, dict) and l.get("say")]
            bad = [n for l in ls for n in _nums(" ".join(str(l.get(k, "")) for k in ("say", "big", "small")))
                   if n.rstrip(".,") not in facts and n.rstrip(".,") not in (str(part), str(part + 1), "6")]
            if 4 <= len(ls) <= 8 and not bad: return ls, lang
            log(f"script rejected ({len(ls)} lines, numbers not in the guide: {bad[:5]}), using the template")
        except Exception as e:
            log(f"script LLM failed: {str(e)[:120]}, using the template")
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(tip.get("summary", ""))) if len(s.split()) >= 4][:3]
    ls = [{"say": f"GTA 6 secret: {str(tip.get('hook') or tip.get('title', '')).lower()}.", "label": "GTA 6 SECRET", "big": tip.get("hook", ""), "small": ""}]
    for i, s in enumerate(sents, 1):
        big = s.split()[:4]
        while len(big) > 2 and big[-1].lower() in ("the", "a", "an", "to", "of", "with", "and", "for", "in", "on", "your", "you"): big.pop()
        ls.append({"say": s, "label": f"STEP {i}", "big": " ".join(big), "small": ""})
    ls.append({"say": f"What you get: {tip.get('what', '')}.", "label": "REWARD", "big": tip.get("what", ""), "small": ""})
    ls.append({"say": f"Follow for part {part + 1}.", "label": "FOLLOW FOR", "big": f"PART {part + 1}", "small": "a new GTA 6 secret every day"})
    return ls, "en"


def vo_lines(vo, hook="", lang="en"):
    """Narration lines from a Short's existing voiceover script (make_short s['vo']): one caption card per sentence."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", clean_say(vo)) if s.strip()]
    out = []
    for i, s in enumerate(sents):
        big = " ".join(s.split()[:5]) if latin(s) else (hook if i == 0 else "")
        out.append({"say": s, "label": "" if i else "", "big": big, "small": ""})
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True); ap.add_argument("--lines", required=True); ap.add_argument("--out", default="narrated.mp4")
    ap.add_argument("--lang", default="hi"); ap.add_argument("--credit", default=""); ap.add_argument("--start", type=float, default=0)
    a = ap.parse_args()
    print(json.dumps(render(a.clip, json.load(open(a.lines)), a.out, "/tmp/narrate_test", lang=a.lang, credit=a.credit, start=a.start),
                     ensure_ascii=False)[:600])
