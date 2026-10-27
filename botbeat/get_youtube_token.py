import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def main():
    client_id = input("Paste Client ID: ").strip()
    client_secret = input("Paste Client Secret: ").strip()
    
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]
        }
    }
    
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = "http://localhost"
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    
    print("\n=== OPEN THIS LINK IN YOUR BROWSER ===")
    print(auth_url)
    print("======================================\n")
    
    redirect_response = input("Paste the full redirected URL: ").strip()
    
    if "code=" in redirect_response:
        code = redirect_response.split("code=")[1].split("&")[0]
    else:
        code = redirect_response
        
    flow.fetch_token(code=code)
    print("\nYOUR REFRESH TOKEN:\n" + flow.credentials.refresh_token)

if __name__ == "__main__":
    main()
