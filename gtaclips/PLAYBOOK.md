# GTA VI Clips - the money playbook (all 9 steps)

| # | Step | File | Runs |
|---|------|------|------|
| 1 | Agent setup: STATUS.md + RULES.md read every run, gauntlet rule, post only what passes | RULES.md, STATUS.md | always |
| 2 | Outlier channels: videos beating their channel's median 3x+ -> outliers.csv | outliers.py | Mondays (gta-weekly) |
| 3 | Learn titles/thumbnails from outliers -> THUMBNAILS.md, thumbnails made + picked by a fresh reviewer | outliers.py, thumbnail.py | weekly / per episode |
| 4 | Best clips from Twitch, Kick, YouTube ranked by VIEWS PER HOUR; cutscenes/endings/exploits skipped | find_clip.py, episode.py | every run |
| 5 | Daily 16:9 episode: no intro, label + @handle per clip, every streamer credited + chaptered | episode.py | 8:30 PM IST (GTA_EPISODE_CRON) |
| 6 | Verticals: facecam on top / gameplay below, word-by-word streamer captions, native TikTok + Reels | layout.py, captions.py, crosspost.py | every Short |
| 7 | Your own-gameplay video script + shot list (unlocks YouTube ads) | own_video.py | on demand (gta-own-video) |
| 8 | Gauntlet: hard checks + fresh AI reviewer sees frames, YES or NO with faults, verdicts in RULES.md | gate.py | before every post |
| 9 | Post + clipping campaigns: campaign tags/#ad added, Telegram "submit now" ping, STATUS.md log | campaign.py, campaigns.json | after every post |

## Where the money comes from
1. Clipping campaigns (Content Rewards / Whop): streamers pay about $1 per 1,000 views on approved clips, capped per clip. Join a GTA 6 campaign, copy its rules into campaigns.json, set "active": true. Submit each link within 30 minutes (the bot pings you).
2. YouTube ads: needs 1,000 subs + 10M Shorts views in 90 days (or 4,000 watch hours). Channels of only other people's clips can be refused for "reused content", so post your own gameplay videos too (step 7).

## Secrets to add (repo Settings > Secrets and variables > Actions)
Required: GTA_YT_CLIENT_ID, GTA_YT_CLIENT_SECRET, GTA_YT_REFRESH_TOKEN, GTA_YOUTUBE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY (the reviewer needs vision).
For clips: TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET (dev.twitch.tv, free). Optional KICK_CLIENT_ID/SECRET.
For TikTok + Instagram: UPLOAD_POST_API_KEY, UPLOAD_POST_USER (upload-post.com; free = 10 uploads/month, Instagram only; TikTok needs their paid plan). Instagram must be a Business or Creator account.
Optional: THUMB_IMAGE_MODEL (a Gemini image model name) for AI-restyled thumbnails.

## Turn it on, in this order
1. Actions > GTA VI Clips - weekly outliers > Run workflow (builds THUMBNAILS.md first).
2. Actions > GTA VI Clips Shorts > Run workflow with Preview ticked. Check the Telegram video.
3. Actions > GTA VI Clips - daily episode > Run workflow (preview is on by default).
4. Happy? Repo Settings > Variables: GTA_CRON = true, then GTA_EPISODE_CRON = true.

## Rules that keep the channel alive
No leaks, fakes, spoilers or exploit tutorials. Credit every creator. Remove any clip the same day its creator asks.
Copyright claims on streamer footage are still possible; campaign footage is the safest because the streamer paid for it.
