#!/usr/bin/env python3
"""Turn one viral clip into a finished Botbeat Short (1080x1920, <=28 s):
hook bar on top, the real footage in the middle, word-timed captions, pro English voiceover
(signature local OmniVoice narrator, edge-tts/gTTS as backups).
Usage: python make_short.py '<json from find_clip.py>'  -> writes out/short.mp4 + out/metadata.json"""
import json, os, sys, re, asyncio, pathlib, subprocess, shutil, hashlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from ai import ask  # same free AI chain: Gemini -> Groq -> OpenRouter
HERE = pathlib.Path(__file__).resolve().parent
CFG = json.load(open(HERE / "config.json"))
OUT = HERE / "out"; WORK = pathlib.Path("/tmp/botbeat")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H, FPS = 1080, 1920, 30

def sh(*a, **k):
    r = subprocess.run(list(a), capture_output=True, text=True, **k)
    if r.returncode: raise RuntimeError(" ".join(map(str, a))[:200] + "\n" + r.stderr[-1500:])
    return r.stdout

# Order matters: YouTube returns HTTP 403 for anonymous datacenter IPs unless the player
# client is one it allows without a PO token. We verified on a fresh GH-style runner that
# `android` and `android_vr` download cleanly with no JS/PO setup; `web`/`mweb`/`ios`
# demand a GVS PO token and fail with 403. So try the working ones FIRST.
ATTEMPTS = [
    ["--extractor-args", "youtube:player_client=android"],
    ["--extractor-args", "youtube:player_client=android_vr"],
    ["--extractor-args", "youtube:player_client=tv"],
    ["--extractor-args", "youtube:player_client=web"],
    ["--extractor-args", "youtube:player_client=mweb"],
    ["--extractor-args", "youtube:player_client=ios"],
]

def trim(p):
    lim = CFG['max_clip_minutes'] * 60
    try:
        if duration(p) > lim + 1:
            tmp = p.with_name("src_trim.mp4")
            sh("ffmpeg", "-y", "-v", "error", "-i", str(p), "-t", str(lim), "-c", "copy", str(tmp)); tmp.replace(p)
    except Exception as e:
        print("trim skipped:", e, file=sys.stderr)

def download(url):
    """Try several YouTube clients, first direct, then through the free Cloudflare WARP proxy if the workflow set one up.
    YouTube now demands a signed-in session for datacenter IPs (GitHub runners), so authenticated cookies (the
    YT_COOKIES secret -> cookies.txt) are required. The local PO-token provider (pot service on :4416) is used
    when cookies are present to pass the bot check."""
    dst = WORK / "src.mp4"
    if os.path.exists(url): shutil.copy(url, dst); return dst
    base = ["yt-dlp", "-q", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
            "--hls-prefer-native", "--concurrent-fragments", "4", "-o", str(dst)]
    # No --download-sections: it hands the download to ffmpeg, which can't use the WARP SOCKS proxy (ffmpeg exit 8).
    # yt-dlp downloads natively (proxy works), then we trim locally.
    has_cookies = os.path.exists("cookies.txt")
    if has_cookies: base += ["--cookies", "cookies.txt"]
    # With a signed-in session, prefer the web player that mints a PO token via the local provider (port 4416).
    attempts = ATTEMPTS[:]
    if has_cookies:
        attempts.insert(0, ["--extractor-args", "youtube:player_client=web",
                            "--extractor-args", "youtube:po_token=bgutil:http+web"])
    proxies = [None] + ([os.environ["YTDLP_PROXY"]] if os.environ.get("YTDLP_PROXY") else [])
    last = None
    for px in proxies:
        for extra in attempts:
            cmd = base + extra + (["--proxy", px] if px else []) + [url]
            try:
                for f in WORK.glob("src.*"): f.unlink()
                sh(*cmd, timeout=600)
                if dst.exists() and dst.stat().st_size > 100000:
                    trim(dst)
                    print("downloaded via", px or "direct", extra or "default", file=sys.stderr); return dst
            except Exception as e:
                err = str(e)
                if "[Piracy]" in err or "primarily used for piracy" in err.lower():
                    raise RuntimeError(f"yt-dlp blocked this domain (piracy list): {url}") from e
                if "not a bot" in err or "LOGIN_REQUIRED" in err or "Sign in" in err:
                    last = RuntimeError(
                        "YouTube blocked this download (Sign in to confirm you’re not a bot) because the "
                        "GitHub runner looks like a robot. Fix: export cookies from a logged-in Chrome via the "
                        "'Get cookies.txt LOCALLY' extension and set them as the Actions secret YT_COOKIES "
                        "(or commit them as botbeat/cookies.txt), then re-run this workflow. No code change needed.")
                else:
                    last = e
                print("download attempt failed:", px or "direct", extra or "default", file=sys.stderr)
    raise last or RuntimeError("download failed")

