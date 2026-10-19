#!/usr/bin/env python3
"""Shield Audit, step 2a: per-piece index for the "Check my Naka" lookup.
Adds deep["pieceIndex"] = {"asOf": height, "cols": [...], "rows": "<one line per piece id 0..3332>"} to audit_deep.json.
Row format: minted|active|sold|claimed|lastSaleSats|lastSaleHeight|dupActive
"""
import json, collections, pathlib
HERE = pathlib.Path(__file__).resolve().parent.parent / "data"
s = json.load(open(HERE / "state.json"))
deep_p = HERE / "audit_deep.json"; d = json.load(open(deep_p))
minted = collections.Counter(int(m) for m in s["minted"])
by = collections.defaultdict(list)
for l in s["listings"]: by[int(l["assetId"])].append(l)
rows = []
for i in range(3333):
    L = by.get(i, [])
    act = sum(1 for l in L if l["state"] == "ACTIVE")
    sold = [l for l in L if l["state"] == "SOLD"]
    cl = sum(1 for l in L if l["state"] == "CLAIMED")
    last = max(sold, key=lambda l: l["height"]) if sold else None
    rows.append("|".join(map(str, [minted.get(i, 0), act, len(sold), cl,
        int(last["price"]) if last else 0, last["height"] if last else 0, 1 if act > 1 else 0])))
d["pieceIndex"] = {"asOf": s["lastHeight"], "cols": ["mintedCount","activeListings","sales","claims","lastSaleSats","lastSaleHeight","dupActive"], "rows": "\n".join(rows)}
json.dump(d, open(deep_p, "w"), indent=1)
print("pieces", len(rows), "size", len(d["pieceIndex"]["rows"]))
