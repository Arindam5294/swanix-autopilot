"""Offline tests for Studio's message routing. No Telegram or AI calls.
Run: python studio/tests/test_routing.py"""
import json, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"STUDIO_FAKE_TG": "1", "TELEGRAM_CHAT_ID": "111", "TELEGRAM_BOT_TOKEN": "fake:token",
                   "STUDIO_STATE": str(tmp / "state"), "STUDIO_OFFSET": str(tmp / "offset.txt"),
                   "STUDIO_QUEUE": str(tmp / "queue.txt")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import run, tg  # noqa: E402

uid = [100]


def upd(text, chat=111, reply=None, mid=None):
    uid[0] += 1
    m = {"message_id": mid or uid[0], "chat": {"id": chat}, "from": {"id": chat, "is_bot": False}, "text": text}
    if reply:
        m["reply_to_message"] = reply
    return {"update_id": uid[0], "message": m}


def once(*ups):
    box = [list(ups)]
    def f(off):
        out, box[0] = [u for u in box[0] if u["update_id"] >= off], []
        return out
    return f


def last():
    return tg.SENT[-1]


def jobs():
    return {p.stem: json.loads(p.read_text()) for p in run.JOBS.glob("*.json")}


ok = 0


def check(cond, name):
    global ok
    assert cond, name
    ok += 1
    print("PASS", name)


# 1. link -> Botbeat queue
r = run.route(upd("https://x.com/someone/status/123"))
check(r == "queued", "link is queued")
check(run.QUEUE.read_text().strip() == "https://x.com/someone/status/123", "queue.txt has the link")
check(last()["data"]["text"] == "Queued for Botbeat", "reply says Queued for Botbeat")
r = run.route(upd("https://youtu.be/a  https://youtu.be/b"))
check(r == "queued" and run.QUEUE.read_text().count("\n") == 3, "two links queued")
# a sentence containing a link is a prompt, not a queue item
r = run.route(upd("make an infographic from https://example.com/report"))
check(r == "job:auto", "text with a link is a normal request")

# 2. other chats are ignored silently
n = len(tg.SENT)
check(run.route(upd("/pdf hack", chat=999)) == "ignored", "other chat ignored")
check(run.route(upd("https://evil.example", chat=999)) == "ignored", "other chat link ignored")
check(len(tg.SENT) == n, "nothing sent to other chats")

# 3. commands -> jobs with an immediate 'On it' reply (as a reply to the user's message)
r = run.route(upd("/pdf soil testing guide", mid=5001))
check(r == "job:pdf" and jobs()["5001"]["prompt"] == "soil testing guide", "/pdf creates a pdf job")
check(last()["data"]["text"].startswith("On it") and last()["data"]["reply_parameters"]["message_id"] == 5001,
      "On it… sent as a reply")
check(run.route(upd("/info@MyBot drip irrigation")) == "job:info", "/info with @botname")
check(run.route(upd("/video honeybees")) == "job:video", "/video")
check(run.route(upd("/mock 3 SSC CGL Tier 1")) == "job:mock", "/mock")
check(run.route(upd("/book GATE AG")) == "job:book", "/book")
check(run.route(upd("/pdf")) == "usage", "empty command -> usage hint")
check(run.route(upd("/help")) == "help" and "/mock" in last()["data"]["text"], "/help lists commands")
check(run.route(upd("/frobnicate x")) == "unknown", "unknown command")

# 4. reply to a bot output -> edit job; reply to an unknown old file -> too-old message
run.save_output([7001, 7002], {"type": "info", "prompt": "drip vs flood", "source": {"html": "<html></html>"}})
bot = {"id": 1, "is_bot": True}
r = run.route(upd("make it darker", reply={"message_id": 7001, "from": bot, "photo": [{}]}, mid=5100))
check(r == "job:info:edit" and jobs()["5100"]["edit"]["target"] == 7001, "reply to output -> edit job")
r = run.route(upd("bigger title", reply={"message_id": 7002, "from": bot, "document": {}}))
check(r == "job:info:edit", "reply to the document copy (alias) also edits")
r = run.route(upd("change it", reply={"message_id": 4242, "from": bot, "document": {}}))
check(r == "too-old", "reply to a pruned output -> too old")
r = run.route(upd("ok thanks, now do one on rice", reply={"message_id": 4243, "from": bot, "text": "On it…"}))
check(r == "job:auto", "reply to a non-file bot message is a new request")
run.save_output([7100], {"type": "book", "prompt": "GATE AG", "source": {"kind": "book"}})
r = run.route(upd("make chapter 4 deeper", reply={"message_id": 7100, "from": bot, "document": {}}))
check(r == "job:book:edit", "reply to a book -> long edit job")

