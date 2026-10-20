"""Swanix oracle learnings log: grade every past alert (did TP1 or the stop hit first?) and keep a
per-coin scorecard the oracle reads to sharpen confidence. Public Binance data only, no keys.
Not Financial Advice | © Swanix Crypto Inc.™"""
import json, pathlib, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
HORIZON = 24 * 3600          # a setup that hits neither level in 24h is "expired"


def load(p, d):
    try:
        return json.loads(p.read_text())
    except Exception:
        return d


def candles(sym, start_ms):
    u = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=5m&startTime={start_ms}&limit=300"
    with urllib.request.urlopen(u, timeout=15) as r:
        return json.loads(r.read())


def grade(plan, ts):
    side = 1 if plan["side"] == "LONG" else -1
    for k in candles(plan["sym"], int(ts * 1000)):
        hi, lo = float(k[2]), float(k[3])
        hit_sl = lo <= plan["sl"] if side == 1 else hi >= plan["sl"]
        hit_tp = hi >= plan["tp1"] if side == 1 else lo <= plan["tp1"]
        if hit_sl:                       # both in one candle counts as a loss (conservative)
            return "loss", int(k[0] / 1000)
        if hit_tp:
            return "win", int(k[0] / 1000)
    return ("expired", None) if time.time() - ts > HORIZON else (None, None)


def main():
    alerts = load(ROOT / "alerts.json", [])
    L = load(ROOT / "learnings.json", {"graded": {}, "coins": {}})
    for a in alerts:
        p = a.get("plan")
        if not p or a["id"] in L["graded"]:
            continue
        try:
            res, at = grade(p, a["ts"])
        except Exception as e:
            print("grade failed", a["id"], e); continue
        if res:
            L["graded"][a["id"]] = {"sym": p["sym"], "side": p["side"], "result": res, "ts": a["ts"], "at": at}
            print(p["sym"], p["side"], "->", res)
    coins = {}
    for g in L["graded"].values():
        c = coins.setdefault(g["sym"], {"win": 0, "loss": 0, "expired": 0})
        c[g["result"]] += 1
    for c in coins.values():
        n = c["win"] + c["loss"]
        c["win_rate"] = round(c["win"] / n, 3) if n else None
    L["coins"] = coins
    L["updated"] = int(time.time())
    (ROOT / "learnings.json").write_text(json.dumps(L, indent=1))


if __name__ == "__main__":
    main()
