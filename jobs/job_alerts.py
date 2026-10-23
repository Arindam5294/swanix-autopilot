#!/usr/bin/env python3
"""Job alerts (no applying). Pulls free public job feeds, keeps new matches, sends a digest.
Edit KEYWORDS to taste. State lives in jobs/seen.json (committed back by the workflow)."""
import json, os, re, sys, html, pathlib, urllib.request
import xml.etree.ElementTree as ET
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
from notify import telegram

KEYWORDS = {
    "Web3 community": ["community manager", "community moderator", "moderator", "ambassador",
                       "community lead", "community associate", "social media", "growth marketing", "growth manager", "community growth", "discord", "telegram", "kol"],
    "Agri / CAD": ["agricultural engineer", "agri engineer", "agriculture", "farm machinery", "creo",
                   "cad designer", "cad engineer", "design engineer", "mechanical design", "solidworks"],
}
EXCLUDE = ["senior director", "vp ", "head of", "10+ years", "8+ years", "principal"]
UA = {"User-Agent": "Mozilla/5.0 (swanix-autopilot job alerts)"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()

def rss(url, src):
    out = []
    root = ET.fromstring(get(url))
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        out.append({"title": html.unescape(t), "url": (it.findtext("link") or "").strip(),
                    "company": "", "text": html.unescape(it.findtext("description") or "")[:2000], "src": src})
    return out

def remotive():
    out = []
    for q in ["community", "moderator", "crypto", "cad", "agriculture"]:
        d = json.loads(get(f"https://remotive.com/api/remote-jobs?search={q}&limit=50"))
        for j in d.get("jobs", []):
            out.append({"title": j["title"], "url": j["url"], "company": j.get("company_name", ""),
                        "text": j.get("category", "") + " " + j.get("candidate_required_location", ""), "src": "Remotive"})
    return out

def remoteok():
    out = []
    for j in json.loads(get("https://remoteok.com/api?tag=crypto"))[1:]:
        out.append({"title": j.get("position", ""), "url": j.get("url", ""), "company": j.get("company", ""),
                    "text": " ".join(j.get("tags", [])), "src": "RemoteOK"})
    return out

SOURCES = [lambda: rss("https://cryptojobslist.com/rss", "CryptoJobsList"),
           lambda: rss("https://cryptocurrencyjobs.co/index.xml", "CryptocurrencyJobs"),
           remotive, remoteok]

def match(job):
    t = (job["title"] + " " + job["company"]).lower()
    if any(x in t for x in EXCLUDE):
        return None
    for group, kws in KEYWORDS.items():
        if any(k in t for k in kws):
            return group
    return None

def brain_rank(new):
    """Ask Swanix Brain which keyword matches actually fit him. {key: {fit, why}} or {} if Brain is down."""
    try:
        sys.path.insert(0, str(HERE.parent / "common"))
        from ai import think
        items = [{"key": x["url"] or x["title"], "title": x["title"], "company": x["company"], "group": g} for g, js in new.items() for x in js[:25]]
        res = think("These job posts passed a keyword filter. Judge each one for Swanix using what you know about him (crypto/Web3 community, "
                    "moderator, ambassador, growth and marketing roles; Agri Engineering internships; remote or near Raipur; fresher). "
                    "fit = 1-10 (10 = apply today), why = under 15 words, concrete. Penalise senior/10+ yrs roles, scams, unpaid 'ambassador' MLMs and "
                    "roles needing a degree he lacks. Return ONLY JSON list: [{\"key\":\"...\",\"fit\":7,\"why\":\"...\"}]", items, as_json=True, tools=False)
        return {r["key"]: r for r in (res or []) if isinstance(r, dict) and r.get("key")}
    except Exception as e:
        print("Brain ranking skipped:", e); return {}

def main():
    seen_f = HERE / "seen.json"
    seen = set(json.loads(seen_f.read_text())) if seen_f.exists() else set()
    first_run = not seen
    jobs, errors = [], []
    for s in SOURCES:
        try:
            jobs += s()
        except Exception as e:
            errors.append(str(e)[:120])
    new = {}
    for j in jobs:
        key = j["url"] or j["title"]
        if not key or key in seen:
            continue
        seen.add(key)
        g = match(j)
        if g:
            new.setdefault(g, []).append(j)
    seen_f.write_text(json.dumps(sorted(seen)[-5000:]))
    if first_run:
        print("first run: remembered", len(seen), "jobs, no alert sent"); return
    if not new:
        print("no new matching jobs", errors); return
    verdict = brain_rank(new)
    lines = ["🧭 New job matches" + (" (ranked by Brain)" if verdict else "")]
    for g, js in new.items():
        if verdict:
            js = [x for x in js if verdict.get(x["url"] or x["title"], {}).get("fit", 5) >= 6]
            js.sort(key=lambda x: -verdict.get(x["url"] or x["title"], {}).get("fit", 0))
        if not js: continue
        lines.append(f"\n{g} ({len(js)})")
        for j in js[:15]:
            co = f" · {j['company']}" if j["company"] else ""
            v = verdict.get(j["url"] or j["title"], {}) if verdict else {}
            why = f"\n  {v.get('fit')}/10 · {v.get('why')}" if v.get("why") else ""
            lines.append(f"• {j['title']}{co} [{j['src']}]{why}\n  {j['url']}")
    if len(lines) == 1:
        print("Brain found no strong fits this round"); return
    msg = "\n".join(lines)
    if not telegram(msg):
        (HERE / "digest.md").write_text(msg)
    print(msg)

if __name__ == "__main__":
    main()
