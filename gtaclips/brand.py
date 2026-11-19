#!/usr/bin/env python3
"""Client branding, run after the video is made and before review/upload. Sound-effect errors never break a run (original kept);
   a client logo that was asked for but fails stops the run.
  1. Logo watermark, per run: the logo_url box on GitHub's Run workflow form (BRAND_LOGO_URL). Each client gets their own logo;
     blank = no logo. There is NO saved/default logo, so one client's logo can never land on another client's video.
     If a logo link was given and it can't be used, the run STOPS (exit 3) instead of posting a video without the client's logo.
     `python brand.py --check` only downloads + checks the link (runs first, so a bad link fails in seconds).
     BRAND_POS = top-right | top-left | top-center (default: top-right on Shorts, top-left on long videos). BRAND_SIZE = % (default 11).
  2. Sound effects matched to the genre (genre.py): funny = boing / vine-boom / ding, serious = whoosh / deep impact / riser.
     Synthesized with FFmpeg, so no copyrighted sounds. GTA_SFX=off turns them off.
Usage: python brand.py out/short.mp4 [out/short_x.mp4 ...]"""
import os, sys, re, pathlib, subprocess, urllib.request, shutil
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import genre
class LogoError(Exception): pass

def sh(*a, t=900):
    r = subprocess.run(list(map(str, a)), capture_output=True, text=True, timeout=t)
    if r.returncode: raise RuntimeError(r.stderr[-500:])
    return r.stdout + r.stderr

def probe(p):
    o = sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", p)
    import json; j = json.loads(o)
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), {})
    return int(v.get("width", 1080)), int(v.get("height", 1920)), float(j["format"].get("duration", 30)), any(s.get("codec_type") == "audio" for s in j["streams"])

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

