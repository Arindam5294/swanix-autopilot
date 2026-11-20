#!/usr/bin/env python3
"""Turn one viral clip into a finished GTA VI Clips Short (1080x1920, <=28 s):
hook bar on top, the real footage in the middle, word-timed captions, pro English voiceover
(signature local OmniVoice narrator, edge-tts/gTTS as backups).
Usage: python make_short.py '<json from find_clip.py>'  -> writes out/short.mp4 + out/metadata.json"""
import json, os, sys, re, asyncio, pathlib, subprocess, shutil, hashlib
import moments, layout, captions, campaign, hookfirst
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from ai import ask  # same free AI chain: Gemini -> Groq -> OpenRouter
HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
OUT = HERE / "out"; WORK = pathlib.Path("/tmp/gtaclips")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# Hindi edition: GTA_LANG=hi -> Hindi voiceover (Devanagari) + Hinglish title/hook, for the Indian audience.
HINDI = os.environ.get("GTA_LANG", "").strip().lower() == "hi"
_DEVA = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
if HINDI and os.path.exists(_DEVA): FONT = _DEVA   # renders Hindi captions (and Latin) correctly
W, H, FPS = 1080, 1920, 30
# Hook-first edit (cold open on the key moment -> facecam body -> 0.5x REPLAY -> "SEND THIS TO..." end card).
# config "hookfirst": false or env GTA_HOOKFIRST=0 restores the old render.
HOOKFIRST = hookfirst.enabled(CFG)

def sh(*a, **k):
    r = subprocess.run(list(a), capture_output=True, text=True, **k)
    if r.returncode: raise RuntimeError(" ".join(map(str, a))[:200] + "\n" + r.stderr[-1500:])
    return r.stdout

# Restored to the exact order that made every Short up to Oct 25 2026 (runs #33-#40 all green):
# [] = yt-dlp's own default clients, which use deno + the bgutil PO-token service (:4416) automatically.
# The Oct 26 edit that forced android/android_vr/ios first removed this default and every run failed after it.
# Extra clients stay as late fallbacks only.
ATTEMPTS = [
    [],
    ["--extractor-args", "youtube:player_client=tv,web_safari"],
    ["--extractor-args", "youtube:player_client=mweb"],
    ["--extractor-args", "youtube:player_client=android_vr"],
    ["--extractor-args", "youtube:player_client=android"],
]

def trim(p):
    lim = CFG['max_clip_minutes'] * 60
    try:
        if duration(p) > lim + 1:
            tmp = p.with_name("src_trim.mp4")
            sh("ffmpeg", "-y", "-v", "error", "-i", str(p), "-t", str(lim), "-c", "copy", str(tmp)); tmp.replace(p)
    except Exception as e:
        print("trim skipped:", e, file=sys.stderr)

def download(url, section=None):
    """Try several YouTube clients, first direct, then through the free Cloudflare WARP proxy if the workflow set one up.
    YouTube now demands a signed-in session for datacenter IPs (GitHub runners), so authenticated cookies (the
    YT_COOKIES secret -> cookies.txt) are required. The local PO-token provider (pot service on :4416) is used
    when cookies are present to pass the bot check."""
    dst = WORK / "src.mp4"
    if os.path.exists(url): shutil.copy(url, dst); return dst
    base = ["yt-dlp", "-q", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
            "--hls-prefer-native", "--concurrent-fragments", "4", "-o", str(dst)]
    # Normal videos: no --download-sections (ffmpeg can't use the WARP SOCKS proxy). Long streams: we MUST cut
    # just the viral window (a 4 h stream won't fit), so sections are used and the WARP route is skipped.
    if section: base += ["--download-sections", f"*{section[0]:.0f}-{section[0]+section[1]:.0f}", "--force-keyframes-at-cuts"]
    has_cookies = os.path.exists("cookies.txt")
    if has_cookies: base += ["--cookies", "cookies.txt"]
    # With a signed-in session, prefer the web player that mints a PO token via the local provider (port 4416).
    attempts = ATTEMPTS[:]
    # (Oct 26 cookie-only web/po_token override removed: invalid po_token syntax; default clients already use bgutil.)
    # Route order: your own home/residential proxy (secret YTDLP_HOME_PROXY, never blocked like datacenter IPs),
    # then direct, then the free WARP proxy. Same full-quality download on every route.
    proxies = ([os.environ["YTDLP_HOME_PROXY"]] if os.environ.get("YTDLP_HOME_PROXY") else []) + [None] \
              + ([os.environ["YTDLP_PROXY"]] if os.environ.get("YTDLP_PROXY") and not section else [])
    last = None
    # With cookies, also try every route without them: bad/rotated cookies give HTTP 400 'invalid argument'.
    plan = [(px, extra, True) for px in proxies for extra in attempts] if has_cookies else []
    plan += [(px, extra, False) for px in proxies for extra in attempts]
    for px, extra, use_ck in plan:
        if True:
            cmd = (base if use_ck else [a for a in base if a not in ("--cookies", "cookies.txt")]) + extra + (["--proxy", px] if px else []) + [url]
            try:
                for f in WORK.glob("src.*"): f.unlink()
                sh(*cmd, timeout=600)
                if dst.exists() and dst.stat().st_size > 100000:
                    if not section: trim(dst)
                    print("downloaded via", px or "direct", extra or "default", file=sys.stderr); return dst
            except Exception as e:
                err = str(e)
                if "[Piracy]" in err or "primarily used for piracy" in err.lower():
                    raise RuntimeError(f"yt-dlp blocked this domain (piracy list): {url}") from e
                if ("not a bot" in err or "LOGIN_REQUIRED" in err or "HTTP Error 400" in err or "invalid argument" in err
                        or "Failed to extract any player response" in err or "Sign in" in err or "HTTP Error 403" in err
                        or "SABR" in err or "unable to download video data" in err):
                    last = RuntimeError(
                        "YouTube blocked this download (Sign in to confirm you’re not a bot) because the "
                        "GitHub runner looks like a robot. Fix: export cookies from a logged-in Chrome via the "
                        "'Get cookies.txt LOCALLY' extension and set them as the Actions secret YT_COOKIES "
                        "(or commit them as gtaclips/cookies.txt), then re-run this workflow.\nyt-dlp said: " + err[-400:])
                elif not (last and "not a bot" in str(last)):
                    last = e
                print("download attempt failed:", px or "direct", extra or "default", "cookies" if use_ck else "no-cookies", file=sys.stderr)
    raise last or RuntimeError("download failed")

