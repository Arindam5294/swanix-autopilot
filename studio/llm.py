"""Studio AI chain (free tiers only): Gemini -> Groq -> OpenRouter free models -> lighter fallbacks.

ask(prompt, system=..., check=fn) tries each model in turn. A model's answer is only accepted if it was not
cut off (finish reason) and check(text) accepts it, so a truncated HTML page or broken JSON falls through to
the next model automatically. Rate limits (HTTP 429/503) put that provider on a short cooldown.
Never logs keys."""
import json, os, re, sys, time, pathlib, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from ai import http  # same tiny JSON-over-HTTPS helper the rest of the repo uses

DEADLINE = [None]          # run.py sets DEADLINE[0] = epoch seconds; ask() stops retrying after it
_COOL = {}                 # provider -> epoch until which we skip it
_GEMINI_FOUND = []         # discovered Gemini model ids (if the configured one disappears)
_OR_FREE = []              # discovered OpenRouter free models
LAST = {"model": ""}


class AIError(RuntimeError):
    pass


def _left():
    return (DEADLINE[0] - time.time()) if DEADLINE[0] else 1e9


def _scrub(s):
    s = str(s)
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "NARA_API_KEY", "ITERA_API_KEY", "TELEGRAM_BOT_TOKEN"):
        v = os.environ.get(k)
        if v and len(v) > 6:
            s = s.replace(v, "***")
    return s[:300]


# ---------------- providers ----------------
def _gemini(model, prompt, system, max_tokens, temp, search=False):
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        raise AIError("no GEMINI_API_KEY")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    d = http(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body,
             {"x-goog-api-key": k}, timeout=360)
    c = (d.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []) if not p.get("thought"))
    fin = c.get("finishReason", "")
    return text, ("length" if fin == "MAX_TOKENS" else fin.lower()), c.get("groundingMetadata")


