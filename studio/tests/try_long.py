"""Manual end-to-end check of /book or /mock without Telegram (real AI calls, local render).
python studio/tests/try_long.py book "GATE Agricultural Engineering: 2 chapters"
python studio/tests/try_long.py mock "SSC CGL Tier 1, 20 questions"
Optional third arg: an edit instruction applied afterwards (tests reply-to-edit)."""
import json, pathlib, shutil, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import longjobs, llm, render  # noqa: E402

kind, text = sys.argv[1], sys.argv[2]
d = longjobs.book_new(text) if kind == "book" else longjobs.mock_new(text)
ping = lambda m: print("PING:", m)
t = time.time()
while not longjobs.step(d, ping):
    pass
print("files:", d["files"], f"{time.time()-t:.0f}s")
first = d.pop("files")
json.dump(d, open(render.WORK / f"{kind}_state.json", "w"))
if len(sys.argv) > 3:
    longjobs.plan_edit(d, sys.argv[3])
    print("ops:", d["ops"])
    while not longjobs.step(d, ping):
        pass
    print("edited files:", d["files"])
