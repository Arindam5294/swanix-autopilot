#!/usr/bin/env python3
"""Swanix chief-of-staff hunters. Public data only, never applies or posts anything.

  python hunters/hunt.py bounty   -> Bounty Hunter: Superteam Earn dev/design bounties, Devpost hackathons,
                                     Algora GitHub bounties. Ranks against Swanix's builds, drafts a plan.
  python hunters/hunt.py deals    -> Deal Desk: paid content/creator/ambassador gigs (Superteam content bounties,
                                     RemoteOK crypto community/content roles). Drafts an application as "Swanix".

Only NEW items reach Telegram (hunters/seen.json). If every AI is down the list still goes out without drafts.
"""
import html, json, os, re, sys, time, pathlib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
from notify import telegram  # noqa: E402

SEEN = HERE / "seen.json"
IST = timezone(timedelta(hours=5, minutes=30))
UA = {"User-Agent": "Mozilla/5.0 (swanix-hunters)", "Accept": "application/json"}
PROFILE = """Swanix (@swanixcrypto): anon Web3 builder + community lead from India (IST), 6+ years of ambassador,
moderator and community roles. Ships AI x crypto tools: Swanix AI agent (github.com/Arindam5294/Swanix),
Rug Radar (pre-swap token safety score, PayBox hackathon MCP plugin), Dose Buddy (Amazon Alexa+ hackathon entry),
NIXFORGE (Telegram agent that writes and runs GitHub Actions workflows), Botbeat (auto YouTube Shorts pipeline).
Skills: Python, JS/TS, Cloudflare Workers, GitHub Actions, LLM APIs, Telegram bots, X threads, Spaces, short video.
Rug Radar reads on-chain token data (liquidity, holders, contract flags); it does no image work. Never invent
features for his builds; if none fits, say which skill fits instead.
Rules: sign as Swanix only, never real name/face, never mention CropCrack or agriculture. 5-6 h/day."""


