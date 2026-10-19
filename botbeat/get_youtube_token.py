#!/usr/bin/env python3
"""One-time: get the YouTube refresh token. Run in Codespaces:  python botbeat/get_youtube_token.py"""
import json, urllib.parse, urllib.request

cid = input("Paste Client ID: ").strip()
csec = input("Paste Client secret: ").strip()
redirect = "http://localhost"
url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": redirect, "response_type": "code", "access_type": "offline",
    "prompt": "consent", "scope": "https://www.googleapis.com/auth/youtube"})
print("\n1) Open this link, pick the Google account + the Botbeat channel, tap Advanced > Go to app (unsafe) > Continue:\n")
print(url)
print("\n2) The page will fail to load (that's fine). Copy the WHOLE address from the address bar (starts with http://localhost).\n")
back = input("Paste that address here: ").strip()
code = urllib.parse.parse_qs(urllib.parse.urlparse(back).query).get("code", [back])[0]
data = urllib.parse.urlencode({"code": code, "client_id": cid, "client_secret": csec,
                               "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
tok = json.loads(urllib.request.urlopen("https://oauth2.googleapis.com/token", data).read())
if "refresh_token" not in tok:
    print("No refresh token returned:", tok); raise SystemExit(1)
print("\nDone. Add these 3 repository secrets (Settings > Secrets and variables > Actions):")
print("YT_CLIENT_ID      =", cid)
print("YT_CLIENT_SECRET  =", csec)
print("YT_REFRESH_TOKEN  =", tok["refresh_token"])
