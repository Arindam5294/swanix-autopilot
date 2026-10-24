# Repo Doctor (private setup)

Every morning at 6:00 AM IST it looks at all of Swanix's GitHub repos, finds what is broken, writes a fix with
free AI, tests it, merges it if the checks pass, and sends one plain-English report to Telegram.
Reports are also saved in `doctor/reports/`.

**Engine:** the code lives in the public repo [Arindam5294/repo-doctor](https://github.com/Arindam5294/repo-doctor),
pinned to an exact commit in `.github/workflows/repo-doctor.yml`. This folder keeps only the private side:
`config.json` (the rules), `reports/` and `last_run.json`. Secrets stay in this repo's Actions secrets.
To upgrade the engine, change the pinned `ref:` in the workflow.

**Telegram:** `/doctor` shows status · `/doctor now` runs it now · `/doctor off` / `/doctor on`.
**Emergency stop:** create a file `doctor/OFF` (or set repo variable `DOCTOR_OFF=true`).

Hard rules (see `config.json`): never force-push or rewrite history; never deletes anything except its own
`doctor/*` branches after merging; never changes repo settings, visibility or secrets; never edits protected
paths; never re-runs workflows that post, upload or trade; max 10 files / 300 lines per fix; skips archived and
forked repos and its own engine repo; never sends secrets or `.env` files to the AI; free AI only.
