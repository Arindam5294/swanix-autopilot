#!/usr/bin/env python3
"""Repair the YT_COOKIES secret into a valid Netscape cookies.txt.
Pasting on a phone often turns tabs into spaces or adds Windows line endings, and yt-dlp then gets
'HTTP Error 400: Request contains an invalid argument'. Prints counts only, never cookie values."""
import os, sys
raw = os.environ.get("YT_COOKIES", "")
if not raw.strip():
    sys.exit(0)
keep = []
for line in raw.replace("\r", "").split("\n"):
    line = line.strip()
    if line.startswith("#HttpOnly_"):
        line = line[len("#HttpOnly_"):]
    if not line or line.startswith("#"):
        continue
    f = line.split("\t")
    if len(f) != 7:
        f = line.split(None, 6)
    if len(f) != 7 or not f[6].strip():
        continue
    dom = f[0]
    if not any(d in dom for d in ("youtube.com", "google.com")):
        continue
    f[1] = "TRUE" if f[0].startswith(".") else "FALSE"
    keep.append("\t".join(x.strip() for x in f))
open("cookies.txt", "w").write("# Netscape HTTP Cookie File\n" + "\n".join(keep) + "\n")
yt = [l.split("\t")[5] for l in keep if "youtube.com" in l.split("\t")[0]]
ok = any(n in yt for n in ("SAPISID", "__Secure-3PAPISID")) and any(n in yt for n in ("SID", "__Secure-3PSID"))
print(f"cookies.txt: {len(keep)} cookies, {len(yt)} for youtube.com, signed-in session: {'yes' if ok else 'NO'}")
if not ok:
    print("::warning::YT_COOKIES has no signed-in youtube.com session (SAPISID/SID missing). Re-export it.")
