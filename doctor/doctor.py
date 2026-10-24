#!/usr/bin/env python3
"""Repo Doctor: checks every GitHub repo Swanix owns, finds what is broken, writes a fix with free AI,
tests it, merges it when the checks pass, and sends one plain-English report on Telegram.

Safety: never force-pushes, never rewrites history, never deletes anything except its own doctor/* branches
after a merge, never touches repo settings/secrets/visibility, never edits money/trade/wallet code, caps the
size of every fix, and stops at once if doctor/OFF exists (or DOCTOR_OFF=true). Free AI only.
"""
import json, os, re, sys, time, shutil, base64, pathlib, subprocess, urllib.request, urllib.error, urllib.parse, datetime

T0 = time.time()
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CFG = json.load(open(HERE / "config.json"))
OWNER = CFG["owner"]
TOKEN = os.environ.get("DOCTOR_GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
SELF_REPO = os.environ.get("GITHUB_REPOSITORY", f"{OWNER}/swanix-autopilot")
SELF_RUN = os.environ.get("GITHUB_RUN_ID", "")
DRY = os.environ.get("DRY_RUN", "false").lower() == "true"
ONLY = [r.strip().lower() for r in os.environ.get("ONLY_REPOS", "").split(",") if r.strip()]
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
NOW = datetime.datetime.now(datetime.timezone.utc)
TODAY = NOW.astimezone(IST).strftime("%Y-%m-%d")
WORK = pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "doctor-work"
LLM_CALLS = 0

# ---------------------------------------------------------------- secrets never leave the runner
SECRET_VALUES = [v for k, v in os.environ.items()
                 if v and len(v) >= 8 and re.search(r"TOKEN|KEY|SECRET|PASS|PIN|COOKIE|CHAT_ID", k)]
SECRET_PATTERNS = [   # (regex, replacement)
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "***"), (r"github_pat_[A-Za-z0-9_]{20,}", "***"), (r"sk-[A-Za-z0-9_\-]{16,}", "***"),
    (r"AIza[0-9A-Za-z_\-]{30,}", "***"), (r"gsk_[A-Za-z0-9]{20,}", "***"), (r"xox[abpr]-[A-Za-z0-9\-]{10,}", "***"),
    (r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}", "***"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "***"),
    (r"(?i)\b(bearer|authorization:)\s+[A-Za-z0-9._\-]{12,}", r"\1 ***"),
    (r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY)[A-Z0-9_]*)(\s*[=:]\s*)['\"]?[A-Za-z0-9_\-./+=]{12,}['\"]?", r"\1\2***"),
    (r"x-access-token:[^@\s]+@", "x-access-token:***@"),
]


def redact(t):
    t = t or ""
    for v in SECRET_VALUES:
        t = t.replace(v, "***")
    for p, rep in SECRET_PATTERNS:
        t = re.sub(p, rep, t)
    return t


def log(*a):
    print(redact(" ".join(str(x) for x in a)), flush=True)


def minutes_used():
    return (time.time() - T0) / 60


# ---------------------------------------------------------------- GitHub API
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def api(method, path, body=None, raw=False, ok404=False):
    url = path if path.startswith("http") else "https://api.github.com" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "swanix-repo-doctor", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(3):
        try:
            with opener.open(req, timeout=60) as r:
                b = r.read()
                if raw:
                    return b.decode("utf-8", "replace")
                return json.loads(b) if b else {}
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                with urllib.request.urlopen(e.headers["Location"], timeout=60) as r2:   # logs live on blob storage; no auth header there
                    b = r2.read()
                    return b.decode("utf-8", "replace") if raw else json.loads(b)
            if e.code == 404 and ok404:
                return None
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(5); continue
            msg = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]} -> {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(5); continue
            raise


def paged(path, key=None, limit=300):
    out, page = [], 1
    while len(out) < limit:
        sep = "&" if "?" in path else "?"
        d = api("GET", f"{path}{sep}per_page=100&page={page}")
        items = d.get(key, []) if key else d
        out += items
        if len(items) < 100:
            break
        page += 1
    return out


# ---------------------------------------------------------------- shell helpers
def sh(cmd, cwd=None, timeout=600, env=None):
    clean = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"), "CI": "true", "LANG": "C.UTF-8",
             "npm_config_fund": "false", "npm_config_audit": "false", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
             "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        clean.update(env)
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=clean)
        return p.returncode, redact(p.stdout + p.stderr)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, redact(out + f"\n[timed out after {timeout}s]")


def git(cwd, *args, timeout=300):
    q = " ".join(_quote(a) for a in args)
    return sh(f"git {q}", cwd=cwd, timeout=timeout, env={"GIT_TERMINAL_PROMPT": "0"})


def _quote(a):
    return "'" + str(a).replace("'", "'\\''") + "'"


def tail(t, n=120):
    lines = (t or "").splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------- free AI (never Oxion)