# 5. pruning keeps the newest 50 outputs
for i in range(60):
    run.save_output([8000 + i], {"type": "pdf", "prompt": str(i), "source": {}})
check(len(list(run.STATE.glob("*.json"))) == 50, "state pruned to 50")
check(run.output_state(8059) is not None and run.output_state(8000) is None, "oldest pruned, newest kept")

# 6. main() with fake updates: offset saved, routing done, jobs left for processing
tg.get_updates = once(upd("https://youtu.be/zzz"), upd("hello", chat=5))
run.JOBS.mkdir(parents=True, exist_ok=True)
for p in run.JOBS.glob("*.json"):
    p.unlink()
run.main()
check(int(run.OFFSET.read_text()) == uid[0] + 1, "offset advanced past the last update")
check("https://youtu.be/zzz" in run.QUEUE.read_text(), "main() queued the link")

# 7. job processing end to end with fake makers (no AI): deliver, remember for edits, edit, failure message
import quick, longjobs, llm  # noqa: E402
png = tmp / "x.png"; png.write_bytes(b"png")
calls = []
def fake_info(req, html=None, size=None, instruction=None, original=None):
    calls.append((req, html, instruction, original))
    return {"files": [("photo", png, ""), ("document", png, "Full-quality file")], "source": {"html": "<html>v%d</html>" % len(calls), "size": [1080, 1350]}}
quick.make_info = fake_info
quick.classify = lambda t: "info"
for p in run.JOBS.glob("*.json"):
    p.unlink()
tg.get_updates = once(upd("/info solar panels", mid=6001))
run.main()
sent_files = [x for x in tg.SENT if x["method"] in ("sendPhoto", "sendDocument")]
check(len(sent_files) == 2 and sent_files[0]["data"]["reply_parameters"]["message_id"] == 6001, "photo + document sent as replies")
photo_id = tg._FAKE_ID[0] - 1
st = run.output_state(photo_id)
check(st and st["type"] == "info" and st["source"]["html"] == "<html>v1</html>", "output remembered for edits")
check(not list(run.JOBS.glob("*.json")), "finished job removed")
tg.get_updates = once(upd("make it darker", reply={"message_id": photo_id, "from": bot, "photo": [{}]}, mid=6002))
run.main()
check(calls[-1][1] == "<html>v1</html>" and calls[-1][2] == "make it darker" and calls[-1][3] == "solar panels",
      "edit gets previous HTML + instruction + original prompt")
check(run.output_state(tg._FAKE_ID[0] - 1)["edits"] == ["make it darker"], "edit history stored")
def boom(*a, **k):
    raise RuntimeError("chrome crashed")
quick.make_info = boom
tg.get_updates = once(upd("/info broken", mid=6003))
run.main()
check("Sorry, that failed" in tg.SENT[-1]["data"]["text"] and "rendered" in tg.SENT[-1]["data"]["text"], "failure -> plain-English error")
# long job: resumes step by step and delivers at the end
steps = {"n": 0}
def fake_step(d, ping):
    steps["n"] += 1
    ping(f"Chapter {steps['n']}/3 done")
    if steps["n"] >= 3:
        d["files"] = [["document", str(png), ""]]
        return True
    return False
longjobs.step = fake_step
tg.get_updates = once(upd("/book tiny test", mid=6004))
run.main()
check(steps["n"] == 3 and any("Chapter 3/3" in (x["data"].get("text") or "") for x in tg.SENT), "long job pinged progress")
check(tg.SENT[-1]["method"] == "sendDocument" and "Reply to this file" in tg.SENT[-1]["data"]["caption"], "book delivered")
check(run.output_state(tg._FAKE_ID[0])["type"] == "book", "book remembered for edits")

print(f"\nall {ok} routing tests passed")