def levels(p):
    """RMS loudness every 0.25 s."""
    o = sh("ffmpeg", "-hide_banner", "-nostats", "-i", p, "-vn", "-af",
           "aresample=8000,asetnsamples=n=2000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-", t=1800)
    return [float(x) if "inf" not in x else -90.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", o)]

def hit_time(lv, a, b):
    """Biggest loudness jump between a and b seconds (where the reaction hits), or None."""
    best, bt = 0, None
    for i in range(max(4, int(a * 4)), min(len(lv) - 2, int(b * 4))):
        jump = sum(lv[i:i+2]) / 2 - sum(lv[i-4:i]) / 4
        if jump > best: best, bt = jump, i * 0.25
    return bt if best > 4 else None

# synthesized, royalty-free effects: (ffmpeg lavfi source, length s, volume)
SFX = {
 "whoosh": ("anoisesrc=d=0.6:c=pink:a=0.6,highpass=f=300,lowpass=f=5000,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.25", 0.9),
 "impact": ("sine=f=55:d=1.2,volume=2.5,afade=t=out:st=0.05:d=1.1", 1.3),
 "riser":  ("sine=f=220:d=1.4,vibrato=f=9:d=0.3,afade=t=in:d=1.2,afade=t=out:st=1.25:d=0.15,volume=0.5", 0.7),
 "boom":   ("sine=f=48:d=1.0,volume=3,afade=t=out:st=0.03:d=0.95", 1.4),   # vine-boom style
 "boing":  ("aevalsrc=0.6*sin(2*PI*(180+120*sin(2*PI*7*t))*t)*exp(-3*t):d=0.8", 0.8),
 "ding":   ("aevalsrc=0.5*sin(2*PI*1320*t)*exp(-4*t)+0.3*sin(2*PI*1980*t)*exp(-5*t):d=1.0", 0.6),
 "chime":  ("aevalsrc=0.4*sin(2*PI*660*t)*exp(-2*t)+0.3*sin(2*PI*990*t)*exp(-2.5*t):d=1.4", 0.5),
}
PLAN = {"funny": ["boing", "boom", "ding"], "serious": ["whoosh", "impact", "riser"], "soft": ["whoosh", "chime", "chime"]}

def build(src, dst):
    w, h, dur, has_a = probe(src)
    lg = logo()
    md = genre.mood() if os.environ.get("GTA_SFX", "").lower() not in ("off", "0", "false", "no") else ""
    if not lg and not md: print("brand: no logo, no genre sound effects, nothing to do", file=sys.stderr); return False
    ins, fc = ["-i", src], ""
    vmap, amap = "0:v", "0:a?"
    if lg:
        pct = float(os.environ.get("BRAND_SIZE", "11")) / 100
        lw = int(min(w, h) * pct) // 2 * 2; m = int(min(w, h) * 0.03)
        y = m
        # Shorts: top-right (the hook sits in the middle). Long videos: top-left (episode.py puts the streamer handle top-right)
        pos = os.environ.get("BRAND_POS") or ("top-right" if h > w else "top-left")
        x = {"top-left": str(m), "top-center": "(W-w)/2"}.get(pos, f"W-w-{m}")
        ins += ["-loop", "1", "-i", lg]
        fc += f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.85[lg];[0:v][lg]overlay={x}:{y}:shortest=1[v];"
        vmap = "[v]"
    if md and has_a:
        plan = PLAN[md]; cues = []
        try: lv = levels(src)
        except Exception: lv = []
        marks = []
        mf = os.environ.get("BRAND_MARKS", "")
        if mf and pathlib.Path(mf).exists():
            import json; marks = sorted(float(x) for x in json.load(open(mf)))
        if marks:   # long video: a transition sound at every new clip + one genre hit inside each clip
            ends = marks[1:] + [dur]
            for k, (m0, m1) in enumerate(zip(marks, ends)):
                cues.append((plan[0], max(0.05, m0 - 0.15)))
                h = hit_time(lv, m0 + 1.5, m1 - 1.0) if lv else None
                if h: cues.append((plan[1], h - 0.05))
        else:       # Short: intro sound, genre hit on the reaction, end sound under the CTA
            h = (hit_time(lv, 2.0, dur - 3.0) if lv else None) or dur * 0.45
            cues = [(plan[0], 0.05), (plan[1], max(0.3, h - 0.05)), (plan[2], max(0.5, dur - 2.7))]
        n0 = 2 if lg else 1; labels = []
        for k, (name_, t) in enumerate(cues):
            gsrc, vol = SFX[name_]
            ins += ["-f", "lavfi", "-i", gsrc]   # each effect = its own synthesized input
            fc += f"[{n0+k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={int(t*1000)}|{int(t*1000)}[s{k}];"
            labels.append(f"[s{k}]")
        fc += f"[0:a]aresample=48000,aformat=channel_layouts=stereo[main];[main]{''.join(labels)}amix=inputs={len(labels)+1}:duration=first:normalize=0,alimiter=limit=0.95[a];"
        amap = "[a]"
        print(f"brand: {md} sound effects: " + ", ".join(f"{n}@{t:.1f}s" for n, t in cues), file=sys.stderr)
    if fc.endswith(";"): fc = fc[:-1]
    sh("ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", vmap, "-map", amap, "-t", f"{dur:.3f}",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
       "-movflags", "+faststart", dst, t=1800)
    print(f"brand: {'logo ' if lg else ''}{'+ ' + md + ' sfx ' if md else ''}-> {src}", file=sys.stderr)
    return True

if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:   # fail fast on a bad logo link, before the video is made
        try:
            lg = logo()
            from PIL import Image
            print("brand: client logo OK" + (f" {Image.open(lg).size}" if lg else " (none for this run)"))
        except LogoError as e:
            print(f"::error::Client logo problem: {e}. Nothing was made or posted. Fix the logo_url box and run again."); sys.exit(3)
        sys.exit(0)
    bad = False
    for f in sys.argv[1:]:
        p = pathlib.Path(f)
        if not p.exists(): continue
        tmp = p.with_name(p.stem + "_brand.mp4")
        try:
            if build(p, tmp): tmp.replace(p)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            if os.environ.get("BRAND_LOGO_URL", "").strip() and os.environ.get("BRAND_LOGO", "").lower() not in ("off", "0", "false", "no"):
                if not isinstance(e, LogoError) and os.environ.get("GTA_SFX", "").lower() != "off":
                    print(f"brand: failed with sound effects ({str(e)[:200]}), retrying logo only", file=sys.stderr)
                    os.environ["GTA_SFX"] = "off"
                    try:
                        if build(p, tmp): tmp.replace(p)
                        continue
                    except Exception as e2: e = e2; tmp.unlink(missing_ok=True)
                print(f"::error::Client logo could not be added to {p.name}: {str(e)[:300]}. Stopping so it never posts without the logo.")
                bad = True
            else:
                print(f"brand: skipped for {p.name}, original kept: {str(e)[:300]}", file=sys.stderr)
    sys.exit(3 if bad else 0)
