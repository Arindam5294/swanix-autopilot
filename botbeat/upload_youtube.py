#!/usr/bin/env python3
"""Get out/short.mp4 onto YouTube, trying every route in order:
  1. YouTube API, public upload, then verify it really is public (not locked private).
     If YouTube locked it: delete the locked copy, remember the API is locked (retried weekly).
  2. YouTube API scheduled publish (private + publishAt), a different path some unaudited apps get through.
  3. Headless browser upload through YouTube Studio using your YT_COOKIES (if set).
Exit 0 = uploaded (link in out/youtube_link.txt). Exit 1 = every route failed (workflow sends the file on Telegram).
Writes out/upload_notes.txt with what happened, so Telegram tells you."""
import datetime, json, os, pathlib, sys, time, urllib.error, urllib.parse, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
STATE_F = HERE / "upload_state.json"
state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
notes = []
m = json.load(open(OUT / "metadata.json"))
VIDEO = OUT / "short.mp4"
now = datetime.datetime.now(datetime.timezone.utc)

def save_state(): STATE_F.write_text(json.dumps(state, indent=1))
def note(s): print(s, file=sys.stderr); notes.append(s)

def req(url, data=None, headers=None, method=None):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(r, timeout=900) as resp:
        raw = resp.read()
        return resp.status, dict(resp.headers), raw

def done(link, route):
    (OUT / "youtube_link.txt").write_text(link)
    (OUT / "upload_route.txt").write_text(route)
    (OUT / "upload_notes.txt").write_text("\n".join(notes))
    state["last_ok_route"] = route; save_state()
    print(link); sys.exit(0)

# ---------- API routes ----------
cid, csec, rtok = (os.environ.get(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))

