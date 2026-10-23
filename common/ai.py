"""Free AI chain: Swanix Brain auto-router first, then Gemini -> Groq -> proxies -> OpenRouter.
ask(prompt, min_len) returns text. think(task, data, as_json) asks Brain for a judgment call (it plans,
can search the web, knows Swanix's preferences, and self-checks); falls back to ask()."""

import json, os, sys, time, pathlib, subprocess, urllib.request, urllib.error

def http(url, body, headers, timeout=600):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "swanix-autopilot/1.0", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def gemini(prompt):
    k = os.environ.get("GEMINI_API_KEY")
    if not k: raise RuntimeError("no GEMINI_API_KEY")
    m = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    d = http(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
             {"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": 60000, "temperature": 0.4}},
             {"x-goog-api-key": k})
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])

# Free Mistral/Nara/Itera/NVIDIA/Inception keys stay in Cloudflare; calls go through the Swanix Brain proxy, authed by the Telegram bot token.
BRAIN_LLM = "https://swanix-brain.swanixjobs.workers.dev/llm"

def openai_style(url, key_env, model, prompt):
    k = os.environ.get(key_env)
    if not k: raise RuntimeError(f"no {key_env}")
    d = http(url, {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 16000 if url == BRAIN_LLM else 30000, "temperature": 0.4}, {"Authorization": f"Bearer {k}"})
    return d["choices"][0]["message"]["content"]

def brain_auto(p, hard=True):
    return openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "auto|hard" if hard else "auto", p)

PROVIDERS = [
    ("brain-auto", brain_auto),   # Swanix Brain picks the best free model (Gemini, Groq, SambaNova, Cohere, Z.ai, Mistral...) with fallback
    ("gemini", gemini),
    ("groq", lambda p: openai_style("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY",
                                    os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), p)),
    ("mistral", lambda p: openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "mistral|mistral-medium-latest", p)),
    ("nara", lambda p: openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "nara|minimax-m3.1-flash:free", p)),
    ("nvidia", lambda p: openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "nvidia|z-ai/glm-5.3-flash", p)),
    ("inception", lambda p: openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "inception|mercury-3", p)),
    ("itera", lambda p: openai_style(BRAIN_LLM, "TELEGRAM_BOT_TOKEN", "itera|qwen/qwen3.8-27b", p)),
    ("openrouter", lambda p: openai_style("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY",
                                          os.environ.get("OPENROUTER_MODEL", "openrouter/free"), p)),
]

def ask(prompt, min_len=2000):
    last = None
    for attempt in range(3):
        for name, fn in PROVIDERS:
            try:
                out = fn(prompt)
                if out and len(out) > min_len:
                    print(f"   via {name}, {len(out)} chars"); return out
            except Exception as e:
                last = f"{name}: {e}"; print("   ", last[:200])
        time.sleep(60 * (attempt + 1))
    raise RuntimeError(f"all providers failed: {last}")


def think(task, data="", as_json=False, tools=True):
    """Judgment step through Swanix Brain. Returns text (or parsed JSON when as_json). Falls back to ask()."""
    import re
    try:
        k = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not k: raise RuntimeError("no TELEGRAM_BOT_TOKEN")
        d = http(BRAIN_LLM, {"think": True, "task": task, "data": data, "json": as_json, "tools": tools, "messages": []},
                 {"Authorization": f"Bearer {k}"}, timeout=300)
        out = (d.get("reply") or "").strip()
        if not out: raise RuntimeError("empty think reply")
        print(f"   think via {d.get('by')}, steps {len(d.get('steps') or [])}")
    except Exception as e:
        print("   think fallback:", str(e)[:160])
        out = ask(task + "\n\nDATA:\n" + (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)), min_len=10)
    if not as_json: return out
    m = re.search(r"(\[.*\]|\{.*\})", out, re.S)
    return json.loads(m.group(1)) if m else None
