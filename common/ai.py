"""Free AI chain: Gemini -> Groq -> OpenRouter. ask(prompt, min_len) returns text."""

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

def openai_style(url, key_env, model, prompt):
    k = os.environ.get(key_env)
    if not k: raise RuntimeError(f"no {key_env}")
    d = http(url, {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 16000 if "mistral" in url or "bynara" in url or "itera" in url else 30000, "temperature": 0.4}, {"Authorization": f"Bearer {k}"})
    return d["choices"][0]["message"]["content"]

PROVIDERS = [
    ("gemini", gemini),
    ("groq", lambda p: openai_style("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY",
                                    os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), p)),
    ("mistral", lambda p: openai_style("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY", "mistral-medium-latest", p)),
    ("nara", lambda p: openai_style("https://router.bynara.id/v1/chat/completions", "NARA_API_KEY", "minimax-m3.1-flash:free", p)),
    ("itera", lambda p: openai_style("https://api.iteracompute.com/v1/chat/completions", "ITERA_API_KEY", "qwen/qwen3.8-27b", p)),
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
