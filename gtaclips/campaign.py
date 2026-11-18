#!/usr/bin/env python3
"""Playbook step 9: clipping campaigns (Content Rewards / Whop and similar). Streamers and brands PAY per 1,000 views
for clips of them, posted on your own TikTok / Instagram / YouTube, IF you follow their brief and submit the link
(Content Rewards: within 30 minutes of posting). There is no public submit API, so the bot does everything up to
the submit button and pings you on Telegram with the links + where to submit.

campaigns.json (you fill it from the campaign pages you joined):
[{"name": "Kai Cenat GTA 6", "active": true, "match": ["kai cenat", "kaicenat"],
  "platforms": ["tiktok", "instagram", "youtube"], "cpm": 1.0, "max_per_clip": 200,
  "submit_url": "https://contentrewards.com/...", "must_include": "#ad @KaiCenat", "hashtags": ["#KaiCenat"],
  "footage": "twitch.tv/kaicenat streams only", "notes": "no reuploads, own edit, keep it 15-60s"}]

python campaign.py notify   -> after posting: Telegram 'submit now' message, appends to STATUS.md."""
import json, pathlib, sys, os, datetime
HERE = pathlib.Path(__file__).resolve().parent
F = HERE / "campaigns.json"

def load():
    try: return [c for c in json.loads(F.read_text()) if c.get("active", True)]
    except Exception: return []

def match(text):
    t = (text or "").lower()
    for c in load():
        if any(k.lower() in t for k in c.get("match", [])): return c
    return None

def boost_names():
    return [k.lower() for c in load() for k in c.get("match", [])]

def apply(s, clip):
    """Add the campaign's required credit / disclosure / tags to the post copy."""
    c = match(" ".join(str(clip.get(k, "")) for k in ("uploader", "title", "url")))
    if not c: return s
    s["campaign"] = {k: c.get(k) for k in ("name", "submit_url", "platforms", "cpm", "max_per_clip", "must_include", "hashtags")}
    extra = " ".join(x for x in [c.get("must_include", ""), " ".join(c.get("hashtags", []))] if x).strip()
    if extra:
        s["description"] = s["description"].rstrip() + "\n" + extra
        s["social_extra"] = extra
    print(f"campaign: clip matches '{c['name']}'", file=sys.stderr)
    return s

def notify():
    sys.path.insert(0, str(HERE.parent / "common"))
    from notify import telegram
    out = HERE / "out"; m = json.loads((out / "metadata.json").read_text())
    links = {}
    if (out / "youtube_link.txt").exists(): links["youtube"] = (out / "youtube_link.txt").read_text().strip()
    if (out / "crosspost.json").exists():
        links.update({k: v for k, v in json.loads((out / "crosspost.json").read_text()).get("links", {}).items() if v})
    status_line(m, links)
    c = m.get("campaign")
    if not c: return
    body = "\n".join(f"• {p}: {u}" for p, u in links.items()) or "• (no public links came back, open the apps and copy them)"
    telegram(f"💰 SUBMIT NOW (within 30 min) to campaign: {c['name']}\n{c.get('submit_url','')}\n\nPost links:\n{body}\n\n"
             f"Pays ${c.get('cpm','?')} per 1,000 views, max ${c.get('max_per_clip','?')} per clip. Don't edit or delete the post after submitting.")

def status_line(m, links):
    f = HERE / "STATUS.md"
    if not f.exists(): f.write_text("# GTA VI Clips status\nEvery post, newest last.\n\n| date (UTC) | title | campaign | links |\n|---|---|---|---|\n")
    d = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    camp = (m.get("campaign") or {}).get("name", "")
    with f.open("a") as fh:
        fh.write(f"| {d} | {m.get('title','')[:60].replace('|','/')} | {camp} | {' '.join(links.values())} |\n")

if __name__ == "__main__":
    if sys.argv[1:] == ["notify"]: notify()
    else: print(json.dumps(load(), indent=1))
