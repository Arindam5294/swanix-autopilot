#!/usr/bin/env python3
"""Hook-first edit: the shared structure every automated GTA VI Clips video uses (flag: config "hookfirst", default ON;
env GTA_HOOKFIRST=0 turns it off for one run).

  1. COLD OPEN  0-1.2 s  the key moment itself, full frame (no facecam), big 2-line hook. Motion from frame 1.
  2. BODY       the clip from its setup, facecam panel on top (when the stream has a webcam), "clip: twitch.tv/<streamer>".
  3. REPLAY     the key moment again at 0.5x with a ~1.3x punch-in and a "REPLAY" tag (this is what makes it an edit).
  4. END CARD   last ~1.5 s, darkened gameplay + share CTA "SEND THIS TO THE FRIEND WHO ...".
Original audio throughout (slowed with the replay), loudness -14 LUFS, 1080x1920 30 fps H.264, one font (Montserrat).

Key moment: clip["key_at"] (manual) -> a vision pick passed in by the caller (gta_secrets payoff, refined) ->
loudness jump + scene-change peak (streamer yelling / explosion / hard cut), shifted back ~0.4 s for reaction lag.

    import hookfirst
    key, why = hookfirst.pick_key(src, clip)
    info = hookfirst.render(src, dst, hook="GTA 6 PLANES|ARE NOT NORMAL", cta="SEND THIS TO THE FRIEND WHO CAN'T FLY",
                            credit="clip: twitch.tv/clix", key=key, cam=layout.find_facecam(src))
CLI test:  python hookfirst.py clip.mp4 out.mp4 "HOOK LINE 1|LINE 2" "SEND THIS TO ..." "clip: twitch.tv/x" [key_seconds]"""
import json, os, pathlib, re, shutil, statistics, subprocess, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
W, H, FPS = 1080, 1920, 30
COLD, END, SLOW, ZOOM = 1.2, 1.5, 0.5, 1.3
R_PRE, R_POST = 0.8, 0.8          # replay window around the key moment (source seconds) -> 3.2 s at 0.5x
YELLOW = "0xFFD60A"
FONT_DIR = HERE / "fonts"
FONT = FONT_DIR / "Montserrat-BlackItalic.ttf"
FONT_URL = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-BlackItalic.ttf"
DEVA = pathlib.Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf")
FALLBACK = pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def enabled(cfg=None):
    env = os.environ.get("GTA_HOOKFIRST", "").strip().lower()
    if env in ("0", "false", "off", "no"): return False
    if env in ("1", "true", "on", "yes"): return True
    return bool((cfg or {}).get("hookfirst", True))


def log(*a): print("hookfirst:", *a, file=sys.stderr, flush=True)


def sh(*a, timeout=1800):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError(" ".join(map(str, a))[:200] + "\n" + r.stderr[-1500:])
    return r.stdout


def duration(p):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).strip())


def probe_wh(p):
    w, h = sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", p).strip().split(",")[:2]
    return int(w), int(h)


def has_audio(p):
    return "audio" in sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", p)


def font_file(text=""):
    if re.search(r"[\u0900-\u097F]", text or "") and DEVA.exists(): return DEVA
    if not FONT.exists():
        try:
            FONT_DIR.mkdir(parents=True, exist_ok=True); urllib.request.urlretrieve(FONT_URL, FONT)
        except Exception as e:
            log("Montserrat download failed:", e)
    return FONT if FONT.exists() else FALLBACK


# ------------------------------------------------------------------------------------------------ key moment
def _loudness(path, step=0.1):
    """RMS dB every `step` seconds."""
    n = int(8000 * step)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        f"aresample=8000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    return [float(x) if "inf" not in x else -90.0 for x in re.findall(r"RMS_level=(-?[\d.]+|-?inf)", r.stderr)]


