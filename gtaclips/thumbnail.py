#!/usr/bin/env python3
"""Playbook step 3: thumbnails. Picks the most striking frames of a video and puts 1-3 huge words on them
(the style THUMBNAILS.md says wins). Makes several candidates; the fresh reviewer picks the best.
Free: frames + PIL text. Optional: if GEMINI_API_KEY can use an image model (THUMB_IMAGE_MODEL), it restyles the
best frame into a punchier thumbnail. Usage: python thumbnail.py video.mp4 "WORDS ONE|WORDS TWO" out_dir"""
import base64, json, os, pathlib, re, subprocess, sys, urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageStat
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def candidate_frames(video, n=4, size=(1280, 720)):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                               capture_output=True, text=True).stdout or 10)
    scored = []
    for i in range(12):
        t = dur * (i + 0.5) / 12
        b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-f", "image2", "-c:v", "png", "-"],
                           capture_output=True).stdout
        if not b: continue
        p = pathlib.Path(f"/tmp/thf_{i}.png"); p.write_bytes(b)
        im = Image.open(p).convert("RGB")
        st = ImageStat.Stat(im.convert("HSV"))
        scored.append((st.mean[1] + st.stddev[2], p))   # saturation + contrast = pops in the feed
    return [Image.open(p).convert("RGB") for _, p in sorted(scored, reverse=True)[:n]]

def cover(im, size):
    W, H = size; r = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))

def draw_words(im, words, color=(255, 230, 0)):
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.35)).enhance(1.15)
    d = ImageDraw.Draw(im); W, H = im.size
    words = words.upper().strip()
    size = int(H * 0.26)
    while size > 40:
        f = ImageFont.truetype(FONT, size)
        if d.textlength(words, font=f) < W * 0.9: break
        size -= 6
    x = (W - d.textlength(words, font=f)) / 2; y = H - size * 1.35
    d.text((x, y), words, font=f, fill=color, stroke_width=max(6, size // 9), stroke_fill=(0, 0, 0))
    return im

def ai_restyle(im, words):
    key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("THUMB_IMAGE_MODEL")
    if not (key and model): return None
    try:
        import io
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
        body = {"contents": [{"parts": [{"text": f"Make this GTA 6 game frame a bold YouTube thumbnail: boost color and contrast, keep the "
                                                 f"real game scene, add the huge text '{words.upper()}' with thick black outline. 16:9."},
                                        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}]}]}
        rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                    json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        d = json.loads(urllib.request.urlopen(rq, timeout=180).read())
        for p in d["candidates"][0]["content"]["parts"]:
            if "inline_data" in p or "inlineData" in p:
                data = (p.get("inline_data") or p.get("inlineData"))["data"]
                return cover(Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB"), (1280, 720))
    except Exception as e:
        print("thumbnail: AI restyle skipped:", str(e)[:200], file=sys.stderr)
    return None

def make(video, word_options, out_dir):
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    frames = [cover(f, (1280, 720)) for f in candidate_frames(video)]
    paths = []
    colors = [(255, 230, 0), (255, 255, 255), (0, 240, 255)]
    for i, words in enumerate(word_options[:3]):
        base = frames[i % len(frames)]
        im = ai_restyle(base, words) or draw_words(base.copy(), words, colors[i % 3])
        p = out_dir / f"thumb_{i}.jpg"; im.save(p, "JPEG", quality=92); paths.append(p)
    return paths

def pick(paths, title):
    """Fresh reviewer picks the best thumbnail (Gemini vision). Falls back to the first."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or len(paths) < 2: return paths[0]
    try:
        parts = [{"text": f"You are a fresh reviewer. Video title: {title!r}. Which thumbnail would get the most clicks on YouTube "
                          f"while staying honest to a GTA 6 video? Text must be readable on a phone. Reply ONLY the number 0-{len(paths)-1}."}]
        parts += [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}} for p in paths]
        rq = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                                    json.dumps({"contents": [{"parts": parts}]}).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        t = "".join(p.get("text", "") for p in json.loads(urllib.request.urlopen(rq, timeout=90).read())["candidates"][0]["content"]["parts"])
        return paths[int(re.search(r"\d", t).group(0)) % len(paths)]
    except Exception as e:
        print("thumbnail: pick fallback:", str(e)[:150], file=sys.stderr); return paths[0]

if __name__ == "__main__":
    ps = make(sys.argv[1], sys.argv[2].split("|"), sys.argv[3]); print(pick(ps, sys.argv[2]))
