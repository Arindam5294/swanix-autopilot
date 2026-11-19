#!/usr/bin/env python3
"""Playbook step 6: word-by-word captions of what the STREAMER says (the narrator's words are already captioned
by the overlay). faster-whisper gives word timings; each word pops in big, in the safe zone (clear of the
Shorts/TikTok/Reels buttons on the right and the caption text at the bottom).
Usage: burn(video_in, video_out, start_after=seconds, y=center_y)."""
import subprocess, sys, pathlib, re

def words(video, start_after=0.0, model=None, end_before=None):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("captions: faster-whisper missing, skipping streamer captions", file=sys.stderr); return []
    import numpy as np
    # decode with ffmpeg ourselves (faster-whisper's PyAV decoder breaks across av versions)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    if audio.size < 16000: return []
    try:
        m = WhisperModel(model or "base.en", device="cpu", compute_type="int8")
        segs, _ = m.transcribe(audio, word_timestamps=True, vad_filter=True, language="en")
        segs = list(segs)
    except Exception as e:
        print("captions: whisper failed:", str(e)[:200], file=sys.stderr); return []
    out = []
    for s in segs:
        for w in (s.words or []):
            t = w.word.strip()
            if t and w.start >= start_after - 0.05 and (end_before is None or w.start < end_before - 0.1):
                out.append((max(w.start, start_after), w.end if end_before is None else min(w.end, end_before), t))
    return out

def _ts(t):
    t = max(0, t); return f"{int(t//3600)}:{int(t%3600//60):02d}:{int(t%60):02d}.{int(t*100%100):02d}"

CLEAN = re.compile(r"[^\w'!?.,-]+")

def ass(ws, y=1000, fontname="DejaVu Sans", bold=1, italic=0):
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: W,{fontname},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,{bold},{italic},0,0,100,100,0,0,1,7,3,5,150,150,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev, colors = [], ["&H00FFFFFF", "&H0000E5FF"]   # white / yellow, alternating so the eye follows
    for i, (a, b, t) in enumerate(ws):
        t = CLEAN.sub("", t).upper()
        if not t: continue
        nxt = ws[i + 1][0] if i + 1 < len(ws) else b + 0.3
        end = min(max(b, a + 0.18), nxt) if nxt > a else b
        pop = r"{\fscx70\fscy70\t(0,90,\fscx108\fscy108)\t(90,160,\fscx100\fscy100)}"
        ev.append(f"Dialogue: 0,{_ts(a)},{_ts(end)},W,,0,0,0,,{{\\pos(540,{y})\\c{colors[i % 2]}}}{pop}{t}")
    return head + "\n".join(ev) + "\n"

def burn(src, dst, start_after=0.0, y=1000, end_before=None, font=None):
    """end_before: only caption words before this time (hook-first: the body, not the slow-mo replay).
    font: a .ttf (e.g. Montserrat) so captions use the same font as the rest of the video; default DejaVu Sans."""
    ws = words(src, start_after, end_before=end_before)
    if not ws:
        print("captions: no streamer speech found, no extra captions", file=sys.stderr); return False
    sub = pathlib.Path(dst).with_suffix(".ass")
    vf = f"subtitles={sub}"
    if font and pathlib.Path(font).exists():
        try:
            fam = (subprocess.run(["fc-scan", "--format", "%{family}", str(font)], capture_output=True, text=True).stdout.strip().split(",")[-1]
                   or "Montserrat Black")
        except OSError:
            fam = "Montserrat Black"
        it = 1 if "italic" in pathlib.Path(font).stem.lower() else 0
        sub.write_text(ass(ws, y, fontname=fam, bold=0 if "black" in pathlib.Path(font).stem.lower() else 1, italic=it))
        vf += f":fontsdir={pathlib.Path(font).parent}"
    else:
        sub.write_text(ass(ws, y))
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "copy",
                        "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    if r.returncode: print("captions: burn failed:", r.stderr[-400:], file=sys.stderr); return False
    print(f"captions: {len(ws)} streamer words burned in", file=sys.stderr); return True

if __name__ == "__main__":
    burn(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0)
