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
    # v5: Swanix Brain judges each keyword match (fit for HIM, not just the keyword) and ranks; keyword list is the fallback.
    flat = [dict(jb, group=g, key=str(i)) for i, (g, jb) in enumerate((g, jb) for g, js in new.items() for jb in js[:25])]
    verdict = None
    try:
        sys.path.insert(0, str(HERE.parent / "common")); from ai import judge
        verdict = judge("These jobs matched keywords for Swanix (6+ yrs Web3 community/ambassador/moderator roles, builds AI x crypto tools; "
                        "also a B.Tech Agricultural Engineering fresher in India wanting private Agri Engg/CAD roles, remote or near Raipur). "
                        "For each, decide if it is a REAL fit he can land (seniority, location/remote, skills), score 1-10, and give a one-line why "
                        "plus a one-line opener he could use. Drop spam, senior-only, on-site abroad. "
                        'Return ONLY JSON list: [{"key":"0","keep":true,"score":8,"why":"...","opener":"..."}]',
                        [{"key": x["key"], "title": x["title"], "company": x["company"], "source": x["src"], "group": x["group"]} for x in flat], tools=False)
    except Exception as e:
        print("judge skipped:", e)
    if isinstance(verdict, list) and verdict:
        vd = {str(v.get("key")): v for v in verdict if isinstance(v, dict)}
        picks = sorted([dict(x, **vd[x["key"]]) for x in flat if vd.get(x["key"], {}).get("keep")], key=lambda x: -float(x.get("score") or 0))
        if not picks:
            print("Brain kept none of", len(flat)); return
        lines = ["🧭 New jobs that fit you (ranked by Swanix Brain)"]
        for x in picks[:15]:
            co = f" · {x['company']}" if x["company"] else ""
            lines.append(f"\n{x.get('score', '?')}/10 {x['title']}{co} [{x['src']}]\n  {x['url']}\n  Why: {x.get('why', '')}\n  Opener: {x.get('opener', '')}")
    else:
        lines = ["🧭 New job matches"]
        for g, js in new.items():
            lines.append(f"\n{g} ({len(js)})")
            for j in js[:15]:
                co = f" · {j['company']}" if j["company"] else ""
                lines.append(f"• {j['title']}{co} [{j['src']}]\n  {j['url']}")
    msg = "\n".join(lines)
    if not telegram(msg):
        (HERE / "digest.md").write_text(msg)
    print(msg)

if __name__ == "__main__":
    main()
