#!/usr/bin/env python3
"""Find the most viral moment inside a long video, a finished stream, or a live stream.
Signals, best first:
  1. YouTube 'Most replayed' heatmap (what viewers actually rewatched)
  2. Live-chat burst in the stream replay (chat spams when something big happens)
  3. Audio hype: loudness spikes (streamer yelling, explosions) + fast scene cuts
Returns (start_seconds, length_seconds) of the best window."""
import json, os, re, subprocess, sys, pathlib, collections
WIN = 45          # seconds of raw footage around the peak (Short keeps the best ~32-40 s)
PRE = 12          # start this many seconds before the peak, so the build-up is in

def run(cmd, t=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=t)

def ytdlp_base():
    b = ["yt-dlp", "--no-warnings", "--no-playlist"]
    if os.path.exists("cookies.txt"): b += ["--cookies", "cookies.txt"]
    if os.environ.get("YTDLP_HOME_PROXY"): b += ["--proxy", os.environ["YTDLP_HOME_PROXY"]]
    return b

def info(url):
    r = run(ytdlp_base() + ["-J", url], 180)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}

def from_heatmap(j):
    hm = j.get("heatmap") or []
    hm = [h for h in hm if h.get("start_time", 0) > 20]          # skip the intro spike
    if not hm: return None
    # smooth over ~3 buckets so one blip doesn't win
    best, bt = -1, 0
    for i in range(len(hm)):
        sc = sum(h["value"] for h in hm[max(0, i-1):i+2]) + hm[i]["value"] * 0.01
        if sc > best: best, bt = sc, hm[i]["start_time"]
    return bt, f"most-replayed heatmap ({best:.2f})"

def from_chat(url, dur, work):
    """Download the live-chat replay (text only, small) and find the busiest 20-second stretch."""
    if dur < 600: return None
    out = work / "chat"
    run(ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(out), url], 600)
    f = next(work.glob("chat*.live_chat.json"), None)
    if not f: return None
    buckets = collections.Counter()
    for line in f.read_text(errors="ignore").splitlines():
        m = re.search(r'"videoOffsetTimeMsec":\s*"(\d+)"', line)
        if m: buckets[int(m.group(1)) // 20000] += 1
    if len(buckets) < 10: return None
    avg = sum(buckets.values()) / max(1, max(buckets) + 1)
    b, n = max(((k, v) for k, v in buckets.items() if k * 20 > 30), key=lambda x: x[1], default=(None, 0))
    if b is None or n < avg * 2.5: return None                     # no real spike
    return b * 20, f"chat spike ({n} msgs/20s vs avg {avg:.0f})"

def audio_peak(path, offset=0.0):
    """Loudest sustained ~10 s of a media file (works on audio-only or video)."""
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
             "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", "-"], 1800)
    lv = [float(x) if x not in ("-inf", "inf") else -90.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", r.stderr)]
    if len(lv) < 30: return None
    base = sorted(lv)[len(lv) // 2]
    best, bt = -999, 0
    for i in range(20, len(lv) - 10):                               # each value = 1 s
        sc = sum(lv[i:i+10]) / 10 - base
        if sc > best: best, bt = sc, i
    return offset + bt, f"audio hype (+{best:.1f} dB over median)"

def from_audio(url, dur, work):
    """For a long VOD: grab only the lowest-bitrate audio (a 4 h stream is ~60-90 MB) and find the hype peak."""
    a = work / "aud.m4a"
    r = run(ytdlp_base() + ["-f", "worstaudio/ba", "-o", str(a), url], 1500)
    f = next(work.glob("aud.*"), None)
    return audio_peak(f) if f else None

def record_live(url, work, seconds):
    """Record the next N seconds of a live stream (MPEG-TS so a stopped recording is still playable)."""
    ts = work / "live.ts"
    routes = [[]] + ([["--proxy", os.environ["YTDLP_PROXY"]]] if os.environ.get("YTDLP_PROXY") and not os.environ.get("YTDLP_HOME_PROXY") else [])
    f = None
    for extra in routes:   # direct first; if YouTube blocks GitHub, record through the WARP proxy
        p = subprocess.Popen(ytdlp_base() + extra + ["-f", "b[height<=1080]/b", "--hls-use-mpegts", "--no-part", "-o", str(ts), url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try: p.wait(timeout=seconds)
        except subprocess.TimeoutExpired: p.terminate(); p.wait(30)
        f = next(work.glob("live.*"), None)
        if f and f.stat().st_size >= 500000: break
        print("record_live: route failed, trying next", file=sys.stderr)
    if not f or f.stat().st_size < 500000: raise RuntimeError("live recording failed (stream ended or blocked)")
    mp4 = work / "live.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-c", "copy", str(mp4)], 300)
    return mp4

def best_window(clip, work):
    """-> (start, length, reason) for a VOD/stream replay. None = use the whole thing (short video)."""
    j = info(clip["url"])
    dur = float(j.get("duration") or clip.get("duration") or 0)
    if dur and dur <= 75: return None
    try:   # AI judge: the candidate peak that is truly engaging and fits GTA_GENRE (old peak pick if this fails)
        import genre_moments; g = genre_moments.vod_window(clip, work, j, dur)
        if g: print(f"moments: {g[2]} at {int(g[0])}s", file=sys.stderr); return g
    except Exception as e: print("moments: AI pick failed, using peak pick:", str(e)[:200], file=sys.stderr)
    for fn in (lambda: from_heatmap(j), lambda: from_chat(clip["url"], dur, work), lambda: from_audio(clip["url"], dur, work)):
        try:
            hit = fn()
        except Exception as e:
            print("moments: signal failed:", e, file=sys.stderr); hit = None
        if hit:
            t, why = hit
            start = max(0, t - PRE)
            if dur: start = min(start, max(0, dur - WIN))
            print(f"moments: peak at {int(t)//60}:{int(t)%60:02d} from {why}", file=sys.stderr)
            return start, WIN, why
    return (max(0, dur * 0.4), WIN, "no signal, middle of video") if dur else None

def subs_text(url, start, length, work):
    """Auto-captions for just the chosen window, so the AI knows what was actually said (no guessing)."""
    try:
        run(ytdlp_base() + ["--skip-download", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
                            "-o", str(work / "subs"), url], 180)
        f = next(work.glob("subs*.vtt"), None)
        if not f: return ""
        words, keep = [], False
        for line in f.read_text(errors="ignore").splitlines():
            m = re.match(r"(\d+):(\d+):(\d+)\.\d+ -->", line)
            if m:
                t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]); keep = start <= t <= start + length; continue
            if keep and line.strip() and "-->" not in line:
                clean = re.sub(r"<[^>]+>", "", line).strip()
                if clean and (not words or words[-1] != clean): words.append(clean)
        return " ".join(words)[:1200]
    except Exception:
        return ""