def duration(p):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)).strip())

REFUSAL = re.compile(r"no source (was )?provided|source (details|data|information)|empty fields|cannot be generated|"
                     r"can(no|')t (generate|create|write|help)|unable to (generate|create|write)|please (provide|paste|share)|"
                     r"kindly (provide|paste)|in your next message|no[- ]invention|as an ai\b|i('m| am) sorry|"
                     r"insufficient (information|details)|not enough (information|details)|placeholder", re.I)

def check_script(s):
    """Reject an LLM refusal or meta-chatter before it becomes a Short (exit 6 = drop this clip, no upload)."""
    for k in ("hook", "vo", "cta", "title", "description"):
        if not str(s.get(k, "")).strip():
            print(f"Script is missing '{k}'. Not making a Short from it.", file=sys.stderr); sys.exit(6)
    blob = " ".join(str(s.get(k, "")) for k in ("hook", "vo", "cta", "title", "description"))
    m = REFUSAL.search(blob)
    if m:
        print(f"Script is an AI refusal, not a story ('{m.group(0)}'). Not making a Short from it.", file=sys.stderr); sys.exit(6)
    if len(s["vo"].split()) < s.get("_min_words", 30):
        print("Voiceover too short to be a real script. Not making a Short from it.", file=sys.stderr); sys.exit(6)
    return s

import datetime as _dt
# GTA 6 unlocks at midnight local time on 19 Nov 2026, first in New Zealand = 2026-11-18 11:00 UTC (16:30 IST).
LAUNCHED = _dt.datetime.now(_dt.timezone.utc) >= _dt.datetime(2026, 11, 18, 11, tzinfo=_dt.timezone.utc)
LAUNCH_FACT = ("GTA 6 is out: it unlocks at midnight local time on November 19, 2026 (New Zealand first) on PS5 and Xbox Series X|S (Rockstar confirmed). Some countries may still be waiting." if LAUNCHED else
               "GTA 6 launches November 19, 2026 on PS5 and Xbox Series X|S (Rockstar confirmed). It is NOT out yet, so nobody is playing it.")

