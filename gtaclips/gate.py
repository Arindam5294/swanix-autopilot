#!/usr/bin/env python3
"""Playbook step 8: the gauntlet rule. Nothing posts until a FRESH reviewer that never saw the script or notes says yes.
1) Hard checks with ffmpeg (size, length, silence, black/frozen screen).
2) A fresh AI reviewer looks at 6 frames + the post copy (Gemini vision; text-only fallback) and answers
   {"pass": bool, "faults": [...], "better_title": "..."}.
3) If the only faults are in the copy, it takes the better title once and checks again. Visual faults = no post.
Every verdict is appended to RULES.md. Exit 0 = post it. Exit 7 = rejected (workflow tells you why).
Usage: python gate.py [video] [metadata.json]"""
import base64, json, os, pathlib, re, subprocess, sys, datetime, urllib.request
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
VIDEO = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out" / "short.mp4"
META = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "out" / "metadata.json"
RULES = HERE / "RULES.md"
VERTICAL = "short" in VIDEO.name

def probe(*a):
    return subprocess.run(["ffprobe", "-v", "error", *a, str(VIDEO)], capture_output=True, text=True).stdout.strip()

def hard_checks():
    faults = []
    try:
        w, h = map(int, probe("-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0").split(","))
        dur = float(probe("-show_entries", "format=duration", "-of", "csv=p=0"))
    except Exception as e:
        return [f"video unreadable: {e}"], 0
    if VERTICAL and (w, h) != (1080, 1920): faults.append(f"not 1080x1920 ({w}x{h})")
    if VERTICAL and not 8 <= dur <= 60: faults.append(f"length {dur:.0f}s is outside 8-60s")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(VIDEO), "-vf", "blackdetect=d=1.5:pix_th=0.08,freezedetect=n=0.002:d=4",
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    if "black_start" in r: faults.append("a black screen of 1.5s+")
    if "freeze_start" in r: faults.append("frozen picture for 4s+")
    mv = re.search(r"mean_volume: (-?[\d.]+)", r)
    if not mv or float(mv.group(1)) < -38: faults.append("audio is silent or nearly silent")
    return faults, dur

def frames(dur, n=6):
    out = []
    for i in range(n):
        t = 0.05 if i == 0 else dur * i / n          # frame 0 = what people see first
        b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(VIDEO), "-frames:v", "1", "-vf", "scale=360:-2",
                            "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True).stdout
        if b: out.append(base64.b64encode(b).decode())
    return out

BRIEF = """You are a strict, fresh reviewer for a GTA 6 fan channel. You have NOT seen the script or the editor's notes.
Look at the frames (first frame first) and the post copy. Say YES only if ALL hold:
- The first frame grabs attention (action, a face reacting, or bold text) - no blank/loading/menu screen.
- On-screen text is readable and NOT under the right-side buttons or the bottom 15% of a vertical video.
- It is real GTA 6 footage, not GTA 5, a fake, a concept, a mobile game, a leak or a 'play early' scam.
- No story spoilers: no ending, no late-game cutscene, no boss/finale reveal. No exploit or glitch tutorial.
- The title promises only what the frames show; no fake clickbait; nothing claims to be Rockstar/official.
- The description credits the original creator/streamer{campaign}.
Reply ONLY JSON: {{"pass": true|false, "faults": ["short concrete fault", ...], "visual_fault": true|false,
"better_title": "a fixed title if the title is the problem, else empty"}}"""

def review(meta, imgs):
    camp = meta.get("campaign")
    brief = BRIEF.format(campaign=(f" and includes '{camp.get('must_include')}' (paid-promotion label required by the campaign)"
                                   if camp and camp.get("must_include") else ""))
    copy = f"TITLE: {meta['title']}\nDESCRIPTION:\n{meta['description'][:1500]}"
    key = os.environ.get("GEMINI_API_KEY")
    txt = ""
    if key and imgs:
        try:
            parts = [{"text": brief + "\n\n" + copy}] + [{"inline_data": {"mime_type": "image/jpeg", "data": d}} for d in imgs]
            body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 800}}
            rq = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('GATE_MODEL','gemini-2.5-flash')}:generateContent",
                                        json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
            d = json.loads(urllib.request.urlopen(rq, timeout=120).read())
            txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        except Exception as e:
            print("gate: vision review failed, text-only:", str(e)[:200], file=sys.stderr)
    if not txt:
        from ai import ask
        txt = ask(brief + "\n(You cannot see frames this time: judge the copy only, assume visuals are fine.)\n\n" + copy, min_len=20)
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else {"pass": False, "faults": ["reviewer gave no verdict"], "visual_fault": False}

def log(meta, v, tries):
    if not RULES.exists(): RULES.write_text((HERE / "RULES.template.md").read_text() if (HERE / "RULES.template.md").exists() else "# RULES\n")
    txt = RULES.read_text()
    if "## Verdict log" not in txt: txt += "\n## Verdict log (newest last, keep 60)\n"
    head, logpart = txt.split("## Verdict log", 1)
    lines = [l for l in logpart.splitlines()[1:] if l.startswith("- ")][-59:]
    d = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    lines.append(f"- {d} {'YES' if v.get('pass') else 'NO '} ({tries} check{'s' if tries > 1 else ''}) {meta['title'][:60]!r}: "
                 + ("; ".join(v.get("faults", [])) or "clean")[:300])
    RULES.write_text(head + "## Verdict log (newest last, keep 60)\n" + "\n".join(lines) + "\n")

def main():
    meta = json.loads(META.read_text())
    faults, dur = hard_checks()
    if faults:
        v = {"pass": False, "faults": faults, "visual_fault": True}; log(meta, v, 1)
    else:
        imgs = frames(dur)
        v = review(meta, imgs); tries = 1
        if not v.get("pass") and not v.get("visual_fault") and (v.get("better_title") or "").strip():
            new = v["better_title"].strip()
            if "#shorts" in meta["title"] and "#shorts" not in new and VERTICAL: new = (new[:90] + " #shorts")
            print(f"gate: copy fault, retitling to {new!r} and checking again", file=sys.stderr)
            meta["title"] = new; META.write_text(json.dumps(meta, indent=1))
            v = review(meta, imgs); tries = 2
        log(meta, v, tries)
    (META.parent / "gate.json").write_text(json.dumps(v, indent=1))
    print("gate:", "YES" if v.get("pass") else "NO", "-", "; ".join(v.get("faults", [])))
    sys.exit(0 if v.get("pass") else 7)

if __name__ == "__main__":
    main()
