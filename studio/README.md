# Studio

A Telegram bot that makes PDFs, infographics, narrated videos, mock tests and study books. It runs on GitHub Actions (`.github/workflows/studio.yml`) every 5 minutes, using free AI tiers only.

## Use it

Send your bot a message:

| Message | You get |
|---|---|
| any request, e.g. `water cycle for class 8` | the AI picks: infographic for short asks, PDF for guides, notes and reports |
| `/info <topic>` | infographic image (1080x1350; add "story" for 1080x1920) plus a full-quality file |
| `/pdf <topic>` | polished multi-page A4 PDF |
| `/video <topic>` | narrated vertical video, 30-60 seconds, with captions |
| `/mock <exam and post>` | full mock test PDF: exam pattern, questions, answer key, explanations. `/mock 3 <exam>` makes 3 mocks |
| `/book <exam or topic>` | study book PDF: contents, chapters with notes, tables, formulas, practice MCQs, index |
| `/help` | this list |
| just a link | queued for the Botbeat Shorts workflow |

**Edit anything:** reply to a file the bot sent with what to change, e.g. "make it darker", "add a slide on costs", "make chapter 4 deeper", "add 20 more reasoning questions". You get a revised file as a reply. The last 50 outputs can be edited.

**Timing:** replies arrive in about 5-15 minutes, because GitHub starts scheduled runs late when it is busy. Books and mock tests take longer (several runs); you get progress messages like "Chapter 3/12 done", and work resumes automatically if a run ends.

**Mock test patterns:** when Google Search grounding is available on the Gemini key, the pattern is checked online and the sources are named on the cover. Otherwise the cover says "Pattern from AI knowledge, verify with the official notice". Arithmetic answers are re-calculated in Python and the answer key is corrected if the AI got it wrong.

## Secrets needed

Repository → Settings → Secrets and variables → Actions:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (required; messages from any other chat are ignored)
- `GEMINI_API_KEY` (main writer), `GROQ_API_KEY` and `OPENROUTER_API_KEY` (free backups)

Also set Settings → Actions → General → Workflow permissions to **Read and write** (Studio saves its progress in `studio/state/`).

## Notes

- Studio is the only reader of the bot's messages; do not add another `getUpdates` poller, or messages get lost.
- Video images come from Pollinations (free, no key, about one image per 45 seconds). If it is unavailable, a scene uses a gradient title card instead.
- Files over 50 MB (Telegram's bot limit) are split into parts.
- Optional variables: `GEMINI_MODELS`, `GROQ_MODEL`, `OPENROUTER_MODELS` (comma-separated model ids) to override the defaults.
- Tests: `python studio/tests/test_routing.py` (offline, no Telegram or AI calls).
