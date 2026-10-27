#!/usr/bin/env python3
"""Diagnose why the Botbeat YouTube upload auth is failing.
Run with secrets in env (as GitHub Actions does):
    python botbeat/diagnose_youtube.py
Reports exactly WHICH secret is missing/bad, without printing the secrets themselves.
"""
import os, json, urllib.error, urllib.parse, urllib.request

def fetch(url, data=None, headers=None):
    r = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="ignore")

print("\n=== Secret presence ===")
cid = os.environ.get("YT_CLIENT_ID", "").strip()
csec = os.environ.get("YT_CLIENT_SECRET", "").strip()
rtok = os.environ.get("YT_REFRESH_TOKEN", "").strip()
print("YT_CLIENT_ID      set:", bool(cid), f"(len={len(cid)})" if cid else "")
print("YT_CLIENT_SECRET  set:", bool(csec), f"(len={len(csec)})" if csec else "")
print("YT_REFRESH_TOKEN  set:", bool(rtok), f"(len={len(rtok)})" if rtok else "")

if not (cid and csec and rtok):
    print("\nRESULT: missing secret(s) above. Add them in GitHub > Settings > Secrets and variables > Actions.")
    raise SystemExit(0)

print("\n=== Refresh token exchange ===")
data = urllib.parse.urlencode({"client_id": cid, "client_secret": csec,
                               "refresh_token": rtok, "grant_type": "refresh_token"}).encode()
code, body = fetch("https://oauth2.googleapis.com/token", data)
try:
    j = json.loads(body)
except Exception:
    j = {}

if code == 200:
    print("Token exchange OK. expires_in =", j.get("expires_in"))
    tok = j.get("access_token")
    if tok:
        sc, b2 = fetch("https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
                       headers={"Authorization": f"Bearer {tok}"})
        if sc == 200:
            items = json.loads(b2).get("items", [])
            print("YouTube API OK. channel_id:", items[0].get("id") if items else "(none - scope/brand account check)")
        else:
            print("Channel check failed:", sc, b2[:200])
else:
    err = j.get("error") or ""
    desc = j.get("error_description") or ""
    print("HTTP", code, "error=", err)
    print("detail:", desc[:300])
    if "invalid_grant" in "".join([str(code), body]):
        print("\nCAUSE: refresh token expired / revoked / replaced.")
        print("FIX: rerun  python botbeat/get_youtube_token.py  and save the new YT_REFRESH_TOKEN.")
        print("     If this keeps recurring, your OAuth consent screen is in TESTING mode,")
        print("     so refresh tokens die every ~7 days. Publish the app to PRODUCTION:")
        print("     Google Cloud console > APIs & Services > OAuth consent screen > Publish app.")
    elif "invalid_client" in body:
        print("\nCAUSE: YT_CLIENT_ID or YT_CLIENT_SECRET is wrong.")
        print("FIX: paste the correct OAuth client credentials into those two secrets.")
    else:
        print("CAUSE: see detail above.")
