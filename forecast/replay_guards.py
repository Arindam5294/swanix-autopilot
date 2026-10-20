"""Dry-run of the guard rails on real public Binance candles (no model, no posting).
  python forecast/replay_guards.py           replay the real alerts in calls.json at their own timestamps
  python forecast/replay_guards.py --sweep   14-day sweep: naive hourly SHORT/LONG setups with and without the guards
Every lookup uses only candles that had CLOSED by the decision time."""
import json, sys, time
import guards as G

COINS = "BTC ETH SOL BNB XRP DOGE SUI LINK".split()


def replay():
    cfg = G.config()
    calls = sorted(G.load(G.LEDGER_F, []), key=lambda c: c["ts"])
    print("Replay of the real alerts (as of each alert's own time):")
    blocked = 0
    for i, c in enumerate(calls):
        at = c["ts"] * 1000
        h1 = G.fetch(c["sym"], "1h", 30, end_ms=at)
        sl = G.structural_stop(c["side"], c["entry"], c["sl"], [float(r[2]) for r in h1], [float(r[3]) for r in h1])
        ok_rr, rr, why_rr = G.rr_ok(c["side"], c["entry"], sl, c["tp1"], cfg["min_rr"])
        htf = G.htf_trend(c["sym"], at_ms=at)
        ok_t, why_t = G.trend_allows(c["side"], htf)
        ok_b, why_b = G.bias_guard([x["side"] for x in calls[:i]], c["side"], htf)
        reasons = [w for ok, w in ((ok_rr, why_rr), (ok_t, why_t), (ok_b, why_b)) if not ok]
        blocked += bool(reasons)
        t4, t1 = htf["4h"], htf["1d"]
        print(f"- {c['sym']} {c['side']} @ {c['entry']:g}: 4h {t4['trend']} (close {t4['close']:g} vs EMA50 {t4['ema50']:.5g}, slope {t4['ema_slope_pct']:+.2f}%), "
              f"1D {t1['trend']} · R:R as posted {G.net_rr(c['side'], c['entry'], c['sl'], c['tp1'])[0]:.2f}, after real stop {rr:.2f}"
              f"\n    -> {'BLOCKED: ' + ' | '.join(reasons) if reasons else 'would still pass'}")
    print(f"{blocked}/{len(calls)} real alerts blocked by the new guards")
    return blocked, len(calls)


def sweep(days=14):
    cfg = G.config()
    now = int(time.time())
    out = {"SHORT": {"raw": [0, 0, 0], "kept": [0, 0, 0]}, "LONG": {"raw": [0, 0, 0], "kept": [0, 0, 0]}}
    for coin in COINS:
        sym = coin + "USDT"
        h1 = G.fetch(sym, "1h", 1000)
        h4 = G.fetch(sym, "4h", 1000)
        d1 = G.fetch(sym, "1d", 400)
        start = now - days * 86400
        for i in range(30, len(h1) - 24):
            t_close = int(h1[i][6]) // 1000
            if t_close < start or t_close + G.HORIZON > now:
                continue
            ms = int(h1[i][6])
            htf = {"4h": G.tf_trend([float(r[4]) for r in h4 if int(r[6]) <= ms][-200:]),
                   "1d": G.tf_trend([float(r[4]) for r in d1 if int(r[6]) <= ms][-200:]),
                   "1h": G.tf_trend([float(r[4]) for r in h1[max(0, i - 199):i + 1]])}
            e = float(h1[i][4]); hs = [float(r[2]) for r in h1[i - 11:i + 1]]; ls = [float(r[3]) for r in h1[i - 11:i + 1]]
            fut = h1[i + 1:i + 25]
            for side in ("SHORT", "LONG"):
                s = 1 if side == "LONG" else -1
                sl = G.structural_stop(side, e, e * (1 - s * 0.006), hs, ls)
                risk = abs(sl / e - 1)
                tp = e * (1 + s * 1.8 * (risk + 4 * G.FEE))      # target sized to clear the 1.8 floor
                c = G.grade_call(G.new_call(sym, "1h", side, e, sl, tp, tp, None, "sweep", now=t_close), fut, now=now)
                idx = {"win": 0, "loss": 1, "expired": 2}[c["status"]] if c["status"] != "open" else None
                if idx is None:
                    continue
                out[side]["raw"][idx] += 1
                if G.trend_allows(side, htf)[0]:
                    out[side]["kept"][idx] += 1
    print(f"\n{days}-day sweep, 8 coins, every closed 1h candle, structural stop, TP at 1.8R, 24h horizon:")
    for side, d in out.items():
        for k_ in ("raw", "kept"):
            w, l, x = d[k_]; n = w + l + x
            print(f"  {side:5} {'no filter ' if k_ == 'raw' else 'trend filter'}: {n:5} setups · {w} win / {l} loss / {x} expired · hit {w / n * 100 if n else 0:.1f}%")
    return out


if __name__ == "__main__":
    replay()
    if "--sweep" in sys.argv:
        sweep()
