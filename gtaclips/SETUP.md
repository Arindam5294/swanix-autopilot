# GTA VI Clips (@GTAviclipxx) autopilot

Same engine as Botbeat, aimed at GTA 6. Every run it:
1. Hunts GTA 6 everywhere: YouTube videos, YouTube live streams and stream replays, Twitch live streams and top clips, and Kick live streams and top clips. Trusted creators and outlets come first. Fakes, concepts, leaks, "play early" and GTA 5 videos are filtered out.
   For long streams it finds the viral moment from YouTube's Most Replayed graph, then live-chat spikes, then the streamer's loudest reaction, and downloads only that ~45 s. For a live stream it records ~7 minutes and keeps the peak.
2. Short videos: cuts the most action-packed moments on scene changes into a 1080x1920 Short of 32 s or less.
3. Has the AI write a hook, a 45 to 60 word hype voiceover with one real takeaway, an end question, title, description, tags and hashtags.
4. Adds the voiceover, word-by-word captions, a hook bar and a credit line (Botbeat's Remotion overlay).
5. Uploads it to @GTAviclipxx and sends you the link plus all the copy on Telegram. If the upload fails, it sends you the file.
It never reuses a clip, and every description credits the creator and links the original.

## Setup (10 min)
1. Copy `gtaclips/` and `.github/workflows/gtaclips.yml` into your swanix-autopilot repo and push.
2. Connect the new channel: run `python gtaclips/get_youtube_token.py` the way you did for Botbeat. On Google's screen, choose the **GTA VI CLIPS** channel, not Botbeat. Save the refresh token it prints as the repo secret **GTA_YT_REFRESH_TOKEN**.
   Optional, for Twitch: create an app at dev.twitch.tv/console and save TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET.
   Optional, for Kick live: create an app at kick.com/settings/developer and save KICK_CLIENT_ID and KICK_CLIENT_SECRET. Kick clips work without keys when Kick doesn't block them.
   It reuses YT_CLIENT_ID, YT_CLIENT_SECRET, YOUTUBE_API_KEY, GEMINI_API_KEY, the Telegram secrets, YT_COOKIES and YTDLP_HOME_PROXY from Botbeat. Nothing else to add.
3. Test it: Actions → "GTA VI Clips Shorts" → Run workflow → tick **Preview only**. The Short lands on Telegram without uploading.
4. Go live: Settings → Variables → add **GTA_CRON** = `true`. It then posts 4 a day: 8 AM, 1 PM, 6 PM and 10 PM IST.

To force a clip, run the workflow and paste a YouTube, Twitch or Kick link into the "url" box.

## Rules that keep the channel alive
- Credit is in every description. If a creator asks you to take a clip down, delete it the same day.
- The AI commentary and edit are what make it "transformed" for YouTube. Don't switch them off.
- Copyright claims on other creators' footage can still happen. Official Rockstar trailers get claimed often, so creator gameplay is the safer bet.
- Nothing about leaks or "play early" tricks, ever. The filters and the script check enforce this.
- Tune what it hunts in `gtaclips/config.json`: search_terms, trusted_channels, skip_words, min_views.
