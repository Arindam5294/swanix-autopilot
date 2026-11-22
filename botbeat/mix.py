"""Botbeat content mix: keeps the channel GLOBAL (not China-only).

Why (2026-11-22): every run searched 30 terms x 2 regions (~6,000 API units). The second run of the day hit the
10,000-unit quota, fell back to yt-dlp, and that fallback only ever searched search_terms[0] = "China humanoid robot".
Picks were also ranked by raw views, which Chinese gala / martial-arts clips win. Result: 8 of the last 10 Shorts were China.

Now:
- Topics live in config["buckets"] (frontier AI, US robots, Europe/Japan/Korea, research labs, everyday/funny, china).
  Each run searches 2 rotating terms per bucket, one global search per term (~1,100 units/run, safe for 2 runs/day).
- China is capped: at most config["china_max_in_last"] (default 1) China picks in the last config["china_window"] (default 4).
- Same bucket twice in a row is penalised, so the feed rotates between AI models, robots, research and fun.
- Ranking = momentum (views per day) + reach (views) + trusted channel, not raw views alone.
- picks.json remembers each upload's bucket + china flag (union-saved by mark_used.py)."""
import json, math, pathlib, re, datetime

HERE = pathlib.Path(__file__).resolve().parent
PICKS = HERE / "picks.json"

DEFAULT_BUCKETS = {
    "frontier_ai": ["OpenAI new model demo", "Google Gemini demo", "Anthropic Claude AI", "AI agent does task",
                    "Nvidia AI announcement", "Meta AI glasses", "xAI Grok", "Microsoft Copilot AI", "AI breakthrough 2026",
                    "Apple Intelligence"],
    "us_robots": ["Tesla Optimus robot", "Figure AI humanoid", "Boston Dynamics Atlas", "Agility Robotics Digit",
                  "Apptronik Apollo robot", "1X NEO home robot", "Amazon warehouse robot", "Waymo robotaxi",
                  "Tesla Cybercab", "Zipline drone delivery"],
    "world_robots": ["Engineered Arts Ameca", "Neura Robotics humanoid", "Sanctuary AI Phoenix", "Toyota robot",
                     "Honda robot", "Hyundai robot", "Samsung robot", "Japan humanoid robot", "Korea humanoid robot",
                     "India humanoid robot", "Disney robot", "ANYbotics robot", "Wayve self driving"],
    "research": ["MIT robot", "Stanford robot", "Google DeepMind robotics", "robot learns new skill", "surgical robot",
                 "robot hand dexterity", "soft robot", "exoskeleton", "brain computer interface Neuralink", "robot surgery first"],
    "everyday": ["robot fails", "robot vs human", "two AIs talking", "AI job interview", "robot dog", "delivery robot",
                 "robot chef", "robot bartender", "AI prank call", "robot lawn mower"],
    "china": ["China humanoid robot", "Unitree robot", "UBTECH Walker", "Xpeng Iron robot"],
}

CHINA_RE = re.compile(
    r"\bchina\b|chinese|beijing|shanghai|shenzhen|hangzhou|guangzhou|wuhan|chengdu|unitree|ubtech|xpeng|agibot|engineai|"
    r"\bfourier\b|galbot|leju|kepler robot|dobot|cgtn|\bcctv\b|xinhua|global times|spring festival|\bgala\b|coolchina|"
    r"long in china|ambrose in china|people's daily", re.I)


def is_china(m):
    return bool(CHINA_RE.search(" ".join(str(m.get(k, "")) for k in ("title", "uploader")) + " " + str(m.get("desc", ""))[:400]))


def buckets(cfg):
    return cfg.get("buckets") or DEFAULT_BUCKETS


def history():
    try:
        return [x for x in json.loads(PICKS.read_text()) if isinstance(x, str)]
    except Exception:
        return []


def recent(n):
    """Last n picks as [{'bucket':..,'china':bool}] (newest last). Entries look like 'vid|bucket|cn' or 'vid|bucket|-'."""
    out = []
    for e in history()[-n:]:
        p = e.split("|")
        out.append({"bucket": p[1] if len(p) > 1 else "", "china": len(p) > 2 and p[2] == "cn"})
    return out


def china_allowed(cfg):
    win, mx = int(cfg.get("china_window", 4)), int(cfg.get("china_max_in_last", 1))
    return sum(r["china"] for r in recent(win - 1)) < mx


def last_bucket():
    r = recent(1)
    return r[0]["bucket"] if r else ""


def plan_terms(cfg, per_bucket=2, now=None):
    """Rotating search plan: (term, bucket) pairs. China bucket only when the cap allows, 1 term."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    slot = int(now.timestamp() // (6 * 3600))          # changes every run (2 runs/day) -> fresh terms each time
    plan, allow_cn = [], china_allowed(cfg)
    for name, terms in buckets(cfg).items():
        if not terms: continue
        if name == "china":
            if allow_cn: plan.append((terms[slot % len(terms)], name))
            continue
        for k in range(per_bucket):
            plan.append((terms[(slot * per_bucket + k) % len(terms)], name))
    return plan


def age_days(m):
    try:
        t = datetime.datetime.strptime(m["published"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return max(0.5, (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 86400)
    except Exception:
        return 30.0


def score(m, trusted):
    v = max(1, m.get("views", 0))
    s = math.log10(v) + 1.3 * math.log10(max(1.0, v / age_days(m)))
    if m.get("uploader", "").lower() in trusted: s += 0.8
    if m.get("bucket") and m.get("bucket") == last_bucket(): s -= 0.7
    if is_china(m): s -= 0.6          # even when allowed, a non-China clip of similar strength wins
    return s


def record(vid, bucket, china):
    h = history()
    h.append(f"{vid}|{bucket or 'other'}|{'cn' if china else '-'}")
    PICKS.write_text(json.dumps(h))
