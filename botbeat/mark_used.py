#!/usr/bin/env python3
"""Lock the clip as used BEFORE uploading, and push that right away.
Exit 0 = locked, go upload. Exit 4 = this video was already used (by another/older run): do NOT upload.
Stops duplicates from re-runs, cancelled runs and two runs overlapping."""
import json, pathlib, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from find_clip import vid_id, load_used
m = json.load(open(HERE / "out" / "metadata.json"))
src = m["source_url"]
if vid_id(src) in load_used():
    print(f"mark_used: {src} was already posted by another run, skipping upload"); sys.exit(4)
f = HERE / "used.json"
used = json.loads(f.read_text()) if f.exists() else []
used.append(src); f.write_text(json.dumps(used))
q = HERE / "queue.txt"
if q.exists():
    q.write_text("".join(l + "\n" for l in q.read_text().splitlines()
                         if l.strip() and vid_id(l.split()[0]) != vid_id(src)))
r = subprocess.run([sys.executable, str(HERE.parent / "common" / "gitsave.py"), "botbeat: lock clip " + vid_id(src),
                    "--union", "botbeat/used.json", "--lines", "botbeat/queue.txt"], cwd=HERE.parent)
sys.exit(0 if r.returncode == 0 else 1)