def access_token():
    try:
        _, _, b = req("https://oauth2.googleapis.com/token", urllib.parse.urlencode(
            {"client_id": cid, "client_secret": csec, "refresh_token": rtok, "grant_type": "refresh_token"}).encode())
        return json.loads(b)["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        if "invalid_grant" in body:
            note("YouTube login expired or was revoked. Rerun: python botbeat/get_youtube_token.py and update YT_REFRESH_TOKEN "
                 "(also check the consent screen says 'In production').")
        else:
            note(f"YouTube token error {e.code}: {body[:200]}")
        return None

def api_upload(tok, status):
    meta = {"snippet": {"title": m["title"][:100], "description": m["description"][:4900],
                        "tags": m.get("tags", [])[:20], "categoryId": "28"},
            "status": {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": False, "embeddable": True, **status}}
    data = VIDEO.read_bytes()
    _, h, _ = req("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                  json.dumps(meta).encode(),
                  {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                   "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))})
    loc = h.get("Location") or h.get("location")
    for attempt in range(3):
        try:
            _, _, b = req(loc, data, {"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"}, "PUT")
            return json.loads(b)
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500: raise
            note(f"upload interrupted ({e}), retrying"); time.sleep(10 * (attempt + 1))
    raise RuntimeError("upload kept failing")

def video_status(tok, vid):
    _, _, b = req(f"https://www.googleapis.com/youtube/v3/videos?part=status&id={vid}", headers={"Authorization": f"Bearer {tok}"})
    items = json.loads(b).get("items", [])
    return items[0]["status"] if items else {}

def delete(tok, vid):
    try: req(f"https://www.googleapis.com/youtube/v3/videos?id={vid}", headers={"Authorization": f"Bearer {tok}"}, method="DELETE")
    except Exception as e: note(f"couldn't delete locked copy {vid}: {e}")

def api_routes():
    if not (cid and csec and rtok):
        note("API route skipped: YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN not set."); return
    lock = state.get("api_locked_until")
    if lock and now.isoformat() < lock:
        note(f"API route skipped: YouTube locked API uploads to private; retrying it after {lock[:10]}."); return
    tok = access_token()
    if not tok: return
    locked = False
    # Route 1: public
    try:
        r = api_upload(tok, {"privacyStatus": "public"}); vid = r["id"]
        time.sleep(90)
        st = video_status(tok, vid)
        if st.get("privacyStatus") == "public" and st.get("uploadStatus") not in ("rejected", "failed"):
            state.pop("api_locked_until", None); done(f"https://youtube.com/shorts/{vid}", "api-public")
        locked = True
        note(f"API public upload came back {st.get('privacyStatus')}/{st.get('uploadStatus')} (locked). Deleted it, trying scheduled publish.")
        delete(tok, vid)
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors="ignore")
        note(f"API public upload failed {e.code}: {b[:250]}")
        if "quotaExceeded" in b or "uploadLimitExceeded" in b: return
    except Exception as e:
        note(f"API public upload failed: {e}")
    # Route 2: scheduled publish
    try:
        when = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        r = api_upload(tok, {"privacyStatus": "private", "publishAt": when}); vid = r["id"]
        time.sleep(30)
        st = video_status(tok, vid)
        if st.get("publishAt") and st.get("uploadStatus") not in ("rejected", "failed"):
            state["pending_check"] = {"id": vid, "publishAt": when}
            done(f"https://youtube.com/shorts/{vid}", "api-scheduled")
        note(f"Scheduled upload lost its publish time ({st}). Deleted it.")
        delete(tok, vid)
        if locked:
            state["api_locked_until"] = (now + datetime.timedelta(days=7)).isoformat(); save_state()
    except Exception as e:
        note(f"API scheduled upload failed: {e}")

def check_previous_scheduled():
    """If last run's scheduled Short never went public, the scheduled route is locked too."""
    p = state.get("pending_check")
    if not p or not (cid and csec and rtok) or now.strftime("%Y-%m-%dT%H:%M:%S.000Z") < p["publishAt"]: return
    tok = access_token()
    if not tok: return
    try:
        st = video_status(tok, p["id"])
        if st and st.get("privacyStatus") != "public":
            note(f"Last scheduled Short ({p['id']}) never went public: YouTube locked it. Switching to the browser route for a week.")
            state["api_locked_until"] = (now + datetime.timedelta(days=7)).isoformat()
            state.setdefault("locked_videos", []).append(p["id"])
    except Exception as e:
        note(f"couldn't check last Short: {e}")
    state.pop("pending_check", None); save_state()

# ---------- Browser route ----------
def load_cookies(path):
    out = []
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line.startswith("#HttpOnly_"): line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"): continue
        p = line.split("\t")
        if len(p) < 7: continue
        dom, _, path_, secure, exp, name, val = p[:7]
        if not any(d in dom for d in ("youtube.com", "google.com")): continue
        c = {"name": name, "value": val, "domain": dom, "path": path_, "secure": secure.upper() == "TRUE"}
        if exp.isdigit() and int(exp) > 0: c["expires"] = int(exp)
        out.append(c)
    return out

def browser_route():
    ck = HERE / "cookies.txt"
    if not ck.exists():
        note("Browser route skipped: no YT_COOKIES secret."); return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        note("Browser route skipped: Playwright not installed."); return
    shot = OUT / "upload_error.png"
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            ctx = b.new_context(locale="en-US", viewport={"width": 1400, "height": 1000},
                                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36")
            ctx.add_cookies(load_cookies(ck))
            pg = ctx.new_page(); pg.set_default_timeout(60000)
            pg.goto("https://www.youtube.com/upload?hl=en")
            if "accounts.google.com" in pg.url:
                note("Browser route: YT_COOKIES are signed out/expired. Export fresh YouTube cookies."); b.close(); return
            pg.set_input_files("input[type=file]", str(VIDEO))
            title = pg.locator("#title-textarea #textbox, ytcp-social-suggestions-textbox#title-textarea #textbox").first
            title.wait_for(); title.click(); pg.keyboard.press("Control+A"); pg.keyboard.type(m["title"][:100])
            desc = pg.locator("#description-textarea #textbox").first
            desc.click(); desc.type(m["description"][:4900])
            pg.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first.click()
            for _ in range(3):
                pg.locator("#next-button").first.click(); pg.wait_for_timeout(2500)
            pg.locator("tp-yt-paper-radio-button[name='PUBLIC']").first.click()
            link = ""
            for _ in range(60):  # wait for the link to appear (up to ~5 min)
                a = pg.locator("a.ytcp-video-info, span.video-url-fadeable a").first
                if a.count() and (a.get_attribute("href") or "").startswith("http"):
                    link = a.get_attribute("href"); break
                pg.wait_for_timeout(5000)
            pg.locator("#done-button").first.click()
            pg.wait_for_timeout(15000)  # let the publish dialog finish
            b.close()
        if link:
            vid = link.rstrip("/").split("/")[-1].split("=")[-1]
            done(f"https://youtube.com/shorts/{vid}", "browser")
        note("Browser route: published but couldn't read the link; check YouTube Studio.")
        done("https://studio.youtube.com", "browser-nolink")
    except SystemExit:
        raise
    except Exception as e:
        note(f"Browser route failed: {str(e)[:250]}")
        try: pg.screenshot(path=str(shot))
        except Exception: pass

check_previous_scheduled()
api_routes()
browser_route()
(OUT / "upload_notes.txt").write_text("\n".join(notes))
print("All upload routes failed:\n" + "\n".join(notes), file=sys.stderr)
sys.exit(1)
