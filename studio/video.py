"""Narrated vertical video (1080x1920): AI script -> Pollinations images (free, no key) -> edge-tts voice per scene
-> slow zoom/pan per scene (ffmpeg) -> burned-in word captions -> MP4 under 50 MB."""
import asyncio, json, os, pathlib, random, re, shutil, subprocess, time, urllib.parse, urllib.request, urllib.error
import llm, render, prompts

W, H, FPS = 1080, 1920, 30
ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def voice():
    try:
        return json.load(open(ROOT / "botbeat" / "config.json")).get("voice", "en-US-AndrewNeural")
    except Exception:
        return "en-US-AndrewNeural"


def font(size):
    from PIL import ImageFont
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    try:
        p = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True).stdout.strip()
        if p:
            return ImageFont.truetype(p, size)
    except Exception:
        pass
    return ImageFont.load_default(size)


def ensure_tools():
    if not shutil.which("ffmpeg"):
        render.apt("ffmpeg")
    render.ensure_fonts()
    render.ensure_module("edge_tts", "edge-tts")
    render.ensure_module("PIL", "pillow")


# ---------------- script ----------------
def _check_script(t):
    d = llm.json_obj(t)
    sc = d.get("scenes") or []
    if not (3 <= len(sc) <= 10) or not all(s.get("narration") and s.get("image_prompt") for s in sc):
        raise llm.AIError("video script missing scenes")
    return d


def write_script(req, old=None, instruction=None):
    if instruction:
        p = (f"Here is the current video script JSON (original request: <<<{req}>>>):\n{json.dumps(old, ensure_ascii=False)}\n\n"
             f"Revise it for this instruction: <<<{instruction}>>>. Keep scenes that don't need changing identical "
             "(same image_prompt text) so their images can be reused. Return the full JSON.")
    else:
        p = f"Make a narrated vertical video about:\n<<<{req}>>>"
    s = llm.ask(p, system=prompts.VIDEO_SYSTEM, max_tokens=6000, check=_check_script, temp=0.7, label="video-script")
    s["scenes"] = s["scenes"][:8]
    style = s.get("style") or ""
    for sc in s["scenes"]:
        sc.setdefault("seed", random.randint(1, 10 ** 6))
    if old:  # reuse seeds for unchanged prompts so the same images come back
        prev = {o.get("image_prompt"): o.get("seed") for o in old.get("scenes", [])}
        for sc in s["scenes"]:
            if sc["image_prompt"] in prev and prev[sc["image_prompt"]]:
                sc["seed"] = prev[sc["image_prompt"]]
    return s


# ---------------- images ----------------
def pollinations(prompt, seed, dst, budget_until):
    q = urllib.parse.quote(prompt[:600] + ", cinematic, highly detailed, vertical 9:16 composition, no text, no watermark")
    url = f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={seed}&model=flux"
    from PIL import Image
    while time.time() < budget_until:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 studio"})
            with urllib.request.urlopen(req, timeout=150) as r:
                data = r.read()
            dst.write_bytes(data)
            Image.open(dst).verify()
            return True
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:400]
            except Exception:
                pass
            m = re.search(r"[Rr]etry after (\d+)", body)
            wait = int(m.group(1)) + 2 if m else 20
            print(f"   image wait {wait}s (HTTP {e.code})")
            time.sleep(min(wait, max(0, budget_until - time.time())))
        except Exception as e:
            print("   image error:", str(e)[:120])
            time.sleep(10)
    return False