def script(clip):
    """GTA VI Clips: commentary that ADDS value on top of the credited clip (YouTube reused-content safe)."""
    prompt = f"""You write YouTube Shorts for GTA VI Clips (@GTAviclipxx), a fan channel that reposts the best GTA 6 moments
with its own hype-but-trustworthy English commentary. Fan page: never say or imply it is Rockstar or official.
Source clip title: {clip['title']}
Source description: {clip['desc'][:1200]}
Creator: {clip.get('uploader','')}  Views: {clip.get('views',0)}
Facts you may state: only what the title/description say, plus: {LAUNCH_FACT}
Never invent features, story details, prices, sales numbers or quotes. Never mention leaks, rumors, "play early" tricks or free downloads.
No story spoilers in the hook or title. Energetic gamer tone, short sentences, no slurs.
Return ONLY JSON with keys:
"hook": top-of-screen hook, 2-5 words, ALL CAPS, curiosity gap about what the clip shows (e.g. "THIS NPC REACTION", "HIDDEN CAR NOBODY FOUND");
"vo": voiceover, 45-60 words, first sentence is the hook spoken, add one useful takeaway (a tip, a detail to notice, or why it matters), end with a short question;
"cta": max 5 words ALL CAPS question shown at the end, e.g. "WOULD YOU TRY THIS?";
"share_cta": ALL CAPS share line that starts with "SEND THIS TO" and names who should see it, tied to what happens in the clip,
  max 9 words, e.g. "SEND THIS TO THE FRIEND WHO CAN'T DRIVE" or "SEND THIS TO THE WORST PILOT YOU KNOW" (English even for Hindi);
"title": viral title under 70 characters, contains "GTA 6", one emoji max, no clickbait the clip doesn't deliver, ending with " #shorts";
"description": 2 sentences + a line "🎥 Clip: <creator> (link below)" + a line "Fan page. Not affiliated with Rockstar Games." + 6 hashtags starting with #GTA6 #GTAVI;
"tags": list of 15 search tags (gta 6, gta vi, gta 6 gameplay, vice city, leonida, ...)."""
    if clip.get("kind") in ("live", "stream", "twitch_clip") or clip.get("moment"):
        prompt += f"""
THIS IS A VIRAL STREAM MOMENT, not an edited video. {clip.get('uploader','The streamer')} was streaming GTA 6.
What was said in this exact moment (auto-captions, may be empty): {clip.get('said','')[:900]}
Override: "vo" must be ONLY a 12-22 word intro that sets up the moment WITHOUT spoiling it (e.g. "{clip.get('uploader','He')} was NOT ready for what happened next in GTA 6..."),
because the streamer's real reaction plays right after. Describe only what the captions/title support.
Title must name the streamer, e.g. "<Streamer> loses it in GTA 6 😭 #shorts". Description credit line: "🎥 <Streamer>'s stream (link below)"."""
        if LAUNCHED:
            prompt += """
THE GAME IS RELEASED (worldwide rollout from 2026-11-18 11:00 UTC). Stream titles often still say "early access",
"play early" or count down to launch: IGNORE that wording. Never write "early access", "play early", "before launch",
"hours before", "pre-release" or any countdown in hook, vo, title or description. Say they are playing GTA 6 on launch day."""
        if not LAUNCHED:
            prompt += """
PRE-LAUNCH: the game is not out yet, so this stream is a countdown / hype / talk stream, NOT gameplay.
Never say or imply they are playing GTA 6. Title and vo must frame it honestly, e.g. "<Streamer>'s GTA 6 hype before launch 😭 #shorts"."""
    if HINDI:
        prompt += """
HINDI EDITION (Indian audience). Override the language rules above:
"vo": natural spoken Hindi in DEVANAGARI script, casual Indian gamer tone like Hindi YouTube creators (bhai, kya scene hai),
  English game words may stay in English letters (GTA 6, Vice City, police, car). Same length limits as above.
"hook" and "cta": Hinglish in ROMAN letters, ALL CAPS (e.g. "BHAI YE DEKHO", "AAP KYA KARTE?").
"title": Hinglish in Roman letters, must contain "GTA 6", e.g. "<Streamer> ka GTA 6 mein bura haal 😭 #shorts".
"description": Hinglish, same credit and fan-page lines, hashtags include #GTA6 #GTA6Hindi #GTA6India.
"tags": add hindi tags (gta 6 hindi, gta 6 india, gta 6 gameplay hindi)."""
    hk = HERE / "HOOKS.md"
    if hk.exists():   # proven formats from the most-viewed GTA V videos, remade for GTA 6
        prompt += "\nProven hook/title formats (choose the one that honestly fits this clip):\n" + hk.read_text()[:1800]
    pat = HERE / "THUMBNAILS.md"
    if pat.exists():   # weekly outlier study (outliers.py): copy what's winning on GTA channels right now
        prompt += "\nTitle/hook patterns that are beating other GTA channels this week (follow them, stay honest):\n" + pat.read_text()[:1800]
    try:
        import genre; prompt += genre.prompt_line()   # write in the chosen genre's style
    except Exception: pass
    txt = ask(prompt + "\nAnswer with valid JSON only.", min_len=200)
    s = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    if clip.get("kind") in ("live", "stream", "twitch_clip") or clip.get("moment"): s["_min_words"] = 10
    if re.search(r"\b(leak|leaked|play early|free download|apk)\b", s.get("vo", "") + s.get("title", ""), re.I):
        raise RuntimeError("script mentioned leaks/free downloads")
    if LAUNCHED:   # belt and braces: the reviewer fails any early-access / countdown framing after launch
        _fix = [(r"\bearly[- ]access\b", "launch day"), (r"\b(hours|minutes|days) before (the )?(midnight )?(launch|release)\b", "on launch day"),
                (r"\bbefore (the )?(midnight )?(launch|release)\b", "on launch day"), (r"\bpre[- ]?release\b", "launch day")]
        for k in ("hook", "vo", "cta", "title", "description"):
            if isinstance(s.get(k), str):
                for pat, rep in _fix: s[k] = re.sub(pat, rep, s[k], flags=re.I)
        if isinstance(s.get("tags"), list):
            s["tags"] = [t for t in s["tags"] if not re.search(r"early access|before launch|pre[- ]?release", str(t), re.I)]
    at = f" (moment at {clip['moment_at']})" if clip.get("moment_at") else ""
    s["description"] = s.get("description", "").rstrip() + f"\n\nOriginal{' stream' if clip.get('kind') != 'video' else ''}: {clip['url']}{at}"
    return s