def _scenes(path):
    """(time, scene score) per frame, decoded small so it stays cheap."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                        "scale=320:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    t = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    s = [float(x) for x in re.findall(r"lavfi\.scene_score=([\d.]+)", r.stderr)]
    return list(zip(t, s))


def _z(xs):
    if len(xs) < 2: return [0.0] * len(xs)
    m, sd = statistics.mean(xs), statistics.pstdev(xs) or 1.0
    return [(x - m) / sd for x in xs]


def _black(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", "scale=320:-2,blackdetect=d=0.3:pix_th=0.10",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]


def candidates(path, near=None, spread=2.0, lag=0.4, k=4):
    """Ranked key-moment candidates [(seconds, score)], best first, >= 1.5 s apart.
    Signals: loud level + sudden rise vs the previous ~2.5 s (baseline never below the clip's median - 6 dB, so coming
    back from silence is not a 'rise') + sustained motion/scene change. Black stretches are never picked."""
    dur = duration(path)
    step = 0.1
    lv = _loudness(path, step) if has_audio(path) else []
    sc = _scenes(path)
    n = max(len(lv), int(dur / step))
    if not lv: lv = [-90.0] * n
    sm = [sum(lv[max(0, i - 2):i + 3]) / len(lv[max(0, i - 2):i + 3]) for i in range(len(lv))]
    med = statistics.median(sm) if sm else -90.0
    rise = []
    for i in range(len(sm)):
        base = statistics.median(sm[max(0, i - 25):max(1, i - 4)]) if i > 5 else sm[i]
        rise.append(sm[i] - max(base, med - 6))
    mot = [0.0] * len(sm)
    for t, v in sc:
        i = int(t / step)
        if 0 <= i < len(mot): mot[i] = max(mot[i], v)
    motion = [sum(mot[max(0, i - 5):i + 6]) / 11 for i in range(len(mot))]
    zl, zr, zm = _z(sm), _z(rise), _z(motion)
    blk = _black(path)
    # Twitch/Kick clips end shortly after the moment (people press "clip" right after it), and the first seconds are
    # usually the cut-in: skip the first 15% and give later moments a mild bonus.
    lo, hi = max(1.0, 0.15 * dur if dur > 12 else 1.0), max(1.1, dur - 1.0)
    if near is not None: lo, hi = max(1.0, near - spread), min(hi, near + spread)
    scored = []
    for i in range(len(sm)):
        t = i * step
        if not lo <= t <= hi or sm[i] < -60: continue
        if any(a - 1.0 <= t - lag <= b + 0.5 for a, b in blk): continue
        scored.append((0.6 * zl[i] + 1.0 * zr[i] + 0.7 * zm[i] + (0.6 * t / dur if near is None else 0.0), t))
    scored.sort(reverse=True)
    out = []
    for sc_, t in scored:
        kt = round(min(max(0.6, t - lag), dur - 0.6), 2)
        if all(abs(kt - o[0]) >= 1.5 for o in out): out.append((kt, round(sc_, 2)))
        if len(out) >= k: break
    return out


def vision_key(path, cands, title=""):
    """Optional Gemini vision pick among the loudness candidates (needs GEMINI_API_KEY). -> index or None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or len(cands) < 2: return None
    import base64
    parts = [{"text": f"These {len(cands)} frame pairs (A = the moment, B = 0.5 s later) come from a GTA 6 stream clip"
                      f"{' titled ' + repr(title) if title else ''}. Which pair shows the most dramatic, funny or shocking VISUAL moment "
                      "(crash, explosion, ragdoll, impact, wild NPC action)? A menu, map, loading screen or calm walking is never it. "
                      'Reply ONLY JSON: {"best": <pair number starting at 0>, "why": "short"}'}]
    for i, (t, _) in enumerate(cands):
        for dt_ in (0.0, 0.5):
            b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t + dt_:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2",
                                "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"], capture_output=True).stdout
            if b:
                parts.append({"text": f"pair {i} {'A' if dt_ == 0 else 'B'}"})
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b).decode()}})
    try:
        body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0, "maxOutputTokens": 300}}
        rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('GATE_MODEL', 'gemini-2.5-flash')}:generateContent",
                                    json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        d = json.loads(urllib.request.urlopen(rq, timeout=90).read())
        txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        v = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        i = int(v.get("best"))
        if 0 <= i < len(cands):
            log(f"vision picked candidate {i} at {cands[i][0]}s: {str(v.get('why', ''))[:100]}"); return i
    except Exception as e:
        log("vision key pick failed, using loudness:", str(e)[:150])
    return None


