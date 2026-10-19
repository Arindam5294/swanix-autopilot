"""Motion-graphics renderer for Studio videos (1080x1920, 30 fps, CPU only).

A storyboard is a list of scenes, each one of a fixed set of animated templates:
  hook_title, stat_counter, bar_chart, line_chart, comparison, list_reveal, quote, cta
Every frame is composed with numpy/PIL/OpenCV (eased motion, gradient glow background, particles,
kinetic word-by-word captions, a top progress bar) and piped as raw RGB into ffmpeg.
Rendering is split across CPU cores: each worker encodes a frame range, the parts are joined losslessly.
No AI images anywhere."""
import functools, math, os, pathlib, re, shutil, subprocess, urllib.request

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
CX = W // 2
WORK = pathlib.Path(os.environ.get("STUDIO_WORK", "/tmp/studio"))
FONT_DIR = WORK / "fonts"
EMOJI_DIR = WORK / "emoji"
TYPES = ("hook_title", "stat_counter", "bar_chart", "line_chart", "comparison", "list_reveal", "quote", "cta")
CAP_Y = 1545          # caption centre line (clear of the Shorts UI at the bottom)
CONTENT_TOP, CONTENT_BOTTOM = 250, 1360

THEMES = {
    "violet": dict(bg=(9, 8, 20), bg2=(20, 12, 42), g1=(139, 92, 246), g2=(60, 242, 166), acc=(60, 242, 166), acc2=(150, 110, 255)),
    "ocean": dict(bg=(4, 12, 26), bg2=(6, 30, 52), g1=(30, 144, 255), g2=(0, 212, 200), acc=(0, 229, 255), acc2=(255, 196, 0)),
    "ember": dict(bg=(18, 7, 6), bg2=(44, 14, 10), g1=(255, 94, 58), g2=(255, 176, 50), acc=(255, 204, 64), acc2=(255, 104, 72)),
    "forest": dict(bg=(4, 15, 11), bg2=(8, 36, 24), g1=(34, 197, 94), g2=(163, 230, 53), acc=(190, 242, 100), acc2=(34, 211, 120)),
    "rose": dict(bg=(16, 6, 16), bg2=(40, 10, 38), g1=(236, 72, 153), g2=(139, 92, 246), acc=(255, 214, 10), acc2=(244, 114, 182)),
}
WHITE = (246, 246, 250)
MUTED = (178, 182, 200)


# ---------------- easing ----------------
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_out(x):
    x = clamp(x); return 1 - (1 - x) ** 3


def ease_in(x):
    x = clamp(x); return x ** 3


def ease_io(x):
    x = clamp(x); return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def back_out(x, s=1.6):
    x = clamp(x); return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


# ---------------- fonts ----------------
POPPINS = {"xb": "Poppins-ExtraBold", "b": "Poppins-Bold", "sb": "Poppins-SemiBold", "m": "Poppins-Medium"}
FALLBACK = ["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def ensure_fonts():
    """Poppins from Google Fonts (OFL) once per machine; system bold sans if offline."""
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for name in POPPINS.values():
        p = FONT_DIR / f"{name}.ttf"
        if p.exists() and p.stat().st_size > 50000:
            continue
        try:
            req = urllib.request.Request(f"https://github.com/google/fonts/raw/main/ofl/poppins/{name}.ttf",
                                         headers={"User-Agent": "studio"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) > 50000:
                p.write_bytes(data)
        except Exception as e:
            print("   font download failed:", str(e)[:80])


@functools.lru_cache(None)
def font(wt, size):
    p = FONT_DIR / f"{POPPINS.get(wt, POPPINS['b'])}.ttf"
    if p.exists():
        return ImageFont.truetype(str(p), size)
    for f in FALLBACK:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def wrap(text, f, max_w):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and f.getlength(t) > max_w:
            lines.append(cur); cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


@functools.lru_cache(4096)
def fit(text, wt, max_w, max_lines, size, min_size=28):
    """Largest size <= size where text wraps into <= max_lines lines that all fit max_w."""
    s = size
    while True:
        f = font(wt, s)
        lines = wrap(text, f, max_w)
        if (len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines)) or s <= min_size:
            if len(lines) > max_lines:          # still too long at the minimum size: trim with an ellipsis
                lines = lines[:max_lines]
                while lines and f.getlength(lines[-1] + "…") > max_w and " " in lines[-1]:
                    lines[-1] = lines[-1].rsplit(" ", 1)[0]
                lines[-1] += "…"
            return s, tuple(lines)
        s = max(min_size, int(s * 0.93))


# ---------------- sprites ----------------
@functools.lru_cache(2048)
def text_sprite(text, wt, size, color, stroke=0, shadow=True):
    f = font(wt, size)
    asc, desc = f.getmetrics()
    pad = 14 + stroke + (10 if shadow else 0)
    w = int(f.getlength(text)) + pad * 2
    h = asc + desc + pad * 2
    im = Image.new("RGBA", (max(w, 2), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad, pad + 6), text, font=f, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(7)))
        d = ImageDraw.Draw(im)
    d.text((pad, pad), text, font=f, fill=tuple(color) + (255,), stroke_width=stroke, stroke_fill=(8, 8, 12, 255))
    return np.array(im)


def block_sprite(lines, wt, size, color, align="center", gap=1.12, shadow=True):
    sps = [text_sprite(l, wt, size, color, 0, shadow) for l in lines]
    lh = int(size * gap * 1.18)
    w = max(s.shape[1] for s in sps)
    h = lh * (len(sps) - 1) + sps[-1].shape[0]
    out = np.zeros((h, w, 4), np.uint8)
    for k, s in enumerate(sps):
        x = (w - s.shape[1]) // 2 if align == "center" else 0
        y = k * lh
        hh = min(s.shape[0], h - y)
        reg = out[y:y + hh, x:x + s.shape[1]]
        a = s[:hh, :, 3:4].astype(np.float32) / 255
        reg[:] = (reg * (1 - a) + s[:hh] * a).astype(np.uint8)
        reg[..., 3] = np.maximum(reg[..., 3], s[:hh, :, 3])
    return out