def is_slideshow(src):
    """Photo slideshow / static news card? (frozen picture most of the time). Real footage moves."""
    try:
        d = min(duration(src), 120)
        out = subprocess.run(["ffmpeg", "-hide_banner", "-t", str(d), "-i", str(src), "-vf", "scale=320:-2,freezedetect=n=0.003:d=1.5",
                              "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=300).stderr
        frozen = sum(float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", out))
        print(f"slideshow check: {frozen:.0f}s of {d:.0f}s frozen", file=sys.stderr)
        return d > 0 and frozen / d >= CFG.get("max_frozen_ratio", 0.45)
    except Exception as e:
        print("slideshow check skipped:", e, file=sys.stderr); return False

def omni_tts(clean, mp3):
    """Signature Botbeat voice: OmniVoice (VoiceStudio's engine), local + free. Returns words or None."""
    if os.environ.get("BOTBEAT_VOICE", CFG.get("voice_engine", "omni")) != "omni":
        return None
    try:
        import omnivoice  # noqa: F401
    except Exception:  # install on the runner (CPU torch, ~3 min); any hiccup -> edge-tts
        try:
            pip = [sys.executable, "-m", "pip", "install", "-q"]
            subprocess.run(pip + ["torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True, timeout=300)
            subprocess.run(pip + ["omnivoice", "soundfile", "faster-whisper"], check=True, timeout=300)
            print("tts: OmniVoice installed", file=sys.stderr)
        except Exception as e:
            print(f"tts: OmniVoice install failed ({type(e).__name__}), using edge-tts", file=sys.stderr); return None
    tf, wj = WORK / "vo.txt", WORK / "vo_words.json"
    tf.write_text(clean)
    try:
        subprocess.run([sys.executable, str(HERE / "omni_tts.py"), str(tf), mp3, str(wj)],
                       check=True, timeout=int(CFG.get("omni_timeout", 900)))
        words = json.load(open(wj))
        if os.path.getsize(mp3) > 1000 and words:
            print("tts: used OmniVoice signature voice", file=sys.stderr); return words
    except Exception as e:
        print(f"tts: OmniVoice failed ({type(e).__name__}), falling back to edge-tts", file=sys.stderr)
    return None

async def tts(text, mp3):
    """OmniVoice signature voice first; edge-tts with retries and backup voices next; gTTS last, so a Short is never lost."""
    import edge_tts, asyncio as _a
    clean = re.sub(r"[^\w\s.,!?'%$\u0900-\u097F-]", " ", text)   # keep Hindi letters + matras
    clean = re.sub(r"\s+", " ", clean).strip()
    words = None if HINDI else omni_tts(clean, mp3)
    if words: return words
    voices = ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"] if HINDI else [CFG["voice"], "en-US-GuyNeural", "en-US-ChristopherNeural"]
    for voice in voices:
        for attempt in range(3):
            words = []
            try:
                c = edge_tts.Communicate(clean, voice, rate="+4%", boundary="WordBoundary")
                with open(mp3, "wb") as f:
                    async for ch in c.stream():
                        if ch["type"] == "audio": f.write(ch["data"])
                        elif ch["type"] == "WordBoundary": words.append({"t": ch["offset"]/1e7, "d": ch["duration"]/1e7, "w": ch["text"]})
                if os.path.getsize(mp3) > 1000:
                    if not words: words = _even_words(clean, mp3)
                    return words
            except Exception as e:
                print(f"tts failed: {voice} try {attempt+1}: {type(e).__name__}", file=sys.stderr)
            await _a.sleep(5 * (attempt + 1))
    from gtts import gTTS
    gTTS(clean, lang="hi" if HINDI else "en", **({} if HINDI else {"tld": "com"})).save(mp3)
    print("tts: used gTTS backup", file=sys.stderr)
    return _even_words(clean, mp3)

def _even_words(text, mp3):
    ws = text.split(); d = duration(mp3) or len(ws) * 0.35; step = d / max(1, len(ws))
    return [{"t": i * step, "d": step, "w": w} for i, w in enumerate(ws)]

def pick_segments(src, total):
    """Fast cuts on scene changes from the most active part of the clip."""
    d = duration(src)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-t", str(min(d, 600)),
                          "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    cuts = sorted({round(float(x), 2) for x in re.findall(r"pts_time:([\d.]+)", out)})
    starts = [0.0] + [c for c in cuts if c < d - 2]
    segs, t = [], 0.0
    for i, s in enumerate(starts):
        nxt = starts[i+1] if i+1 < len(starts) else d
        ln = max(1.2, min(3.2, nxt - s))
        if s + ln > d: continue
        segs.append((s, ln)); t += ln
        if t >= total: break
    if t < total:   # few scene changes: one continuous run from the start
        segs = [(0.0, min(d, total))]
    return segs

def wrap(s, n=16):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    return lines + [cur] if cur else lines

def overlay(words, hook, cta, total, credit):
    from PIL import Image, ImageDraw, ImageFont
    F = lambda s: ImageFont.truetype(FONT, s)
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or w["w"].endswith((".", ",", "?", "!")): chunks.append(cur); cur = []
    if cur: chunks.append(cur)
    caps = []
    for i, c in enumerate(chunks):
        t0 = c[0]["t"] + 0.15; t1 = chunks[i+1][0]["t"] + 0.15 if i+1 < len(chunks) else total
        caps.append((t0, t1, " ".join(x["w"] for x in c).upper()))
    d_ = WORK / "ov"; shutil.rmtree(d_, ignore_errors=True); d_.mkdir()
    cache = {}
    def txt(d, y, s, size, fill):
        d.text((W//2, y), s, font=F(size), fill=fill, stroke_width=max(6, size//8), stroke_fill=(0, 0, 0), anchor="mm")
    for i in range(int(total * FPS)):
        t = i / FPS
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        hl = wrap(hook, 14)[:3]
        size, gap = (92, 115) if len(hl) <= 2 else (74, 92)
        for k, line in enumerate(hl):
            txt(d, 170 + k*gap, line, size, (255, 214, 0) if k == len(hl) - 1 else (255, 255, 255))
        if t > total - 2.6:
            for k, line in enumerate(wrap(cta, 16)[:2]): txt(d, 1500 + k*115, line, 84, (255, 214, 0))
        else:
            for t0, t1, s in caps:
                if t0 <= t < t1: txt(d, 1500, s, 78 if len(s) <= 18 else 62, (255, 255, 255))
        d.text((W-30, 1360), credit, font=F(28), fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0), anchor="rm")
        key = hashlib.md5(im.tobytes()).hexdigest(); p = d_ / f"{i:05d}.png"
        if key in cache: os.link(cache[key], p)
        else: im.save(p, compress_level=1); cache[key] = p
    return d_

REMOTION = HERE.parent / "botbeat" / "remotion"   # shares Botbeat's Remotion overlay

def remotion_overlay(words, hook, cta, total, credit):
    """Animated overlay (spring-in hook bar, karaoke captions, CTA pop, progress bar) rendered by Remotion as a
    transparent PNG sequence. Same frame count and file names as overlay(), so the FFmpeg step is unchanged.
    Returns the frame folder, or None (Node/Chrome missing, BOTBEAT_OVERLAY=pil, any render error) -> PIL overlay."""
    if os.environ.get("BOTBEAT_OVERLAY", CFG.get("overlay_engine", "remotion")) != "remotion":
        return None
    if not (REMOTION / "node_modules").exists() or not shutil.which("npx"):
        print("overlay: Remotion not installed, using PIL overlay", file=sys.stderr); return None
    frames = int(total * FPS)
    props = WORK / "overlay_props.json"
    json.dump({"hook": hook, "cta": cta, "credit": credit, "frames": frames,
               "words": [{"t": float(w["t"]), "d": float(w["d"]), "w": str(w["w"])} for w in words]}, open(props, "w"))
    raw, d_ = WORK / "ov_remotion", WORK / "ov"
    shutil.rmtree(raw, ignore_errors=True); shutil.rmtree(d_, ignore_errors=True)
    try:
        sh("npx", "remotion", "render", "src/index.tsx", "Overlay", str(raw), "--sequence", "--image-format=png",
           f"--props={props}", "--log=error", cwd=str(REMOTION), timeout=int(CFG.get("remotion_timeout", 900)))
        pngs = sorted(raw.glob("*.png"), key=lambda p: int(re.findall(r"(\d+)", p.stem)[-1]))
        if len(pngs) < frames:
            raise RuntimeError(f"Remotion rendered {len(pngs)} of {frames} frames")
        d_.mkdir()
        for i, p in enumerate(pngs[:frames]): p.replace(d_ / f"{i:05d}.png")
        print(f"overlay: Remotion animated overlay, {frames} frames", file=sys.stderr)
        return d_
    except Exception as e:
        print(f"overlay: Remotion failed ({str(e)[:300]}), using PIL overlay", file=sys.stderr)
        shutil.rmtree(d_, ignore_errors=True)
        return None

def send_x(path, s, clip):
    """Telegram gets the landscape file + ready caption, so posting to X is one tap (X has no free upload API)."""
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    cap = (s.get("title", "") + "\n\n" + " ".join(s.get("hashtags", []) if isinstance(s.get("hashtags"), list) else [])).strip()
    camp = campaign.match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url"))) or {}
    if camp.get("must_include"): cap += "\n" + camp["must_include"]
    text = ("X VERSION (16:9). Post this on X, then submit the link within 30 min.\n\n" + cap)[:1000]
    subprocess.run(["curl", "-s", "-F", f"chat_id={chat}", "-F", f"caption={text}", "-F", f"video=@{path}",
                    f"https://api.telegram.org/bot{tok}/sendVideo"], capture_output=True, timeout=180)

def main_narrated(clip, s, src, kind):
    """Stream moment as a NARRATED Short: the AI voiceover script (s["vo"]) spoken over the clip's gameplay, facecam/chat/subtitles
    cropped out, streamer audio muted, caption card per sentence. Transformed content, so IG/YT don't flag it as a reupload."""
    import narrate
    box = None
    try:
        vw, vh = map(int, sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)).strip().split(",")[:2])
        if vw > vh: box = layout.find_facecam(src)
    except Exception as e:
        print("layout: skipped:", e, file=sys.stderr)
    lang = "hi" if HINDI else "en"
    lines = narrate.vo_lines(s["vo"], s.get("hook", ""), lang)
    if lines: lines[0].update({"label": "GTA 6", "big": s.get("hook", "") or lines[0]["big"]})
    info = narrate.render(src, lines, WORK / "short.mp4", WORK / "narr", lang=lang, rate=CFG.get("narrate_rate", "+22%"),
                          pill="GTA 6", credit=f"clip: {clip.get('uploader') or 'streamer'}", cam=box,
                          start=0.0, game_db=CFG.get("narrate_game_db"), max_seconds=CFG.get("max_seconds", 32) - 0.5)
    shutil.copy(WORK / "short.mp4", OUT / "short.mp4")
    must = (campaign.match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url"))) or {}).get("must_include", "")
    s.update({"clean": False, "mode": clip.get("mode", "ai"), "topic": clip.get("topic", ""), "source_url": clip["url"], "kind": kind,
              "moment": clip.get("moment", ""), "moment_at": clip.get("moment_at", ""), "source_views": clip.get("views", 0),
              "uploader": clip.get("uploader", ""), "lang": lang, "must_include": must, "seconds": round(info["seconds"], 1),
              "layout": info["layout"], "structure": "narrated", "voiceover": True, "voice": info["voice"]})
    json.dump(s, open(OUT / "metadata.json", "w"), indent=1)
    print("made", OUT / "short.mp4", round(info["seconds"], 1), "s (narrated)")


def main_hookfirst(clip, s, src, kind, clean, moment):
    """Real-audio clip, hook-first structure (hookfirst.py): cold open -> facecam body + credit -> 0.5x REPLAY -> share end card.
    Streamer captions are burned on the body only. Same out/short.mp4, out/short_x.mp4 and out/metadata.json as before."""
    box = None
    if CFG.get("split_layout", True):
        try:
            vw, vh = map(int, sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)).strip().split(",")[:2])
            if vw > vh: box = layout.find_facecam(src)
        except Exception as e:
            print("layout: skipped:", e, file=sys.stderr)
    try:   # fix 7: streamer voice up front (game/music down) + dead air trimmed so setup -> payoff stays tight
        import tighten
        src, key, why = tighten.prep(src, clip, WORK, CFG); htotal = tighten.total_for(src, CFG)
    except Exception as e:
        print("tighten: skipped:", str(e)[-300:], file=sys.stderr)
        key, why = hookfirst.pick_key(src, clip); htotal = CFG.get("hookfirst_seconds", 18)
    print(f"hook-first: key moment {key}s ({why}), {htotal:.1f}s Short", file=sys.stderr)
    info = hookfirst.render(src, WORK / "short.mp4", hook=s["hook"], cta=s["share_cta"], credit=hookfirst.credit_for(clip),
                            key=key, cam=box, total=htotal, work=WORK)
    final, total = WORK / "short.mp4", info["seconds"]
    b0, b1 = info["timeline"]["body"]
    if CFG.get("streamer_captions", True) and hookfirst.has_audio(src):
        if captions.burn(final, WORK / "short_cap.mp4", start_after=b0, y=CFG.get("caption_y", 1180), end_before=b1,
                         font=str(hookfirst.font_file()) if not HINDI else None):
            final = WORK / "short_cap.mp4"
    shutil.copy(final, OUT / "short.mp4")
    if CFG.get("x_version", True):
        try:
            sh("ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(OUT / "short.mp4"), "-filter_complex", hookfirst.x_filter(info["xsegs"]),
               "-map", "[xv]", "-map", "1:a?", "-t", str(total), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT / "short_x.mp4"), timeout=900)
        except Exception as e:
            print("x version skipped:", str(e)[:300], file=sys.stderr)
    must = (campaign.match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url"))) or {}).get("must_include", "")
    s.update({"clean": clean, "mode": clip.get("mode", "ai"), "topic": clip.get("topic", ""), "source_url": clip["url"], "kind": kind,
              "moment": clip.get("moment", ""), "moment_at": clip.get("moment_at", ""), "source_views": clip.get("views", 0),
              "uploader": clip.get("uploader", ""), "lang": "hi" if HINDI else "en", "must_include": must, "seconds": round(total, 1),
              "layout": "split" if box else "frame", "structure": "hookfirst", "key_at": info["key_at"], "key_why": why,
              "share_cta": s["share_cta"], "voiceover": False})
    json.dump(s, open(OUT / "metadata.json", "w"), indent=1)
    print("made", OUT / "short.mp4", round(total, 1), "s (hook-first)")

def main():
    clip = json.loads(sys.argv[1])
    shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True); OUT.mkdir(exist_ok=True)
    kind = clip.get("kind", "video")
    u = clip["url"].split("?")[0].rstrip("/")
    if ("twitch.tv" in u or "kick.com" in u) and kind != "live":
        # a bare channel link (twitch.tv/name) is a LIVE stream: record it, never "download" it (that never ends)
        is_live = "/clip/" not in u and "/videos/" not in u and "clips.twitch.tv" not in u and len(u.split("/")) <= 4
        kind = clip["kind"] = "live" if is_live else "twitch_clip"
    try:
        if kind == "live":
            src = moments.record_live(clip["url"], WORK, CFG.get("live_record_seconds", 420))
            try:   # AI reads what was said at each loud spike and picks the moment that fits the genre
                import genre_moments; hit = genre_moments.live_pick(src, clip)
            except Exception: hit = None
            hit = hit or moments.audio_peak(src)
            st = max(0, (hit[0] if hit else 0) - moments.PRE); print("live moment:", hit, file=sys.stderr)
            cut = WORK / "src.mp4"; sh("ffmpeg", "-y", "-v", "error", "-ss", str(st), "-i", str(src), "-t", str(moments.WIN), "-c", "copy", str(cut)); src = cut
        elif kind == "stream" or (kind == "video" and clip.get("duration", 0) > 75):
            win = moments.best_window(clip, WORK)
            src = download(clip["url"], section=win[:2] if win else None)
            if win:
                clip["moment"] = win[2]
                clip["said"] = moments.subs_text(clip["url"], win[0], win[1], WORK)
                clip["moment_at"] = f"{int(win[0])//3600}:{int(win[0])%3600//60:02d}:{int(win[0])%60:02d}"
        else:
            src = download(clip["url"])
    except RuntimeError as e:
        if "not a bot" in str(e):   # blocked, not broken: exit 3 so the workflow skips cleanly and retries this clip next run
            print(e, file=sys.stderr); sys.exit(3)
        raise
    if clip.get("source", "").startswith("trend") and is_slideshow(src):
        print("This trend clip is a photo slideshow, not real footage. Skipping it.", file=sys.stderr); sys.exit(5)
    if not (clip.get("title") or "").strip():
        print("Clip has no title/description (metadata lookup failed). Skipping, clip retried next run.", file=sys.stderr); sys.exit(3)
    s = campaign.apply(check_script(script(clip)), clip)   # paid clipping campaign? add its credit/#ad/tags
    # Clean mode: campaigns that ban AI-generated videos (Reach: Lacy, Clix, Jason...) get NO AI voiceover.
    # Only the streamer's real footage + real audio, facecam split, and word captions of what the streamer says.
    camp = campaign.match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url"))) or {}
    clean = bool(camp.get("clean")) or bool(CFG.get("clean_mode")) or os.environ.get("GTA_CLEAN") == "1"
    moment = kind in ("live", "stream", "twitch_clip") or clip.get("moment")
    # Hook-first on a real-audio clip (stream moment / clean campaign): the cold-open hook replaces the AI intro,
    # so there is no voiceover at all - only the streamer's real audio.
    hf_real = HOOKFIRST and bool(moment or clean)
    share = hookfirst.share_cta(s, clip) if HOOKFIRST else ""
    if share:
        s["share_cta"] = share
        s["description"] = s.get("description", "").rstrip() + "\n\n" + hookfirst.cta_sentence(share)
    if moment and not clean and CFG.get("narrate_moments", True):
        import narrate
        if narrate.enabled(CFG):
            try:
                return main_narrated(clip, s, src, kind)
            except Exception as e:   # never lose a run: fall back to the hook-first / classic render
                print("narrated render failed, using the old render:", str(e)[-600:], file=sys.stderr)
    if clean or hf_real:
        sh("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.3", "-q:a", "9", str(WORK / "vo.mp3"))
        words = []
        print(f"clean mode: no AI voiceover ({camp.get('name', 'config')})" if clean else "hook-first: real audio only, no AI voiceover", file=sys.stderr)
    else:
        words = asyncio.run(tts(s["vo"], str(WORK / "vo.mp3")))
    vo_len = 0.0 if (clean or hf_real) else duration(WORK / "vo.mp3")
    if hf_real:
        try:
            return main_hookfirst(clip, s, src, kind, clean, moment)
        except Exception as e:   # never lose a run to the new edit: fall back to the classic render (with its AI intro)
            print("hook-first render failed, using the classic render:", str(e)[-600:], file=sys.stderr)
            if not clean:
                words = asyncio.run(tts(s["vo"], str(WORK / "vo.mp3"))); vo_len = duration(WORK / "vo.mp3")
    if moment:   # viral stream moment: the reaction IS the content -> uncut footage, real audio after the intro
        total = min(CFG.get("moment_seconds", 40), duration(src))
        total = max(total, vo_len + 1.5)
        segs = [(0.0, min(duration(src), total))]
    elif clean:   # no narrator to time against: keep the real clip, uncut
        total = min(CFG.get("moment_seconds", 40), duration(src))
        segs = [(0.0, total)]
    else:
        total = min(CFG["max_seconds"], vo_len + 2.8)
        segs = pick_segments(src, total)
    BG = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=28:5,eq=brightness=-0.25"
    FG = "scale=1080:-2:flags=lanczos,crop=1080:'min(ih,1000)'"
    # Playbook step 6: streams with a webcam box get facecam on top, gameplay below. Everything else keeps the blur frame.
    box = None
    if moment and CFG.get("split_layout", True):
        try:
            vw, vh = map(int, sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)).strip().split(","))
            if vw > vh: box = layout.find_facecam(src)
        except Exception as e:
            print("layout: skipped:", e, file=sys.stderr)
    fc, L = "", []
    for i, (ss, ln) in enumerate(segs):
        if box:
            fc += f"[0:v]trim=start={ss}:duration={ln},setpts=PTS-STARTPTS,fps={FPS}[t{i}];" + layout.split_filter(f"[t{i}]", f"s{i}", box)
        else:
            fc += (f"[0:v]trim=start={ss}:duration={ln},setpts=PTS-STARTPTS,fps={FPS},split[a{i}][b{i}];"
                   f"[a{i}]{BG}[bg{i}];[b{i}]{FG}[fg{i}];[bg{i}][fg{i}]overlay=0:(H-h)/2,setsar=1[s{i}];")
        L.append(f"[s{i}]")
    fc += "".join(L) + f"concat=n={len(segs)}:v=1:a=0,trim=duration={total}[base];"
    # Voice clash fix: while the narrator talks, the original audio is muffled (speech filtered out, only low rumble/beat
    # stays), so two voices never talk over each other. When the narrator stops, the original fades back in at full.
    E = 0.0 if clean else vo_len + 0.25
    duck = (f"[0:a]asplit=2[o1][o2];"
            f"[o1]lowpass=f=280,lowpass=f=280,volume='0.5*(1-clip((t-{E:.2f})/0.4,0,1))':eval=frame[m];"
            f"[o2]volume='{1.0 if moment else 0.8}*clip((t-{E:.2f})/0.4,0,1)':eval=frame[f];"
            f"[m][f]amix=inputs=2:normalize=0,atrim=0:{total}[bed];")
    fc += f"[base][1:v]overlay=0:0[v];[2:a]adelay=150|150,volume=1.6,apad[vo];{duck}[vo][bed]amix=inputs=2:duration=shortest:normalize=0,atrim=0:{total},loudnorm=I=-15[a]"
    credit = f"clip: {clip.get('uploader') or 'source'}"
    ov = remotion_overlay(words, s["hook"], s["cta"], total, credit) or overlay(words, s["hook"], s["cta"], total, credit)
    has_audio = "audio" in sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(src))
    if not has_audio:
        fc = fc.replace(f"{duck}[vo][bed]amix=inputs=2:duration=shortest:normalize=0,", "[vo]")
    sh("ffmpeg", "-v", "error", "-y", "-i", str(src), "-framerate", str(FPS), "-i", str(ov / "%05d.png"), "-i", str(WORK / "vo.mp3"),
       "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", str(total), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(WORK / "short.mp4"), timeout=1500)
    final = WORK / "short.mp4"
    # Playbook step 6: word-by-word captions of what the streamer says after the intro (narrator words are already captioned)
    if (moment or clean) and CFG.get("streamer_captions", True) and has_audio:
        if captions.burn(final, WORK / "short_cap.mp4", start_after=E, y=CFG.get("caption_y", 1180)): final = WORK / "short_cap.mp4"
    xbody, _acc = [], 0.0   # body segments trimmed to the body's real length, so the X timeline matches the Short
    for ss, ln in segs:
        if _acc >= total: break
        xbody.append((ss, min(ln, total - _acc))); _acc += ln
    hfinfo = None
    if HOOKFIRST:   # narrated Short: cold open before it, 0.5x replay + share end card after it
        try:
            key, why = hookfirst.pick_key(src, clip); print("hook-first key:", key, why, file=sys.stderr)
            hfinfo = hookfirst.render(src, WORK / "short_hf.mp4", hook=s["hook"], cta=share, credit=credit, key=key,
                                      cam=box, work=WORK, body_file=final)
            final = WORK / "short_hf.mp4"; total = hfinfo["seconds"]
        except Exception as e:
            print("hook-first wrap failed, posting the plain Short:", str(e)[-400:], file=sys.stderr); hfinfo = None
    shutil.copy(final, OUT / "short.mp4")
    # X version: landscape 16:9 (X plays landscape best; Lacy/Clix count X views). Same clip, same audio as the Short.
    if CFG.get("x_version", True):
        try:
            if hfinfo:
                xf = hookfirst.x_filter(hfinfo["xsegs"], body_segs=xbody).replace("[xv]", f",trim=duration={total}[xv]")
            else:
                xf = ""
                for i, (ss, ln) in enumerate(segs):
                    xf += (f"[0:v]trim=start={ss}:duration={ln},setpts=PTS-STARTPTS,fps={FPS},"
                           f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[x{i}];")
                xf += "".join(f"[x{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0,trim=duration={total}[xv]"
            sh("ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(OUT / "short.mp4"), "-filter_complex", xf,
               "-map", "[xv]", "-map", "1:a?", "-t", str(total), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT / "short_x.mp4"), timeout=900)
            # send_short.py now sends the X video WITH the YouTube link + ready captions after upload
        except Exception as e:
            print("x version skipped:", str(e)[:300], file=sys.stderr)
    if hfinfo: s.update({"structure": "hookfirst", "key_at": hfinfo["key_at"], "share_cta": share})
    s.update({"clean": clean, "mode": clip.get("mode", "ai"), "topic": clip.get("topic", ""), "source_url": clip["url"], "kind": kind, "moment": clip.get("moment", ""), "moment_at": clip.get("moment_at", ""), "source_views": clip.get("views", 0), "uploader": clip.get("uploader", ""), "lang": "hi" if HINDI else "en", "must_include": (campaign.match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url"))) or {}).get("must_include", ""), "seconds": round(total, 1), "layout": "split" if box else "frame"})
    json.dump(s, open(OUT / "metadata.json", "w"), indent=1)
    print("made", OUT / "short.mp4", round(total, 1), "s")

if __name__ == "__main__":
    main()