def key_moment(path, near=None, spread=2.0, lag=0.4, title=""):
    """-> (seconds, why). Best loudness/motion candidate, or Gemini's pick among the top candidates when a key is set."""
    c = candidates(path, near, spread, lag)
    if not c:
        d = duration(path); t = near if near is not None else d * 0.6
        return round(min(max(0.6, t), d - 0.6), 2), "no signal, fallback"
    vi = vision_key(path, c, title) if near is None else None
    if vi is not None: return c[vi][0], f"Gemini vision pick among {len(c)} loudness/motion peaks"
    return c[0][0], f"loudness/motion peak (score {c[0][1]})" + (f" near {near:.1f}s" if near is not None else "")


def pick_key(src, clip=None):
    clip = clip or {}
    for k in ("key_at", "key"):
        v = clip.get(k)
        if v not in (None, ""):
            try: return float(v), "manual key_at"
            except ValueError: pass
    near = clip.get("payoff")
    try:
        return key_moment(src, near=float(near) if near is not None else None, title=clip.get("title", ""))
    except Exception as e:
        log("key moment detection failed:", str(e)[:200])
        d = duration(src); return round(float(near) if near is not None else d * 0.6, 2), "fallback (detection failed)"


# ------------------------------------------------------------------------------------------------ copy helpers
_CTA_RULES = [
    (r"plane|seaplane|fly|flying|pilot|heli|jet|takeoff|take off", "THE FRIEND WHO SAYS HE CAN FLY"),
    (r"wheelie|bike|motorcycle|moto", "THE FRIEND WHO THINKS HE CAN RIDE"),
    (r"crash|car|drive|driving|driver|race|boat|phone while", "THE WORST DRIVER YOU KNOW"),
    (r"cop|police|chase|wanted|arrest|jail", "THE FRIEND WHO ALWAYS GETS CAUGHT"),
    (r"rob|heist|steal|stole|bank|store", "YOUR HEIST PARTNER"),
    (r"npc|karen|ped|random", "THE FRIEND WHO ACTS LIKE THIS NPC"),
    (r"fight|shoot|gun|shootout|punch", "THE FRIEND WHO STARTS EVERY FIGHT"),
]


def share_cta(s=None, clip=None):
    """'SEND THIS TO THE FRIEND WHO ...' (ALL CAPS). Uses the script's share_cta when it is well-formed."""
    s, clip = s or {}, clip or {}
    c = re.sub(r"\s+", " ", str(s.get("share_cta") or clip.get("share_cta") or "")).strip().strip('"').upper()
    c = c.rstrip(".!")
    if c.startswith("SEND THIS TO ") and 3 <= len(c.split()) - 3 <= 8 and len(c) <= 60:
        return c
    blob = " ".join(str(x) for x in (clip.get("title"), clip.get("desc"), s.get("hook"), s.get("title"))).lower()
    for pat, tail in _CTA_RULES:
        if re.search(r"\b(" + pat + r")", blob): return "SEND THIS TO " + tail
    return "SEND THIS TO YOUR GTA 6 SQUAD"


def cta_sentence(cta):
    """'SEND THIS TO THE FRIEND WHO CAN'T FLY' -> 'Send this to the friend who can't fly 📤' for captions/descriptions."""
    t = cta.strip().lower()
    t = re.sub(r"\bgta\b", "GTA", t)
    return (t[:1].upper() + t[1:]).rstrip(".!") + " 📤"