@functools.lru_cache(None)
def block(text, wt, max_w, max_lines, size, color, align="center", min_size=28):
    s, lines = fit(text, wt, max_w, max_lines, size, min_size)
    return block_sprite(lines, wt, s, color, align)


def _aa_mask(w, h, r):
    k = 2
    im = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w * k - 1, h * k - 1], r * k, fill=255)
    return np.array(im.resize((w, h), Image.LANCZOS), np.float32) / 255


@functools.lru_cache(512)
def rrect_mask(w, h, r):
    return _aa_mask(max(2, w), max(2, h), min(r, w // 2, h // 2))


@functools.lru_cache(256)
def card(w, h, r=36, fill=(255, 255, 255), fa=20, border=(255, 255, 255), ba=46, tint=None):
    """Glass card with a hairline border and a soft drop shadow (padding 40 px each side)."""
    pad = 40
    out = np.zeros((h + pad * 2, w + pad * 2, 4), np.float32)
    sh = np.zeros((h + pad * 2, w + pad * 2), np.float32)
    sh[pad + 14:pad + 14 + h, pad:pad + w] = rrect_mask(w, h, r) * 0.55
    sh = cv2.GaussianBlur(sh, (0, 0), 18)
    out[..., 3] = sh * 255
    m = rrect_mask(w, h, r)
    inner = rrect_mask(w - 4, h - 4, max(2, r - 2))
    edge = m.copy(); edge[2:h - 2, 2:w - 2] -= inner; edge = np.clip(edge, 0, 1)
    body = np.zeros((h, w, 4), np.float32)
    col = np.array(fill, np.float32)
    if tint is not None:   # vertical tint gradient
        t = np.linspace(0, 1, h)[:, None, None]
        col = np.array(tint, np.float32) * (1 - t) + col * t
    body[..., :3] = col
    body[..., 3] = m * fa
    e = edge * ba
    body[..., :3] = (body[..., :3] * body[..., 3:4] + np.array(border, np.float32) * e[..., None]) / np.maximum(body[..., 3:4] + e[..., None], 1e-3)
    body[..., 3] = np.clip(body[..., 3] + e, 0, 255)
    reg = out[pad:pad + h, pad:pad + w]
    ab = body[..., 3:4] / 255
    asd = reg[..., 3:4] / 255
    oa = ab + asd * (1 - ab)
    reg[..., :3] = body[..., :3] * ab / np.maximum(oa, 1e-4)      # the shadow under the glass is black
    reg[..., 3:4] = oa * 255
    return out.astype(np.uint8)


@functools.lru_cache(2048)
def _bar(w, h, c1, c2, r):
    return _bar_impl(w, h, c1, c2, r)


def bar_sprite(w, h, c1, c2, r=None):
    return _bar(max(int(w) // 2 * 2, 2), int(h), tuple(c1), tuple(c2), r)


def _bar_impl(w, h, c1, c2, r=None):
    w = max(int(w), 2)
    r = h // 2 if r is None else r
    m = rrect_mask(int(w // 2 * 2), h, r)
    w = m.shape[1]
    x = np.linspace(0, 1, w)[None, :, None]
    rgb = np.array(c1, np.float32) * (1 - x) + np.array(c2, np.float32) * x
    hl = np.linspace(1.18, 0.92, h)[:, None, None]       # top highlight
    rgb = np.clip(rgb * hl, 0, 255)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = (m * 255).astype(np.uint8)
    return out


@functools.lru_cache(64)
def glow_dot(r, color):
    s = r * 6
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    d = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2) / (s / 2)
    a = np.clip(1 - d, 0, 1) ** 2.4
    core = np.clip(1 - d * 3, 0, 1)
    return (a * 0.55 + core)[..., None] * np.array(color, np.float32)[None, None]


def _cp(ch):
    cps = [f"{ord(c):x}" for c in ch if ord(c) not in (0xFE0F,)]
    return "_".join(cps)


@functools.lru_cache(256)
def emoji(ch, size):
    """Noto emoji as RGBA. Google's static PNG per emoji first, then the system colour-emoji font, then a disc."""
    ch = (ch or "").strip()
    EMOJI_DIR.mkdir(parents=True, exist_ok=True)
    img = None
    if ch:
        code = _cp(ch)
        p = EMOJI_DIR / f"{code}.png"
        if not p.exists():
            for c in dict.fromkeys([code, "_".join(f"{ord(c):x}" for c in ch)]):
                try:
                    req = urllib.request.Request(f"https://fonts.gstatic.com/s/e/notoemoji/latest/{c}/512.png",
                                                 headers={"User-Agent": "Mozilla/5.0 studio"})
                    data = urllib.request.urlopen(req, timeout=15).read()
                    if data[:4] == b"\x89PNG":
                        p.write_bytes(data); break
                except Exception:
                    pass
        if p.exists():
            try:
                img = Image.open(p).convert("RGBA")
            except Exception:
                img = None
        if img is None:
            for fp in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf"):
                if os.path.exists(fp):
                    try:
                        f = ImageFont.truetype(fp, 109)
                        im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                        ImageDraw.Draw(im).text((80, 80), ch, font=f, embedded_color=True, anchor="mm")
                        if im.getbbox():
                            img = im.crop(im.getbbox())
                    except Exception:
                        pass
                    break
    if img is None:      # neutral disc with the first letter / a star
        k = 4
        im = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([0, 0, size * k - 1, size * k - 1], fill=(255, 255, 255, 40), outline=(255, 255, 255, 140), width=3 * k)
        g = re.sub(r"[^A-Za-z0-9]", "", ch)[:1].upper() or "★"
        d.text((size * k / 2, size * k / 2), g, font=font("xb", int(size * k * 0.5)), fill=(255, 255, 255, 230), anchor="mm")
        img = im
    img.thumbnail((size, size), Image.LANCZOS)
    return np.array(img)


# ---------------- compositing ----------------
def over(dst, src, x, y, alpha=1.0):
    if alpha <= 0.004 or src is None:
        return
    h, w = src.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:4].astype(np.float32) * (alpha / 255.0)
    d = dst[y0:y1, x0:x1]
    d += (s[..., :3].astype(np.float32) - d) * a


def add_light(dst, src, x, y, k=1.0):
    h, w = src.shape[:2]
    x, y = int(x), int(y)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    dst[y0:y1, x0:x1] += src[y0 - y:y1 - y, x0 - x:x1 - x] * k


def scaled(sp, s):
    if abs(s - 1) < 0.004:
        return sp
    s = max(s, 0.02)
    h, w = sp.shape[:2]
    return cv2.resize(sp, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)


def put(dst, sp, cx, cy, s=1.0, alpha=1.0):
    sp = scaled(sp, s)
    h, w = sp.shape[:2]
    over(dst, sp, cx - w / 2, cy - h / 2, alpha)


def put_top(dst, sp, cx, top, s=1.0, alpha=1.0):
    sp = scaled(sp, s)
    h, w = sp.shape[:2]
    over(dst, sp, cx - w / 2, top, alpha)
    return h


# ---------------- background ----------------
class Backdrop:
    """Brand gradient + two drifting colour glows (rendered at 1/4 res, upscaled) + faint dot grid + particles."""
    LW, LH = W // 4, H // 4

    def __init__(self, th, seed=3):
        self.th = th
        t = np.linspace(0, 1, self.LH)[:, None, None]
        self.base = (np.array(th["bg2"], np.float32) * (1 - t) ** 1.5 + np.array(th["bg"], np.float32) * (1 - (1 - t) ** 1.5))
        self.base = np.repeat(self.base, self.LW, axis=1)
        yy, xx = np.mgrid[0:self.LH, 0:self.LW].astype(np.float32)
        self.yy, self.xx = yy, xx
        r = np.sqrt(((xx - self.LW / 2) / (self.LW * 0.62)) ** 2 + ((yy - self.LH / 2) / (self.LH * 0.62)) ** 2)
        self.vig = (1 - np.clip(r - 0.5, 0, 1) * 0.6)[..., None]
        grid = np.zeros((H, W), np.float32)
        grid[30::60, 30::60] = 1
        grid = cv2.GaussianBlur(grid, (0, 0), 1.1) * 16
        self.grid = grid[..., None] * np.array([1, 1, 1], np.float32)
        rng = np.random.default_rng(seed)
        n = 34
        self.px = rng.uniform(0, W, n); self.py = rng.uniform(0, H, n)
        self.pv = rng.uniform(18, 60, n); self.pr = rng.integers(2, 5, n); self.ph = rng.uniform(0, 6.28, n)
        self.pc = [th["g1"] if k % 3 else th["g2"] for k in range(n)]

    def glow(self, img, cx, cy, rad, col, k):
        d2 = ((self.xx - cx) ** 2 + (self.yy - cy) ** 2) / (rad * rad)
        a = np.clip(1 - d2, 0, 1) ** 2
        img += a[..., None] * (np.array(col, np.float32) * k)

    def frame(self, t, pulse=0.0):
        lw, lh = self.LW, self.LH
        img = self.base.copy()
        self.glow(img, lw * (0.78 + 0.12 * math.sin(t * 0.42)), lh * (0.2 + 0.07 * math.cos(t * 0.33)), lw * 0.95, self.th["g1"], 0.30 + 0.12 * pulse)
        self.glow(img, lw * (0.2 + 0.12 * math.cos(t * 0.37)), lh * (0.82 + 0.06 * math.sin(t * 0.5)), lw * 1.05, self.th["g2"], 0.20 + 0.1 * pulse)
        img *= self.vig
        big = cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)
        big += self.grid
        for k in range(len(self.px)):
            y = (self.py[k] - t * self.pv[k]) % (H + 80) - 40
            x = self.px[k] + 22 * math.sin(t * 0.6 + self.ph[k])
            tw = 0.35 + 0.35 * (1 + math.sin(t * 1.7 + self.ph[k])) / 2
            dot = glow_dot(int(self.pr[k]), tuple(self.pc[k]))
            add_light(big, dot, x - dot.shape[1] / 2, y - dot.shape[0] / 2, tw)
        return big


@functools.lru_cache(4)
def streak(color):
    w, h = 2200, 420
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.exp(-((yy - h / 2) / (h * 0.18)) ** 2) * np.clip(1 - np.abs(xx - w / 2) / (w / 2), 0, 1)
    sp = a[..., None] * np.array(color, np.float32)
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 24, 1.0)
    return cv2.warpAffine(sp, M, (w, h + 600), borderValue=(0, 0, 0))


# ---------------- scene timing / sfx plan ----------------
def _num(v, default=0.0):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else default


def _first_mention(words, text, after=0.0):
    """Local time of the first narration word that matches a key word of text (for syncing reveals)."""
    keys = [k for k in re.findall(r"[a-z0-9]+", str(text).lower()) if len(k) > 3 or k.isdigit()]
    for w in words:
        ww = re.sub(r"[^a-z0-9]", "", w["w"].lower())
        if w["t"] >= after and ww and any(ww == k or (len(k) > 4 and ww.startswith(k[:5])) for k in keys):
            return w["t"]
    return None


def plan(scene, D, words):
    """Fill scene['_tm'] (reveal times) and return sfx events [(local_t, kind)]."""
    t, d = scene["type"], scene["data"]
    tm, ev = {}, [(0.0, "whoosh")]
    if t == "hook_title":
        ev = [(0.0, "boom"), (0.05, "whoosh")]
        tm["emoji"] = 0.1
        ev.append((0.25, "pop"))
    elif t == "stat_counter":
        tm["count"] = 0.45
        tm["count_d"] = min(1.6, max(0.9, D * 0.35))
        ev += [(tm["count"], "tick"), (tm["count"] + tm["count_d"], "ding")]
    elif t == "bar_chart":
        n = len(d["bars"])
        span = min(D * 0.5, 0.35 * n + 0.6)
        tm["bars"] = [0.55 + k * span / max(n, 1) for k in range(n)]
        ev += [(x + 0.1, "pop") for x in tm["bars"]]
    elif t == "line_chart":
        tm["draw"] = (0.55, min(2.4, D * 0.55))
        ev += [(0.55, "riser"), (sum(tm["draw"]), "ding")]
    elif t == "comparison":
        tm["left"], tm["right"], tm["vs"] = 0.15, 0.35, 0.7
        tm["verdict"] = max(1.6, D * 0.62)
        ev += [(0.7, "boom_s"), (tm["verdict"], "pop")]
    elif t == "list_reveal":
        n = len(d["items"])
        auto = [0.5 + k * min(1.1, (D * 0.7 - 0.5) / max(n, 1)) for k in range(n)]
        out, last = [], 0.3
        for k, it in enumerate(d["items"]):
            m = _first_mention(words, it["text"], last)
            x = m - 0.15 if m is not None and m - 0.15 >= last and m < D - 0.8 else max(auto[k], last + 0.3)
            out.append(min(x, D - 0.7)); last = out[-1] + 0.25
        tm["items"] = out
        ev += [(x, "pop") for x in out]
    elif t == "quote":
        tm["words_end"] = max(1.0, D * 0.7)
        ev += [(tm["words_end"], "ding_s")]
    elif t == "cta":
        ev += [(0.25, "pop"), (0.6, "ding")]
    scene["_tm"] = tm
    return ev


# ---------------- templates ----------------
class Ctx:
    def __init__(self, u, D, th):
        self.u, self.D, self.th = u, D, th
        self.X = ease_in((u - (D - 0.32)) / 0.32)          # exit progress

    def ent(self, delay=0.0, dur=0.55, fn=ease_out):
        return fn((self.u - delay) / dur)

    def a(self, delay=0.0, dur=0.45):
        return clamp((self.u - delay) / dur) * (1 - self.X)

    def dy(self, delay=0.0, dist=70):
        return (1 - self.ent(delay)) * dist - self.X * 50


def draw_title(F, c, text, top=CONTENT_TOP + 30, size=68, delay=0.0, color=WHITE, max_lines=2):
    if not text:
        return top
    sp = block(text, "xb", 940, max_lines, size, color)
    h = put_top(F, sp, CX, top + c.dy(delay), alpha=c.a(delay))
    return top + h


def kicker_pill(F, c, text, cy, delay=0.0):
    s, lines = fit(text.upper(), "b", 820, 1, 38, 26)
    t = text_sprite(lines[0], "b", s, (12, 12, 18), 0, False)
    w = t.shape[1] + 30
    pill = bar_sprite(w, t.shape[0] - 6, c.th["acc"], c.th["acc2"])
    sc = 0.7 + 0.3 * back_out((c.u - delay) / 0.5)
    put(F, pill, CX, cy + c.dy(delay, 40), sc, c.a(delay))
    put(F, t, CX, cy + c.dy(delay, 40), sc, c.a(delay))


def t_hook(F, c, d):
    th = c.th
    title = str(d.get("title", ""))
    hl = set(re.findall(r"[a-z0-9']+", str(d.get("highlight", "")).lower()))
    e = emoji(d.get("emoji", ""), 250) if d.get("emoji") else None
    size = 132 if e is not None else 150
    while True:                                   # title must fit the space above the captions
        size, lines = fit(title, "xb", 940, 4, size, 56)
        lh = int(size * 1.16)
        total = (110 if d.get("kicker") else 0) + (300 if e is not None else 0) + len(lines) * lh + 60
        if total <= 1380 - 300 or size <= 60:
            break
        size = int(size * 0.92)
    y = max(300, (300 + 1380) / 2 - total / 2)
    if d.get("kicker"):
        kicker_pill(F, c, d["kicker"], y + 40, 0.05)
        y += 110
    if e is not None:
        s = back_out((c.u - 0.1) / 0.6) * (1 - 0.15 * c.X)
        bob = math.sin(c.u * 2.2) * 12
        put(F, e, CX, y + 135 + bob - c.X * 40, max(s, 0.01), c.a(0.1, 0.25))
        y += 300
    y0 = y
    f = font("xb", size)
    space = f.getlength(" ")
    k = 0
    for li, line in enumerate(lines):
        ws = line.split(" ")
        x = CX - f.getlength(line) / 2
        for w in ws:
            ww = f.getlength(w)
            key = re.sub(r"[^a-z0-9']", "", w.lower())
            col = th["acc"] if key and key in hl else WHITE
            sp = text_sprite(w, "xb", size, col, 0, True)
            dl = 0.28 + k * 0.11
            p = back_out((c.u - dl) / 0.42)
            sc = 0.55 + 0.45 * p if p < 1 else 1.0
            cy = y0 + li * lh + size * 0.6 + (1 - clamp((c.u - dl) / 0.3)) * 40 - c.X * 60
            put(F, sp, x + ww / 2, cy, sc, clamp((c.u - dl) / 0.18) * (1 - c.X))
            x += ww + space
            k += 1
    p = ease_io((c.u - (0.3 + k * 0.11)) / 0.5)     # underline sweep
    if p > 0:
        put(F, bar_sprite(max(int(420 * p), 4), 14, th["acc2"], th["acc"]), CX, y0 + len(lines) * lh + 34 - c.X * 60, 1, 1 - c.X)


def fmt_num(v, dec):
    if dec <= 0:
        s = f"{int(round(v)):,}"
    else:
        s = f"{v:,.{dec}f}"
    return s


def t_stat(F, c, d):
    th, tm = c.th, c._tm
    top = draw_title(F, c, d.get("label", ""), CONTENT_TOP + 40, 66, 0.0, WHITE, 3)
    cy = max(top + 380, 860)
    R = 300
    p = ease_out((c.u - tm["count"]) / tm["count_d"])
    ring = d.get("ring")
    frac = clamp((ring if ring is not None else 100) / 100.0) * p
    S = 2 * R + 120
    sp = np.zeros((S, S, 4), np.uint8)
    cv2.circle(sp, (S // 2, S // 2), R, (255, 255, 255, 36), 30, cv2.LINE_AA)
    if frac > 0.002:
        ang = 360 * frac
        steps = max(2, int(ang / 4))
        for k in range(steps):     # gradient arc in short segments
            a0, a1 = -90 + ang * k / steps, -90 + ang * (k + 1) / steps + 0.6
            m = k / max(steps - 1, 1)
            col = tuple(int(th["acc2"][i] * (1 - m) + th["acc"][i] * m) for i in range(3))
            cv2.ellipse(sp, (S // 2, S // 2), (R, R), 0, a0, a1, col + (255,), 30, cv2.LINE_AA)
        ex = S / 2 + R * math.cos(math.radians(-90 + ang)); ey = S / 2 + R * math.sin(math.radians(-90 + ang))
        cv2.circle(sp, (int(ex), int(ey)), 15, th["acc"] + (255,), -1, cv2.LINE_AA)
    sc = 0.8 + 0.2 * ease_out(c.u / 0.5)
    put(F, sp, CX, cy - c.X * 40, sc * (1 - 0.06 * c.X), c.a(0.0, 0.35))
    v = _num(d.get("value"))
    dec = int(d.get("decimals", 1 if abs(v - round(v)) > 1e-9 else 0))
    txt = f"{d.get('prefix', '')}{fmt_num(v * p, dec)}{d.get('suffix', '')}"
    full = f"{d.get('prefix', '')}{fmt_num(v, dec)}{d.get('suffix', '')}"
    size, _ = fit(full, "xb", 2 * R - 110, 1, 190, 60)
    land = back_out((c.u - tm["count"] - tm["count_d"]) / 0.35)
    bump = 1 + 0.08 * math.sin(math.pi * clamp((c.u - tm["count"] - tm["count_d"]) / 0.35))
    put(F, text_sprite(txt, "xb", size, WHITE if p < 1 else th["acc"], 0, True), CX, cy - c.X * 40, bump * sc, c.a(0.2, 0.3))
    if d.get("sub"):
        sub = block(d["sub"], "sb", 900, 3, 50, MUTED)
        put_top(F, sub, CX, cy + R + 90 + c.dy(tm["count"] + 0.3), alpha=c.a(tm["count"] + 0.3) * (1 if land > 0 else 1))


def t_bars(F, c, d):
    th, tm = c.th, c._tm
    top = draw_title(F, c, d.get("title", ""), CONTENT_TOP + 40, 66)
    bars = d["bars"]
    n = len(bars)
    vmax = max(abs(_num(b["value"])) for b in bars) or 1
    area_top, area_bot = max(top + 60, 470), CONTENT_BOTTOM - 20
    row = min(200, (area_bot - area_top) / n)
    area_top += max(0, (area_bot - area_top - row * n) / 2 - 20)     # centre the chart in the free space
    bh = int(min(70, row * 0.36))
    unit = d.get("unit", "")
    imax = max(range(n), key=lambda k: _num(bars[k]["value"]))
    for k, b in enumerate(bars):
        dl = tm["bars"][k]
        y = area_top + k * row + c.dy(dl - 0.25, 50)
        al = c.a(dl - 0.25, 0.3)
        lab = block(str(b["label"]), "sb", 900, 1, 44, WHITE, "left", 28)
        over(F, lab, 70, y, al)
        p = ease_out((c.u - dl) / 0.8)
        v = _num(b["value"])
        full = 760 * abs(v) / vmax
        bw = max(8, full * p)
        y2 = y + lab.shape[0] - 10
        over(F, bar_sprite(800, bh, (255, 255, 255), (255, 255, 255)), 84, y2, 0.09 * al)
        c1, c2 = (th["acc2"], th["acc"]) if k == imax else (tuple(int(x * 0.55) for x in th["acc2"]), th["acc2"])
        if p > 0:
            over(F, bar_sprite(bw, bh, c1, c2), 84, y2, 1 - c.X)
            dec = 1 if abs(v - round(v)) > 1e-9 else 0
            vt = text_sprite(f"{d.get('prefix', '')}{fmt_num(v * p, dec)}{unit}", "xb", int(bh * 0.72) + 14, WHITE, 0, True)
            vx = 84 + bw + 14
            if vx + vt.shape[1] > W - 30:          # value inside the bar when it would run off screen
                vx = 84 + bw - vt.shape[1] - 8
            over(F, vt, vx, y2 + bh / 2 - vt.shape[0] / 2, clamp((c.u - dl) / 0.25) * (1 - c.X))


def t_line(F, c, d):
    th, tm = c.th, c._tm
    top = draw_title(F, c, d.get("title", ""), CONTENT_TOP + 40, 66)
    pts = d["points"]
    PW, PH = 960, 720
    cy0 = max(top + 50, 480)
    put_top(F, card(PW, PH, 40), CX, cy0 - 40 + c.dy(0.1, 60), alpha=c.a(0.1, 0.35))
    ox, oy = CX - PW / 2, cy0 + c.dy(0.1, 60)
    ys = [_num(p["y"]) for p in pts]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1
    lo2 = lo - (hi - lo) * 0.15 if lo != 0 else 0
    lo2 = min(lo2, lo)
    gx0, gx1, gy0, gy1 = 80, PW - 70, 110, PH - 120
    def P(k, yv):
        x = gx0 + (gx1 - gx0) * k / max(len(pts) - 1, 1)
        y = gy1 - (gy1 - gy0) * (yv - lo2) / (hi - lo2)
        return x, y
    sp = np.zeros((PH, PW, 4), np.uint8)
    for g in range(4):
        yy = int(gy0 + (gy1 - gy0) * g / 3)
        cv2.line(sp, (gx0, yy), (gx1, yy), (255, 255, 255, 26), 2, cv2.LINE_AA)
    t0, td = tm["draw"]
    p = ease_io((c.u - t0) / td)
    n = len(pts)
    pos = p * (n - 1)
    xy = [P(k, ys[k]) for k in range(n)]
    drawn = [xy[k] for k in range(int(pos) + 1)]
    if p < 1 and int(pos) + 1 < n:
        f = pos - int(pos)
        a, b = xy[int(pos)], xy[int(pos) + 1]
        drawn.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    if len(drawn) >= 2 and p > 0:
        poly = np.array(drawn + [(drawn[-1][0], gy1), (drawn[0][0], gy1)], np.int32)
        fill = np.zeros((PH, PW), np.uint8)
        cv2.fillPoly(fill, [poly], 255, cv2.LINE_AA)
        grad = np.linspace(0.42, 0.0, PH)[:, None]
        fa = (fill.astype(np.float32) * grad).astype(np.uint8)
        layer = np.zeros_like(sp); layer[..., :3] = th["acc2"]; layer[..., 3] = fa
        a = layer[..., 3:4].astype(np.float32) / 255
        sp[..., :3] = (sp[..., :3] * (1 - a) + layer[..., :3] * a).astype(np.uint8)
        sp[..., 3] = np.maximum(sp[..., 3], layer[..., 3])
        cv2.polylines(sp, [np.array(drawn, np.int32)], False, th["acc"] + (255,), 10, cv2.LINE_AA)
    for k in range(n):
        if k <= pos + 1e-6:
            cv2.circle(sp, (int(xy[k][0]), int(xy[k][1])), 11, (255, 255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(sp, (int(xy[k][0]), int(xy[k][1])), 11, th["acc"] + (255,), 4, cv2.LINE_AA)
    over(F, sp, ox, oy, c.a(0.1, 0.35))
    if p > 0 and drawn:
        g = glow_dot(10, th["acc"])
        add_light(F, g, ox + drawn[-1][0] - g.shape[1] / 2, oy + drawn[-1][1] - g.shape[0] / 2, 1.2 * (1 - c.X))
    unit = d.get("unit", "")
    step = max(1, math.ceil(n / 6))
    for k in range(n):
        if k % step and k != n - 1:
            continue
        lab = text_sprite(str(pts[k]["x"])[:10], "m", 34, MUTED, 0, False)
        put(F, lab, ox + xy[k][0], oy + gy1 + 50, 1, c.a(t0 + td * k / max(n - 1, 1), 0.3))
    for k in (0, n - 1):                       # first and last values labelled
        if k <= pos + 1e-6:
            dec = 1 if abs(ys[k] - round(ys[k])) > 1e-9 else 0
            vt = text_sprite(f"{d.get('prefix', '')}{fmt_num(ys[k], dec)}{unit}", "xb", 50 if k else 40,
                             th["acc"] if k else WHITE, 0, True)
            x = clamp(ox + xy[k][0], 40 + vt.shape[1] / 2, W - 40 - vt.shape[1] / 2)
            pp = back_out((c.u - (t0 + td * k / max(n - 1, 1))) / 0.4)
            put(F, vt, x, oy + xy[k][1] - 60, max(0.01, pp), 1 - c.X)
    if d.get("caption"):
        put_top(F, block(d["caption"], "sb", 900, 2, 46, MUTED), CX, oy + PH + 10, alpha=c.a(t0 + td, 0.4))


def t_compare(F, c, d):
    th, tm = c.th, c._tm
    top = draw_title(F, c, d.get("title", ""), CONTENT_TOP + 40, 66)
    cw, chh = 456, 760
    y0 = max(top + 50, 470)
    sides = [("left", d["left"], CX - cw / 2 - 18, -1, tm["left"]), ("right", d["right"], CX + cw / 2 + 18, 1, tm["right"])]
    for name, s, cx, sgn, dl in sides:
        p = ease_out((c.u - dl) / 0.6)
        x = cx + sgn * (1 - p) * 520 + sgn * c.X * 80
        al = clamp((c.u - dl) / 0.3) * (1 - c.X)
        tint = th["acc2"] if sgn < 0 else th["acc"]
        put_top(F, card(cw, chh, 40, fa=22, tint=tuple(int(v * 0.5) for v in tint)), x, y0 - 40, alpha=al)
        yy = y0 + 40
        if s.get("emoji"):
            e = emoji(s["emoji"], 130)
            put(F, e, x, yy + 70, back_out((c.u - dl - 0.15) / 0.5) or 0.01, al)
            yy += 160
        nm = block(str(s.get("name", "")), "xb", cw - 60, 2, 58, tint)
        yy += put_top(F, nm, x, yy, alpha=al) + 10
        for j, pt in enumerate(s.get("points", [])[:4]):
            pa = clamp((c.u - dl - 0.45 - j * 0.2) / 0.3) * (1 - c.X)
            b = block(str(pt), "m", cw - 56, 3, 42, WHITE, "center", 28)
            if yy + b.shape[0] - 24 > y0 + chh - 30:
                break
            dot = bar_sprite(46, 6, tint, tint)
            put(F, dot, x, yy + 6, 1, pa * 0.8)
            yy += put_top(F, b, x, yy + 10 + (1 - pa) * 20, alpha=pa) - 6
    vs = text_sprite("VS", "xb", 64, (12, 12, 18), 0, False)
    disc = np.zeros((150, 150, 4), np.uint8)
    cv2.circle(disc, (75, 75), 66, th["acc"] + (255,), -1, cv2.LINE_AA)
    sc = back_out((c.u - tm["vs"]) / 0.45) * (1 + 0.04 * math.sin(c.u * 6)) * (1 - 0.2 * c.X)
    vy = y0 + 110
    put(F, disc, CX, vy, max(sc, 0.01), 1 - c.X)
    put(F, vs, CX, vy, max(sc, 0.01), 1 - c.X)
    if d.get("verdict"):
        vb = block(str(d["verdict"]), "b", 920, 2, 50, th["acc"])
        put_top(F, vb, CX, y0 + chh + 10 + c.dy(tm["verdict"], 30), alpha=c.a(tm["verdict"], 0.35))


def t_list(F, c, d):
    th, tm = c.th, c._tm
    top = draw_title(F, c, d.get("title", ""), CONTENT_TOP + 40, 66)
    items = d["items"]
    n = len(items)
    y0 = max(top + 40, 450)
    gap = 24
    rh = int(min(190, (CONTENT_BOTTOM - y0 - gap * (n - 1)) / n))
    y0 += max(0, (CONTENT_BOTTOM - y0 - n * rh - (n - 1) * gap) / 2 - 30)
    for k, it in enumerate(items):
        dl = tm["items"][k]
        p = ease_out((c.u - dl) / 0.5)
        al = clamp((c.u - dl) / 0.25) * (1 - c.X)
        if al <= 0.004:
            continue
        y = y0 + k * (rh + gap)
        x = CX + (1 - p) * 300 + c.X * -60
        put_top(F, card(960, rh, 34), x, y - 40, alpha=al)
        ex = x - 480 + 30 + rh * 0.42
        if it.get("emoji"):
            e = emoji(it["emoji"], int(rh * 0.62))
        else:
            e = np.zeros((int(rh * 0.7), int(rh * 0.7), 4), np.uint8)
            r = e.shape[0] // 2
            cv2.circle(e, (r, r), r - 2, th["acc"] + (255,), -1, cv2.LINE_AA)
            num = text_sprite(str(k + 1), "xb", int(rh * 0.36), (12, 12, 18), 0, False)
            nh, nw = num.shape[:2]
            tmp = np.zeros_like(e, np.float32); tmp[..., :3] = e[..., :3]
            e = e.copy()
            ox, oy = r - nw // 2, r - nh // 2
            sub = e[max(oy, 0):oy + nh, max(ox, 0):ox + nw]
            ns = num[max(-oy, 0):max(-oy, 0) + sub.shape[0], max(-ox, 0):max(-ox, 0) + sub.shape[1]]
            a = ns[..., 3:4].astype(np.float32) / 255
            sub[..., :3] = (sub[..., :3] * (1 - a) + ns[..., :3] * a).astype(np.uint8)
        put(F, e, ex, y + rh / 2, max(0.01, back_out((c.u - dl - 0.05) / 0.45)), al)
        tx = ex + rh * 0.45
        tw = int(x + 480 - 30 - tx)
        tb = block(str(it["text"]), "sb", tw, 2, 48, WHITE, "left", 28)
        over(F, tb, tx, y + rh / 2 - tb.shape[0] / 2, al)


def t_quote(F, c, d):
    th, tm = c.th, c._tm
    text = str(d.get("text", ""))
    size, lines = fit(text, "b", 900, 6, 84, 44)
    f = font("b", size)
    lh = int(size * 1.3)
    total = sum(len(l.split()) for l in lines)
    y0 = int((CONTENT_TOP + 120 + CONTENT_BOTTOM) / 2 - (len(lines) * lh + 90) / 2)
    q = text_sprite("\u201C", "xb", 300, th["acc"], 0, False)
    put(F, q, 200, y0 - 60 + c.dy(0, 40), back_out(c.u / 0.5) or 0.01, c.a(0, 0.3))
    k = 0
    for li, line in enumerate(lines):
        x = 90
        for w in line.split(" "):
            dl = 0.3 + (tm["words_end"] - 0.3) * k / max(total, 1)
            al = clamp((c.u - dl) / 0.25) * (1 - c.X)
            sp = text_sprite(w, "b", size, WHITE, 0, True)
            over(F, sp, x - 24, y0 + li * lh + (1 - clamp((c.u - dl) / 0.3)) * 18, al)
            x += f.getlength(w + " ")
            k += 1
    if d.get("by"):
        by = block("— " + str(d["by"]), "sb", 880, 1, 46, MUTED, "left")
        over(F, by, 90 - 24, y0 + len(lines) * lh + 40, c.a(tm["words_end"], 0.4))


def t_cta(F, c, d):
    th = c.th
    if d.get("emoji"):
        e = emoji(d["emoji"], 240)
        put(F, e, CX, 560 + math.sin(c.u * 2.5) * 10, max(0.01, back_out((c.u - 0.1) / 0.6)), 1 - c.X)
    top = 760
    tb = block(str(d.get("title", "")), "xb", 940, 4, 104, WHITE, "center", 50)
    h = put_top(F, tb, CX, top + c.dy(0.2), alpha=c.a(0.2))
    if d.get("sub"):
        s, lines = fit(str(d["sub"]), "b", 800, 1, 50, 30)
        t = text_sprite(lines[0], "b", s, (12, 12, 18), 0, False)
        pill = bar_sprite(t.shape[1] + 50, t.shape[0] + 10, th["acc"], th["acc2"])
        pulse = 1 + 0.035 * math.sin(c.u * 5)
        sc = back_out((c.u - 0.6) / 0.5) * pulse
        y = top + h + 110
        put(F, pill, CX, y, max(sc, 0.01), c.a(0.6, 0.2))
        put(F, t, CX, y, max(sc, 0.01), c.a(0.6, 0.2))


DRAW = {"hook_title": t_hook, "stat_counter": t_stat, "bar_chart": t_bars, "line_chart": t_line,
        "comparison": t_compare, "list_reveal": t_list, "quote": t_quote, "cta": t_cta}


# ---------------- captions ----------------
def draw_captions(F, t, groups, th):
    g = None
    for gr in groups:
        if gr["t0"] - 0.05 <= t < gr["t1"]:
            g = gr; break
    if not g:
        return
    words = g["words"]
    texts = [w["w"].upper() for w in words]
    size = 76
    f = font("xb", size)
    while size > 46 and f.getlength(" ".join(texts)) + 24 * (len(texts) - 1) > 960:
        size -= 3; f = font("xb", size)
    lines, cur = [], []
    for k, tx in enumerate(texts):
        if cur and f.getlength(" ".join(texts[j] for j in cur + [k])) + 24 * len(cur) > 980:
            lines.append(cur); cur = [k]
        else:
            cur.append(k)
    lines.append(cur)
    last = max([k for k, w in enumerate(words) if t >= w["t"] - 0.04] or [-1])
    gin = 0.75 + 0.25 * back_out((t - g["t0"] + 0.05) / 0.18)
    lh = int(size * 1.22)
    y = CAP_Y - (len(lines) - 1) * lh / 2
    sp_w = f.getlength(" ") + 24
    for li in lines:
        tot = f.getlength(" ".join(texts[k] for k in li)) + 24 * (len(li) - 1)
        x = CX - tot / 2
        for k in li:
            w = words[k]
            ww = f.getlength(texts[k])
            spoken = t >= w["t"] - 0.04
            cur_w = k == last
            col = th["acc"] if cur_w else WHITE
            sp = text_sprite(texts[k], "xb", size, col, 7, True)
            p = (t - w["t"] + 0.04) / 0.16
            sc = gin * ((0.92 + 0.14 * back_out(p)) if cur_w else 1.0)
            put(F, sp, x + ww / 2, y + (1 - gin) * 20, sc, (1.0 if spoken else 0.55) * gin)
            x += ww + sp_w
        y += lh


# ---------------- whole video ----------------
def frame_at(i, sb, bd):
    t = i / FPS
    scenes = sb["scenes"]
    k = 0
    while k + 1 < len(scenes) and t >= scenes[k + 1]["_t0"]:
        k += 1
    sc = scenes[k]
    u = t - sc["_t0"]
    th = sb["_theme"]
    pulse = math.exp(-u * 3) if u < 1.5 else 0
    F = bd.frame(t, pulse)
    c = Ctx(u, sc["_D"], th)
    c._tm = sc["_tm"]
    try:
        DRAW[sc["type"]](F, c, sc["data"])
    except Exception as e:      # one broken element must not kill the render
        print("   scene draw error:", sc["type"], str(e)[:120])
    if k > 0 and u < 0.45:
        s = streak(th["g1"])
        x = -1600 + (W + 2400) * ease_io(u / 0.45)
        add_light(F, s, x, H * 0.18, 0.45)
    draw_captions(F, t, sb["_caps"], th)
    # progress bar
    tot = sb["_total"]
    pw = int((W - 120) * clamp(t / tot))
    F[56:64, 60:W - 60] = F[56:64, 60:W - 60] * 0.7 + 255 * 0.3 * 0.25
    if pw > 4:
        over(F, bar_sprite(pw, 8, th["acc2"], th["acc"]), 60, 56, 0.95)
    np.clip(F, 0, 255, out=F)
    return F.astype(np.uint8)


def _render_part(args):
    sb, f0, f1, out = args
    cv2.setNumThreads(1)
    bd = Backdrop(sb["_theme"])
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(sb.get("_crf", 22)),
                          "-pix_fmt", "yuv420p", "-g", "60", "-threads", "2", str(out)], stdin=subprocess.PIPE)
    try:
        for i in range(f0, f1):
            p.stdin.write(frame_at(i, sb, bd).tobytes())
    finally:
        p.stdin.close()
        p.wait()
    if p.returncode:
        raise RuntimeError("ffmpeg encode failed")
    return out


def render(sb, out_mp4, workers=None):
    """sb: storyboard with _t0/_D/_tm per scene, _caps, _total, _theme. Writes a silent H.264 MP4."""
    import multiprocessing as mp
    ensure_fonts()
    for sc in sb["scenes"]:                       # warm caches (emoji downloads) before forking
        d = sc["data"]
        for e in [d.get("emoji")] + [i.get("emoji") for i in d.get("items", [])] + \
                 [(d.get(s) or {}).get("emoji") for s in ("left", "right")]:
            if e:
                emoji(e, 260 if sc["type"] in ("hook_title", "cta") else 130)
    n = int(math.ceil(sb["_total"] * FPS))
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    tmp = pathlib.Path(out_mp4).with_suffix("")
    tmp.mkdir(parents=True, exist_ok=True)
    cuts = [round(n * k / workers) for k in range(workers + 1)]
    jobs = [(sb, cuts[k], cuts[k + 1], tmp / f"part{k}.mp4") for k in range(workers) if cuts[k + 1] > cuts[k]]
    if len(jobs) == 1:
        parts = [_render_part(jobs[0])]
    else:
        with mp.get_context("fork").Pool(len(jobs)) as pool:
            parts = pool.map(_render_part, jobs)
    lst = tmp / "parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_mp4)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("ffmpeg concat failed: " + r.stderr[-300:])
    shutil.rmtree(tmp, ignore_errors=True)
    return out_mp4
