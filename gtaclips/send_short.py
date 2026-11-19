#!/usr/bin/env python3
"""After a Short lands on YouTube, DM you a ready-to-share kit on Telegram:
1) the vertical video (for Instagram Reels)  2) the 16:9 X video  3) X caption  4) Instagram caption + hashtags.
Every caption carries the YouTube link. Each caption is its own message, so one long-press copies it.
If the upload failed, it sends the file + metadata so you can upload by hand (same as before)."""
import json, os, pathlib, re, subprocess
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
m = json.load(open(OUT / "metadata.json"))
tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
API = f"https://api.telegram.org/bot{tok}/"

def tg_text(t):
    if tok and chat:
        subprocess.run(["curl", "-s", "--data-urlencode", f"text={t[:4096]}", "-d", f"chat_id={chat}",
                        "-d", "disable_web_page_preview=true", API + "sendMessage"], capture_output=True, timeout=60)
def tg_video(path, cap):
    if tok and chat and path.exists():
        subprocess.run(["curl", "-s", "-F", f"chat_id={chat}", "-F", f"video=@{path}", "-F", "supports_streaming=true",
                        "-F", f"caption={cap[:1000]}", API + "sendVideo"], capture_output=True, timeout=300)

title = re.sub(r"\s*#shorts\b", "", m["title"], flags=re.I).strip()
who = (m.get("uploader") or "").strip()
hindi = m.get("lang") == "hi"
must = (m.get("must_include") or "").strip()          # campaign tag (e.g. "LACY #LACY"), required for payouts
desc_tags = [w for w in m.get("description", "").split() if w.startswith("#") and w.lower() != "#shorts"]
handle = "#" + re.sub(r"[^A-Za-z0-9]", "", who) if who else ""
# hook-first: share CTA line + a comment question in the Instagram caption
share = ""
if m.get("share_cta"):
    try:
        import hookfirst; share = hookfirst.cta_sentence(m["share_cta"])
    except Exception:
        share = str(m["share_cta"]).capitalize()
_q = str(m.get("cta") or "").strip()
ask = ("💬 " + _q[:1].upper() + _q[1:].lower() + " Comment below 👇") if _q.endswith("?") else ""

def uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x and x.lower() not in seen: seen.add(x.lower()); out.append(x)
    return out

def x_caption(link):
    tags = uniq(["#GTA6", "#GTAVI", handle] + (["#GTA6Hindi"] if hindi else []))
    head = title + (f"\n\n🎥 {who}" if who else "") + (f"\n{must}" if must else "")
    tail = f"\n\nFull Short 👉 {link}"
    for n in range(len(tags), -1, -1):                 # X: 280 chars, a link always counts as 23
        t = head + tail + ("\n\n" + " ".join(tags[:n]) if n else "")
        if len(t) - len(link) + 23 <= 280: return t
    return (head[:280 - 23 - len(tail) + len(link)] + tail)

def ig_caption(link):
    base = ["#GTA6", "#GTAVI", "#GTA6Gameplay", "#GrandTheftAuto6", "#ViceCity", "#Leonida", "#GTA", "#Gaming",
            "#GamingClips", "#GamingReels", "#Streamer", "#Twitch", "#ReelsInstagram", "#Viral", "#Explore"]
    if hindi: base = ["#GTA6Hindi", "#GTA6India", "#IndianGamer", "#HindiGaming"] + base
    tags = uniq([handle] + desc_tags + base)[:28]       # Instagram allows 30, keep 2 spare
    lines = [title, ""]
    if share: lines.append(share)                      # hook-first share CTA ("Send this to the friend who ...")
    if ask: lines += [ask, ""]
    elif share: lines.append("")
    if who: lines.append(f"🎥 Clip: {who} (all credit to the streamer)")
    if must: lines.append(must)
    lines += [f"▶️ Watch on YouTube: {link}", "🔔 Follow for daily GTA 6 moments" + (" | roz Hindi clips 8PM" if hindi else ""), "",
              "Fan page. Not affiliated with Rockstar Games.", "", " ".join(tags)]
    return "\n".join(lines)[:2200]

link_f = OUT / "youtube_link.txt"
notes = (OUT / "upload_notes.txt").read_text().strip() if (OUT / "upload_notes.txt").exists() else ""
if os.environ.get("BOTBEAT_PREVIEW"):
    tg_video(OUT / "short.mp4", "👀 PREVIEW ONLY - not uploaded.\n\n" + m["title"])
    print("preview sent")
elif link_f.exists():
    link = link_f.read_text().strip()
    route = (OUT / "upload_route.txt").read_text().strip() if (OUT / "upload_route.txt").exists() else ""
    when = "goes public in ~15 min" if route == "api-scheduled" else "live now"
    tg_text(f"✅ New Short on YouTube ({m.get('seconds','?')} s), {when}:\n{link}\n\n{m['title']}\nSource: {m['source_url']}"
            + (f"\n\nNotes:\n{notes[:800]}" if notes else "") + "\n\nShare kit below 👇 (long-press a message to copy)")
    tg_video(OUT / "short.mp4", "📱 INSTAGRAM REEL video (9:16). Caption is the next-but-one message.")
    tg_video(OUT / "short_x.mp4", "🐦 X video (16:9). Caption is the next message.")
    tg_text(x_caption(link))
    tg_text(ig_caption(link))
    print(x_caption(link)); print("----"); print(ig_caption(link))
elif os.environ.get("HAND_UPLOAD"):
    ch = "youtube.com/@GTAviclipxx"
    tg_text(f"🖐 HAND-UPLOAD Short ({m.get('seconds','?')} s), no API quota used. Post it from the YouTube app: + → Short → pick the video below.\nSource: {m['source_url']}"
            + (f"\n\nNotes:\n{notes[:800]}" if notes else ""))
    tg_video(OUT / "short.mp4", "📱 YouTube Short + Instagram Reel video (9:16). Title, description and tags are the next 3 messages.")
    tg_text(m["title"])
    tg_text(m["description"])
    tg_text(", ".join(m.get("tags", [])) or "#GTA6, #GTAVI, #Shorts")
    tg_video(OUT / "short_x.mp4", "🐦 X video (16:9). Captions follow.")
    tg_text(x_caption(ch)); tg_text(ig_caption(ch))
    print("hand-upload kit sent")
else:
    text = (f"⚠️ Auto-upload failed on every route, so here's the file to upload yourself.\n{notes[:1200]}\n\n"
            f"TITLE:\n{m['title']}\n\nDESCRIPTION:\n{m['description']}\n\nTAGS:\n{', '.join(m.get('tags', []))}\n\nSource: {m['source_url']}")
    tg_video(OUT / "short.mp4", "Upload this one by hand (YouTube app → + → Short).")
    tg_text(text)
    if not (tok and chat): (OUT / "upload_me.txt").write_text(text)
    print(text)
