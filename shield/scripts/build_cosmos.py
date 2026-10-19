#!/usr/bin/env python3
"""Rebuild cosmos_data.json from the freshest /workspace/work/shield/state.json (run run_audit.py first).
Keeps the inscription list (insc) from the previous cosmos_data.json. Prints the new asOf block."""
import json, collections
import os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data") + "/"
s = json.load(open(D + "state.json"))
old = json.load(open(D + "cosmos_data.json"))
notes = s["notes"]
mint_notes = [n for n in notes if n["kind"] == "mint"]
blocks = collections.defaultdict(lambda: {"m": [], "b": 0, "c": 0, "t": 0})
for n, a in zip(mint_notes, s["minted"]):
    blocks[str(n["height"])]["m"].append(a)
tx_h = {}
for n in notes:
    k = n["kind"]
    if k == "buy": blocks[str(n["height"])]["b"] += 1
    elif k == "claim": blocks[str(n["height"])]["c"] += 1
    elif k == "transfer": blocks[str(n["height"])]["t"] += 1
    tx_h.setdefault(n["txid"], n["height"])
sales, claims, lists, active = [], [], [], []
for l in s["listings"]:
    a, p = int(l["assetId"]), int(l["price"])
    lists.append([l["height"], a, p])
    if l["state"] == "SOLD" and l.get("soldTxid") in tx_h: sales.append([tx_h[l["soldTxid"]], p, a])
    if l["state"] == "CLAIMED" and l.get("claimTxid") in tx_h: claims.append([tx_h[l["claimTxid"]], a])
    if l["state"] == "ACTIVE": active.append([a, p])
sales.sort(); claims.sort(); lists.sort(); active.sort(key=lambda x: x[1])
out = {"asOf": s["lastHeight"], "first": min(int(h) for h in blocks), "supply": 3333,
       "blocks": dict(sorted(blocks.items(), key=lambda x: int(x[0]))),
       "sales": sales, "claims": claims, "lists": lists, "active": active, "insc": old["insc"]}
json.dump(out, open(D + "cosmos_data.json", "w"), separators=(",", ":"))
print(out["asOf"], len(sales), len(claims), len(active))
