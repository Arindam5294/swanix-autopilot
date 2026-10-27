import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Google's granular consent can drop unticked scopes; don't crash, check instead
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",     # upload videos
    "https://www.googleapis.com/auth/youtube.force-ssl",  # status check + delete (videos.list / videos.delete)
]

def main():
    client_id = input("Paste Client ID: ").strip()
    client_secret = input("Paste Client Secret: ").strip()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = "http://localhost"
    auth_url, _ = flow.authorization_url(
        prompt="consent", access_type="offline", include_granted_scopes="false"
    )

    print("\n=== OPEN THIS LINK IN YOUR BROWSER ===")
    print(auth_url)
    print("Sign in with the account that OWNS the channel (pick the brand channel if asked).")
    print("On the permissions screen, TICK EVERY CHECKBOX.")
    print("======================================\n")

    redirect_response = input("Paste the full redirected URL: ").strip()
    code = redirect_response.split("code=")[1].split("&")[0] if "code=" in redirect_response else redirect_response

    flow.fetch_token(code=code)
    granted = set(flow.oauth2session.token.get("scope", []) or [])
    if isinstance(flow.oauth2session.token.get("scope"), str):
        granted = set(flow.oauth2session.token["scope"].split())

    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("\nNOT SAVED. These permissions weren't granted (a checkbox was left unticked):")
        for s in missing:
            print("  -", s)
        print("Run the script again and tick every box.")
        raise SystemExit(1)

    print("\nAll permissions granted.")
    print("YOUR REFRESH TOKEN (put it in GitHub secret YT_REFRESH_TOKEN):\n" + flow.credentials.refresh_token)

if __name__ == "__main__":
    main()
    