def raw(url, timeout=40):
    req = urllib.request.Request(url, headers={**UA, "Accept": "*/*", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def get(url, timeout=40):
    return json.loads(raw(url, timeout))


def rss(url, src, days=10, want=None):
    out = []
    try:
        for it in ET.fromstring(raw(url)).findall(".//item"):
            t, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
            try:
                d = parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                d = None
            if d and (datetime.now(timezone.utc) - d).days > days:
                continue
            if want and not want.search(t):
                continue
            out.append({"key": "rss:" + re.sub(r"\W+", "", t.lower())[:90], "src": src, "title": html.unescape(t)[:160],
                        "reward": "", "usd": 0, "deadline": None,
                        "dl_text": "posted " + d.astimezone(IST).strftime("%d %b") if d else "", "cat": "", "by": "",
                        "url": link, "subs": None, "posted": d})
    except Exception as e:
        print(src, "failed:", e)
    return out


def gnews(q, src="Google News", days=10, want=None):
    return rss("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-IN&gl=IN&ceid=IN:en", src, days, want)


def web3career(path, days=30):
    out = []
    try:
        s = raw(f"https://web3.career/{path}")
        for m in re.finditer(r"<tr data-jobid=(\d+)(.*?)</tr>", s, re.S):
            jid, body = m.group(1), m.group(2)
            t = re.search(r"<h2[^>]*>\s*(.*?)\s*</h2>", body, re.S)
            c = re.search(r"<h3[^>]*>\s*(.*?)\s*</h3>", body, re.S)
            href = re.search(r'href="(/[^"]+/' + jid + ')"', body)
            tm = re.search(r'datetime="([^"]+)"', body)
            sal = re.search(r'text-salary[^>]*>\s*([^<]*?)\s*<', body)
            if not (t and href):
                continue
            d = when((tm.group(1).replace(" ", "T", 1)) if tm else None)
            if d and (datetime.now(timezone.utc) - d).days > days:
                continue
            if any(o["key"] == f"w3:{jid}" for o in out):
                continue
            out.append({"key": f"w3:{jid}", "src": "web3.career", "title": f"{html.unescape(t.group(1))} at {html.unescape(c.group(1)) if c else '?'}",
                        "reward": (sal.group(1).strip() if sal and sal.group(1).strip() else "pay not listed"), "usd": 0,
                        "deadline": None, "dl_text": "posted " + d.astimezone(IST).strftime("%d %b") if d else "",
                        "cat": "ambassador", "by": c.group(1) if c else "", "url": "https://web3.career" + href.group(1), "subs": None})
    except Exception as e:
        print("web3.career", path, "failed:", e)
    return out


GOV_WANT = re.compile(r"agri|icar|kvk|krishi|nabard|\bfci\b|\bcwc\b|warehous|iffco|nafed|seed|fertili|farm|horticult|soil|"
                      r"irrigation|water resources|watershed|food|dairy|fisher|rural|young professional|\bsms\b|drone", re.I)
GOV_SKIP = re.compile(r"result|answer key|admit card|cut ?off|syllabus|exam date|exam centre|detained|scam|arrest", re.I)


def government():
    out = []
    for q in ['"agricultural engineering" (recruitment OR vacancy OR notification OR internship) when:7d',
              '(ICAR OR KVK OR "agricultural university") (recruitment OR "young professional" OR walkin OR internship) when:7d',
              '(NABARD OR FCI OR CWC OR NSC OR IFFCO OR NAFED OR "agriculture officer" OR "agriculture development officer") recruitment when:7d',
              'agriculture internship (government OR ministry OR ICAR OR MANAGE) 2026 when:14d']:
        out += gnews(q, "Google News", 14)
    out += rss("https://www.freejobalert.com/feed/", "FreeJobAlert", 7, GOV_WANT)
    keep, seen = [], set()
    for i in out:
        if GOV_SKIP.search(i["title"]):
            continue
        k = re.sub(r"\s+-\s+[^-]+$", "", i["title"]).lower()[:60]   # same notice from several papers
        if k in seen:
            continue
        seen.add(k); keep.append(i)
    return keep


def gov_check(items):
    """AI marks which notices a B.Tech Agricultural Engineering fresher can apply to. Keyword fallback if AI is down."""
    try:
        from ai import ask
        lst = [{"key": i["key"], "title": i["title"]} for i in items]
        txt = ask("A fresh B.Tech Agricultural Engineering graduate in India (age 22, General category; SEBC in Chhattisgarh) "
                  "wants government jobs and internships. For each notice title below, say if he is likely eligible "
                  "(posts open to B.Tech Agri Engg / Agri / any graduate / engineering, internships, Young Professional, "
                  "JRF/SRF/project roles in agri engineering). Do not guess dates you can't see.\n"
                  + json.dumps(lst, ensure_ascii=False) +
                  '\nReturn ONLY JSON list: [{"key":"...","ok":true,"why":"post + who can apply, under 18 words"}]', min_len=20)
        res = {r["key"]: r for r in json.loads(re.search(r"\[.*\]", txt, re.S).group(0)) if isinstance(r, dict)}
        return [dict(i, why=res[i["key"]].get("why", "")) for i in items if res.get(i["key"], {}).get("ok")]
    except Exception as e:
        print("gov AI check skipped:", e)
        return [i for i in items if re.search(r"agricultur\w* engineer|young professional|icar|kvk|agri", i["title"], re.I)]


def when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def days_left(d):
    return None if not d else (d - datetime.now(timezone.utc)).total_seconds() / 86400


def fmt_deadline(d):
    return d.astimezone(IST).strftime("%d %b, %I:%M %p IST") if d else "see page"


# ---------------- sources ----------------
def superteam(categories):
    out, ids = [], set()
    for c in categories:
        try:
            q = "take=50" + (f"&category={urllib.parse.quote(c)}" if c else "")
            for x in get(f"https://superteam.fun/api/listings?{q}"):
                if x["id"] in ids or x.get("status") != "OPEN":
                    continue
                ids.add(x["id"])
                d = when(x.get("deadline"))
                out.append({"key": "st:" + x["id"], "src": "Superteam Earn", "title": x["title"],
                            "reward": f"{x.get('rewardAmount') or '?'} {x.get('token') or ''}".strip(),
                            "usd": float(x.get("rewardAmount") or 0), "deadline": d, "cat": c or "featured",
                            "by": (x.get("sponsor") or {}).get("name", ""),
                            "url": f"https://superteam.fun/earn/listing/{x['slug']}",
                            "subs": (x.get("_count") or {}).get("Submission")})
        except Exception as e:
            print("superteam", c, "failed:", e)
    return out


def devpost():
    out = []
    try:
        for x in get("https://devpost.com/api/hackathons?status[]=open&order_by=prize-amount&per_page=40")["hackathons"]:
            if x.get("invite_only"):
                continue
            loc = (x.get("displayed_location") or {}).get("location", "")
            if "online" not in loc.lower():
                continue
            prize = re.sub(r"<[^>]+>", "", html.unescape(x.get("prize_amount") or ""))
            usd = float(re.sub(r"[^\d.]", "", prize) or 0) if "$" in prize else 0
            out.append({"key": f"dp:{x['id']}", "src": "Devpost", "title": x["title"], "reward": prize or "prizes",
                        "usd": usd, "deadline": None, "dl_text": x.get("time_left_to_submission", ""),
                        "cat": ", ".join(t["name"] for t in x.get("themes", [])), "by": x.get("organization_name", ""),
                        "url": x["url"], "subs": x.get("registrations_count")})
    except Exception as e:
        print("devpost failed:", e)
    return out


def algora():
    out = []
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%d")
        q = urllib.parse.quote(f'label:"💎 Bounty" is:open is:issue created:>{since}')
        d = get(f"https://api.github.com/search/issues?q={q}&sort=created&per_page=50")
        for x in d.get("items", []):
            amt = [l["name"] for l in x.get("labels", []) if re.match(r"^\$\s?[\d,]+", l["name"])]
            if not amt:
                continue
            usd = float(re.sub(r"[^\d.]", "", amt[0]) or 0)
            repo = x["repository_url"].split("/repos/")[-1]
            out.append({"key": f"gh:{x['id']}", "src": "Algora (GitHub)", "title": x["title"][:120], "reward": amt[0],
                        "usd": usd, "deadline": None, "dl_text": "open until solved", "cat": repo, "by": repo,
                        "url": x["html_url"], "subs": x.get("comments")})
    except Exception as e:
        print("algora failed:", e)
    return out


def remoteok():
    out = []
    want = re.compile(r"ambassador|community|moderat|content|social media|kol|creator|marketing|growth|writer", re.I)
    try:
        for x in (get("https://remoteok.com/api?tag=crypto")[1:] + get("https://remoteok.com/api?tag=web3")[1:]):
            if any(o["key"] == f"ro:{x.get('id')}" for o in out):
                continue
            text = f"{x.get('position','')} {' '.join(x.get('tags', []))}"
            d = when(x.get("date"))
            if not want.search(text) or (days_left(d) or 0) < -14:
                continue
            sal = f"${x['salary_min']:,}-{x['salary_max']:,}/yr" if x.get("salary_min") else "salary not listed"
            out.append({"key": f"ro:{x.get('id')}", "src": "RemoteOK", "title": f"{x['position']} at {x.get('company','')}",
                        "reward": sal, "usd": float(x.get("salary_min") or 0) / 12, "deadline": None,
                        "dl_text": "posted " + (d.astimezone(IST).strftime("%d %b") if d else "recently"),
                        "cat": "job", "by": x.get("company", ""), "url": x.get("url", ""), "subs": None})
    except Exception as e:
        print("remoteok failed:", e)
    return out


# ---------------- ranking + drafts ----------------
def ai_drafts(mode, items):
    """One AI call for the whole shortlist. Returns {key: {"fit":..., "draft":...}} or {} on failure."""
    try:
        from ai import ask
    except Exception:
        return {}
    task = ("For each bounty write: fit = one line on why Swanix can win it (name the build of his it reuses), "
            "draft = a 3-step plan to ship the entry in his free hours, concrete, no fluff.") if mode == "bounty" else \
           ("For each gig write: fit = one line on why Swanix suits it, draft = a short application or DM opener "
            "(max 70 words) in English, in his voice, signed 'Swanix', no real name, no CropCrack, no invented numbers.")
    lst = [{"key": i["key"], "title": i["title"], "source": i["src"], "reward": i["reward"], "by": i["by"],
            "category": i["cat"]} for i in items]
    prompt = (f"{PROFILE}\n\n{task}\nAlso give score 1-10 for how likely he wins/lands it.\n"
              f"Items:\n{json.dumps(lst, ensure_ascii=False)}\n"
              'Return ONLY a JSON list: [{"key":"...","score":7,"fit":"...","draft":"..."}]')
    try:
        txt = ask(prompt, min_len=50)
        m = re.search(r"\[.*\]", txt, re.S)
        return {r["key"]: r for r in json.loads(m.group(0)) if isinstance(r, dict) and r.get("key")}
    except Exception as e:
        print("AI drafts skipped:", e)
        return {}


def run(mode):
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    if mode == "bounty":
        items = [i for i in superteam(["Development", "Design", "Other", ""]) if i["cat"] != "Content"]
        items += devpost() + algora()
        title, limit = "🎯 Bounty Hunter", 6
    elif mode == "deals":
        role = re.compile(r"ambassador|community|moderat|content|creator|kol|social|growth|marketing|advocate|evangelist|devrel", re.I)
        items = web3career("ambassador-jobs", 75) + web3career("community-manager-jobs", 30)
        items += rss("https://cryptocurrencyjobs.co/index.xml", "CryptocurrencyJobs", 21, role)
        items += rss("https://cryptojobslist.com/rss", "CryptoJobsList", 21, role)
        items += superteam(["Content"]) + remoteok()
        title, limit = "🤝 Deal Desk (ambassador + creator)", 8
    elif mode == "gov":
        items = government()
        title, limit = "🏛 Govt jobs + internships (B.Tech Agri Engg)", 10
    print(f"{mode}: {len(items)} live items fetched")
    if not items:
        raise SystemExit(f"{mode}: every source failed")   # red run = visible failure, never silent
    fresh = []
    for i in items:
        dl = days_left(i["deadline"])
        if dl is not None and dl < 1.5:          # not enough time left to make a real entry
            continue
        if i["key"] in seen:
            continue
        fresh.append(i)
    fresh.sort(key=lambda i: -i["usd"])
    per, short = {}, []                       # mix sources: Devpost prize pools must not drown crypto bounties
    for i in fresh:
        if per.get(i["src"], 0) < 5:
            per[i["src"]] = per.get(i["src"], 0) + 1; short.append(i)
    if mode == "gov":
        short = gov_check(fresh[:40])
        notes = {}
    else:
        notes = ai_drafts(mode, short) if short else {}
    if notes:
        short.sort(key=lambda i: (-(notes.get(i["key"], {}).get("score") or 0), -i["usd"]))
    pick, per = [], {}
    for i in short:
        if (mode == "gov" or per.get(i["src"], 0) < 3) and len(pick) < limit:
            per[i["src"]] = per.get(i["src"], 0) + 1; pick.append(i)
    now = datetime.now(IST).strftime("%d %b %I:%M %p")
    if not pick:
        print("nothing new"); return
    lines = [f"{title} · {now} IST · {len(pick)} new (from {len(items)} live)\n" + ("Listed only, never applied. Check the official notice for last date and eligibility.\n" if mode == "gov" else "Nothing was applied or sent. You decide.\n")]
    for n, i in enumerate(pick, 1):
        dl = fmt_deadline(i["deadline"]) if i["deadline"] else i.get("dl_text", "")
        reg = re.match(r"Superteam (.+)", i.get("by") or "")
        if reg and reg.group(1).strip().lower() not in ("india", "earn", ""):
            i["title"] += f"  ⚠️ check region ({reg.group(1).strip()})"
        if mode == "gov":
            lines.append(f"{n}. {i['title']}\n{('✅ ' + i['why']) if i.get('why') else ''}\n📅 {dl} · {i['src']}\n{i['url']}\n")
            continue
        lines.append(f"{n}. {i['title']}\n💰 {i['reward'] or 'pay not listed'} · ⏳ {dl} · {i['src']}" + (f" · {i['subs']} entries so far" if i.get("subs") is not None and i["src"] == "Superteam Earn" else ""))
        a = notes.get(i["key"])
        if a:
            lines.append(f"Why you: {a.get('fit','')}")
            lines.append(("Plan: " if mode == "bounty" else "Draft: ") + str(a.get("draft", "")).strip())
        lines.append(i["url"] + "\n")
    msg = "\n".join(lines)
    print(msg)
    if not telegram(msg):
        (HERE / f"digest_{mode}.md").write_text(msg)
    for i in pick:
        seen[i["key"]] = int(time.time())
    cutoff = time.time() - 60 * 86400
    SEEN.write_text(json.dumps({k: v for k, v in seen.items() if v > cutoff}, indent=0))


def run_safe(m):
    try:
        run(m)
        return True
    except BaseException as e:
        print(m, "FAILED:", e)
        return False


if __name__ == "__main__":
    modes = sys.argv[1:] or ["bounty", "deals", "gov"]
    ok = [run_safe(m) for m in modes]       # one broken source never stops the others
    if not all(ok):
        sys.exit(1)
