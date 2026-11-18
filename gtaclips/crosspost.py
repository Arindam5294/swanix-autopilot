#!/usr/bin/env python3
"""Playbook step 6/9: post the same vertical natively to TikTok and Instagram Reels through upload-post.com
(they run approved TikTok/Meta apps, so no developer review for you).
Secrets: UPLOAD_POST_API_KEY, UPLOAD_POST_USER (the profile name you connected the accounts to).
Free plan: 10 uploads a month, Instagram only. TikTok needs their paid plan (the API answers 403 on free).
Writes out/crosspost.json. Never fails the workflow."""
import json, os, pathlib, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
CFG = json.load(open(HERE / "config.json"))

def caption(m):
    t = m["title"].replace("#shorts", "").replace("#Shorts", "").strip()
    credit = next((l for l in m["description"].splitlines() if "🎥" in l), "")
    tags = " ".join(w for w in m["description"].split() if w.startswith("#") and w.lower() != "#shorts")[:300]
    extra = m.get("social_extra", "")
    return "\n".join(x for x in [t, credit, extra, tags] if x)[:2100]

def main():
    key, user = os.environ.get("UPLOAD_POST_API_KEY"), os.environ.get("UPLOAD_POST_USER")
    plats = CFG.get("crosspost_platforms", ["instagram", "tiktok"])
    m = json.loads((OUT / "metadata.json").read_text())
    camp = m.get("campaign")
    if camp and camp.get("platforms"): plats = [p for p in plats if p in camp["platforms"]] or plats
    res = {"platforms": plats, "links": {}, "notes": []}
    if not (key and user):
        res["notes"].append("TikTok/Instagram skipped: add UPLOAD_POST_API_KEY and UPLOAD_POST_USER secrets.")
    else:
        cap = caption(m)
        for p in plats:   # one call per platform, so a TikTok 403 on the free plan doesn't block Instagram
            cmd = ["curl", "-s", "-m", "600", "-X", "POST", "https://api.upload-post.com/api/upload",
                   "-H", f"Authorization: Apikey {key}", "-H", f"Idempotency-Key: {m['source_url'][-40:]}-{p}",
                   "-F", f"user={user}", "-F", f"platform[]={p}", "-F", f"video=@{OUT/'short.mp4'}", "-F", f"title={cap}"]
            if p == "tiktok": cmd += ["-F", "privacy_level=PUBLIC_TO_EVERYONE"]
            if p == "instagram": cmd += ["-F", "media_type=REELS", "-F", "share_to_feed=true"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            try: j = json.loads(r.stdout or "{}")
            except Exception: j = {"raw": r.stdout[:300]}
            url = ""
            for v in (j.get("results") or {}).values() if isinstance(j.get("results"), dict) else []:
                if isinstance(v, dict): url = v.get("url") or v.get("post_url") or url
            res["links"][p] = url
            ok = j.get("success") is not False and "error" not in json.dumps(j).lower()[:200]
            if not ok:
                msg = json.dumps(j)[:250]
                if p == "tiktok" and ("403" in msg or "paid" in msg.lower()): msg = "TikTok needs upload-post's paid plan."
                res["notes"].append(f"{p}: failed: {msg}")
            else:
                res["notes"].append(f"{p}: posted{(' ' + url) if url else ' (link appears in the app in a minute)'}")
    (OUT / "crosspost.json").write_text(json.dumps(res, indent=1))
    n = OUT / "upload_notes.txt"
    n.write_text(((n.read_text() + "\n") if n.exists() else "") + "\n".join(res["notes"]))
    print("\n".join(res["notes"]))

if __name__ == "__main__":
    try: main()
    except Exception as e: print("crosspost failed:", e, file=sys.stderr)