def credit_for(clip):
    u = (clip.get("url") or "").split("?")[0]
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", u)
    if m and m.group(1).lower() not in ("videos", "clip"): return f"clip: twitch.tv/{m.group(1).lower()}"
    m = re.search(r"kick\.com/([A-Za-z0-9_-]+)", u)
    if m: return f"clip: kick.com/{m.group(1).lower()}"
    return f"clip: {clip.get('credit') or clip.get('uploader') or 'source'}"


# ------------------------------------------------------------------------------------------------ text helpers
def _w(text, size, font):
    from PIL import ImageFont
    return ImageFont.truetype(str(font), size).getlength(text)


def fit(text, size, maxw=960, max_lines=2, minsize=48, font=None):
    """Greedy wrap that shrinks until it fits max_lines at maxw. -> (lines, size)."""
    font = font or font_file(text)
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        if cur and _w(cur + " " + w_, size, font) > maxw: lines.append(cur); cur = w_
        else: cur = (cur + " " + w_).strip()
    if cur: lines.append(cur)
    if (len(lines) > max_lines or any(_w(l, size, font) > maxw for l in lines)) and size > minsize:
        return fit(text, size - 4, maxw, max_lines, minsize, font)
    return lines[:max_lines], size


def hook_lines(hook):
    """'LINE 1|LINE 2' or free text -> at most 2 balanced lines."""
    hook = re.sub(r"\s+", " ", hook or "").strip().upper()
    if "|" in hook: return [x.strip() for x in hook.split("|") if x.strip()][:2]
    w_ = hook.split()
    if len(w_) < 3: return [hook]
    best = min(range(1, len(w_)), key=lambda i: abs(len(" ".join(w_[:i])) - len(" ".join(w_[i:]))))
    return [" ".join(w_[:best]), " ".join(w_[best:])]


class Text:
    """drawtext with textfile= (no escaping problems with ' : % in hooks)."""
    def __init__(self, work):
        self.work = pathlib.Path(work); self.n = 0

    def __call__(self, text, y, size, color, t0, t1, bw=10, x="(w-text_w)/2", font=None, shadow=True):
        self.n += 1
        p = self.work / f"hf_txt{self.n}.txt"; p.write_text(text.replace("\r", ""))
        f = font or font_file(text)
        sh_ = ":shadowx=0:shadowy=6:shadowcolor=black@0.6" if shadow else ""
        return (f"drawtext=fontfile='{f}':textfile='{p}':expansion=none:fontsize={size}:fontcolor={color}:borderw={bw}:bordercolor=black"
                f"{sh_}:x={x}:y={y}:enable='between(t,{t0:.3f},{t1:.3f})'")


# ------------------------------------------------------------------------------------------------ plan
def plan(dur, key, total=18.0, body_len=None):
    """Source windows for the 4 parts. total = target output length (12-20 s). body_len overrides (narrated body file)."""
    total = max(12.0, min(float(os.environ.get("HF_MAX", "20")), float(total)))   # HF_MAX: longer Shorts keep the whole setup -> payoff
    key = min(max(0.0, key), dur)
    c0 = min(max(0.0, key - R_PRE), max(0.0, dur - COLD))     # same start as the replay: the impact is in frame 1
    cold = (round(c0, 3), round(min(dur, c0 + COLD), 3))
    r0, r1 = max(0.0, key - R_PRE), min(dur, key + R_POST)
    rep = (round(r0, 3), round(r1, 3))
    rep_out = (r1 - r0) / SLOW
    if body_len is None:
        B = total - COLD - rep_out - END
        B = max(4.0, min(B, dur))
        s = key - 0.62 * B
        s = max(0.0, min(s, dur - END - B)) if dur - END - B > 0 else 0.0
        e = min(dur, s + B)
        if not s <= key <= e:                                  # very short clip: keep the key inside the body
            s = max(0.0, key - 0.62 * B); e = min(dur, s + B)
        body = (round(s, 3), round(e, 3))
        ea = e if e + END <= dur else max(0.0, dur - END)
        endw = (round(ea, 3), round(min(dur, ea + END), 3))
    else:
        body = None
        ea = min(dur - END, key + R_POST) if dur > END else 0.0
        endw = (round(max(0.0, ea), 3), round(min(dur, max(0.0, ea) + END), 3))
    return {"cold": cold, "body": body, "replay": rep, "end": endw}


