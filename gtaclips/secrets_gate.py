#!/usr/bin/env python3
"""Gauntlet for GTA 6 SECRETS Shorts: runs gate.py unchanged (hard checks + fresh AI reviewer, verdict log in RULES.md)
with secrets rules on top:
1) length 12-32 s (the series format), 1080x1920.
2) tip: out/tip_check.json (written by gta_secrets.py) must say the tip's supporting quote was found verbatim in the guide.
3) footage: metadata visual_safety must start with "checked" (Gemini vision: shows the tip + safe), speech check must not be unavailable-and-flagged.
Exit 0 = post it, exit 7 = rejected (same as gate.py). Usage: python secrets_gate.py [video] [metadata.json]"""
import json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate

CFG = {**(json.load(open(HERE / "gta_secrets.json")) if (HERE / "gta_secrets.json").exists() else {}), **json.load(open(HERE / "config.json"))}
LO, HI = CFG.get("secrets_min_seconds", 12), CFG.get("secrets_max_seconds", 25)
_hard = gate.hard_checks

def tip_faults():
    f = gate.META.parent / "tip_check.json"
    if not f.exists(): return ["no tip check found (out/tip_check.json) - tip unverified"]
    tc = json.loads(f.read_text())
    return [] if tc.get("pass") else [f"tip not verified against its guide: {tc.get('verified') or 'failed'}"]

def safety_faults():
    m = json.loads(gate.META.read_text())
    out = []
    if m.get("test_only"): out.append("test-only video (--no-llm or hand-made), never postable")
    if not str(m.get("visual_safety", "")).startswith("checked"): out.append(f"footage safety: {m.get('visual_safety') or 'not checked'}")
    return out

def hard_checks():
    faults, dur = _hard()
    faults = [f for f in faults if not f.startswith("length ")]
    if not LO - 0.5 <= dur <= HI + 0.5: faults.append(f"length {dur:.0f}s is outside {LO}-{HI}s")
    return faults + tip_faults() + safety_faults(), dur

gate.hard_checks = hard_checks
gate.BRIEF = gate.BRIEF.replace("Look at the frames", "This is a 'GTA 6 SECRETS · PART N' tip Short: one streamer clip (webcam on top is fine), "
    "a hook-first cold open, a yellow series banner, WHERE / WHAT YOU GET captions, a 0.5x REPLAY of the payoff and a darkened "
    "'SEND THIS TO ... / FOLLOW FOR PART N+1' end card (all intended). "
    "OR the NARRATED version: an AI voiceover (often Hindi) explains the tip over gameplay (webcam cropped out, streamer audio muted), "
    "with big caption cards like STEP 1 / REWARD / FOLLOW FOR and the yellow PART pill on top - also intended and fine. "
    "A guide tip about a place, vehicle, weapon or money spot is NOT an exploit. Bikinis, lingerie and thongs are fine. "
    "Say NO if any frame shows full nudity (bare genitals, bare breasts, naked NPCs), a stream sub/donation alert overlay, or a slur on screen. Look at the frames", 1)
if __name__ == "__main__":
    gate.main()
