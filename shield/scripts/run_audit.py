#!/usr/bin/env python3
"""Run the Shield Audit with the stored `shielded` API key (injected on the wire by host).
Writes /workspace/work/shield/audit_latest.json. Usage: python3 run_audit.py"""
import json, hashlib, subprocess, collections, datetime, os
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

API = "https://shielded-public-api-production.up.railway.app"

def get(path, key=False):
    cmd = ["curl", "-s", "-f", "--max-time", "90", API + path]
    return json.loads(subprocess.check_output(cmd))

state = get("/state")
status = get("/status")
pinfo = get("/pieces/info")
json.dump(state, open(DATA + "/state.json", "w"))

h = state["lastHeight"]
root = next(r[1] for r in state["roots"] if r[0] == h)
nfs = sorted(str(x) for x in state["nullifiers"])
minted = sorted(state["minted"])
lst = sorted([str(l["nf"]), l["state"], int(l["assetId"]), int(l["tickets"]), str(l["price"])] for l in state["listings"])
payload = json.dumps({"root": str(root), "leaves": state["noteCount"], "nfs": nfs, "minted": minted, "listings": lst}, separators=(",", ":"))
digest = hashlib.sha256(payload.encode()).hexdigest()

L = state["listings"]
active = [l for l in L if l["state"] == "ACTIVE"]
sold = [l for l in L if l["state"] == "SOLD"]
claimed = [l for l in L if l["state"] == "CLAIMED"]
dup_active = [a for a, c in collections.Counter(l["assetId"] for l in active).items() if c > 1]
dup_mint = [a for a, c in collections.Counter(minted).items() if c > 1]
dup_nf = len(nfs) - len(set(nfs))
bad_sold = sum(1 for l in sold if not l.get("soldTxid"))
bad_claim = sum(1 for l in claimed if not l.get("claimTxid"))
prices = [int(l["price"]) for l in sold]
transfers = sum(1 for n in state["notes"] if n.get("kind") in ("transfer", "send", "private"))
art = pinfo.get("art", {})

out = {
  "block": h,
  "blockHash": status.get("blockHash"),
  "checkedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
  "digestLocal": digest,
  "digestApi": status.get("digest") if status.get("lastHeight") == h else None,
  "digestMatch": (digest == status.get("digest")) if status.get("lastHeight") == h else None,
  "mints": len(minted), "supply": 3333,
  "dupActive": len(dup_active), "dupMints": len(dup_mint), "dupNullifiers": dup_nf,
  "nullifiers": len(nfs), "listingsTotal": len(L),
  "active": len(active), "sold": len(sold), "claimed": len(claimed),
  "badSold": bad_sold, "badClaim": bad_claim,
  "volumeBTC": round(sum(prices) / 1e8, 7), "highestBTC": max(prices) / 1e8 if prices else 0,
  "avgBTC": round(sum(prices) / len(prices) / 1e8, 6) if prices else 0,
  "floorBTC": min(int(l["price"]) for l in active) / 1e8 if active else None,
  "artHave": art.get("have"), "artOf": art.get("of"), "artVerified": art.get("verified"),
  "noteKinds": dict(collections.Counter(n.get("kind") for n in state["notes"])),
}
json.dump(out, open(DATA + "/audit_latest.json", "w"), indent=1)
print(json.dumps(out, indent=1))