BRAIN_LLM = "https://swanix-brain.swanixjobs.workers.dev/llm"


def _post(url, body, headers, timeout=240):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json",
                                                                  "User-Agent": "swanix-repo-doctor", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _chat(url, key, model, prompt, max_tokens=12000):
    d = _post(url, {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                    "temperature": 0.2}, {"Authorization": f"Bearer {key}"})
    return d["choices"][0]["message"]["content"]


def _gemini(prompt):
    k = os.environ["GEMINI_API_KEY"]
    d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('DOCTOR_GEMINI_MODEL', 'gemini-flash-latest')}:generateContent",
              {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 16000, "temperature": 0.2}},
              {"x-goog-api-key": k})
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])


def providers():
    e, out = os.environ, []
    if e.get("TELEGRAM_BOT_TOKEN"):   # Swanix Brain's free-model router (Gemini, Groq, Cerebras, Mistral, NVIDIA...), authed by the bot token
        out.append(("brain", lambda p: _chat(BRAIN_LLM, e["TELEGRAM_BOT_TOKEN"], "auto|hard", p)))
    if e.get("GEMINI_API_KEY"):
        out.append(("gemini", _gemini))
    if e.get("GROQ_API_KEY"):
        out.append(("groq", lambda p: _chat("https://api.groq.com/openai/v1/chat/completions", e["GROQ_API_KEY"], "openai/gpt-oss-120b", p, 8000)))
    if e.get("TELEGRAM_BOT_TOKEN"):
        out.append(("cerebras", lambda p: _chat(BRAIN_LLM, e["TELEGRAM_BOT_TOKEN"], "cerebras|gpt-oss-120b", p)))
        out.append(("nvidia", lambda p: _chat(BRAIN_LLM, e["TELEGRAM_BOT_TOKEN"], "nvidia|z-ai/glm-5.3-flash", p)))
        out.append(("mistral", lambda p: _chat(BRAIN_LLM, e["TELEGRAM_BOT_TOKEN"], "mistral|mistral-medium-latest", p)))
    if e.get("OPENROUTER_API_KEY"):
        out.append(("openrouter", lambda p: _chat("https://openrouter.ai/api/v1/chat/completions", e["OPENROUTER_API_KEY"], "openrouter/free", p)))
    return [(n, f) for n, f in out if "oxion" not in n.lower()]


def ask_json(prompt):
    """Ask the free models in order; return (parsed JSON, provider)."""
    global LLM_CALLS
    prompt = redact(prompt)
    last, errs = "no AI keys available", []
    for name, fn in providers() + [("wait", None)] + providers():
        if fn is None:
            time.sleep(60); continue
        if LLM_CALLS >= CFG["max_llm_calls"]:
            raise RuntimeError("AI call budget for this run used up")
        LLM_CALLS += 1
        try:
            out = fn(prompt) or ""
            m = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", out) or re.search(r"(\{[\s\S]*\})", out)
            if not m:
                last = f"{name}: no JSON in reply"; continue
            return json.loads(m.group(1)), name
        except Exception as ex:
            last = f"{name}: {str(ex)[:100]}"
            errs.append(last)
            log("   AI", last)
    raise RuntimeError("all free AI providers were busy or failed (" + "; ".join(errs[-5:])[:400] + ")")


# ---------------------------------------------------------------- rules
def hold_for(repo, path):
    for h in CFG.get("hold_paths", []):
        if h["repo"].lower() == repo.lower() and path.startswith(h["path"]) and TODAY <= h.get("until", "9999"):
            return h
    return None


def forbidden(repo, path):
    p = path.lower()
    if path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
        return "outside the repo"
    for bad in CFG["never_edit"]:
        if bad.lower() in p:
            return f"protected ({bad})"
    h = hold_for(repo, path)
    if h:
        return f"on hold: {h['why']}"
    return None


def rerun_allowed(wf_path, wf_name):
    s = (wf_path + " " + wf_name).lower()
    return not any(w in s for w in CFG["never_rerun"])


def ignored(rel):
    return any(part in CFG["ignore_dirs"] for part in pathlib.PurePosixPath(rel).parts)


# ---------------------------------------------------------------- workflow failures
def failing_runs(full, branch):
    """Latest finished run of every active workflow on the default branch; return the failing ones."""
    try:
        wfs = api("GET", f"/repos/{full}/actions/workflows?per_page=100").get("workflows", [])
    except Exception as ex:
        log("  workflows:", ex); return [], 0
    bad, n = [], 0
    for wf in wfs:
        if wf.get("state") != "active" or "repo-doctor" in (wf.get("path") or ""):
            continue
        try:
            runs = api("GET", f"/repos/{full}/actions/workflows/{wf['id']}/runs?branch={urllib.parse.quote(branch)}&per_page=10").get("workflow_runs", [])
        except Exception:
            continue
        done = [r for r in runs if r["status"] == "completed" and r["conclusion"] not in ("cancelled", "skipped", "neutral", None)]
        if not done:
            continue
        n += 1
        r = done[0]
        age = (NOW - datetime.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))).days
        if r["conclusion"] in ("failure", "timed_out", "startup_failure", "action_required") and age <= 30:
            r.setdefault("path", wf.get("path"))
            bad.append(r)
    return bad, n