def _openai(url, key_env, model, prompt, system, max_tokens, temp):
    k = os.environ.get(key_env)
    if not k:
        raise AIError(f"no {key_env}")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    d = http(url, {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": temp},
             {"Authorization": f"Bearer {k}", "HTTP-Referer": "https://github.com", "X-Title": "studio"}, timeout=360)
    if "choices" not in d:
        raise AIError(str(d.get("error", d))[:200])
    ch = d["choices"][0]
    return (ch.get("message") or {}).get("content") or "", ch.get("finish_reason") or "", None


def gemini_models():
    """Configured Gemini model first; if Google retired it, discover current Flash models from the API."""
    pref = [m.strip() for m in os.environ.get("GEMINI_MODELS", "gemini-2.5-flash").split(",") if m.strip()]
    return pref + [m for m in _GEMINI_FOUND if m not in pref]


def _discover_gemini():
    if _GEMINI_FOUND or not os.environ.get("GEMINI_API_KEY"):
        return
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        names = []
        for m in d.get("models", []):
            n = m["name"].split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" in n and not re.search(r"image|tts|live|audio|embed|exp|thinking|8b", n):
                names.append(n)
        def ver(n):
            v = re.findall(r"(\d+(?:\.\d+)?)", n)
            return (float(v[0]) if v else 0, "lite" not in n, "preview" not in n)
        _GEMINI_FOUND.extend(sorted(set(names), key=ver, reverse=True)[:3])
    except Exception as e:
        print("   gemini discovery failed:", _scrub(e))


def openrouter_free():
    if _OR_FREE:
        return _OR_FREE
    pref = [m.strip() for m in os.environ.get("OPENROUTER_MODELS", "").split(",") if m.strip()]
    good = ["nemotron-3-super", "minimax", "nemotron-3-ultra", "deepseek", "qwen", "gemma-4-31b", "glm", "kimi", "llama"]
    found = []
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://openrouter.ai/api/v1/models", headers={"User-Agent": "studio"}), timeout=30).read())
        for m in d.get("data", []):
            i = m["id"]
            mx = (m.get("top_provider") or {}).get("max_completion_tokens") or 0
            if i.endswith(":free") and (m.get("context_length") or 0) >= 60000 and mx >= 16000 \
                    and not re.search(r"safety|guard|code|omni|-vl|nano|mini-|lfm|2\.6b|inkling", i):
                found.append(i)
        found.sort(key=lambda i: next((k for k, g in enumerate(good) if g in i), 99))
    except Exception as e:
        print("   openrouter model list failed:", _scrub(e))
    _OR_FREE.extend(pref + [f for f in found if f not in pref][:3] + ["openrouter/free"])
    return _OR_FREE


def chain(max_tokens):
    """(label, provider, callable(prompt, system, temp)) in preference order."""
    out = []
    for m in gemini_models():
        out.append((f"gemini:{m}", "gemini", lambda p, s, t, m=m: _gemini(m, p, s, min(max_tokens * 2, 65000), t)))
    gq = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    out.append((f"groq:{gq}", "groq", lambda p, s, t: _openai("https://api.groq.com/openai/v1/chat/completions",
                                                            "GROQ_API_KEY", gq, p, s, min(max_tokens, 60000), t)))
    # Free keys added 2026-10-22 (skipped when the secret is missing)
    for lab, url, kenv, m in (("mistral", "https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY", "mistral-medium-latest"),
                              ("nara", "https://router.bynara.id/v1/chat/completions", "NARA_API_KEY", "minimax-m3.1-flash:free"),
                              ("itera", "https://api.iteracompute.com/v1/chat/completions", "ITERA_API_KEY", "qwen/qwen3.8-27b"),
                              ("mistral-small", "https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY", "mistral-small-latest"),
                              ("nara-nemotron", "https://router.bynara.id/v1/chat/completions", "NARA_API_KEY", "nemotron-3.5-lightning-free")):
        if os.environ.get(kenv):
            out.append((f"{lab}:{m}", lab, lambda p, s, t, url=url, kenv=kenv, m=m: _openai(url, kenv, m, p, s, min(max_tokens, 16000), t)))
    for m in openrouter_free():
        out.append((f"openrouter:{m}", "openrouter:" + m, lambda p, s, t, m=m: _openai(
            "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", m, p, s, min(max_tokens, 60000), t)))
    out.append(("gemini:gemini-2.5-flash-lite", "gemini-lite",
                lambda p, s, t: _gemini("gemini-2.5-flash-lite", p, s, min(max_tokens * 2, 65000), t)))
    out.append(("groq:openai/gpt-oss-20b", "groq20", lambda p, s, t: _openai(
        "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "openai/gpt-oss-20b", p, s, min(max_tokens, 60000), t)))
    return out


def ask(prompt, system="", max_tokens=16000, check=None, temp=0.5, rounds=2, label="ai"):
    """Return check(text) (or text) from the first model whose answer passes. Raises AIError."""
    last = "no provider tried"
    for rnd in range(rounds):
        for name, prov, fn in chain(max_tokens):
            if _COOL.get(prov, 0) > time.time():
                continue
            if _left() < 60:
                raise AIError(f"out of time ({last})")
            t0 = time.time()
            try:
                text, fin, _ = fn(prompt, system, temp)
                if fin in ("length", "max_tokens"):
                    raise AIError("answer was cut off (too long for this model)")
                if not text or not text.strip():
                    raise AIError("empty answer")
                res = check(text) if check else text
                LAST["model"] = name
                print(f"   [{label}] {name} ok in {time.time()-t0:.0f}s, {len(text)} chars")
                return res
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode()[:200]
                except Exception:
                    pass
                last = f"{name}: HTTP {e.code} {body}"
                if e.code in (429, 503, 529, 500, 502):
                    _COOL[prov] = time.time() + (90 if e.code == 429 else 45)
                elif e.code == 404 and prov == "gemini":
                    _discover_gemini()
                elif e.code in (400, 401, 403) and prov.startswith("gemini"):
                    _COOL[prov] = time.time() + 3600
            except Exception as e:
                last = f"{name}: {e}"
                if "Request too large" in str(e) or "413" in str(e):
                    _COOL[prov] = time.time() + 60
            print(f"   [{label}] {_scrub(last)}")
        if rnd + 1 < rounds:
            wait = min(60, max(0, _left() - 120))
            if wait <= 0:
                break
            time.sleep(wait)
    raise AIError(_scrub(last))


def grounded(prompt, system=""):
    """Gemini + Google Search grounding (free tier allows it on Flash). Returns (text, sources) or (None, [])."""
    if not os.environ.get("GEMINI_API_KEY"):
        return None, []
    for m in gemini_models()[:2]:
        try:
            text, fin, meta = _gemini(m, prompt, system, 16000, 0.2, search=True)
            if not text:
                continue
            src = []
            for ch in (meta or {}).get("groundingChunks", []):
                w = ch.get("web") or {}
                if w.get("title"):
                    src.append(w["title"])
            if src:
                return text, sorted(set(src))[:6]
            return None, []
        except Exception as e:
            print("   grounding unavailable:", _scrub(e))
    return None, []


# ---------------- answer checkers ----------------
def strip_fences(t):
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def html_doc(t):
    """Full HTML document; must contain </html> (otherwise the model was cut off)."""
    t = strip_fences(t)
    i = min([x for x in (t.lower().find("<!doctype"), t.lower().find("<html")) if x >= 0] or [-1])
    j = t.lower().rfind("</html>")
    if i < 0 or j < 0 or j < i:
        raise AIError("HTML incomplete (no </html>)")
    doc = t[i:j + 7]
    if len(doc) < 800 or "<body" not in doc.lower():
        raise AIError("HTML too short")
    return doc


def html_fragment(t, end="<!--END-->"):
    t = strip_fences(t)
    if end not in t:
        raise AIError("fragment incomplete (no END marker)")
    t = t[:t.rfind(end)]
    t = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|</?(html|head|body)[^>]*>|<!doctype[^>]*>", "", t)
    if len(t) < 400:
        raise AIError("fragment too short")
    return t.strip()


def json_obj(t):
    t = strip_fences(t)
    i = min([x for x in (t.find("{"), t.find("[")) if x >= 0] or [-1])
    if i < 0:
        raise AIError("no JSON in answer")
    j = max(t.rfind("}"), t.rfind("]"))
    s = t[i:j + 1]
    try:
        return json.loads(s)
    except Exception:
        s2 = re.sub(r",\s*([}\]])", r"\1", s)              # trailing commas
        s2 = re.sub(r"(?<!\\)\\(?![\"\\/bfnrtu])", r"\\\\", s2)  # lone backslashes (LaTeX)
        try:
            return json.loads(s2)
        except Exception as e:
            raise AIError(f"bad JSON: {e}")
