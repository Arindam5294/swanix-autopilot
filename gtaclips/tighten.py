#!/usr/bin/env python3
"""Clip prep before the hook-first edit (fix 7). Two steps, each one skips itself safely if it can't run:

1. voice_forward: AI splits the streamer's voice from the game/music (demucs htdemucs, CPU, ~1x realtime).
   The voice stays up front; game audio + the streamer's background music drop ~9 dB and duck further under speech.
2. dead_air: long stretches with no talking (> DEAD_GAP s) are shortened to 1 s on each side, so the setup and the
   payoff sit close together. Never cuts near the key moment.

prep(src, clip, work) -> (new_src, key, why). Config keys (config.json): voice_forward, dead_air, dead_gap,
music_drop_db, hookfirst_max."""
import json, os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
SEP = r'''
import sys, torch, soundfile as sf
from demucs.pretrained import get_model
from demucs.apply import apply_model
torch.set_num_threads(max(1, __import__("os").cpu_count() or 2))
m = get_model("htdemucs"); m.eval()
x, sr = sf.read(sys.argv[1], dtype="float32")
w = torch.tensor(x.T)[None]
ref = w.mean(1, keepdim=True); mu, sd = ref.mean(), ref.std() + 1e-8
with torch.no_grad():
    out = apply_model(m, (w - mu) / sd, split=True, overlap=0.1, progress=False)[0] * sd + mu
vi = m.sources.index("vocals")
sf.write(sys.argv[2], out[vi].numpy().T, sr)
sf.write(sys.argv[3], (out.sum(0) - out[vi]).numpy().T, sr)
'''


def _run(cmd, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def voice_forward(src, work, drop_db=9.0):
    """Returns a copy of src with the voice pulled forward, or None if demucs isn't available / fails."""
    work = pathlib.Path(work); wav, voc, bg = work / "vf_in.wav", work / "vf_voc.wav", work / "vf_bg.wav"
    out = work / "src_voice.mp4"
    try:
        if _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(wav)]).returncode: return None
        (work / "vf_sep.py").write_text(SEP)
        r = _run([sys.executable, str(work / "vf_sep.py"), str(wav), str(voc), str(bg)], timeout=1200)
        if r.returncode or not voc.exists():
            print("voice_forward: skipped (demucs not available):", (r.stderr or "")[-200:], file=sys.stderr); return None
        fc = (f"[1:a]highpass=f=90,acompressor=threshold=-24dB:ratio=3:attack=5:release=120,volume=2dB,asplit=2[v][sc];"
              f"[2:a]volume=-{drop_db}dB[b];[b][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[bd];"
              f"[v][bd]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=9[a]")
        r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(voc), "-i", str(bg), "-filter_complex", fc,
                  "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)])
        if r.returncode: print("voice_forward: mix failed:", r.stderr[-200:], file=sys.stderr); return None
        print(f"voice_forward: streamer voice up front, game/music -{drop_db} dB + ducked under speech", file=sys.stderr)
        return out
    except Exception as e:
        print("voice_forward: skipped:", str(e)[:200], file=sys.stderr); return None


def _dur(p):
    return float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout.strip() or 0)


def motion(src, fps=4):
    """Per-sample visual change (0.25 s steps). Action with no talking (a crash, a flip) must never count as dead air."""
    try:
        import cv2, numpy as np
        cap, prev, out = cv2.VideoCapture(str(src)), None, []
        step = max(1, int(round((cap.get(cv2.CAP_PROP_FPS) or 30) / fps))); i = 0
        while True:
            ok, f = cap.read()
            if not ok: break
            if i % step == 0:
                g = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY).astype("float32")
                out.append(0.0 if prev is None else float(np.abs(g - prev).mean())); prev = g
            i += 1
        return [(k / fps, v) for k, v in enumerate(out)]
    except Exception as e:
        print("dead_air: motion check unavailable:", str(e)[:120], file=sys.stderr); return None


def quiet_runs(c0, c1, mo, pad=1.0, min_len=2.0):
    """Parts of [c0, c1] with no big on-screen action (motion under 1.3x the clip median), padded by pad s."""
    if mo is None: return []   # can't see the picture -> don't cut anything
    vals = sorted(v for _, v in mo); med = vals[len(vals) // 2] if vals else 0
    busy = [t for t, v in mo if c0 <= t <= c1 and v > 1.3 * med]
    runs, s = [], c0
    for t in busy:
        if t - pad - s >= min_len: runs.append((s, t - pad))
        s = max(s, t + pad)
    if c1 - s >= min_len: runs.append((s, c1))
    return runs


def keep_windows(words, dur, key, gap=3.5, edge=1.0, guard=(2.5, 1.5), mo=None):
    """Windows of the source to keep: every silent stretch longer than gap shrinks to edge s on each side,
    except one that touches [key-guard0, key+guard1] (the payoff's own build-up stays)."""
    t, cuts = 0.0, []
    for (a, b, _w) in list(words) + [(dur, dur, "")]:
        if a - t > gap:
            c0, c1 = t + edge, a - edge
            if not (c1 > key - guard[0] and c0 < key + guard[1]): cuts += quiet_runs(c0, c1, mo)
        t = max(t, b)
    keep, s = [], 0.0
    for c0, c1 in cuts:
        keep.append((s, c0)); s = c1
    keep.append((s, dur))
    return [(round(float(a), 3), round(float(b), 3)) for a, b in keep if b - a > 0.2]


def map_time(t, keep):
    acc = 0.0
    for a, b in keep:
        if t <= b: return round(acc + max(0.0, t - a), 3)
        acc += b - a
    return round(acc, 3)


def dead_air(src, work, key, gap=3.5):
    try:
        import captions
        ws = captions.words(src)
    except Exception as e:
        print("dead_air: skipped (no transcript):", str(e)[:150], file=sys.stderr); return None, key
    dur = _dur(src)
    if not ws or dur <= 0: return None, key
    keep = keep_windows(ws, dur, key, gap=gap, mo=motion(src))
    kept = sum(b - a for a, b in keep)
    if len(keep) < 2 or dur - kept < 1.5 or kept < 8.0: return None, key
    fc = "".join(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS[a{i}];" for i, (a, b) in enumerate(keep))
    fc += "".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[v][a]"
    out = pathlib.Path(work) / "src_tight.mp4"
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)])
    if r.returncode: print("dead_air: cut failed:", r.stderr[-200:], file=sys.stderr); return None, key
    print(f"dead_air: {dur:.1f}s -> {kept:.1f}s, kept {keep}", file=sys.stderr)
    return out, map_time(key, keep)


def prep(src, clip, work, cfg=None):
    import hookfirst
    cfg = cfg or {}
    key, why = hookfirst.pick_key(src, clip)
    cur = src
    if cfg.get("voice_forward", True):
        cur = voice_forward(cur, work, float(cfg.get("music_drop_db", 9))) or cur
    if cfg.get("dead_air", True) and not (clip or {}).get("key_at"):
        new, key2 = dead_air(cur, work, key, float(cfg.get("dead_gap", 3.5)))
        if new: cur, key, why = new, key2, why + " (dead air trimmed)"
    return cur, key, why


def total_for(src, cfg=None):
    """Whole setup -> payoff fits: cold 1.2 + body + replay 3.2 + end 1.5, capped at hookfirst_max (default 30)."""
    cfg = cfg or {}
    cap = float(cfg.get("hookfirst_max", 30)); os.environ.setdefault("HF_MAX", str(cap))
    return max(float(cfg.get("hookfirst_seconds", 18)), min(cap, 1.2 + _dur(src) + 3.2 + 1.5))
