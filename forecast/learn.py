"""Swanix oracle grader: resolve every logged call (forecast/calls.json) as win / loss / expired from later
closed 5m candles, then write the rolling scorecard (forecast/scorecard.json) that decides whether public
posting is allowed, plus the per-coin record the oracle reads (forecast/learnings.json).
  python forecast/learn.py            grade + write files
  python forecast/learn.py --summary  print the stats only (no network)
Public Binance data only, no keys. Not Financial Advice | © Swanix Crypto Inc.™"""
import json, sys, time
import guards as G

ROOT = G.ROOT


def import_alerts(ledger):
    """Older alerts that went out before the ledger existed get graded too (mode 'live')."""
    have = {c.get("alert_id") for c in ledger}
    for a in G.load(ROOT / "alerts.json", []):
        p = a.get("plan")
        if not p or a["id"] in have:
            continue
        ledger.append(G.new_call(p["sym"], "1h", p["side"], p["entry"], p["sl"], p["tp1"], p.get("tp2"),
                                 None, "live", now=a["ts"], extra={"alert_id": a["id"], "imported": True}))
    return ledger


def candles_from(sym, ts):
    out, start = [], ts * 1000
    end = (ts + G.HORIZON) * 1000
    while start < end:
        rows = G.fetch(sym, "5m", 1000, start_ms=start)
        if not rows:
            break
        out += rows
        start = int(rows[-1][0]) + 1
        if len(rows) < 1000:
            break
    return out


def summary(card):
    hr = f"{card['hit_rate']*100:.0f}%" if card["hit_rate"] is not None else "n/a"
    ex = f"{card['expectancy_r']:+.2f}R" if card["expectancy_r"] is not None else "n/a"
    mode = "LIVE (posting)" if card["posting"] else "SHADOW (logging + grading, not posting)"
    lines = [f"🐸 Swanix Oracle scorecard · mode: {mode}",
             f"Rolling last {card['window']} resolved: {card['wins']}W / {card['losses']}L / {card['expired']} expired · hit {hr} · expectancy {ex}",
             f"Open calls: {card['open_calls']} · resolved all-time: {card['resolved_total']} · sides {card['sides_resolved']}",
             f"Gate: {'OPEN' if card['gate_open'] else 'closed'} ({card['gate_reason']}) · shadow flag: {'on' if card['shadow_flag'] else 'off'}"]
    bp = card.get("blocked_paper") or {}
    if bp.get("resolved"):
        lines.append(f"Blocked setups graded on paper: {bp['wins']}/{bp['resolved']} would have hit TP1 · expectancy {bp['expectancy_r']:+.2f}R")
        for why, d in bp["by_guard"].items():
            lines.append(f"  {why}: {d['win']}W {d['loss']}L {d['expired']}E ({d['sum_r']:+.2f}R)")
    for sym, d in sorted(card["coins"].items()):
        lines.append(f"  {sym}: {d['win']}W {d['loss']}L {d['expired']}E")
    return "\n".join(lines)


def main():
    cfg = G.config()
    ledger = G.load(G.LEDGER_F, [])
    if "--summary" in sys.argv:
        print(summary(G.scorecard(ledger, cfg)))
        return
    ledger = import_alerts(ledger)
    for c in ledger:
        if c.get("status", "open") != "open":
            continue
        try:
            G.grade_call(c, candles_from(c["sym"], c["ts"]))
            if c["status"] != "open":
                print(c["sym"], c["side"], "->", c["status"], c.get("r"))
        except Exception as e:
            print("grade failed", c["id"], e)
    G.save(G.LEDGER_F, sorted(ledger, key=lambda c: c["ts"]))
    card = G.scorecard(ledger, cfg)
    G.save(G.SCORE_F, card)
    # per-coin record in the shape kronos_forecast.learned() reads
    coins = {s: {"win": d["win"], "loss": d["loss"] + d["expired"], "expired": d["expired"], "win_rate": d["win_rate"]}
             for s, d in card["coins"].items()}
    G.save(ROOT / "learnings.json", {"coins": coins, "updated": int(time.time()), "source": "calls.json"})
    print(summary(card))


if __name__ == "__main__":
    main()
