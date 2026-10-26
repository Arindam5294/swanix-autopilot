"""Swanix oracle guard rails (added 2026-10-21 after a run of losing SHORT calls in an up-trending market).

Pure-python, no torch: the oracle, the grader and the tests all import this.
  1. Shadow mode    - calls are logged + graded, never posted, until the graded record earns it.
  2. HTF trend      - no SHORT when the 4h or 1D trend is up, no LONG when either is down.
  3. Min R:R        - stop moved past real structure (recent swing + ATR buffer), then R:R >= 1.5 net of fees.
  4. Closed candles - only candles whose close time has passed are ever used.
  5. Grading ledger - every call stored with entry/stop/targets/time, resolved win/loss/expired by later candles.
  6. Bias guard     - if >80% of the last 10 calls point one way while the HTF trend points the other, block.
Signals only. Not Financial Advice | (c) Swanix Crypto Inc.
"""
import json, os, pathlib, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_F = ROOT / "oracle_config.json"
LEDGER_F = ROOT / "calls.json"
BLOCKED_F = ROOT / "blocked.json"
SCORE_F = ROOT / "scorecard.json"
FEE = 0.001                      # 0.1% per side
HORIZON = 24 * 3600              # a call that hits neither stop nor TP1 in 24h is "expired"
API = "https://data-api.binance.vision/api/v3/klines"

DEFAULTS = {"shadow": True, "min_rr": 1.5, "gate_min_calls": 20, "gate_min_hit": 0.55,
            "gate_window": 30, "bias_window": 10, "bias_max_share": 0.8, "dedupe_hours": 6}


