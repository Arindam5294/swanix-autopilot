#!/usr/bin/env python3
"""Prints one line: ALERT <failing checks> | RECOVERED | OK. Remembers state in alert_state.txt so each incident alerts once."""
import json, pathlib
H = pathlib.Path(__file__).resolve().parent.parent / "data"
a = json.load(open(H/"audit_latest.json")); d = json.load(open(H/"audit_deep.json"))
fails = []
if not a.get("digestMatch"): fails.append("digest mismatch")
for k, label in [("dupActive","duplicate active listings"),("dupMints","duplicate mints"),("dupNullifiers","double-spent nullifiers"),("badSold","sold listings missing txid"),("badClaim","claimed listings missing txid")]:
    if a.get(k): fails.append(f"{label} ({a[k]})")
if a.get("mints",0) > a.get("supply",3333): fails.append("mints above supply")
fails += [f"deep check {k} failed" for k, v in d.get("checks",{}).items() if not v]
sp = H/"alert_state.txt"; prev = sp.read_text().strip() if sp.exists() else "OK"
if fails:
    sp.write_text("FAIL")
    print(("ALERT " if prev != "FAIL" else "STILLFAIL ") + f"block {a['block']}: " + "; ".join(fails))
else:
    sp.write_text("OK")
    print("RECOVERED" if prev == "FAIL" else "OK")
