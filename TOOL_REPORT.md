# Tool Report — Swanix Open-Source Tooling Test

Date: 2026-10-23 · Machine: Linux x86_64 (Azure), 4 vCPU, ~16 GB RAM, Python 3.12.15, **no GPU**
Scope: tested free/open-source tools on a real machine for Botbeat · trading/Kronos · swanix-autopilot · local AI · budget.

---

## 1) yt-dlp — ✅ USE (fixes Botbeat)
- Installed: `pip install -U yt-dlp` → **version 2026.10.21** (rc=0, 3s).
- Metadata extraction proof (non-YouTube public source): `yt-dlp --dump-json --simulate` → `title: flower | ext: mp4`.
- ⚠️ **Honest finding:** YouTube itself now blocks anonymous datacenter IPs with **"Sign in to confirm you're not a bot"**. Extraction from YouTube requires `--cookies-from-browser` or a cookies file (or a residential IP). A JS runtime (deno) is also now wanted — install with `curl -fsSL https://deno.land/install.sh | sh` (deno 2.9.8 works, we installed it).
- **Botbeat fix:** the "missing package" error is fixed by `pip install -U yt-dlp`; but note the extra YouTube auth wall — Botbeat needs cookies if it pulls from YouTube specifically.

## 2) MoneyPrinterTurbo — ✅ USE (video for Botbeat/community)
- Cloned `harry0703/MoneyPrinterTurbo`. Deps are **light**: moviepy, streamlit, fastapi, edge-tts, openai, faster-whisper, litellm — no torch dependency in requirements.txt.
- **GPU: optional, not required.** Default pipeline uses cloud LLM APIs (OpenAI/others) for script + free Pexels stock footage + edge-tts TTS + ffmpeg. Runs headless via `main.py`/API; web UI is Streamlit.
- Needs API keys (config.toml) for the LLM provider only. No local GPU needed for the default path.

## 3) TradingAgents — maybe (trading/Kronos; costs money)
- Cloned `TauricResearch/TradingAgents`. Deps: langchain/langgraph, yfinance, pandas, pandas>=3.0.6, anthropic/google-genai/openai.
- **Needs paid LLM APIs:** supports OpenAI, Anthropic (Claude), Google Gemini, DeepSeek, Qwen, Mistral (per cli/prompts.py). It is a multi-agent framework — heavy token usage → real API cost. Good fit conceptually for Kronos research but not free to run at scale. Use sparingly / with a cheap model (DeepSeek).

## 4) Ollama — ✅ USE (free local LLM)
- Installed via official curl script → **client 0.40.10**, server on :11434.
- Pulled `llama3.2:1b` → **1.3 GB**, pulled in 11.3s. One-line inference in **~2.1s**.
- ⚠️ CPU-only here (no GPU detected) — fine for small models, slow for 7B+ on CPU. GPU would give big speed-up.

## 5) Whisper (openai-whisper) — ✅ USE (transcription)
- `pip install openai-whisper` (requires ffmpeg: `apt-get install ffmpeg` → 6.1.1).
- Test: 3.4s speech WAV → `whisper --model tiny` transcribed in **5.6s** on CPU (tiny = 72 MB model).
- Great for Botbeat/local audio → text. Use `tiny`/`base` on CPU.

## 6) yt-dlp → Whisper combo — ✅ proven
- yt-dlp extracted audio (WAV, 596s) from an archive.org clip in ~42s; Whisper tiny transcribed it in **220.7s** on CPU (≈2.7x realtime). Pipeline works end-to-end.
- Note: clip audio was mostly animation/music, so transcript is garbled — expected; works on actual speech.

## 7) n8n — ✅ USE (autopilot orchestration)
- Self-host via Docker (installed: Docker 28.0.4):
  `docker volume create n8n_data`
  `docker run -it --rm --name n8n -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n`
- 1500+ integrations, visual canvas + custom code, webhooks for triggers (connect to GitHub/Telegram). Free self-hosted. Good candidate to orchestrate jobs/shield/hunters.

## 8) Lightweight review (repo → verdict)
| Repo | Verdict | Why |
|---|---|---|
| **LibreChat** | maybe | Self-hosted ChatGPT UI; needs MongoDB (docker). Heavier than needed. |
| **Fooocus** | skip | Stable-Diffusion image gen; requires torch + ~GPU (16GB VRAM recommended). Not for a CPU box. |
| **Plausible** | maybe | Privacy-first analytics, Elixir/Postgres via Docker; nice but extra infra for side projects. |
| **AppFlowy** | maybe | Notion alternative; Rust+Flutter, has docker build. Fine if he wants local workspace. |
| **Penpot** | maybe | Open-source design tool; docker-compose, heavier (Rust/Clojure). Good if he needs design. |
| **Cal.com** | maybe | Scheduling; Node/Postgres; heavy to self-host, great if he needs booking. |
| **Bitwarden** | maybe | Password manager server, C#/.NET + SQL Server; heavier; free tier/vaultwarden lighter alternative. |
| **Fincept Terminal** | maybe | Multi-agent quant/AI research desk; enterprise tier is paid ($15–45/mo); open-source repo stays AGPL/free. Fit for trading. |
| **HyperFrames** | maybe | HeyGen's open-source "write HTML → render video" (Node>=22, npm) — very relevant for Botbeat video generation, lightweight (no GPU). Worth a look. |

---

## Top recommendations for Swanix (budget + CPU-only box)
1. **yt-dlp** — immediate Botbeat fix; add deno + note the YouTube cookies wall.
2. **Ollama + Whisper (tiny/base)** — free local LLM + transcription; no GPU needed.
3. **yt-dlp → Whisper** pipeline — proven end-to-end for Botbeat audio→text.
4. **MoneyPrinterTurbo** — GPU-free short-video generation (needs just an LLM API key).
5. **n8n (Docker)** — free automation orchestrator for autopilot (jobs/shield/hunters).
6. **HyperFrames** (Node) — promising lightweight video path, no GPU.
7. **TradingAgents / Fincept** — use as periodic research (cheap model like DeepSeek), not continuous; API costs apply.
