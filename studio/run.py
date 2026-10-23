#!/usr/bin/env python3
"""Studio: the ONLY reader of the Telegram bot's messages (getUpdates).

Every run (GitHub Actions, every ~5 min):
 1. read new messages from TELEGRAM_CHAT_ID (others ignored), save the offset in studio/offset.txt
    - just a link        -> appended to botbeat/queue.txt, reply "Queued for Botbeat"
    - /help or /start    -> usage
    - reply to an output -> edit job;  /pdf /info /video /book /mock or plain text -> new job
    each new job gets an immediate "On it…" reply and a file in studio/state/jobs/
 2. work through jobs (quick ones first) until the time budget is used; long /book and /mock jobs save
    after every step and continue on the next run
 3. outputs are remembered in studio/state/<message_id>.json (last 50) so replies can edit them
The workflow then commits studio/ and botbeat/queue.txt back to the repo."""
import json, os, pathlib, re, sys, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tg, llm  # noqa: E402

ROOT = HERE.parent
STATE = pathlib.Path(os.environ.get("STUDIO_STATE", HERE / "state"))
JOBS = STATE / "jobs"
OFFSET = pathlib.Path(os.environ.get("STUDIO_OFFSET", HERE / "offset.txt"))
QUEUE = pathlib.Path(os.environ.get("STUDIO_QUEUE", ROOT / "botbeat" / "queue.txt"))
KEEP = 50
LONG = ("book", "mock")
URL = re.compile(r"^https?://\S+$")
HELP = """Studio, how to use:
• Send any request, e.g. "water cycle for class 8": I pick infographic or PDF.
• /info <topic>: infographic image (add "story" for 1080x1920)
• /pdf <topic>: polished multi-page PDF
• /video <topic>: narrated vertical video (30-60 s)
• /mock <exam and post>: full mock test with answer key, e.g. /mock 3 SSC CGL for 3 mocks
• /book <exam or topic>: study book, chapter by chapter, with practice MCQs
• Reply to anything I sent with a change ("make it darker", "add a slide on costs", "make chapter 4 deeper").
• Send just a link to queue it for Botbeat.\n• Or tap a button below, then send the topic.
Replies take about 5-15 min (GitHub runs this every few minutes). Books and mocks take longer; you get progress pings."""

BUTTONS = {"📄 PDF": "pdf", "🖼 Infographic": "info", "🎬 Video": "video", "📝 Mock test": "mock", "📚 Book": "book", "❓ Help": "help"}
ASK = {"pdf": "📄 PDF: what topic? e.g. solar panels explained", "info": "🖼 Infographic: what topic? Add \"story\" for a 1080x1920 one.",
       "video": "🎬 Video: what topic? e.g. how drip irrigation works", "mock": "📝 Mock test: which exam and post? e.g. 3 SSC CGL Tier 1",
       "book": "📚 Book: which exam or topic? e.g. GATE Agricultural Engineering"}
KEYBOARD = {"keyboard": [[{"text": "📄 PDF"}, {"text": "🖼 Infographic"}], [{"text": "🎬 Video"}, {"text": "📝 Mock test"}],
                         [{"text": "📚 Book"}, {"text": "❓ Help"}]], "resize_keyboard": True, "is_persistent": True}
MENU = [{"command": c, "description": d} for c, d in [("pdf", "Polished PDF on a topic"), ("info", "Infographic image"),
        ("video", "Narrated vertical video"), ("mock", "Full mock test with answers"), ("book", "Study book with MCQs"), ("help", "How to use Studio")]]


def send_kb(chat, text, mid=None):
    """Send with the button keyboard attached."""
    return tg.call("sendMessage", {"chat_id": str(chat), "text": text, "reply_markup": KEYBOARD, "reply_parameters": tg._reply(mid)})


def now():
    return time.time()


def chat_id():
    return str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()


# ---------------- state helpers ----------------
def jload(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def jsave(p, obj):
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    tmp.replace(p)


def read_offset():
    for p in (OFFSET, ROOT / "botbeat" / "tg_offset.txt"):   # first run: continue where Botbeat stopped
        try:
            return int(p.read_text().strip())
        except Exception:
            continue
    return 0


def output_state(msg_id):
    d = jload(STATE / f"{msg_id}.json")
    if d and d.get("alias"):
        d = jload(STATE / f"{d['alias']}.json")
    return d


def save_output(msg_ids, rec):
    rec["created"] = now()
    main = msg_ids[0]
    jsave(STATE / f"{main}.json", rec)
    for m in msg_ids[1:]:
        jsave(STATE / f"{m}.json", {"alias": main, "created": rec["created"]})
    prune()


def prune():
    files = []
    for p in STATE.glob("*.json"):
        d = jload(p, {}) or {}
        files.append((d.get("created", 0), p))
    files.sort(reverse=True)
    for _, p in files[KEEP:]:
        p.unlink(missing_ok=True)


# ---------------- routing ----------------
def parse_command(text):
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text.strip(), re.S)
    if not m:
        return None, text.strip()
    return m.group(1).lower(), m.group(2).strip()