def main_ci_green(full, branch):
    """True when the repo's own CI ran on the latest default-branch commit and everything passed."""
    try:
        sha = api("GET", f"/repos/{full}/commits/{urllib.parse.quote(branch)}")["sha"]
        cr = api("GET", f"/repos/{full}/commits/{sha}/check-runs?per_page=50").get("check_runs", [])
    except Exception:
        return False
    return bool(cr) and all(c["status"] == "completed" and c["conclusion"] in ("success", "skipped", "neutral") for c in cr)


def run_logs(full, run):
    out = []
    try:
        jobs = api("GET", f"/repos/{full}/actions/runs/{run['id']}/jobs?per_page=50").get("jobs", [])
    except Exception as ex:
        return f"(could not list jobs: {ex})"
    failed = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out")][:2]
    if not failed:
        return f"(run concluded '{run['conclusion']}' with no failed job; this usually means the workflow file itself is invalid or a required input/permission is missing)"
    for j in failed:
        steps = [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"]
        try:
            txt = api("GET", f"/repos/{full}/actions/jobs/{j['id']}/logs", raw=True)
        except Exception as ex:
            txt = f"(logs unavailable: {ex})"
        txt = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", txt, flags=re.M)
        lines = txt.splitlines()
        keep = set(range(max(0, len(lines) - 90), len(lines)))
        for i, l in enumerate(lines):
            if "##[error]" in l or re.search(r"Traceback|Error:|error:|ERR!|FAILED|Exception", l):
                keep |= set(range(max(0, i - 25), min(len(lines), i + 6)))
        snippet = "\n".join(lines[i] for i in sorted(keep))[-9000:]
        out.append(f"--- job '{j['name']}' failed at step(s): {', '.join(steps) or '?'}\n{snippet}")
    return redact("\n".join(out))


# ---------------------------------------------------------------- local health checks
def tracked(repo_dir):
    rc, out = git(repo_dir, "ls-files")
    return [l for l in out.splitlines() if l and not ignored(l)]


def health(repo_dir, files):
    """Return list of problems: {check, cmd, output}. Runs with a clean env (no tokens)."""
    probs, ran = [], []
    # workflow files must parse
    try:
        import yaml
        for f in files:
            if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")):
                try:
                    y = yaml.safe_load(open(os.path.join(repo_dir, f)))
                    if not isinstance(y, dict) or "jobs" not in y:
                        probs.append({"check": f"workflow file {f}", "output": "file has no 'jobs:' section"})
                except Exception as ex:
                    probs.append({"check": f"workflow file {f}", "output": str(ex)[:1500]})
        ran.append("workflow files")
    except ImportError:
        pass
    # json files must parse
    for f in files:
        if f.endswith(".json") and os.path.getsize(os.path.join(repo_dir, f)) < 2_000_000:
            try:
                json.load(open(os.path.join(repo_dir, f), encoding="utf-8"))
            except Exception as ex:
                if not f.endswith(("tsconfig.json", "jsconfig.json")) and ".vscode/" not in f:   # these allow comments
                    probs.append({"check": f"JSON file {f}", "output": str(ex)[:500]})
    # python syntax
    pys = [f for f in files if f.endswith(".py")]
    if pys:
        ran.append("python syntax")
        for f in pys:
            try:
                compile(open(os.path.join(repo_dir, f), encoding="utf-8", errors="replace").read(), f, "exec")
            except SyntaxError as ex:
                probs.append({"check": f"python syntax {f}", "output": f"{f} line {ex.lineno}: {ex.msg}\n{(ex.text or '').rstrip()}"})
            except Exception:
                pass
    # python dependencies + tests, per folder with requirements
    for req in [f for f in files if os.path.basename(f) == "requirements.txt"][:4]:
        d = os.path.dirname(req) or "."
        rc, out = sh("python -m pip install --dry-run -q -r requirements.txt", cwd=os.path.join(repo_dir, d), timeout=240)
        ran.append(f"pip resolve {req}")
        if rc and rc != 124 and re.search(r"ResolutionImpossible|No matching distribution|Could not find a version|Invalid requirement", out):
            probs.append({"check": f"python requirements {req}", "cmd": "pip install -r requirements.txt", "output": tail(out, 40)})
    if any(re.match(r"(tests?/|.*test_[^/]*\.py$)", f) for f in pys) and os.path.exists(os.path.join(repo_dir, "requirements.txt")):
        rc, out = sh("python -m venv /tmp/dvenv && . /tmp/dvenv/bin/activate && pip install -q -r requirements.txt pytest && python -m pytest -q -x --maxfail=3", cwd=repo_dir, timeout=600)
        ran.append("pytest")
        if rc and rc not in (5, 124):   # 5 = no tests collected
            probs.append({"check": "python tests", "cmd": "pytest", "output": tail(out, 80)})
        shutil.rmtree("/tmp/dvenv", ignore_errors=True)
    # node projects
    for pj in [f for f in files if os.path.basename(f) == "package.json"][:3]:
        d = os.path.join(repo_dir, os.path.dirname(pj))
        try:
            pkg = json.load(open(os.path.join(repo_dir, pj)))
        except Exception:
            continue
        scripts = pkg.get("scripts") or {}
        lock = os.path.exists(os.path.join(d, "package-lock.json"))
        inst = "npm ci --no-audit --no-fund" if lock else "npm install --no-audit --no-fund --no-package-lock"
        rc, out = sh(inst + " --ignore-scripts=false", cwd=d, timeout=480)
        ran.append(f"npm install ({pj})")
        if rc and rc != 124:
            probs.append({"check": f"install packages ({pj})", "cmd": inst, "output": tail(out, 60)})
            continue
        for s in ("build", "lint", "test"):
            body = scripts.get(s, "")
            if not body or "no test specified" in body or "watch" in body:
                continue
            rc, out = sh(f"npm run {s} --silent", cwd=d, timeout=480, env={"NODE_OPTIONS": "--max-old-space-size=4096"})
            ran.append(f"npm run {s} ({pj})")
            if rc and rc != 124:
                probs.append({"check": f"npm run {s} ({pj})", "cmd": f"npm run {s}", "output": tail(out, 80)})
        shutil.rmtree(os.path.join(d, "node_modules"), ignore_errors=True)
    git(repo_dir, "reset", "-q", "--hard")      # throw away build output; committed fixes stay
    git(repo_dir, "clean", "-fdxq")
    return probs, ran


# ---------------------------------------------------------------- AI fix
def related_files(repo_dir, files, text, extra):
    picks = []
    for f in sorted(files, key=len, reverse=True):
        base = os.path.basename(f)
        if (f in text or (len(base) > 5 and base in text)) and f not in picks:
            picks.append(f)
    for f in extra:
        if f in files and f not in picks:
            picks.append(f)
    for f in ("package.json", "requirements.txt", "pyproject.toml"):
        if f in files and f not in picks:
            picks.append(f)
    out, total = [], 0
    for f in picks[:12]:
        if re.search(r"(^|/)\.env|secret|\.pem$|id_rsa|credentials", f, re.I):
            continue
        p = os.path.join(repo_dir, f)
        try:
            body = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if len(body) > 8000:
            body = body[:8000] + "\n…(file cut here)…"
        if total + len(body) > 26000:
            break
        total += len(body)
        out.append(f"===== FILE: {f}\n{body}")
    return redact("\n".join(out))


FIX_PROMPT = """You are Repo Doctor, fixing a broken GitHub repo for its owner (he cannot code).
Repo: {repo}
What is failing:
{problems}

File list (partial): {tree}

Relevant files:
{files}

{history}
Rules:
- Make the SMALLEST change that fixes the root cause. At most {maxf} files and {maxl} changed lines.
- Never touch secrets, .env files, money/trading/wallet logic, or these protected paths: {protected}.
- If the real cause is NOT the code (missing secret/API key, expired login, rate limit, quota, outside service down,
  billing, needs a human decision), do not edit anything: set "fixable": false and say what the owner must do.
- Edits use exact search/replace: "find" must be copied EXACTLY from the file (enough lines to be unique).
  To create a new file use "find": "" and put the whole file in "replace".
Reply with ONLY this JSON:
{{"cause": "one or two plain-English sentences a non-coder understands",
  "fixable": true or false,
  "owner_action": "what he must do himself if not fixable, plain English, else empty",
  "change_summary": "plain-English summary of what you changed",
  "edits": [{{"path": "relative/path", "find": "exact old text", "replace": "new text"}}]}}"""


def apply_edits(repo, repo_dir, edits):
    touched = []
    for e in edits:
        path = (e.get("path") or "").strip()
        while path.startswith("./"):
            path = path[2:]
        why = forbidden(repo, path)
        if why:
            return None, f"the AI wanted to change {path}, which is {why}"
        full = os.path.join(repo_dir, path)
        find, rep = e.get("find", ""), e.get("replace", "")
        if not find:
            if os.path.exists(full):
                return None, f"the AI tried to overwrite {path} instead of editing it"
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            open(full, "w", encoding="utf-8").write(rep)
        else:
            if not os.path.exists(full):
                return None, f"the AI pointed at a file that does not exist ({path})"
            body = open(full, encoding="utf-8").read()
            n = body.count(find)
            if n != 1:
                return None, f"the AI's edit for {path} did not match the file exactly ({n} matches)"
            open(full, "w", encoding="utf-8").write(body.replace(find, rep, 1))
        if path not in touched:
            touched.append(path)
    git(repo_dir, "add", "-A")
    rc, num = git(repo_dir, "diff", "--cached", "--numstat")
    rows = [l.split("\t") for l in num.splitlines() if l.strip()]
    lines = sum(int(a) + int(b) for a, b, *_ in rows if a.isdigit() and b.isdigit())
    if not rows:
        return None, "the AI's edit changed nothing"
    if len(rows) > CFG["max_files_per_fix"] or lines > CFG["max_lines_per_fix"]:
        return None, f"the fix was too big ({len(rows)} files, {lines} lines; limit {CFG['max_files_per_fix']} files / {CFG['max_lines_per_fix']} lines)"
    rc, diff = git(repo_dir, "diff", "--cached")
    if redact(diff) != diff:
        return None, "the fix contained something that looks like a secret, so it was thrown away"
    return {"files": [r[2] for r in rows], "lines": lines}, None


def wait_checks(full, sha, minutes=12):
    """Return 'green', 'red', or 'none' (no CI on this repo)."""
    t, seen = time.time(), False
    while time.time() - t < minutes * 60:
        time.sleep(30)
        try:
            cr = api("GET", f"/repos/{full}/commits/{sha}/check-runs?per_page=50").get("check_runs", [])
        except Exception:
            cr = []
        if cr:
            seen = True
            if any(c["status"] != "completed" for c in cr):
                continue
            bad = [c for c in cr if c["conclusion"] in ("failure", "timed_out", "startup_failure", "action_required")]
            return ("red", bad) if bad else ("green", cr)
        if not seen and time.time() - t > 120:
            return "none", []
    return ("red", ["CI did not finish in time"]) if seen else ("none", [])


def wait_dispatched(full, wf_id, since, minutes=12):
    t = time.time()
    while time.time() - t < minutes * 60:
        time.sleep(30)
        try:
            runs = api("GET", f"/repos/{full}/actions/workflows/{wf_id}/runs?per_page=5").get("workflow_runs", [])
        except Exception:
            continue
        new = [r for r in runs if r["created_at"] >= since]
        if new and new[0]["status"] == "completed":
            return new[0]["conclusion"], new[0]["html_url"]
    return "still running", None


def workflow_dispatchable(repo_dir, wf_path):
    try:
        import yaml
        y = yaml.safe_load(open(os.path.join(repo_dir, wf_path)))
        on = y.get("on", y.get(True))
        if isinstance(on, str):
            return on == "workflow_dispatch"
        if isinstance(on, list):
            return "workflow_dispatch" in on
        if isinstance(on, dict) and "workflow_dispatch" in on:
            inp = (on["workflow_dispatch"] or {}).get("inputs") or {}
            return not any(v.get("required") and "default" not in v for v in inp.values() if isinstance(v, dict))
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- per repo
def doctor_repo(r):
    full, name, branch = r["full_name"], r["name"], r["default_branch"] or "main"
    res = {"repo": name, "url": r["html_url"], "status": "healthy", "notes": []}
    pushed = datetime.datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) if r.get("pushed_at") else NOW
    recent = (NOW - pushed).days < CFG["recent_days"]
    bad_runs, n_wf = failing_runs(full, branch)
    held = [x for x in bad_runs if hold_for(name, x.get("path") or "")]
    bad_runs = [x for x in bad_runs if x not in held]
    for x in held:
        res["notes"].append(f"skipped '{x['name']}' (on hold: {hold_for(name, x['path'])['why']})")
    if not recent and not bad_runs:
        res["notes"].append("no changes this week and nothing failing, so only a quick look")
        res["status"] = "healthy"
        return res
    if not r.get("size"):
        res["notes"].append("empty repo, nothing to check")
        return res
    open_prs = [p for p in api("GET", f"/repos/{full}/pulls?state=open&per_page=50") if p["head"]["ref"].startswith("doctor/")]
    # clone
    d = WORK / name
    shutil.rmtree(d, ignore_errors=True)
    rc, out = sh(f"git clone -q --depth 30 --branch {_quote(branch)} https://x-access-token:{TOKEN}@github.com/{full}.git {_quote(str(d))}", timeout=300,
                 env={"GIT_TERMINAL_PROMPT": "0"})
    if rc:
        res.update(status="attention", cause="I couldn't download this repo to check it.", detail=tail(out, 5))
        return res
    git(d, "config", "user.name", "Repo Doctor"); git(d, "config", "user.email", "doctor@users.noreply.github.com")
    files = tracked(d)
    problems, ran = health(d, files)
    for run in bad_runs:
        problems.append({"check": f"GitHub Actions workflow '{run['name']}' ({run.get('path')}) failed on {run['updated_at'][:10]}",
                         "run": run, "output": run_logs(full, run)})
    res["checks"] = ran
    env_checks = ("npm run", "install packages", "python tests", "python requirements")
    if any(p["check"].startswith(env_checks) for p in problems) and main_ci_green(full, branch):
        dropped = [p["check"] for p in problems if p["check"].startswith(env_checks)]
        problems = [p for p in problems if not p["check"].startswith(env_checks)]
        res["notes"].append("my own build/test run failed (" + ", ".join(dropped) + ") but your repo's CI passes on the latest commit, so I trust your CI and changed nothing")
    if not problems:
        res["notes"].append(f"checked: {', '.join(ran) or 'workflow runs'}; all fine")
        return res
    res["problems"] = [p["check"] for p in problems]
    if open_prs:
        res.update(status="attention", cause=f"Still has problems, and my earlier fix is waiting in PR #{open_prs[0]['number']} (it didn't pass checks, so I didn't merge it).",
                   pr=open_prs[0]["html_url"])
        return res
    if name.lower() in [x.lower() for x in CFG.get("report_only_repos", [])] or DRY:
        mode = "dry run" if DRY else "this repo is set to report-only (it deals with crypto mining), so I don't change it"
        try:
            j, _ = ask_json(FIX_PROMPT.format(repo=full, problems=_problem_text(problems), tree=", ".join(files[:150]),
                                              files=related_files(d, files, _problem_text(problems), [p['run'].get('path') for p in problems if p.get('run')]),
                                              history="", maxf=CFG["max_files_per_fix"], maxl=CFG["max_lines_per_fix"], protected=", ".join(CFG["never_edit"])))
            res.update(status="attention", cause=j.get("cause", "?"),
                       owner_action=((j.get("owner_action") or "").strip() + f" (No change made: {mode}.)").strip())
        except Exception as ex:
            res.update(status="attention", cause=f"Problems found ({mode}); AI diagnosis failed: {ex}")
        return res
    if minutes_used() > CFG["time_budget_minutes"]:
        res.update(status="attention", cause="Problems found, but today's time budget ran out before I could fix them. I'll try again tomorrow.")
        return res
    return fix_repo(r, d, files, problems, res)


