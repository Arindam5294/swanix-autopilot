"""Free AI chain. ask(prompt, min_len) returns text; judge(task, data, as_json) asks Swanix Brain for a judgment call.
v5 (2026-10-23): Swanix Brain first (it routes to the best free model and falls back on 429), then the direct chain."""

import json, os, re, sys, time, pathlib, subprocess, urllib.request, urllib.error

def http(url, body, headers, timeout=600):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "swanix-autopilot/1.0", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

BRAIN_LLM = "https://swanix-brain.swanixjobs.workers.dev/llm"

def brain_auto(prompt, hard=True):
    k = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not k: raise RuntimeError("no TELEGRAM_BOT_TOKEN")
    d = http(BRAIN_LLM, {"model": "auto|hard" if hard else "auto", "messages": [{"role": "user", "content": prompt}], "max_tokens": 12000},
             {"Authorization": f"Bearer {k}"}, timeout=180)
    return d["choices"][0]["message"]["content"]

def judge(task, data, as_json=True, tools=True):
    """Brain judgment step: understands Swanix's goals (its memory of him), may web-search, returns text or parsed JSON (None on failure)."""
    k = os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        d = http(BRAIN_LLM, {"think": True, "task": task, "data": data, "json": as_json, "tools": tools}, {"Authorization": f"Bearer {k}"}, timeout=240)
        txt = d.get("reply") or ""
        print("   judged via", d.get("by"))
        if not as_json: return txt
        m = re.search(r"[\[{][\s\S]*[\]}]", txt)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        print("   judge failed:", str(e)[:200]); return None

def gemini(prompt):
    k = os.environ.get("GEMINI_API_KEY")
    if not k: raise RuntimeError("no GEMINI_API_KEY")
    m = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    d = http(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
             {"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": 60000, "temperature": 0.4}},
             {"x-goog-api-key": k})
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])


def openai_style(url, key_env, model, prompt):
    k = os.environ.get(key_env)
    if not k: raise RuntimeError(f"no {key_env}")
    d = http(url, {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 16000 if url == BRAIN_LLM else 30000, "temperature": 0.4}, {"Authorization": f"Bearer {k}"})
    return d["choices"][0]["message"]["content"]

PROVIDERS = [
    ("brain", brain_auto),
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
