#!/usr/bin/env python3
"""Genre picker for GTA VI Clips. Set GTA_GENRE (workflow box "genre", or repo variable GTA_GENRE for scheduled runs):
funny, rage, racing, police, chaos, fails, wtf, clutch, scary, emotional - or type ANY words (e.g. "speed screaming").
Blank / auto = no genre, the bot picks the most viral clip like before. Never blocks a run: if nothing matches,
the bot falls back to the normal pick and says so in the log."""
import os, re, sys, json
GENRES = {
    "funny":     r"funny|lol|lmao|laugh|hilarious|comedy|troll|prank|silly|goofy|bruh|ragdoll|npc|dumb|wtf|😂|🤣|💀",
    "rage":      r"rage|raging|angry|mad|tilted|scream|screaming|yell|smash|broke|meltdown|lost it|loses it|furious|😡|🤬",
    "racing":    r"race|racing|drift|car|cars|speed|fastest|supercar|bike|motorcycle|street race|lap|nitro|🏎",
    "police":    r"police|cops?|chase|wanted|stars|swat|arrest|pursuit|escape|getaway|🚓",
    "chaos":     r"chaos|rampage|explosion|explode|crash|destroy|shootout|heist|robbery|robbing|bank|💥",
    "fails":     r"fail|fails|fell|falls?|oops|died|dies|death|wasted|worst|bug|glitch|😭",
    "wtf":       r"wtf|insane|crazy|unbelievable|what just|no way|impossible|weird|strange|😳|🤯",
    "clutch":    r"clutch|epic|perfect|godlike|best|insane escape|1v|skill|pro|sniped|🔥",
    "scary":     r"scary|horror|jump ?scare|creepy|night|ghost|scared|😱",
    "emotional": r"emotional|cry|crying|tears|sad|wholesome|finally|dream|❤",
}
# sound-effect mood per genre (sfx in brand.py)
MOOD = {"funny": "funny", "fails": "funny", "wtf": "funny", "scary": "serious", "emotional": "soft",
        "rage": "serious", "racing": "serious", "police": "serious", "chaos": "serious", "clutch": "serious"}

def name():
    g = (os.environ.get("GTA_GENRE") or "").strip().lower()
    return "" if g in ("", "auto", "any", "none", "all") else g

def pattern(g=None):
    g = name() if g is None else g
    if not g: return None
    parts = [GENRES[k] for k in GENRES if k in re.split(r"[\s,/+]+", g)]
    words = [re.escape(w) for w in re.split(r"[\s,/+]+", g) if w and w not in GENRES and len(w) > 2]
    # whole words only ("mad" must not match "made", "car" not "card")
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts + words) + r")(?:s|es|ed|d|ing)?(?![a-z0-9])", re.I) if parts or words else None

def mood():
    """Sound-effect style: GTA_SFX (funny / serious / soft / off) wins, else it follows the genre. Blank genre = no effects."""
    forced = (os.environ.get("GTA_SFX") or "").strip().lower()
    if forced in ("funny", "serious", "soft"): return forced
    if forced in ("off", "none", "0", "no"): return ""
    g = name()
    for k in re.split(r"[\s,/+]+", g):
        if k in MOOD: return MOOD[k]
    return "funny" if re.search(r"funny|lol|troll|meme|scream|fail", g) else "serious" if g else ""

STREAMER_ALIASES = {"speed": "ishowspeed", "ishowspeed": "ishowspeed", "xqc": "xqc", "kai": "kaicenat", "kaicenat": "kaicenat"}

def streamers(g=None):
    """Words in the genre box that are not genres or style words = streamer names ("funny ishowspeed" -> ["ishowspeed"])."""
    g = name() if g is None else g
    style = {"clip", "clips", "moment", "moments", "video", "best", "from", "the", "and", "with", "gta", "gta6"}
    out = []
    for w in re.split(r"[\s,/+]+", g.lower()):
        w = re.sub(r"[^a-z0-9_]", "", w)
        if w in STREAMER_ALIASES: out.append(STREAMER_ALIASES[w]); continue
        if w and w not in GENRES and w not in style and len(w) > 2 and not any(re.fullmatch(GENRES[k], w) for k in GENRES):
            out.append(STREAMER_ALIASES.get(w, w).replace("_", ""))
    return list(dict.fromkeys(out))

def text_of(c): return " ".join(str(c.get(k, "")) for k in ("title", "desc", "uploader"))

def filter_pool(pool, label="clips"):
    """Keep only clips that fit the genre. Keyword match first, then the AI sorts the top titles, else no filter."""
    g = name()
    if not g or not pool: return pool
    rx = pattern(g)
    hits = [c for c in pool if rx and rx.search(text_of(c))]
    if not hits:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
            from ai import ask
            top = pool[:40]
            out = ask(f'Which of these GTA 6 clip titles fit the genre "{g}"? Return ONLY a JSON list of the numbers that fit.\n'
                      + "\n".join(f"{i}. {c.get('title','')[:120]}" for i, c in enumerate(top)), min_len=1)
            idx = json.loads(re.search(r"\[.*?\]", out, re.S).group(0))
            hits = [top[int(i)] for i in idx if str(i).isdigit() and int(i) < len(top)]
        except Exception as e:
            print(f"genre: AI sort failed ({str(e)[:120]})", file=sys.stderr)
    if hits:
        print(f"genre '{g}': {len(hits)} of {len(pool)} {label} fit", file=sys.stderr); return hits
    print(f"genre '{g}': no {label} fit, using the normal viral pick this run", file=sys.stderr); return pool

def prompt_line():
    g = name()
    if not g: return ""
    return (f'\nGENRE FOR THIS VIDEO: "{g}". Write hook, vo, title and cta in that style '
            f"(funny = playful jokes, rage = intense and dramatic, racing = fast hype, scary = tense), "
            f"but only claim what the clip shows.")