def _problem_text(problems):
    return "\n\n".join(f"### {p['check']}\n{p.get('cmd', '')}\n{(p.get('output') or '')[-4000:]}" for p in problems)[:10000]


def fix_repo(r, d, files, problems, res):
    full, name, branch = r["full_name"], r["name"], r["default_branch"] or "main"
    history, slug = "", re.sub(r"[^a-z0-9]+", "-", problems[0]["check"].lower())[:30].strip("-")
    newbranch = f"doctor/{TODAY}-{slug or 'fix'}"
    wf_paths = [p["run"].get("path") for p in problems if p.get("run")]
    local_only = [p for p in problems if not p.get("run")]
    summary, cause, applied = "", "", None
    for rnd in range(1, CFG["max_fix_rounds"] + 1):
        ptxt = _problem_text(problems)
        try:
            j, via = ask_json(FIX_PROMPT.format(repo=full, problems=ptxt, tree=", ".join(files[:200]),
                                                files=related_files(d, files, ptxt, wf_paths), history=history,
                                                maxf=CFG["max_files_per_fix"], maxl=CFG["max_lines_per_fix"],
                                                protected=", ".join(CFG["never_edit"])))
        except Exception as ex:
            res.update(status="attention", cause=f"Found problems but couldn't reach the free AI to fix them ({ex}).")
            return res
        cause = j.get("cause") or cause
        if not j.get("fixable") or not j.get("edits"):
            res.update(status="attention", cause=cause or "Not something code can fix.",
                       owner_action=j.get("owner_action") or "")
            return res
        if rnd == 1:
            git(d, "checkout", "-q", "-b", newbranch)
        applied, err = apply_edits(name, d, j["edits"])
        if not applied:
            git(d, "reset", "-q", "--hard")
            history = f"Your previous attempt was rejected: {err}. Try again, following the rules exactly."
            log(f"  round {rnd}: rejected: {err}")
            if rnd == CFG["max_fix_rounds"]:
                res.update(status="attention", cause=cause, owner_action=f"I couldn't make a safe fix: {err}.")
                return res
            continue
        summary = j.get("change_summary") or summary
        git(d, "commit", "-q", "-m", f"Repo Doctor: {summary[:60] or 'fix'}\n\n{cause}\n\nAutomated fix by Repo Doctor (round {rnd}, via {via}).")
        # re-check locally what we can
        new_probs, _ = health(d, tracked(d))
        still = [p for p in new_probs if any(p["check"] == q["check"] for q in local_only)] if local_only else new_probs
        if still and rnd < CFG["max_fix_rounds"]:
            history = "Your previous fix was applied but these checks still fail:\n" + _problem_text(still)
            problems = still + [p for p in problems if p.get("run")]
            log(f"  round {rnd}: local checks still failing; retrying")
            continue
        res["local_ok"] = not still
        break
    # push branch + PR
    rc, out = git(d, "push", "-q", "origin", f"HEAD:refs/heads/{newbranch}")
    if rc:
        res.update(status="attention", cause=cause, owner_action="I wrote a fix but GitHub refused my upload (" + tail(out, 2) + ")."
                   + (" The fix changes a workflow file, which needs extra permission." if "workflow" in out else ""))
        return res
    files_changed = ", ".join(applied["files"]) if applied else "?"
    body = (f"**What was wrong:** {cause}\n\n**What I changed:** {summary}\n\n**Files:** {files_changed}\n\n"
            f"**Problems seen:**\n" + "\n".join(f"- {p['check']}" for p in problems) +
            f"\n\n_Opened automatically by Repo Doctor (run {SELF_RUN}). It merges this only if checks pass._")
    try:
        pr = api("POST", f"/repos/{full}/pulls", {"title": f"Repo Doctor: {summary[:70] or 'automatic fix'}",
                                                  "head": newbranch, "base": branch, "body": redact(body)})
    except Exception as ex:
        res.update(status="attention", cause=cause, owner_action=f"Fix is on branch {newbranch} but I couldn't open the PR ({ex}).")
        return res
    res["pr"] = pr["html_url"]
    res["change"] = summary
    res["cause"] = cause
    sha = pr["head"]["sha"]
    ci, info = wait_checks(full, sha)
    rounds_used = rnd
    while ci == "red" and rounds_used < CFG["max_fix_rounds"] and minutes_used() < CFG["time_budget_minutes"]:
        rounds_used += 1
        ci_logs = ""
        for c in [x for x in info if isinstance(x, dict)][:2]:
            try:
                ci_logs += f"--- check '{c['name']}'\n" + run_logs(full, {"id": int(c["details_url"].split("/runs/")[1].split("/")[0]), "conclusion": "failure"})
            except Exception:
                pass
        try:
            j, via = ask_json(FIX_PROMPT.format(repo=full, problems=_problem_text(problems), tree=", ".join(files[:200]),
                                                files=related_files(d, tracked(d), ci_logs + _problem_text(problems), wf_paths),
                                                history="Your previous fix was pushed, but the repo's CI failed on it:\n" + ci_logs[-5000:] + "\nFix that too (edits apply on top of your previous fix).",
                                                maxf=CFG["max_files_per_fix"], maxl=CFG["max_lines_per_fix"], protected=", ".join(CFG["never_edit"])))
        except Exception:
            break
        if not j.get("fixable") or not j.get("edits"):
            break
        more, err = apply_edits(name, d, j["edits"])
        if not more:
            git(d, "reset", "-q", "--hard"); break
        git(d, "commit", "-q", "-m", f"Repo Doctor: follow-up fix for CI (round {rounds_used}, via {via})")
        rc, out = git(d, "push", "-q", "origin", f"HEAD:refs/heads/{newbranch}")
        if rc:
            break
        summary = (summary + " Then: " + (j.get("change_summary") or "")).strip()
        res["change"] = summary
        rc, sha = git(d, "rev-parse", "HEAD"); sha = sha.strip()
        ci, info = wait_checks(full, sha)
    green = ci == "green" or (ci == "none" and res.get("local_ok", False))
    if not green:
        why = ("the repo's own checks failed on my fix" if ci == "red" else "my own re-check still failed")
        res.update(status="attention", owner_action=f"I left the fix open in PR #{pr['number']} instead of merging, because {why}.")
        return res
    try:
        api("PUT", f"/repos/{full}/pulls/{pr['number']}/merge", {"merge_method": "squash", "sha": sha,
                                                                  "commit_title": f"Repo Doctor: {summary[:70] or 'fix'} (#{pr['number']})"})
    except Exception as ex:
        res.update(status="attention", owner_action=f"Checks passed but GitHub wouldn't merge PR #{pr['number']} ({str(ex)[:120]}).")
        return res
    try:
        api("DELETE", f"/repos/{full}/git/refs/heads/{newbranch}")   # only ever its own doctor/* branch
    except Exception:
        pass
    res["status"] = "fixed"
    # re-run the workflows that were failing, when it is safe to do so
    for p in problems:
        run = p.get("run")
        if not run:
            continue
        wf = run.get("path") or ""
        if not rerun_allowed(wf, run["name"]) or not workflow_dispatchable(d, wf):
            res["notes"].append(f"'{run['name']}' will confirm the fix on its next normal run (I don't start it myself)")
            continue
        since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            api("POST", f"/repos/{full}/actions/workflows/{run['workflow_id']}/dispatches", {"ref": branch})
            concl, url = wait_dispatched(full, run["workflow_id"], since)
            res["notes"].append(f"re-ran '{run['name']}': {concl}")
            if concl not in ("success", "still running"):
                res["status"] = "attention"
                res["owner_action"] = f"I merged a fix, but '{run['name']}' still fails when re-run. I'll look again tomorrow."
        except Exception as ex:
            res["notes"].append(f"couldn't re-run '{run['name']}' ({str(ex)[:80]})")
    return res


