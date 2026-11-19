#!/usr/bin/env python3
"""Playbook steps 4-5: the DAILY EPISODE. Pulls the day's most-viewed GTA 6 clips from Twitch (+ Kick when it answers),
ranks them by views per hour, skips cutscenes/endings/exploits and anything already used, then edits a 16:9 episode:
no intro, opens on the strongest clip, every clip gets a one-line label + the streamer's handle, and every streamer
is credited with a link (and a chapter) in the description. Thumbnail candidates -> fresh reviewer picks one.
Writes out_ep/episode.mp4 + metadata.json + thumb.jpg. Exit 0 made, 2 nothing to make."""
import datetime, json, os, pathlib, re, subprocess, sys, shutil
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
import find_clip, thumbnail
from ai import ask
CFG = json.load(open(HERE / "config.json"))
OUT = HERE / "out_ep"; WORK = pathlib.Path("/tmp/gta_ep")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SKIP = re.compile(r"cutscene|ending|final mission|spoiler|exploit|glitch|money glitch|dupe|leak|mod menu", re.I)
EP_USED = HERE / "episode_used.json"

def vph(c):
    try: t = datetime.datetime.fromisoformat(c["published"].replace("Z", "+00:00"))
    except Exception: return c["views"]
    hrs = max(1.0, (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 3600)
    return c["views"] / hrs

def target_seconds():
    m = re.search(r"long median (\d+)s", (HERE / "THUMBNAILS.md").read_text()) if (HERE / "THUMBNAILS.md").exists() else None
    s = int(m.group(1)) if m else CFG.get("episode_minutes", 8) * 60
    if os.environ.get("EP_MINUTES", "").strip():
        try: return max(180, min(int(float(os.environ["EP_MINUTES"]) * 60), 600))   # you chose 3-10 min
        except ValueError: pass
    return max(180, min(s, 600))   # 3-10 min

def sh(*a):
    r = subprocess.run(list(map(str, a)), capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr[-600:])
    return r.stdout

def esc(t): return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")

def render_part(i, raw, lab, who):
    (WORK / f"l{i}.txt").write_text(lab.upper()); (WORK / f"h{i}.txt").write_text("@" + who)
    vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,"
          f"drawbox=x=0:y=ih-190:w=iw:h=150:color=black@0.55:t=fill:enable='lt(t,5)',"
          f"drawtext=fontfile={FONT}:textfile={WORK/f'l{i}.txt'}:x=60:y=h-178:fontsize=58:fontcolor=white:enable='lt(t,5)',"
          f"drawtext=fontfile={FONT}:textfile={WORK/f'h{i}.txt'}:x=60:y=h-108:fontsize=40:fontcolor=0x9146FF:enable='lt(t,5)',"
          f"drawtext=fontfile={FONT}:textfile={WORK/f'h{i}.txt'}:x=w-tw-40:y=40:fontsize=34:fontcolor=white@0.85:borderw=3")
    p = WORK / f"p{i}.mp4"
    try:
        sh("ffmpeg", "-v", "error", "-y", "-i", raw, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex",
           f"[0:v]{vf}[v];[0:a][1:a]amix=inputs=2:duration=first:normalize=0,aresample=48000,loudnorm=I=-16[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", p)
    except Exception:   # clip without audio
        sh("ffmpeg", "-v", "error", "-y", "-i", raw, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-vf", vf,
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", p)
    return p

def fetch(c, i):
    raw = WORK / f"r{i}.mp4"
    try: sh("yt-dlp", "-q", "-f", "best[height<=1080]/best", "-o", raw, c["url"]); return raw
    except Exception as e: print("episode: download failed", c["url"], str(e)[:120], file=sys.stderr); return None

def main():
    shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True); OUT.mkdir(exist_ok=True)
    find_clip.CFG["twitch_days"] = CFG.get("episode_days", 1)
    used = set(json.loads(EP_USED.read_text())) if EP_USED.exists() else set()
    pool = find_clip.twitch_clips() + find_clip.kick_clips()
    pool = [c for c in pool if c["url"] not in used and not SKIP.search(c["title"]) and c.get("duration", 30) <= 60]
    pool.sort(key=lambda c: -vph(c))
    goal = target_seconds()
    import genre
    g = genre.name()
    got = []   # (clip, raw file, verdict or None)
    if g:
        # GENRE MODE: every clip is watched + listened to by the genre judge; only clips that really fit are used. Never padded with random clips.
        import genre_judge
        rx = genre.pattern(g)
        order = [c for c in pool if rx and rx.search(genre.text_of(c))] + [c for c in pool if not (rx and rx.search(genre.text_of(c)))]
        min_s = float(os.environ.get("EP_MIN_SCORE", os.environ.get("GENRE_MIN_SCORE", "7")))
        max_check = int(os.environ.get("EP_MAX_CHECK", "40"))
        per, total, checked = {}, 0.0, 0
        for c in order:
            if total >= goal or checked >= max_check: break
            if per.get(c["uploader"], 0) >= 2: continue
            raw = fetch(c, len(got) + checked + 1000); checked += 1
            if not raw: continue
            v = genre_judge.judge_file(raw, c, g)
            fits = bool(v) and v["score"] >= min_s and v.get("safe", True) is not False
            print(f"episode: {'FITS' if fits else 'no  '} {v['score'] if v else '?'}/10 {c['uploader']}: {(v or {}).get('what_happens', '')[:100]}", file=sys.stderr)
            if not fits: continue
            got.append((c, raw, v)); per[c["uploader"]] = per.get(c["uploader"], 0) + 1; total += v.get("dur") or c.get("duration", 25) or 25
        if len(got) < 4:
            print(f"episode: only {len(got)} clips truly fit '{g}' today (checked {checked}), no episode", file=sys.stderr)
            try:
                from notify import telegram
                telegram(f"GTA episode: only {len(got)} clips really fit '{g}' today (checked {checked}). No episode made instead of a wrong one.")
            except Exception: pass
            sys.exit(4)   # 4 = genre too strict today (already told you on Telegram)
        got.sort(key=lambda x: -x[2]["score"])   # open on the strongest clip
    else:
        picked, per, total = [], {}, 0.0
        for rnd in (1, 2):   # one clip per streamer per episode first, then fill
            for c in pool:
                if c in picked or per.get(c["uploader"], 0) >= rnd: continue
                if total >= goal: break
                picked.append(c); per[c["uploader"]] = per.get(c["uploader"], 0) + 1; total += c.get("duration", 25) or 25
        if len(picked) < 4:
            print(f"episode: only {len(picked)} usable clips today, no episode", file=sys.stderr); sys.exit(2)
        for i, c in enumerate(picked):
            raw = fetch(c, i)
            if raw: got.append((c, raw, None))
    picked = [c for c, _, _ in got]
    labels = ask("One-line labels (max 6 words, punchy, no spoilers, no emojis) for these GTA 6 stream clips. "
                 "Return ONLY a JSON list of strings in the same order.\n"
                 + "\n".join(f"- {c['uploader']}: {(v or {}).get('what_happens') or c['title']}" for c, _, v in got), min_len=10)
    try: labels = json.loads(re.search(r"\[.*\]", labels, re.S).group(0))
    except Exception: labels = [c["title"][:40] for c in picked]
    parts, chapters, beats, starts, t = [], [], [], [], 0.0
    for i, (c, raw, v) in enumerate(got):
        lab = (labels[i] if i < len(labels) else c["title"])[:60]
        try: p = render_part(i, raw, lab, c["uploader"])
        except Exception as e: print("episode: render failed", c["url"], str(e)[:120], file=sys.stderr); continue
        d = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p))
        chapters.append(f"{int(t)//60}:{int(t)%60:02d} {lab} - {c['uploader']} {c['url']}"); starts.append(round(t, 2))
        for b in (v or {}).get("beats", []):   # the judge's beats (seconds in this clip) -> seconds in the episode, for the sound effects
            if 0 <= b["t"] < d - 0.3: beats.append({**b, "t": round(t + b["t"], 2)})
        t += d; parts.append(p); used.add(c["url"])
    if len(parts) < 4: print("episode: too few clips downloaded", file=sys.stderr); sys.exit(2)
    (OUT / "marks.json").write_text(json.dumps({"marks": starts, "beats": beats}))   # clip starts + judged beats, for brand.py sound effects
    (WORK / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    sh("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", WORK / "list.txt", "-c", "copy", "-movflags", "+faststart", OUT / "episode.mp4")
    if not chapters[0].startswith("0:00"): chapters[0] = "0:00" + chapters[0].split(" ", 1)[1]
    pat = (HERE / "THUMBNAILS.md").read_text()[:2500] if (HERE / "THUMBNAILS.md").exists() else ""
    names = ", ".join(dict.fromkeys(c["uploader"] for c in picked))
    gl = ""
    try:
        import genre; gl = genre.prompt_line()
    except Exception: pass
    copy = ask(gl + f"""Write YouTube copy for a daily GTA 6 best-stream-moments compilation. Streamers: {names}. Clip labels: {labels}.
Patterns that win on GTA channels:\n{pat}\nReturn ONLY JSON: {{"title": "<70 chars, contains GTA 6, no fake promises>",
"thumb_words": ["2-3 WORDS", "2-3 WORDS", "2-3 WORDS"], "intro": "2 sentences", "tags": ["15 tags"]}}""", min_len=50)
    try: j = json.loads(re.search(r"\{.*\}", copy or "", re.S).group(0))
    except Exception: j = {}
    if not str(j.get("title", "")).strip(): j["title"] = "GTA 6: The Best Stream Moments Today (" + ", ".join(names.split(", ")[:3]) + ")"
    desc = (j.get("intro", "") + "\n\nEvery clip, with credit (go follow them):\n" + "\n".join(chapters)
            + "\n\nFan channel. Not affiliated with Rockstar Games. Creators: want a clip removed? Comment and it's gone same day."
            + "\n#GTA6 #GTAVI #GTA6Gameplay")
    meta = {"title": j["title"][:100], "description": desc[:4900], "tags": j.get("tags", [])[:20], "source_url": "episode:" + datetime.date.today().isoformat(),
            "seconds": round(t, 1), "clips": [c["url"] for c in picked]}
    (OUT / "metadata.json").write_text(json.dumps(meta, indent=1))
    ths = thumbnail.make(OUT / "episode.mp4", j.get("thumb_words") or ["BEST CLIPS"], OUT)
    shutil.copy(thumbnail.pick(ths, meta["title"]), OUT / "thumb.jpg")
    EP_USED.write_text(json.dumps(sorted(used)[-2000:]))
    print(f"episode: {len(parts)} clips, {t/60:.1f} min -> {OUT/'episode.mp4'}")

if __name__ == "__main__":
    main()