# ---------- config ----------
def config():
    """oracle_config.json, with env overrides. ORACLE_SHADOW=0 is the only way to ask for live posting,
    and even then posting happens only while the graded gate is open (see can_post)."""
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_F.read_text()))
    except Exception:
        pass
    env = os.getenv("ORACLE_SHADOW", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        cfg["shadow"] = False
    elif env in ("1", "true", "on", "yes"):
        cfg["shadow"] = True
    if os.getenv("ORACLE_MIN_RR"):
        cfg["min_rr"] = float(os.getenv("ORACLE_MIN_RR"))
    cfg["min_rr"] = max(cfg["min_rr"], 1.5)          # hard floor (lowered from 1.8 on 2026-10-26), config can only tighten it
    return cfg


# ---------- candles ----------
def closed_only(rows, now_ms=None):
    """Drop any kline whose close time (index 6) has not passed yet: never read the forming candle."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return [r for r in rows if int(r[6]) < now_ms]


def fetch(sym, tf, limit=200, end_ms=None, start_ms=None):
    u = f"{API}?symbol={sym}&interval={tf}&limit={limit}"
    if end_ms:
        u += f"&endTime={int(end_ms)}"
    if start_ms:
        u += f"&startTime={int(start_ms)}"
    req = urllib.request.Request(u, headers={"User-Agent": "swanix-oracle"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())
    return closed_only(rows, end_ms if end_ms else None)


# ---------- trend ----------
def ema(vals, n):
    k, out = 2 / (n + 1), []
    for v in vals:
        out.append(v if not out else v * k + out[-1] * (1 - k))
    return out


def tf_trend(closes, n=50, slope_bars=5):
    """up: close above EMA50 and EMA50 rising. down: close below EMA50 and EMA50 falling. else flat."""
    if len(closes) < n + slope_bars:
        return {"trend": "unknown"}
    e = ema(closes, n)
    rising, falling = e[-1] > e[-1 - slope_bars], e[-1] < e[-1 - slope_bars]
    c = closes[-1]
    t = "up" if c > e[-1] and rising else "down" if c < e[-1] and falling else "flat"
    return {"trend": t, "close": c, "ema50": e[-1], "ema_slope_pct": (e[-1] / e[-1 - slope_bars] - 1) * 100}


def htf_trend(sym, at_ms=None, fetcher=fetch, signal_tf="1h"):
    """4h + 1D trend, plus the signal timeframe itself: the 21 Oct losing shorts all fired while the 1h
    chart was grinding up above a rising EMA50 even though 4h/1D were still pointing down."""
    out = {}
    for tf in dict.fromkeys(("4h", "1d", signal_tf)):
        rows = fetcher(sym, tf, 200, end_ms=at_ms)
        out[tf] = tf_trend([float(r[4]) for r in rows])
    return out


def _trends(htf):
    return {tf: v.get("trend") for tf, v in htf.items()}


def trend_allows(side, htf):
    """No SHORT if any checked timeframe (4h, 1D, signal tf) is trending up; no LONG if any is trending down."""
    t = _trends(htf)
    desc = ", ".join(f"{tf} {v}" for tf, v in t.items())
    if t.get("4h") in (None, "unknown") or t.get("1d") in (None, "unknown"):
        return False, f"Higher-timeframe trend unknown ({desc}), no call"
    if side == "SHORT" and "up" in t.values():
        return False, f"SHORT blocked: trend is up ({desc})"
    if side == "LONG" and "down" in t.values():
        return False, f"LONG blocked: trend is down ({desc})"
    return True, f"Trend ok ({desc})"


def opposes(side, htf):
    ts = _trends(htf).values()
    return ("up" in ts) if side == "SHORT" else ("down" in ts)


# ---------- stop + reward:risk ----------
def structural_stop(side, entry, model_sl, highs, lows, lookback=12, atr_mult=0.25):
    """Push the stop past the recent swing (last `lookback` closed candles) plus an ATR buffer.
    It can only widen the model's stop, never tighten it."""
    hs, ls = highs[-lookback:], lows[-lookback:]
    atr = sum(h - l for h, l in zip(hs, ls)) / max(1, len(hs))
    if side == "SHORT":
        return max(model_sl, max(hs) + atr_mult * atr)
    return min(model_sl, min(ls) - atr_mult * atr)


def net_rr(side, entry, sl, tp):
    """Reward:risk net of round-trip fees. Returns (rr, reason-or-None). Geometry must be sane."""
    s = 1 if side == "LONG" else -1
    if not (s * (entry - sl) > 0 and s * (tp - entry) > 0):
        return 0.0, f"Bad geometry for {side}: stop {sl:.6g} / entry {entry:.6g} / target {tp:.6g}"
    reward = s * (tp / entry - 1) - 2 * FEE
    risk = s * (1 - sl / entry) + 2 * FEE
    if reward <= 0:
        return 0.0, "Target is inside fees"
    return reward / risk, None


def rr_ok(side, entry, sl, tp, min_rr):
    rr, why = net_rr(side, entry, sl, tp)
    if why:
        return False, rr, why
    if rr < min_rr:
        return False, rr, f"R:R {rr:.2f} after real stop placement is below {min_rr}"
    return True, rr, None


# ---------- direction-bias guard ----------
def bias_guard(recent_sides, side, htf, window=10, max_share=0.8, min_calls=5):
    """Block when >max_share of the last `window` calls are one direction, the new call is that same
    direction, and the higher-timeframe trend opposes it (the 'shorting a grind-up' failure)."""
    last = list(recent_sides)[-window:]
    if len(last) < min_calls:
        return True, None
    dom = max(("LONG", "SHORT"), key=last.count)
    share = last.count(dom) / len(last)
    if share > max_share and side == dom and opposes(dom, htf):
        return False, f"Bias guard: {last.count(dom)}/{len(last)} recent calls were {dom} while the HTF trend opposes it"
    return True, None


# ---------- ledger + grading ----------
def load(p, d):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return d


def save(p, obj):
    pathlib.Path(p).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def recent_sides(ledger, n=10):
    return [c["side"] for c in sorted([c for c in ledger if c.get("mode") != "blocked"], key=lambda c: c["ts"])[-n:]]


def is_dupe(ledger, sym, tf, side, now, hours=6):
    return any(c["sym"] == sym and c.get("tf") == tf and c["side"] == side and now - c["ts"] < hours * 3600 for c in ledger)


def new_call(sym, tf, side, entry, sl, tp1, tp2, candle_open_ms, mode, now=None, extra=None):
    now = int(now or time.time())
    c = {"id": f"{sym}-{tf}-{side}-{now}", "ts": now, "sym": sym, "tf": tf, "side": side,
         "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "candle_open_ms": candle_open_ms,
         "mode": mode, "status": "open"}
    c.update(extra or {})
    return c


def log_blocked(entry, keep_days=7):
    b = load(BLOCKED_F, [])
    b.append(entry)
    now = time.time()
    save(BLOCKED_F, [x for x in b if now - x.get("ts", now) < keep_days * 86400])


def grade_call(call, candles, now=None):
    """candles: closed klines (any tf, ideally 5m) from the call time on. Mutates + returns the call.
    TP1 before stop = win, stop first (or both in one candle) = loss, neither within HORIZON = expired
    (scored mark-to-market at the last candle inside the horizon)."""
    if call.get("status") not in (None, "open"):
        return call
    now = time.time() if now is None else now
    s = 1 if call["side"] == "LONG" else -1
    e, sl, tp = call["entry"], call["sl"], call["tp1"]
    risk = s * (1 - sl / e) + 2 * FEE
    end_ms = (call["ts"] + HORIZON) * 1000
    last_close = None
    for k in candles:
        if int(k[0]) < call["ts"] * 1000 or int(k[0]) >= end_ms:
            continue
        hi, lo, cl = float(k[2]), float(k[3]), float(k[4])
        hit_sl = lo <= sl if s == 1 else hi >= sl
        hit_tp = hi >= tp if s == 1 else lo <= tp
        if hit_sl:
            call.update(status="loss", resolved_at=int(int(k[6]) / 1000), r=-1.0)
            return call
        if hit_tp:
            call.update(status="win", resolved_at=int(int(k[6]) / 1000),
                        r=round((s * (tp / e - 1) - 2 * FEE) / risk, 3))
            return call
        last_close = cl
    if now - call["ts"] >= HORIZON and last_close is not None:
        call.update(status="expired", resolved_at=call["ts"] + HORIZON,
                    r=round((s * (last_close / e - 1) - 2 * FEE) / risk, 3))
    return call


def scorecard(ledger, cfg=None):
    cfg = cfg or config()
    allres = sorted([c for c in ledger if c.get("status") in ("win", "loss", "expired")], key=lambda c: c.get("resolved_at", c["ts"]))
    res = [c for c in allres if c.get("mode") != "blocked"]          # gate = calls that passed every guard
    blk = [c for c in allres if c.get("mode") == "blocked"]          # paper-graded setups the guards stopped
    blk_by = {}
    for c in blk:
        r = (c.get("block_reason") or "").split(":")[0].split(" after")[0][:40] or "other"
        d = blk_by.setdefault(r, {"win": 0, "loss": 0, "expired": 0, "sum_r": 0.0})
        d[c["status"]] += 1; d["sum_r"] = round(d["sum_r"] + c.get("r", 0), 3)
    win = res[-cfg["gate_window"]:]
    n = len(win)
    wins = sum(c["status"] == "win" for c in win)
    hit = wins / n if n else None
    exp = (sum(c.get("r", 0) for c in win) / n) if n else None
    by = {}
    for c in res:
        d = by.setdefault(c["sym"], {"win": 0, "loss": 0, "expired": 0})
        d[c["status"]] += 1
    for d in by.values():
        m = d["win"] + d["loss"] + d["expired"]
        d["win_rate"] = round(d["win"] / m, 3) if m else None
    sides = {s: sum(c["side"] == s for c in res) for s in ("LONG", "SHORT")}
    gate = bool(n >= cfg["gate_min_calls"] and hit is not None and hit >= cfg["gate_min_hit"] and exp > 0)
    if n < cfg["gate_min_calls"]:
        why = f"need {cfg['gate_min_calls']} resolved calls, have {n}"
    elif not gate:
        why = f"hit rate {hit*100:.0f}% (need {cfg['gate_min_hit']*100:.0f}%) and expectancy {exp:+.2f}R (need > 0)"
    else:
        why = f"earned: {wins}/{n} hit ({hit*100:.0f}%), expectancy {exp:+.2f}R"
    return {"updated": int(time.time()), "window": n, "wins": wins,
            "losses": sum(c["status"] == "loss" for c in win), "expired": sum(c["status"] == "expired" for c in win),
            "hit_rate": round(hit, 3) if hit is not None else None, "expectancy_r": round(exp, 3) if exp is not None else None,
            "open_calls": sum(c.get("status") == "open" for c in ledger), "resolved_total": len(res),
            "sides_resolved": sides, "coins": by, "gate_open": gate, "gate_reason": why,
            "shadow_flag": cfg["shadow"], "posting": (not cfg["shadow"]) and gate,
            "blocked_paper": {"resolved": len(blk), "wins": sum(c["status"] == "win" for c in blk),
                              "expectancy_r": round(sum(c.get("r", 0) for c in blk) / len(blk), 3) if blk else None,
                              "by_guard": blk_by}}


def can_post(cfg=None, card=None, ledger=None):
    """Public posting needs BOTH: the shadow flag switched off AND the graded gate open."""
    cfg = cfg or config()
    card = card or scorecard(load(LEDGER_F, []) if ledger is None else ledger, cfg)
    return (not cfg["shadow"]) and card["gate_open"]
