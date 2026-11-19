#!/usr/bin/env python3
"""Gauntlet for LISTICLE Shorts: runs gate.py unchanged (fresh AI reviewer, verdict log in RULES.md) with two listicle rules:
1) length: listicles run 55-68 s, so the hard check allows up to `listicle_gate_max_seconds` (default 70) instead of 60.
2) facts: out/fact_check.json (written by listicle.py) must say every item was found in the fetched sources.
   Any unsourced claim, or a missing fact check, is a hard (visual-class) fault = no post.
Exit 0 = post it, exit 7 = rejected (same as gate.py). Usage: python listicle_gate.py [video] [metadata.json]"""
import json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate   # reads sys.argv for the video / metadata paths, exactly like running gate.py

CFG = {**(json.load(open(HERE / "listicle.json")) if (HERE / "listicle.json").exists() else {}), **json.load(open(HERE / "config.json"))}
MAX_S = CFG.get("listicle_gate_max_seconds", 70)
_hard = gate.hard_checks

def fact_faults():
    f = gate.META.parent / "fact_check.json"
    if not f.exists(): return ["no fact check found (out/fact_check.json) - script facts unverified"]
    fc = json.loads(f.read_text())
    out = [f"script shape: {x}" for x in fc.get("shape_faults") or []]
    if not fc.get("pass"): out += [f"unsourced claim: {x}" for x in fc.get("faults") or ["fact gate did not pass"]]
    return out

def safety_faults():
    m = json.loads(gate.META.read_text())
    v = m.get("visual_safety", "")
    if v.startswith("checked"): return []
    if v.startswith("unchecked") and CFG.get("listicle_allow_unchecked_visuals", False): return []
    return [f"footage safety: {v or 'not checked'} (every cut must pass the cam/title-card vision check)"]

def hard_checks():
    faults, dur = _hard()
    faults = [f for f in faults if not f.startswith("length ")]
    if not 30 <= dur <= MAX_S: faults.append(f"length {dur:.0f}s is outside 30-{MAX_S}s")
    return faults + fact_faults() + safety_faults(), dur

gate.hard_checks = hard_checks
# the fresh reviewer also checks listicle-specific visual rules (no braces here: gate formats BRIEF with .format)
gate.BRIEF = gate.BRIEF.replace("Look at the frames", "This is a fast-cut GTA 6 facts listicle (many short gameplay cuts, own sourced script). "
    "Bikinis, lingerie and thongs are fine. Say NO if any frame shows full nudity (bare genitals, bare breasts, naked NPCs), a stream sub/donation alert overlay, black cinematic bars, or the video is letterboxed. Look at the frames", 1)
if __name__ == "__main__":
    gate.main()
