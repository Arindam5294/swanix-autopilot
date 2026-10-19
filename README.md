# Swanix Autopilot

Everything that used to run on Hark, running free on GitHub Actions. No Hark usage.

| Folder | What it does | When |
|---|---|---|
| `shield/` | Shield Audit + Naka Cosmos: audit, Ed25519 signature, Bitcoin timestamp, alerts, then publishes both pages | every 10 min |
| `jobs/` | Job alerts only (never applies): Web3 community/moderator + Agri/CAD roles | 9 AM and 6 PM IST |
| `botbeat/` | Makes a finished Botbeat Short (viral robot/AI clip, hook, captions, AndrewNeural voice) and sends it + title/description/tags to your Telegram. You upload from the YouTube app (about 1 minute) | 7:30 AM and 7:30 PM IST |
| `alpha/` | Crypto alpha news bot to Telegram. **Off** until you set the variable `ALPHA_ON` = `true` | every 15 min |

## One-time setup
1. Create a **public** repo (public = unlimited free Actions minutes) and upload everything here, including the hidden `.github` folder. Upload from a computer: phone uploads skip folders.
2. **Settings → Secrets and variables → Actions → Secrets → New repository secret**:
   - `ED25519_KEY_B64`: the line in `SIGNING_KEY_secret.txt` (then delete that file)
   - `GEMINI_API_KEY`: free key from aistudio.google.com/apikey
   - `GROQ_API_KEY`: free key from console.groq.com/keys (backup writer)
   - `OPENROUTER_API_KEY`: optional second backup
   - `YOUTUBE_API_KEY`: free key from console.cloud.google.com (enable "YouTube Data API v3", then Credentials → API key). Used only to find viral clips
   - `YT_COOKIES`: optional, only if downloads get blocked
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: optional. Message @BotFather, send /newbot to get the token, then send your bot "hi" and open `https://api.telegram.org/bot<TOKEN>/getUpdates` to read your chat id. Without these, alerts arrive as GitHub issues (GitHub emails you).
3. **Settings → Pages → Source: GitHub Actions.** Your Shield page will be `https://<you>.github.io/<repo>/` and Cosmos at `/cosmos/`.
4. **Settings → Actions → General → Workflow permissions: Read and write.**
5. **Actions tab:** open each workflow and press **Run workflow** once.

## Edit job keywords
`jobs/job_alerts.py` → `KEYWORDS`. The first run only remembers the current jobs; alerts start from the next run.

## Other products
- **TapeOut Audit** (repo `tapeout-audit`): open `.github/workflows/audit.yml`, delete the `# ` in front of `schedule:` and the `- cron` line, and commit. It then runs hourly. Add the `TAPEOUT_SIGNING_KEY` secret there.
- **Unravix** (Cloudflare Pages + Worker), **Rug Radar** (PayBox plugin), **Dose Buddy** (GitHub) and the **Swanix Crypto** page don't depend on Hark, so nothing changes for them.
## Botbeat tips
- Send any clip link (X, YouTube, Instagram) to your Telegram bot and the next run turns it into a Short. Links you send skip the view rule.
- Auto-pick only uses real clips from official robot/AI companies and major news channels (list in config.json) with 1M+ views from the last 30 days, no crypto, no skits or AI fakes. If nothing qualifies, that slot is skipped (your rule).
- Why not auto-upload? YouTube locks every video uploaded by a new, unaudited API app as private. Once you pass YouTube's free API audit, an upload step can be added.
- Rules live in `botbeat/config.json`.