def duration(p):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)).strip())

def script(clip):
    prompt = f"""You write YouTube Shorts for Botbeat, an English AI/robot news channel with calm, professional narration.
Source clip title: {clip['title']}
Source description: {clip['desc'][:1200]}
Uploader: {clip.get('uploader','')}  Views: {clip.get('views',0)}
Rules: only facts stated in the title/description (never invent numbers), no slang, no crypto, no hype words like 'insane'.
Return ONLY JSON with keys:
"hook": top-of-screen hook, max 5 words, ALL CAPS, curiosity gap;
"vo": voiceover, 50-65 words, first sentence is the hook spoken, one surprising fact in the middle, ends with a short question to the viewer;
"cta": max 5 words ALL CAPS question shown at the end, e.g. "WOULD YOU TRUST IT?";
"title": viral title under 70 characters with one emoji, ending with " #shorts #ai";
"description": 2-3 sentences + credit line "Clip: <uploader>" + 8 hashtags;
"tags": list of 14 tags."""
    txt = ask(prompt + "\nAnswer with valid JSON only.", min_len=200)
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0))

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
    clean = re.sub(r"[^\w\s.,!?'%$-]", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    words = omni_tts(clean, mp3)
    if words: return words
    for voice in [CFG["voice"], "en-US-GuyNeural", "en-US-ChristopherNeural"]:
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
    gTTS(clean, lang="en", tld="com").save(mp3)
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

def main():
    clip = json.loads(sys.argv[1])
    shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True); OUT.mkdir(exist_ok=True)
    src = download(clip["url"])
    s = script(clip)
    words = asyncio.run(tts(s["vo"], str(WORK / "vo.mp3")))
    vo_len = duration(WORK / "vo.mp3")
    total = min(CFG["max_seconds"], vo_len + 2.8)
    segs = pick_segments(src, total)
    BG = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=28:5,eq=brightness=-0.25"
    FG = "scale=1080:-2:flags=lanczos,crop=1080:'min(ih,1000)'"
    fc, L = "", []
    for i, (ss, ln) in enumerate(segs):
        fc += (f"[0:v]trim=start={ss}:duration={ln},setpts=PTS-STARTPTS,fps={FPS},split[a{i}][b{i}];"
               f"[a{i}]{BG}[bg{i}];[b{i}]{FG}[fg{i}];[bg{i}][fg{i}]overlay=0:(H-h)/2,setsar=1[s{i}];")
        L.append(f"[s{i}]")
    fc += "".join(L) + f"concat=n={len(segs)}:v=1:a=0,trim=duration={total}[base];"
    fc += f"[base][1:v]overlay=0:0[v];[2:a]adelay=150|150,volume=1.6[vo];[0:a]volume=0.12,atrim=0:{total}[bed];[vo][bed]amix=inputs=2:duration=first:normalize=0,apad,atrim=0:{total},loudnorm=I=-15[a]"
    ov = overlay(words, s["hook"], s["cta"], total, f"clip: {clip.get('uploader') or 'source'}")
    has_audio = "audio" in sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(src))
    if not has_audio:
        fc = fc.replace(f"[0:a]volume=0.12,atrim=0:{total}[bed];[vo][bed]amix=inputs=2:duration=first:normalize=0,apad", "[vo]apad")
    sh("ffmpeg", "-v", "error", "-y", "-i", str(src), "-framerate", str(FPS), "-i", str(ov / "%05d.png"), "-i", str(WORK / "vo.mp3"),
       "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", str(total), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(WORK / "short.mp4"), timeout=1500)
    shutil.copy(WORK / "short.mp4", OUT / "short.mp4")
    s.update({"source_url": clip["url"], "source_views": clip.get("views", 0), "seconds": round(total, 1)})
    json.dump(s, open(OUT / "metadata.json", "w"), indent=1)
    print("made", OUT / "short.mp4", round(total, 1), "s")

if __name__ == "__main__":
    main()