# ------------------------------------------------------------------------------------------------ render
def _full_crop(w, h, cam=None):
    """9:16 crop of a landscape frame, centered, slid away from the webcam box when they overlap."""
    if w * 16 <= h * 9:                                        # already portrait-ish
        cw = w; ch = int(w * 16 / 9) // 2 * 2
        if ch > h: ch = h // 2 * 2
        return cw // 2 * 2, ch, 0, (h - ch) // 2
    ch = h // 2 * 2; cw = int(h * 9 / 16) // 2 * 2
    x = (w - cw) // 2
    if cam:
        bx, by, bw, bh = cam
        if x < bx + bw and x + cw > bx and bh < h * 0.6:     # overlaps the webcam: slide to the far side
            x = min(w - cw, bx + bw + 4) if bx + bw / 2 < w / 2 else max(0, bx - cw - 4)
    return cw, ch, int(x) // 2 * 2, 0


def render(src, dst, hook, cta, credit, key, cam=None, total=18.0, work="/tmp/hookfirst", extra=None,
           end_sub=None, body_file=None, crf=20, preset="veryfast"):
    """Render the hook-first video. extra(tl) may return more drawtext/drawbox filters (output timeline).
    body_file: an already-rendered 1080x1920 body (narrated Short) used as part 2 instead of the raw clip.
    Returns info: seconds, key_at, plan, timeline, xsegs [(src_start, src_len, speed)] for the 16:9 X version."""
    import layout
    work = pathlib.Path(work); work.mkdir(parents=True, exist_ok=True)
    src, dst = str(src), str(dst)
    dur = duration(src); w, h = probe_wh(src)
    landscape = w > h
    cam = cam if (cam and landscape) else None
    blen = duration(body_file) if body_file else None
    P = plan(dur, key, total, body_len=blen)
    aud = has_audio(src)
    parts = [("cold", P["cold"], 1.0), ("body", P["body"], 1.0), ("replay", P["replay"], SLOW), ("end", P["end"], 1.0)]
    cw, ch, cx, cy = _full_crop(w, h, cam)
    TOP, BOT = layout.TOP_H, layout.BOT_H
    fc, tl, t = [], {}, 0.0
    for i, (name, win, sp) in enumerate(parts):
        if name == "body" and body_file:
            ln = blen
            fc.append(f"[1:v]scale={W}:{H},fps={FPS},setsar=1,trim=duration={ln:.3f},setpts=PTS-STARTPTS[v{i}];")
            fc.append(f"[1:a]atrim=0:{ln:.3f},asetpts=PTS-STARTPTS,aresample=48000[au{i}];")
            tl[name] = (round(t, 3), round(t + ln, 3)); t += ln
            continue
        a, b = win
        ln = (b - a) / sp
        nf = max(1, int(round(ln * FPS)))
        fc.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{sp},fps={FPS}[t{i}];")
        full = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,eq=contrast=1.06:saturation=1.15,setsar=1")
        if name == "cold":
            # push-in from frame 1 (motion even if the game camera is still) + the action itself
            fc.append(f"[t{i}]{full},zoompan=z='1+0.10*on/{nf}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
                      f"trim=end_frame={nf},setsar=1[v{i}];")
        elif cam:
            if name == "replay":
                bx, by, bw_, bh_ = cam
                fc.append(f"[t{i}]split[rt{i}][rb{i}];"
                          f"[rt{i}]crop={bw_}:{bh_}:{bx}:{by},scale={W}:{TOP}:force_original_aspect_ratio=increase,crop={W}:{TOP},setsar=1[top{i}];"
                          f"[rb{i}]scale=-2:{BOT}:flags=lanczos,crop={W}:{BOT}:(iw-{W})/2:0,eq=contrast=1.06:saturation=1.15,"
                          f"zoompan=z='min(1+{ZOOM - 1:.2f}*on/9,{ZOOM:.2f})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{BOT}:fps={FPS},"
                          f"trim=end_frame={nf},setsar=1[bot{i}];"
                          f"[top{i}][bot{i}]vstack=inputs=2,drawbox=x=0:y={TOP - 3}:w={W}:h=6:color=white@0.9:t=fill,"
                          f"fade=t=in:st=0:d=0.18:color=white,setsar=1[v{i}];")
            else:
                fc.append(layout.split_filter(f"[t{i}]", f"v{i}", cam))
        else:
            if name == "replay":
                fc.append(f"[t{i}]{full},zoompan=z='min(1+{ZOOM - 1:.2f}*on/9,{ZOOM:.2f})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                          f"s={W}x{H}:fps={FPS},trim=end_frame={nf},fade=t=in:st=0:d=0.18:color=white,setsar=1[v{i}];")
            else:
                fc.append(f"[t{i}]{full}[v{i}];")
        if aud:
            at = f",atempo={sp}" if sp != 1 else ""
            fc.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS{at},aresample=48000,"
                      f"afade=t=in:d=0.03,afade=t=out:st={max(0, ln - 0.04):.3f}:d=0.04,apad,atrim=0:{ln:.3f}[au{i}];")
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{ln:.3f}[au{i}];")
        tl[name] = (round(t, 3), round(t + ln, 3)); t += ln
    total_out = t
    fc.append("".join(f"[v{i}][au{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[vc][ac];")

    T = Text(work)
    txt = []
    # 1) cold-open hook: 2 lines max, white + yellow, heavy stroke, upper-middle (clear of UI buttons and bottom 15%)
    hl = hook_lines(hook)
    y = 520
    for j, line in enumerate(hl):
        lines, sz = fit(line, 118 if j == len(hl) - 1 else 100, 980, 1, 56)
        col = YELLOW if (j == len(hl) - 1 and len(hl) > 1) else "white"
        txt.append(T(lines[0], y, sz, col, 0, tl["cold"][1], bw=14))
        y += int(sz * 1.12)
    # 2) credit (body, replay, end)
    c0 = tl["body"][0] if not body_file else tl["replay"][0]
    txt.append(T(credit, (TOP - 54) if cam else 150, 34, "white@0.92", c0, total_out + 1, bw=4, x="36"))
    # 3) replay tag
    r0, r1 = tl["replay"]
    ty = (TOP + 36) if cam else 230
    tag = "REPLAY"; tsz = 64; tw = _w(tag, tsz, font_file(tag))
    txt.append(f"drawbox=x=30:y={ty - 14}:w={int(tw + 64)}:h={tsz + 30}:color={YELLOW}@1:t=fill:enable='between(t,{r0:.3f},{r1:.3f})'")
    txt.append(T(tag, ty, tsz, "black", r0, r1, bw=0, x="62", shadow=False))
    txt.append(T("0.5X", ty + 8, 46, "white", r0, r1, bw=6, x=str(int(tw + 120))))
    # 4) end card over gameplay
    e0 = tl["end"][0]
    txt.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.55:t=fill:enable='gte(t,{e0:.3f})'")
    cta = re.sub(r"\s+", " ", cta).strip().upper()
    head, rest = ("SEND THIS TO", cta[len("SEND THIS TO"):].strip()) if cta.startswith("SEND THIS TO") else ("", cta)
    y = 640
    if head:
        txt.append(T(head, y, 92, "white", e0, total_out + 1, bw=12)); y += 120
    lines, sz = fit(rest, 112, 980, 3, 60)
    for ln_ in lines:
        txt.append(T(ln_, y, sz, YELLOW, e0, total_out + 1, bw=12)); y += int(sz * 1.15)
    if end_sub:
        txt.append(T(end_sub, y + 40, 60, "white", e0, total_out + 1, bw=8))
    if extra:
        txt += [x for x in extra({**tl, "total": total_out, "cam": bool(cam), "top_h": TOP if cam else 0,
                                  "key_out": tl["body"][0] + (key - P["body"][0]) if P["body"] else None}) if x]
    fc.append("[vc]" + ",".join(txt) + ",format=yuv420p[vo];[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    (work / "hf_filter.txt").write_text("".join(fc))
    inputs = ["-i", src] + (["-i", str(body_file)] if body_file else [])
    sh("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", work / "hf_filter.txt", "-map", "[vo]", "-map", "[ao]",
       "-t", f"{total_out:.3f}", "-r", FPS, "-c:v", "libx264", "-preset", preset, "-crf", crf, "-maxrate", "12M", "-bufsize", "24M",
       "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", dst)
    fix_loudness(dst, work)
    xsegs = [(P["cold"][0], P["cold"][1] - P["cold"][0], 1.0)]
    if P["body"]: xsegs.append((P["body"][0], P["body"][1] - P["body"][0], 1.0))
    xsegs += [(P["replay"][0], P["replay"][1] - P["replay"][0], SLOW), (P["end"][0], P["end"][1] - P["end"][0], 1.0)]
    info = {"structure": "hookfirst", "seconds": round(total_out, 2), "key_at": round(key, 2), "plan": P, "timeline": tl,
            "layout": "facecam-top" if cam else "fullframe", "xsegs": xsegs, "hook": " | ".join(hl), "share_cta": cta}
    log(f"rendered {dst} {total_out:.1f}s key={key:.2f} layout={info['layout']}")
    return info


def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def fix_loudness(path, work, target=-14.0):
    """Single-pass loudnorm lands 1-2 LU low on short clips: measure and nudge the gain (video copied, not re-encoded)."""
    try:
        i = loudness(path)
        if i is None or abs(i - target) <= 0.6 or i < -60: return i
        g = max(-6.0, min(6.0, target - i))
        tmp = pathlib.Path(work) / "hf_gain.mp4"
        sh("ffmpeg", "-v", "error", "-y", "-i", path, "-c:v", "copy", "-af", f"volume={g:.2f}dB,alimiter=limit=0.89:level=false",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", tmp)
        shutil.move(str(tmp), str(path))
        log(f"loudness {i:.1f} -> gain {g:+.1f} dB")
        return loudness(path)
    except Exception as e:
        log("loudness fix skipped:", str(e)[:150]); return None


def x_filter(xsegs, body_segs=None):
    """16:9 X version of the same timeline (the Short's audio is mapped on top). body_segs replace the body part
    for a narrated Short: list of (start, len)."""
    segs = list(xsegs)
    if body_segs is not None:
        segs = [segs[0]] + [(a, l, 1.0) for a, l in body_segs] + segs[-2:]
    f = ""
    for i, (a, l, sp) in enumerate(segs):
        f += (f"[0:v]trim=start={a:.3f}:duration={l:.3f},setpts=(PTS-STARTPTS)/{sp},fps={FPS},"
              f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[x{i}];")
    return f + "".join(f"[x{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[xv]"


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 5: print(__doc__); sys.exit(2)
    import layout as _l
    k, why = (float(a[5]), "manual") if len(a) > 5 else key_moment(a[0])
    log("key", k, why)
    print(json.dumps(render(a[0], a[1], a[2], a[3], a[4], k, cam=_l.find_facecam(a[0])), indent=1, default=str))