# ---------------------------------------------------------------- report
def report(results, skipped):
    fixed = [r for r in results if r["status"] == "fixed"]
    att = [r for r in results if r["status"] == "attention"]
    ok = [r for r in results if r["status"] == "healthy"]
    head = f"🩺 Repo Doctor · {TODAY}\n{len(results)} repos checked: {len(ok)} healthy, {len(fixed)} fixed, {len(att)} need you"
    parts = [head, ""]
    if fixed:
        parts.append("✅ FIXED")
        for r in fixed:
            parts.append(f"• {r['repo']}: {r.get('cause', '')}\n  What I changed: {r.get('change', '')}\n  {r.get('pr', '')}"
                         + ("".join(f"\n  {n}" for n in r["notes"] if n.startswith("re-ran") or "next normal run" in n)))
        parts.append("")
    if att:
        parts.append("⚠️ NEEDS ATTENTION")
        for r in att:
            line = f"• {r['repo']}: {r.get('cause', 'problem found')}"
            if r.get("owner_action"):
                line += f"\n  → {r['owner_action']}"
            if r.get("pr"):
                line += f"\n  {r['pr']}"
            parts.append(line)
        parts.append("")
    if ok:
        parts.append("💚 HEALTHY: " + ", ".join(r["repo"] for r in ok))
    if skipped:
        parts.append("⏭ Skipped: " + ", ".join(skipped))
    parts.append(f"\n({round(minutes_used())} min, {LLM_CALLS} free-AI calls. /doctor off stops me, /doctor now runs me.)")
    msg = redact("\n".join(parts))
    rep = ROOT / "doctor" / "reports" / f"{TODAY}.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    md = [f"# Repo Doctor report {TODAY}", "", msg.replace("\n• ", "\n- "), "", "## Details", ""]
    for r in results:
        md.append(f"### {r['repo']} — {r['status']}")
        for k in ("cause", "change", "owner_action", "pr"):
            if r.get(k):
                md.append(f"- {k.replace('_', ' ')}: {r[k]}")
        for p in r.get("problems", []):
            md.append(f"- problem: {p}")
        for n in r.get("notes", []):
            md.append(f"- note: {n}")
        if r.get("checks"):
            md.append(f"- checks run: {', '.join(r['checks'])}")
        md.append("")
    with open(rep, "a" if rep.exists() else "w") as f:
        f.write(redact("\n".join(md)) + "\n")
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    sent = False
    if tok and chat:
        for i in range(0, len(msg), 3900):
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg[i:i + 3900], "disable_web_page_preview": "true"}).encode()
            try:
                urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30).read(); sent = True
            except Exception as ex:
                log("telegram failed:", ex)
    print(msg)
    json.dump({"date": TODAY, "telegram_sent": sent, "results": results}, open(HERE / "last_run.json", "w"), indent=1, default=str)
    return sent


