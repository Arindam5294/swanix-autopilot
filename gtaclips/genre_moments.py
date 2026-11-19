#!/usr/bin/env python3
"""Pick the moment by WHAT HAPPENS, not just by the loudest / most-replayed timestamp.
Scans the WHOLE video: every 20 s window of what was said is scored for the genre's words and hype, merged with
the most-replayed / loudest peaks, and up to 14 candidates go to the AI judge. Finds candidate peaks (most-replayed heatmap, or loudness spikes), reads what was said in each
(YouTube auto-captions for VODs, Whisper for live recordings), then an AI judge picks the one that is
truly engaging AND fits the genre you typed (GTA_GENRE: funny, rage, racing, ...). Blank genre = "most engaging".
Any failure returns None, so the old peak pick runs instead (never blocks a run)."""
import json, os, re, sys, subprocess, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
import moments, genre

def _spread(pairs, n=8, gap=40):
    """pairs = [(t, score)] -> up to n best times at least `gap` s apart."""
    out = []
    for t, sc in sorted(pairs, key=lambda x: -x[1]):
        if all(abs(t - o) >= gap for o, _ in out): out.append((t, sc))
        if len(out) >= n: break
    return out

def heat_peaks(j):
    hm = [h for h in (j.get("heatmap") or []) if h.get("start_time", 0) > 20]
    return _spread([(h["start_time"], h["value"]) for h in hm], n=6) if hm else []

def loud_peaks(path):
    r = moments.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                     "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                     "-f", "null", "-"], 1800)
    lv = [float(x) if "inf" not in x else -90.0 for x in re.findall(r"RMS_level=(-?[\d.inf]+)", r.stderr)]
    if len(lv) < 40: return []
    return _spread([(i, sum(lv[i:i+10]) / 10) for i in range(15, len(lv) - 10)], n=8, gap=30)

def vtt(url, work):
    moments.run(moments.ytdlp_base() + ["--skip-download", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
                                        "-o", str(work / "gsubs"), url], 240)
    f = next(work.glob("gsubs*.vtt"), None)
    lines, t = [], 0
    if not f: return lines
    for line in f.read_text(errors="ignore").splitlines():
        m = re.match(r"(\d+):(\d+):(\d+)\.\d+ -->", line)
        if m: t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]); continue
        c = re.sub(r"<[^>]+>", "", line).strip()
        if c and "-->" not in c and (not lines or lines[-1][1] != c): lines.append((t, c))
    return lines

def whisper_lines(path):
    try:
        from faster_whisper import WhisperModel
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
        a = np.frombuffer(raw, np.float32)
        segs, _ = WhisperModel("tiny.en", device="cpu", compute_type="int8").transcribe(a, vad_filter=True, language="en")
        return [(s.start, s.text.strip()) for s in segs]
    except Exception as e:
        print("genre_moments: whisper failed:", str(e)[:150], file=sys.stderr); return []

HYPE = re.compile(r"(?<![a-z])(no+|what|bro+|yo+|oh my god|omg|wait|run|go+|let'?s go|why|help|dude|holy|wtf|nah)(?![a-z])", re.I)

def text_peaks(lines, n=8):
    """Scan everything that was said, 20 s steps: genre words count triple, plus hype words and shouting."""
    if not lines: return []
    rx = genre.pattern()
    end = int(max(t for t, _ in lines)); out = []
    for a in range(0, end + 1, 20):
        x = said(lines, a, a + moments.WIN)
        if not x: continue
        sc = (3 * len(rx.findall(x)) if rx else 0) + len(HYPE.findall(x)) + x.count("!") + sum(w.isupper() and len(w) > 2 for w in x.split())
        if sc: out.append((a + moments.PRE, sc))   # +PRE: the window starts PRE seconds before the "peak"
    return _spread(out, n=n, gap=40)

def merge(*groups, n=14):
    out = []
    for g in groups:
        for t, _ in g:
            if all(abs(t - o) >= 25 for o in out): out.append(t)
    return out[:n]

def said(lines, a, b): return " ".join(x for t, x in lines if a <= t <= b)[:500]

def judge(cands, title, who):
    """cands = [(t, text)] -> index of the best, or None."""
    g = genre.name()
    want = f'fits the genre "{g}" best and is genuinely engaging' if g else "is the most genuinely engaging (a real reaction, a payoff, something happens)"
    from ai import ask
    out = ask(f"""You pick the best moment from a GTA 6 stream for a YouTube Short. Stream: "{title}" by {who}.
Below are candidate moments with what was said (auto-captions, may be messy). Pick the ONE that {want}.
Skip boring talk, menus, loading, ads, donations, reading chat. Return ONLY JSON: {{"best": <number>, "fit": <0-10>, "why": "<8 words>"}}
""" + "\n".join(f"{i}. [{int(t)//60}:{int(t)%60:02d}] {x or '(nothing said)'}" for i, (t, x) in enumerate(cands)), min_len=8)
    j = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
    i = int(j.get("best", -1))
    if not 0 <= i < len(cands): return None
    if g and int(j.get("fit", 10)) < 4: print(f"genre_moments: weak '{g}' fit ({j.get('fit')}/10), best available", file=sys.stderr)
    print(f"genre_moments: picked #{i} at {int(cands[i][0])}s ({j.get('why','')})", file=sys.stderr)
    return i

def vod_window(clip, work, j, dur):
    peaks = heat_peaks(j)
    src = "heatmap"
    if not peaks:
        a = work / "gaud.m4a"
        moments.run(moments.ytdlp_base() + ["-f", "worstaudio/ba", "-o", str(a), clip["url"]], 1500)
        f = next(work.glob("gaud.*"), None)
        peaks, src = (loud_peaks(f) if f else []), "audio"
    lines = vtt(clip["url"], work)
    if not lines: return None
    times = merge(text_peaks(lines), peaks)   # the whole transcript first, then the replay/loudness peaks
    if len(times) < 2: return None
    print(f"genre_moments: judging {len(times)} moments from the whole video", file=sys.stderr)
    cands = [(t, said(lines, t - moments.PRE, t - moments.PRE + moments.WIN)) for t in times]
    i = judge(cands, clip.get("title", ""), clip.get("uploader", "the streamer"))
    if i is None: return None
    start = max(0, cands[i][0] - moments.PRE)
    if dur: start = min(start, max(0, dur - moments.WIN))
    return start, moments.WIN, f"AI-picked moment from the whole video ({genre.name() or 'most engaging'})"

def live_pick(path, clip=None):
    """For a live recording: returns (t, why) like moments.audio_peak, or None."""
    try:
        lines = whisper_lines(path)
        if not lines: return None
        times = merge(text_peaks(lines), loud_peaks(path))
        if len(times) < 2: return None
        print(f"genre_moments: judging {len(times)} live moments", file=sys.stderr)
        cands = [(t, said(lines, t - moments.PRE, t - moments.PRE + moments.WIN)) for t in times]
        i = judge(cands, (clip or {}).get("title", ""), (clip or {}).get("uploader", "the streamer"))
        return None if i is None else (cands[i][0], f"AI-picked live moment ({genre.name() or 'most engaging'})")
    except Exception as e:
        print("genre_moments: live pick failed, loudest moment used:", str(e)[:200], file=sys.stderr); return None
