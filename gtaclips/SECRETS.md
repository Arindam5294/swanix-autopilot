# GTA 6 SECRETS · PART N (daily tip Shorts)

Workflow: `.github/workflows/gta-secrets.yml` (Run workflow: preview=true, hand_upload=true by default; cron 00:30 UTC = 6:00 AM IST is commented out until previews look right).

1. **Tip** (`gta_secrets.py`): guide articles from publisher RSS feeds (Sportskeeda, GameRant, TheGamer, Polygon, PC Gamer, GamesRadar, Eurogamer, IGN, Dexerto), the IGN GTA 6 wiki index and DuckDuckGo html search, allowed domains only. Gemini picks ONE tip and must quote the supporting sentence; the quote has to appear verbatim in the fetched article, every number in the captions must be in the article, and a place/item keyword must be in the article and the quote. Otherwise the tip is rejected. Topics in `secrets_topics.json` are skipped.
2. **Clip**: Twitch top clips (keyless GraphQL, EN/HI, LAST_DAY then LAST_WEEK) + Kick, ranked by keyword matches in the clip title; top 5 matches + 2 unmatched are downloaded until one passes Gemini vision (shows the tip's place/item + safe: no full nudity, bikini OK, no stream alerts, no slurs on screen) and a faster-whisper slur check on the streamer audio. Clips in `used.json`, `listicle_used.json`, `secrets_used.json` never come back.
3. **Nothing verified** = exit 5 (no tip) / 3 (no clip): Telegram says "no verified secret today", nothing posted.
4. **Render**: 1080x1920 30 fps, 12-25 s (aim 20), facecam on top when found, yellow series banner, 2 s hook, WHERE / WHAT YOU GET captions, punch-in + white flash at the payoff, FOLLOW FOR PART N+1 end card, clip credit, original audio, no music.
5. **Gate** (`secrets_gate.py`): gate.py hard checks + fresh AI reviewer + tip verified + footage checked.
6. **State**: `secrets_series.json` (last posted part), `secrets_topics.json`, `secrets_used.json`, committed by gitsave. Previews burn the clip only; Part N advances only on a real post.

Settings: `gta_secrets.json`. Test switches (never in the workflow): `--no-llm`, `--dry-run`, `--tip-file/--clip-file`.
