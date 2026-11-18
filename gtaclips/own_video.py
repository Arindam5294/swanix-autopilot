#!/usr/bin/env python3
"""Playbook step 7: YOUR OWN video (this is what unlocks YouTube ads - a channel of only other people's clips
can be refused for 'reused content'). Writes scripts/<date>-<topic>.md: payoff-first cold open, beat-by-beat
script, a shot list of exactly what to record in your own GTA 6 gameplay, 5 titles, thumbnail words, description.
Usage: python own_video.py "the full gta 6 map" "guide|ranking|challenge" -> also sent to Telegram."""
import datetime, json, pathlib, re, statistics, sys, csv
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
from ai import ask

def main():
    topic = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else "the full GTA 6 map"
    fmt = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else "guide"
    pat = (HERE / "THUMBNAILS.md").read_text()[:3000] if (HERE / "THUMBNAILS.md").exists() else "(no outlier data yet)"
    secs = 600
    if (HERE / "outliers.csv").exists():
        L = [int(r["seconds"]) for r in csv.DictReader((HERE / "outliers.csv").open()) if r["format"] == "long"]
        if L: secs = int(statistics.median(L))
    md = ask(f"""Write a production script in markdown for a GTA 6 YouTube video made ONLY from my own gameplay.
Topic: {topic}. Format: {fmt}. Target length: about {secs//60} minutes (the length of the outliers that won).
What wins on GTA channels right now:\n{pat}
Rules: the payoff comes first (show the best thing in the first 5 seconds, then explain). No spoilers of the story ending.
No leaks, exploits or glitch tutorials. Never invent features: if unsure, write [CHECK IN GAME].
Sections:
# Titles (5 options, under 70 chars)
# Thumbnail (3 options of 1-3 huge words + what the picture shows)
# Cold open (0:00-0:15, word for word)
# Script (beat by beat with timestamps, voiceover word for word)
# Shot list - what to record (numbered: location in game, what to do, camera angle, seconds needed)
# Description (with chapters)
# Pinned comment""", min_len=1500)
    d = HERE / "scripts"; d.mkdir(exist_ok=True)
    f = d / f"{datetime.date.today()}-{re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-')[:40]}.md"
    f.write_text(md)
    print(md)
    try:
        from notify import telegram
        telegram(f"🎬 Your own-gameplay script is ready: {topic} ({fmt}, ~{secs//60} min). Saved in gtaclips/scripts/.\n\n" + md)
    except Exception: pass

if __name__ == "__main__":
    main()
