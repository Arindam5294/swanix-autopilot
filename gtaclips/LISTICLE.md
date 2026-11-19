# Listicle mode ("5 crazy things in GTA 6")

Fast 55-68 s Shorts with our own sourced script over real GTA 6 gameplay. Original content (own script, many short
credited cuts), so it is the evergreen, reused-content-safe format for the channel.

Run: Actions -> "GTA VI Listicle Shorts" -> Run workflow. `topic` blank = next unused angle from `listicle.json`.
`preview` (default on) = Telegram only, nothing uploaded, topic NOT marked used. `hand_upload` (default on) = no API
upload; Telegram gets the video + title + description + tags (send_short.py HAND_UPLOAD kit).

Pipeline (listicle.py):
1. Topic: `--topic`/TOPIC, else first angle in `listicle_topics` not in `listicle_used.json`; all used -> AI suggests a new one.
2. Facts: Wikipedia "Grand Theft Auto VI" (Gameplay, Setting, Research/Design, Technology; Leaks dropped),
   rockstargames.com/VI (+ any `listicle_source_urls`), Google News headlines. Saved to out/sources.json.
3. Script (common/ai.py chain): hook + 5 items, each with label, 42-52 word body, source id and a VERBATIM evidence quote.
4. FACT GATE: evidence quote must be in the cited source; numbers and mid-sentence names must appear in the sources;
   then a fresh AI fact-checker lists unsupported claims. Rejected -> rewritten twice with the faults -> still bad = exit 6, no video.
5. Voice: edge-tts (`listicle_voice`, `listicle_rate` +25%), WordBoundary timings, pauses cut to 0.11 s. If it runs over
   `listicle_max_seconds` it is re-voiced faster (270 words needed ~+39%), time-stretch only as last resort.
6. Footage: find_clip.twitch_clips() + kick_clips() (keyless), NSFW titles dropped, Twitch first, one clip per streamer
   first. Each clip: facecam found with layout.find_facecam and kept out of a full-height 9:16 crop (never letterboxed;
   bottom 12% = game subtitles/HUD cropped away), dark/blurred/cutscene-bar samples and big-face (cam) clips skipped.
7. Montage: cuts 0.6-1.5 s (0.55-0.85 s in the hook), a hard cut on every item, max 3.5 s, no stretch of any clip used twice.
   VISUAL SAFETY: Gemini vision checks 2 frames of EVERY cut (nudity, strip club, cams, title cards, bars) and replaces
   flagged cuts. Without GEMINI_API_KEY this is "unchecked" and listicle_gate.py rejects the post
   (set `listicle_allow_unchecked_visuals: true` to override - not recommended: Twitch GTA clips often contain nudity).
8. Text (ASS, Montserrat Black Italic in fonts/, OFL): top title, green "1." + white label per item, captions at 74%
   height, max 3 words, keywords #39FF3C / #FF2E2E.
9. Music: random file from gtaclips/music/*.mp3|m4a or `listicle_music_urls`, ~7 dB under the voice. None configured = no bed.
   Only use no-vocal tracks you have the rights to (e.g. YouTube Audio Library downloads).

Gate: `listicle_gate.py` = gate.py + listicle rules (30-70 s allowed, fact check must pass, every cut vision-checked).
Lock: `python listicle.py --lock` adds `topic:<angle>` + the footage URLs to listicle_used.json and pushes (gitsave --union).
Exit codes: 0 made, 3 not enough clean footage, 6 fact gate / script rejected, 1 error.
Local test: `python listicle.py --script-file tests/listicle_sample_script.json --footage-dir DIR --skip-verifier`.