def route(update):
    """Turn one Telegram update into actions. Returns a short label (used by tests)."""
    m = update.get("message") or {}
    chat = str((m.get("chat") or {}).get("id", ""))
    if not m or chat != chat_id():
        return "ignored"
    text = (m.get("text") or m.get("caption") or "").strip()
    mid = m.get("message_id")
    if not text:
        tg.send(chat, "Send me text: a topic, a command like /pdf, or a link.", mid)
        return "empty"
    if text in BUTTONS:
        k = BUTTONS[text]
        if k == "help":
            send_kb(chat, HELP, mid)
            return "help"
        jsave(STATE / "pending.json", {"kind": k, "t": now()})
        send_kb(chat, ASK[k], mid)
        return "await-topic"
    toks = text.split()
    if all(URL.match(t) for t in toks):
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        existing = QUEUE.read_text() if QUEUE.exists() else ""
        with open(QUEUE, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(toks) + "\n")
        tg.send(chat, "Queued for Botbeat", mid)
        return "queued"
    cmd, arg = parse_command(text)
    if cmd in ("help", "start"):
        try:
            tg.call("setMyCommands", {"commands": MENU})
        except Exception:
            pass
        send_kb(chat, HELP, mid)
        return "help"
    rep = m.get("reply_to_message") or {}
    if rep and (rep.get("from") or {}).get("is_bot") and cmd is None:
        src = output_state(rep.get("message_id"))
        if src:
            return new_job(chat, mid, src["type"], text, edit={"target": rep["message_id"], "instruction": text})
        if any(k in rep for k in ("document", "photo", "video", "animation")):
            tg.send(chat, "That file is too old for me to edit (I keep the last 50). Send a fresh request.", mid)
            return "too-old"
    if cmd in ("pdf", "info", "infographic", "video", "book", "mock"):
        kind = "info" if cmd == "infographic" else cmd
        if not arg:
            tg.send(chat, f"Add what you want after /{cmd}, e.g. /{cmd} " +
                    {"mock": "SSC CGL Tier 1", "book": "GATE Agricultural Engineering"}.get(kind, "solar panels explained"), mid)
            return "usage"
        return new_job(chat, mid, kind, arg)
    if cmd is not None:
        tg.send(chat, "Unknown command. Send /help for the list.", mid)
        return "unknown"
    pend = jload(STATE / "pending.json")
    if pend and now() - pend.get("t", 0) < 86400:
        (STATE / "pending.json").unlink(missing_ok=True)
        return new_job(chat, mid, pend["kind"], text)
    return new_job(chat, mid, "auto", text)


def new_job(chat, mid, kind, prompt, edit=None):
    job = {"id": str(mid), "chat": chat, "msg": mid, "type": kind, "prompt": prompt, "created": now(),
           "fails": 0, "edit": edit}
    if kind in LONG:
        msg = ("On it… applying your edit; I'll ping progress." if edit else
               "On it… this is a big one: I'll send progress pings and the PDF when it's finished.")
    else:
        msg = "On it… applying your change." if edit else "On it…"
    tg.send(chat, msg, mid)
    jsave(JOBS / f"{job['id']}.json", job)
    return f"job:{kind}" + (":edit" if edit else "")


# ---------------- job execution ----------------
def friendly(e):
    s = str(e)
    if isinstance(e, llm.AIError) or "providers" in s or "HTTP 429" in s:
        return "the free AI models are busy or out of quota right now"
    if "cut off" in s or "incomplete" in s:
        return "the AI's answer kept getting cut off"
    if "chrome" in s.lower() or "render" in s.lower():
        return "the page could not be rendered"
    if "ffmpeg" in s or "speech" in s:
        return "the video could not be assembled"
    return "something unexpected went wrong"


def deliver(job, res, kind, source_extra):
    chat, mid = job["chat"], job["msg"]
    ids = []
    for k, path, cap in res["files"]:
        ids.append(tg.send_file(chat, k, path, cap, reply_to=mid))
    rec = {"type": kind, "prompt": source_extra.pop("original", job["prompt"]), **source_extra}
    save_output(ids, rec)
    return ids


def run_quick(job, deadline):
    import quick
    kind, edit = job["type"], job.get("edit")
    src = output_state(edit["target"]) if edit else None
    if edit and not src:
        raise RuntimeError("the original is no longer stored")
    original = src["prompt"] if src else job["prompt"]
    if kind == "auto":
        kind = quick.classify(job["prompt"])
        job["type"] = kind
    if kind in LONG:
        return False
    if kind == "info":
        res = quick.make_info(job["prompt"], html=src and src["source"]["html"],
                              size=src and tuple(src["source"].get("size") or (1080, 1350)),
                              instruction=edit and edit["instruction"], original=original,
                              hero=src and src["source"].get("hero"))
    elif kind == "pdf":
        res = quick.make_pdf(job["prompt"], html=src and src["source"]["html"],
                             instruction=edit and edit["instruction"], original=original)
    elif kind == "video":
        import video
        res = video.make_video(original, old=src and src["source"]["script"],
                               instruction=edit and edit["instruction"], deadline=deadline)
    else:
        raise RuntimeError(f"unknown type {kind}")
    edits = (src.get("edits") if src else None) or []
    deliver(job, res, kind, {"original": original, "source": res["source"],
                             "edits": edits + ([edit["instruction"]] if edit else [])})
    return True