def main():
    if (HERE / "OFF").exists() or os.environ.get("DOCTOR_OFF", "").lower() == "true":
        print("Repo Doctor is switched off (doctor/OFF or DOCTOR_OFF). Nothing done."); return
    if not TOKEN:
        print("no GitHub token"); sys.exit(1)
    WORK.mkdir(parents=True, exist_ok=True)
    me = api("GET", "/user").get("login", "")
    repos = paged("/user/repos?affiliation=owner&sort=pushed")
    if not repos and me.lower() != OWNER.lower():   # fallback: the plain Actions token can only see this repo
        repos = [api("GET", f"/repos/{SELF_REPO}")]
    results, skipped = [], []
    for r in repos:
        if r["owner"]["login"].lower() != OWNER.lower():
            continue
        if r.get("archived") or r.get("fork"):
            skipped.append(f"{r['name']} ({'archived' if r.get('archived') else 'fork'})"); continue
        if r["name"] in CFG["skip_repos"] or (ONLY and r["name"].lower() not in ONLY):
            continue
        log(f"== {r['full_name']}")
        try:
            res = doctor_repo(r)
        except Exception as ex:
            res = {"repo": r["name"], "status": "attention", "cause": f"The doctor itself hit an error on this repo: {str(ex)[:200]}", "notes": []}
        log("  ->", res["status"], res.get("cause", ""))
        results.append(res)
        shutil.rmtree(WORK / r["name"], ignore_errors=True)
    report(results, skipped)


if __name__ == "__main__":
    main()