def gradient_card(text, idx, dst):
    from PIL import Image, ImageDraw
    pals = [((20, 30, 70), (120, 40, 140)), ((10, 60, 70), (20, 140, 120)), ((60, 20, 40), (200, 80, 60)),
            ((15, 25, 45), (50, 100, 200)), ((40, 30, 10), (190, 130, 30))]
    a, b = pals[idx % len(pals)]
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        t = y / H
        c = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    lines = wrap(text, 16)[:4]
    for k, ln in enumerate(lines):
        d.text((W // 2, H // 2 - 200 + k * 110), ln, font=font(84), fill=(255, 255, 255), anchor="mm")
    im.save(dst)


# ---------------- voice + captions ----------------
async def _tts(text, mp3, v):
    import edge_tts
    words = []
    c = edge_tts.Communicate(text, v, rate="+5%", boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"t": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "w": ch["text"]})
    return words


def tts(text, mp3, v):
    saved = {}
    try:
        for i in range(6):
            try:
                w = asyncio.run(_tts(text, mp3, v))
                if os.path.getsize(mp3) > 1000:
                    return w
            except Exception as e:
                print("   tts retry:", str(e)[:120]); time.sleep(3 + 3 * i)
                if "login" in str(e) or "proxy" in str(e).lower():   # sandbox proxies with credentials confuse aiohttp
                    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
                        if k in os.environ:
                            saved[k] = os.environ.pop(k)
        raise RuntimeError("text-to-speech failed")
    finally:
        os.environ.update(saved)


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def wrap(s, n):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + ([cur] if cur else [])


def caption_png(text, dst, top=None):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if top:
        for k, ln in enumerate(wrap(top.upper(), 16)[:3]):
            d.text((W // 2, 230 + k * 105), ln, font=font(88), fill=(255, 214, 0), stroke_width=9, stroke_fill=(0, 0, 0), anchor="mm")
    if text:
        lines = wrap(text.upper(), 18)[:2]
        for k, ln in enumerate(lines):
            d.text((W // 2, 1420 + k * 100), ln, font=font(80), fill=(255, 255, 255), stroke_width=9, stroke_fill=(0, 0, 0), anchor="mm")
    im.save(dst)


# ---------------- main ----------------
def make_video(req, old=None, instruction=None, deadline=None):
    ensure_tools()
    work = render.WORK / f"video-{int(time.time())}"
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    s = write_script(req, old, instruction)
    v = voice()
    deadline = deadline or (time.time() + 1500)
    img_until = min(deadline - 300, time.time() + 60 * min(9, 1.5 * len(s["scenes"])))
    total, caps, clips, auds = 0.0, [], [], []
    for i, sc in enumerate(s["scenes"]):
        mp3 = work / f"v{i}.mp3"
        words = tts(sc["narration"], mp3, v)
        d = dur(mp3) + 0.35
        img = work / f"img{i}.jpg"
        if not pollinations(sc["image_prompt"], sc["seed"], img, img_until):
            gradient_card(sc.get("on_screen") or s.get("title", ""), i, img := work / f"img{i}.png")
        frames = int(d * FPS)
        zin = i % 2 == 0
        z = f"min(1+0.10*on/{frames},1.10)" if zin else f"max(1.10-0.10*on/{frames},1.0)"
        xs = ["iw/2-(iw/zoom/2)", "(iw-iw/zoom)*on/" + str(frames), "(iw-iw/zoom)*(1-on/" + str(frames) + ")"][i % 3]
        trim = "crop=iw:ih*0.94:0:0," if str(img).endswith(".jpg") else ""   # cut the free-tier corner watermark
        vf = (f"{trim}scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='{z}':x='{xs}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
              f"fade=t=in:st=0:d=0.25,fade=t=out:st={max(0, d-0.25):.2f}:d=0.25,format=yuv420p")
        clip = work / f"c{i}.mp4"
        render.sh(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(img), "-vf", vf, "-frames:v", str(frames),
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), str(clip)], timeout=600)
        clips.append(clip); auds.append((mp3, d))
        # caption chunks of up to 3 words, timed to the voice
        chunk = []
        groups = []
        for w in words:
            chunk.append(w)
            if len(chunk) == 3 or w["w"].endswith((".", ",", "?", "!", ";", ":")):
                groups.append(chunk); chunk = []
        if chunk:
            groups.append(chunk)
        for k, g in enumerate(groups):
            t0 = total + g[0]["t"]
            t1 = total + (groups[k + 1][0]["t"] if k + 1 < len(groups) else d - 0.1)
            caps.append((t0, t1, " ".join(x["w"] for x in g)))
        total += d
    # caption track as a PNG sequence with durations (ffconcat)
    hook = s.get("hook") or ""
    lst, t, n = ["ffconcat version 1.0"], 0.0, 0

    def add(text, d_, top=None):
        nonlocal n
        p = work / f"cap{n:04d}.png"; n += 1
        caption_png(text, p, top)
        lst.append(f"file '{p}'"); lst.append(f"duration {max(d_, 0.04):.3f}")
    for t0, t1, text in caps:
        top = hook if t0 < 2.8 else None
        if t0 > t + 0.04:
            add("", t0 - t, hook if t < 2.8 else None)
        add(text, t1 - t0, top)
        t = t1
    if total > t:
        add("", total - t)
    lst.append(lst[-2])  # concat demuxer needs the last file repeated
    (work / "caps.txt").write_text("\n".join(lst))
    (work / "clips.txt").write_text("".join(f"file '{c}'\n" for c in clips))
    (work / "auds.txt").write_text("".join(f"file '{a}'\n" for a, _ in auds))
    # pad each voice to its scene length, then join
    padded = []
    for i, (a, d) in enumerate(auds):
        pa = work / f"pa{i}.wav"
        render.sh(["ffmpeg", "-v", "error", "-y", "-i", str(a), "-af", f"apad,atrim=0:{d:.3f}", "-ar", "44100", "-ac", "2", str(pa)])
        padded.append(pa)
    (work / "auds.txt").write_text("".join(f"file '{a}'\n" for a in padded))
    out = render.WORK / "out" / f"video-{int(time.time())}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    for crf in (21, 26, 30):
        render.sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "clips.txt"),
                   "-f", "concat", "-safe", "0", "-i", str(work / "caps.txt"),
                   "-f", "concat", "-safe", "0", "-i", str(work / "auds.txt"),
                   "-filter_complex", "[1:v]format=rgba[c];[0:v][c]overlay=0:0:format=auto,format=yuv420p[v];[2:a]loudnorm=I=-15[a]",
                   "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", str(crf), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], timeout=1200)
        if out.stat().st_size < 48 * 1024 * 1024:
            break
    cap = s.get("title", "")
    return {"files": [("video", out, cap)], "source": {"script": s}, "seconds": round(total, 1)}