def run_long(job, deadline, path):
    """One unit of a long job (setup, one chapter/section, or the final render). True when delivered."""
    import longjobs
    chat, mid = job["chat"], job["msg"]

    def ping(text):
        try:
            tg.send(chat, text, mid)
        except Exception as e:
            print("ping failed:", e)
    if "data" not in job:
        edit = job.get("edit")
        if edit:
            src = output_state(edit["target"])
            if not src:
                raise RuntimeError("the original is no longer stored")
            job["data"] = src["source"]
            job["original"] = src["prompt"]
            longjobs.plan_edit(job["data"], edit["instruction"])
            ping(f"Edit plan: {', '.join(o.get('op','?').replace('_', ' ') for o in job['data']['ops'])}")
        else:
            job["data"] = longjobs.book_new(job["prompt"]) if job["type"] == "book" else longjobs.mock_new(job["prompt"])
            job["original"] = job["prompt"]
        jsave(path, job)
        return False
    d = job["data"]
    finished = longjobs.step(d, ping)
    job["fails"] = 0
    jsave(path, job)
    if not finished:
        return False
    res = {"files": [(k, pathlib.Path(p), c) for k, p, c in d.pop("files")]}
    k0, p0, c0 = res["files"][0]
    res["files"][0] = (k0, p0, (c0 + " · " if c0 else "") + f"{longjobs.describe(d)}. Reply to this file to edit it.")
    deliver(job, res, job["type"], {"original": job.get("original", job["prompt"]), "source": d,
                                    "edits": d.get("edits") or []})
    return True


def poll():
    """Read new Telegram messages and route them. Safe to call often (between long-job steps)."""
    off = read_offset()
    try:
        ups = tg.get_updates(off)
    except Exception as e:
        print("could not read Telegram:", e); return
    for u in ups:
        off = max(off, u["update_id"] + 1)
        try:
            print("update", u["update_id"], "->", route(u))
        except Exception as e:
            print("routing error:", e)
        OFFSET.write_text(str(off))   # saved after each message so a crash never re-runs handled ones


def pending():
    jobs = []
    for p in JOBS.glob("*.json"):
        j = jload(p)
        if j and j.get("retry_after", 0) <= now():
            jobs.append((p, j))
    jobs.sort(key=lambda x: (x[1]["type"] in LONG, x[1].get("created", 0)))   # quick asks first
    return jobs


def fail(p, j, e):
    traceback.print_exc()
    j["fails"] = j.get("fails", 0) + 1
    long_ = j["type"] in LONG
    if not long_ and j["fails"] == 1 and isinstance(e, llm.AIError):
        j["retry_after"] = now() + 5 * 60      # free models busy: one quiet retry on a later run
        jsave(p, j)
    elif long_ and j["fails"] < 4:
        j["retry_after"] = now() + 15 * 60
        jsave(p, j)
        if j["fails"] == 1:
            tg.send(j["chat"], f"Paused: {friendly(e)}. I'll retry automatically in about 15 minutes "
                               "(progress so far is saved).", j["msg"])
    else:
        p.unlink(missing_ok=True)
        try:
            tg.send(j["chat"], f"Sorry, that failed: {friendly(e)}. Send it again in a while, or try a "
                               "shorter request.", j["msg"])
        except Exception:
            pass


def main():
    t0 = now()
    deadline = t0 + float(os.environ.get("STUDIO_BUDGET_MIN", "24")) * 60
    llm.DEADLINE[0] = deadline
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not chat_id():
        print("Telegram secrets missing; nothing to do."); return
    STATE.mkdir(parents=True, exist_ok=True); JOBS.mkdir(parents=True, exist_ok=True)
    while True:
        poll()                                   # new messages get "On it…" even while a book is being written
        jobs = pending()
        if not jobs:
            break
        p, j = jobs[0]
        left = deadline - now()
        if left < (300 if j["type"] in LONG else 420):
            print("time budget used; remaining jobs continue next run"); break
        try:
            if j["type"] not in LONG:
                if run_quick(j, deadline):
                    p.unlink(missing_ok=True)
                else:
                    jsave(p, j)
            elif run_long(j, deadline, p):
                p.unlink(missing_ok=True)
        except Exception as e:
            fail(p, j, e)
    print(f"done in {now()-t0:.0f}s")


if __name__ == "__main__":
    main()
