#!/usr/bin/env python3
"""Send the finished Short + copy-paste metadata to Telegram, and (mark_used.py marks it used)."""
import json, os, pathlib, subprocess
HERE = pathlib.Path(__file__).resolve().parent
m = json.load(open(HERE / "out" / "metadata.json"))
tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")

def ig_caption(m):
    """Instagram Reels caption: from the script if it wrote one, else built from title/description/tags."""
    c = (m.get("ig_caption") or "").strip()
    if len(c) > 40 and "#" in c: return c[:2150]
    title = m["title"].replace("#shorts", "").replace("#ai", "").strip()
    body = [l for l in m["description"].splitlines() if l.strip() and not l.strip().startswith("#") and "Follow @" not in l]
    credit = next((l for l in body if l.startswith("Clip:")), "")
    facts = " ".join(l for l in body if not l.startswith("Clip:"))[:220]
    base = ["#robots", "#ai", "#technology", "#tech", "#robotics", "#humanoidrobot", "#artificialintelligence",
            "#futuretech", "#innovation", "#techreels"]
    niche = ["#" + "".join(ch for ch in x.lower() if ch.isalnum()) for x in m.get("tags", [])][:8]
    tags = list(dict.fromkeys(base + [x for x in niche if len(x) > 2]))[:15]
    return (f"{title}\n\n{facts}\n\n{m.get('cta','').capitalize()} 👇\nFollow for a new robot or AI moment every day 🤖\n"
            f"{credit}\n\n" + " ".join(tags)).strip()

IG_TEXT = "📸 INSTAGRAM CAPTION (copy-paste):\n\n" + ig_caption(m)
text = (f"🎬 New Botbeat Short ready ({m['seconds']} s)\n\nTITLE:\n{m['title']}\n\nDESCRIPTION:\n{m['description']}\n\n"
        f"TAGS:\n{', '.join(m['tags'])}\n\nSource: {m['source_url']}\n"
        "Upload: YouTube app → + → Short → pick this video. Not made for kids. Altered content: No (real footage, AI voice).")
if os.environ.get("BOTBEAT_PREVIEW"):
    text = "👀 PREVIEW ONLY - NOT uploaded to YouTube.\n\n" + text
link_f = HERE / "out" / "youtube_link.txt"
notes_f = HERE / "out" / "upload_notes.txt"
upnotes = notes_f.read_text().strip() if notes_f.exists() else ""
if link_f.exists():  # already on YouTube: just send the link, no video file to your phone
    route = (HERE / "out" / "upload_route.txt").read_text().strip() if (HERE / "out" / "upload_route.txt").exists() else ""
    when = "goes public in ~15 min" if route == "api-scheduled" else "live now"
    text = f"✅ Botbeat Short uploaded ({m['seconds']} s), {when}:\n{link_f.read_text().strip()}\n\n{m['title']}\nSource: {m['source_url']}"
    if upnotes: text += f"\n\nNotes:\n{upnotes[:1200]}"
elif upnotes:
    text = f"⚠️ Auto-upload failed on every route, so here's the file to upload yourself.\n{upnotes[:1200]}\n\n" + text
if tok and chat and link_f.exists():
    subprocess.run(["curl", "-s", "--data-urlencode", f"text={text}", "-d", f"chat_id={chat}",
                    f"https://api.telegram.org/bot{tok}/sendMessage"], check=True, capture_output=True)
    subprocess.run(["curl", "-s", "-F", f"chat_id={chat}", "-F", f"video=@{HERE/'out'/'short.mp4'}", "-F", "supports_streaming=true",
                    "-F", "caption=For Instagram Reels 👇", f"https://api.telegram.org/bot{tok}/sendVideo"], capture_output=True)
    subprocess.run(["curl", "-s", "--data-urlencode", f"text={IG_TEXT}", "-d", f"chat_id={chat}",
                    f"https://api.telegram.org/bot{tok}/sendMessage"], capture_output=True)
elif tok and chat:
    subprocess.run(["curl", "-s", "-F", f"chat_id={chat}", "-F", f"video=@{HERE/'out'/'short.mp4'}", "-F", "supports_streaming=true",
                    f"https://api.telegram.org/bot{tok}/sendVideo"], check=True, capture_output=True)
    subprocess.run(["curl", "-s", "--data-urlencode", f"text={text}", "-d", f"chat_id={chat}",
                    f"https://api.telegram.org/bot{tok}/sendMessage"], check=True, capture_output=True)
    subprocess.run(["curl", "-s", "--data-urlencode", f"text={IG_TEXT}", "-d", f"chat_id={chat}",
                    f"https://api.telegram.org/bot{tok}/sendMessage"], capture_output=True)
else:
    (HERE / "out" / "upload_me.txt").write_text(text)
# (the clip was already marked used by mark_used.py before uploading)
print(text)
print("\n" + IG_TEXT)
