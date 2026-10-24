# Repo Doctor

Every morning at 6:00 AM IST it looks at all of Swanix's GitHub repos, finds what is broken, writes a fix with
free AI, tests it, merges it if the checks pass, and sends one plain-English report to Telegram.
Reports are also saved in `doctor/reports/`.

**Telegram:** `/doctor` shows status · `/doctor now` runs it now · `/doctor off` / `/doctor on`.
**Emergency stop:** create a file `doctor/OFF` (or set repo variable `DOCTOR_OFF=true`).

What it checks: failing GitHub Actions runs (last 30 days), workflow files, JSON files, Python syntax and
requirements, `npm install` / `build` / `lint` / `test`. Repos with no changes in 7 days and nothing failing
get only a quick look, to save Actions minutes.

How it fixes: one branch `doctor/<date>-<what>` per problem, a pull request explaining the change, then a squash
merge only when the repo's checks (or its own re-check) pass. At most 2 tries; otherwise the PR stays open and the
report says "needs attention".

Hard rules (see `config.json`): never force-push or rewrite history; never deletes anything except its own
`doctor/*` branches after merging; never changes repo settings, visibility or secrets; never edits money, trade,
wallet or oracle code (alpha/forecast stay in shadow mode); never re-runs workflows that post, upload or trade;
max 10 files / 300 lines per fix; skips archived and forked repos; never sends secrets or `.env` files to the AI;
free AI only (Swanix Brain router, Gemini, Groq, Mistral, OpenRouter free).
