"""
🐸 Swanix Price Oracle (by Swanix) - Kronos candle forecaster.
Kronos (github.com/shiyu-coder/Kronos, AAAI 2026) turns candles into tokens and predicts the next ones.
We pull the last 512 public Binance candles, sample many futures, read the regime from how the paths
agree, check the model walk-forward on the past with fees, and send the read to Telegram.
Signals only: it never trades, never touches an exchange account or wallet. Not financial advice.
"""
import json, os, sys, time, urllib.request, pathlib
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Kronos"))
sys.path.insert(0, str(ROOT.parent / "common"))
from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402
import torch  # noqa: E402
import guards as G  # noqa: E402  (shadow mode, HTF trend, min R:R, closed candles, grading, bias guard)

IST = timezone(timedelta(hours=5, minutes=30))
LOOKBACK, PRED = 512, int(os.getenv("PRED_LEN", "24"))
PATHS = int(os.getenv("PATHS", "30"))
FEE = 0.001  # 0.1% per side, Binance spot taker
BT_WINDOWS = int(os.getenv("BT_WINDOWS", "6"))


def klines(sym, tf, limit=1000):
    u = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval={tf}&limit={limit}"
    rows = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "swanix-oracle"}), timeout=30).read())
    df = pd.DataFrame(rows, columns="t open high low close volume ct amount n tb tq x".split())
    now_ms = int(time.time() * 1000)
    df = df[df["ct"].astype("int64") < now_ms]  # closed candles only: never read the forming candle (no repaint)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = df[c].astype(float)
    df["timestamps"] = pd.to_datetime(df["t"], unit="ms")
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)


def paths(pred, hist, n, steps):
    step = hist["timestamps"].iloc[-1] - hist["timestamps"].iloc[-2]
    xt = hist["timestamps"].reset_index(drop=True)
    yt = pd.Series([xt.iloc[-1] + step * (i + 1) for i in range(steps)])
    x = hist[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
    out = pred.predict_batch([x] * n, [xt] * n, [yt] * n, pred_len=steps, T=1.0, top_p=0.9, sample_count=1, verbose=False)
    return np.stack([o["close"].values for o in out]), yt  # (n, steps)


def read(closes, last):
    end = closes[:, -1] / last - 1
    up = float((end > 0).mean())
    agree = max(up, 1 - up)
    regime = "TRENDING UP" if up >= 0.7 else "TRENDING DOWN" if up <= 0.3 else "CHOPPY"
    return {"up_share": up, "agree": agree, "regime": regime, "median": float(np.median(end)),
            "p10": float(np.percentile(end, 10)), "p90": float(np.percentile(end, 90)),
            "spread": float(np.percentile(end, 90) - np.percentile(end, 10))}


def backtest(pred, df, steps):
    """Walk-forward on past windows: trade only when >=70% of paths agree, pay fees both sides."""
    trades, hits, pnl = 0, 0, 0.0
    n = len(df)
    for k in range(BT_WINDOWS, 0, -1):
        end = n - k * steps
        if end < LOOKBACK:
            continue
        hist, fut = df.iloc[end - LOOKBACK:end], df.iloc[end:end + steps]
        c, _ = paths(pred, hist, max(10, PATHS // 3), steps)
        r = read(c, hist["close"].iloc[-1])
        real = fut["close"].iloc[-1] / hist["close"].iloc[-1] - 1
        if r["agree"] >= 0.7:
            side = 1 if r["up_share"] >= 0.7 else -1
            trades += 1
            hits += int(np.sign(real) == side)
            pnl += side * real - 2 * FEE
    return {"trades": trades, "hits": hits, "pnl": pnl, "windows": BT_WINDOWS}


def confidence(r, bt, hist):
    """0-100 score from: how many paths agree, how tight they are vs normal volatility, and the backtest."""
    rets = hist["close"].pct_change().dropna()
    vol = float(rets.std() * np.sqrt(PRED))  # normal move over the forecast horizon
    agree = (r["agree"] - 0.5) / 0.5                      # 0 = coin flip, 1 = every path agrees
    tight = float(np.clip(1 - r["spread"] / (2.5 * vol + 1e-9), 0, 1)) if vol else 0.5
    if bt and bt["trades"]:
        hit = bt["hits"] / bt["trades"]
        btq = float(np.clip((hit - 0.4) / 0.4, 0, 1)) * (1 if bt["pnl"] > 0 else 0.5)
    else:
        btq = 0.3
    score = round(100 * (0.5 * agree + 0.2 * tight + 0.3 * btq))
    warns = []
    if r["regime"] == "CHOPPY":
        warns.append("Paths split both ways: no clear direction, stay flat or size small")
    if r["spread"] > 2 * vol:
        warns.append(f"Very wide range ({r['spread']*100:.1f}%), far above normal volatility ({vol*100:.1f}%)")
    if bt is not None and bt["trades"] == 0:
        warns.append("Backtest made no confident calls, so this read is untested")
    elif bt and bt["pnl"] <= 0:
        warns.append(f"Backtest lost money after fees ({bt['pnl']*100:+.2f}%), don't trust the direction")
    elif bt and bt["trades"] < 3:
        warns.append("Backtest sample is tiny, the hit rate means little")
    if abs(r["median"]) < 2 * FEE:
        warns.append("Expected move is smaller than round-trip fees")
    if hist["volume"].iloc[-24:].mean() < 0.3 * hist["volume"].mean():
        warns.append("Volume is thin right now, moves can be erratic")
    label = "HIGH" if score >= 65 else "MEDIUM" if score >= 40 else "LOW"
    return score, label, vol, warns


WATERMARK = "Not Financial Advice | © Swanix Crypto Inc.™"
SCAN = os.getenv("SCAN") == "1"           # alert mode: only speak when a strong setup exists
SCAN_MIN = int(os.getenv("SCAN_MIN", "72"))
SENT_F = ROOT / "sent.json"
SENT = json.loads(SENT_F.read_text()) if SENT_F.exists() else {}
MARGIN_MIN, MARGIN_MAX = float(os.getenv("MARGIN_MIN", "10")), float(os.getenv("MARGIN_MAX", "40"))
LEV_MIN, LEV_MAX = int(os.getenv("LEV_MIN", "10")), int(os.getenv("LEV_MAX", "50"))


def risk_plan(call, last, c, hist, score):
    """Stop loss / take profits from the sampled paths, leverage that keeps liquidation well past the stop,
    and $ outcomes for Swanix's usual size ($10-40 margin, 10x-50x)."""
    if not call.startswith(("LONG", "SHORT")):
        return None, []
    side = 1 if call.startswith("LONG") else -1
    hi, lo = c.max(axis=1) / last - 1, c.min(axis=1) / last - 1          # path extremes over the horizon
    tr = (hist["high"] - hist["low"]).iloc[-14:].mean() / last            # ATR-ish, one candle
    if side == 1:
        sl = min(np.percentile(lo, 20), -1.2 * tr)                         # 80% of paths never dip this far
        tp1, tp2 = np.percentile(c[:, -1] / last - 1, 60), np.percentile(hi, 70)
    else:
        sl = max(np.percentile(hi, 80), 1.2 * tr)
        tp1, tp2 = np.percentile(c[:, -1] / last - 1, 40), np.percentile(lo, 30)
    sl_d = abs(sl)
    tp1_d, tp2_d = side * tp1, side * tp2
    warns = []
    if tp1_d <= 2 * FEE:
        return None, ["Take-profit is inside fees, skip this trade"]
    rr = tp1_d / sl_d
    # liquidation sits about (1/lev - 0.5% maintenance) away; keep it at least 1.8x the stop distance
    safe = int(min(LEV_MAX, max(1, (1 / (1.8 * sl_d + 0.005)))))
    lev = max(min(safe, LEV_MAX), 1)
    if score < 65:
        lev = max(1, min(lev, int(lev * 0.6)))  # medium confidence: cut leverage
    if lev < LEV_MIN:
        warns.append(f"Stop is {sl_d*100:.1f}% away, so even {LEV_MIN}x risks liquidation before the stop. Use {lev}x or skip")
    if rr < 1.2:
        warns.append(f"Reward to risk is only {rr:.1f}:1")
    margin = MARGIN_MIN if score < 65 else min(MARGIN_MAX, 25)
    notional = margin * lev
    fees = notional * 2 * FEE
    loss = notional * sl_d + fees
    win1, win2 = notional * tp1_d - fees, notional * tp2_d - fees
    if loss > margin * 0.9:
        warns.append("Stop would eat almost the whole margin; liquidation risk is high")
    return {"side": "LONG" if side == 1 else "SHORT", "sl": last * (1 + sl), "tp1": last * (1 + tp1), "tp2": last * (1 + tp2),
            "sl_pct": sl_d, "tp1_pct": tp1_d, "tp2_pct": tp2_d, "rr": rr, "lev": lev, "margin": margin,
            "entry": last, "loss": loss, "win1": win1, "win2": win2, "liq_pct": max(0.0, 1 / lev - 0.005)}, warns


def post_channel(setups):
    """Auto-post fresh trade alerts to the public channel (his rule, 2026-10-20). Falls back to a DM if the bot can't post."""
    tok, ch = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("CHANNEL_ID", "@swanixcrypto")
    if not tok or not setups:
        return
    import urllib.parse
    for s_ in setups:
        body = s_.strip().replace("🚨 TRADE SETUP\n", "")
        text = ("🚨 SWANIX TRADE ALERT 🐸\n\n" + body +
                "\n\n" + WATERMARK)
        try:
            data = urllib.parse.urlencode({"chat_id": ch, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30).read()
            print("posted to", ch)
        except Exception as e:
            err = getattr(e, "read", lambda: b"")().decode()[:200]
            print("channel post failed:", e, err)
            try:
                from notify import telegram
                telegram(f"⚠️ Couldn't post the trade alert to {ch}: {err or e}\nMake the bot an admin of the channel with 'Post messages' so alerts go out automatically.")
            except Exception:
                pass
            return


LEARN = (lambda p: json.loads(p.read_text()) if p.exists() else {})(ROOT / "learnings.json").get("coins", {})


def learned(sym, score, warns):
    """Feed the real track record back in: past alerts on this coin that hit TP1 vs the stop."""
    c = LEARN.get(sym)
    n = (c["win"] + c["loss"]) if c else 0
    if n < 3:
        return score, warns
    wr = c["win"] / n
    score = int(max(0, min(100, score + (wr - 0.5) * 30)))
    if n >= 4 and wr < 0.35:
        warns = warns + [f"Track record: only {c['win']}/{n} past {sym} alerts hit TP1, skip this coin for now"]
    else:
        warns = warns + [f"Track record: {c['win']}/{n} past {sym} alerts hit TP1"]
    return score, warns


def live_price(sym):
    """Price right now (the model reads closed candles, and sampling takes minutes, so the candle close goes stale)."""
    try:
        with urllib.request.urlopen(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}", timeout=10) as r:
            return float(json.loads(r.read())["price"])
    except Exception:
        return None


def reanchor(plan, now_px):
    """Keep the forecast's absolute stop and targets, but quote entry, % and $ from the live price.
    Returns (plan or None, warning)."""
    side = 1 if plan["side"] == "LONG" else -1
    e0 = plan["entry"]
    if side * (now_px - plan["sl"]) <= 0:
        return None, f"Price already hit the stop zone ({fmt(now_px)}), setup dead"
    done = side * (now_px - e0) / (side * (plan["tp1"] - e0))
    if done >= 0.5:
        return None, f"Price already moved {done*100:.0f}% of the way to TP1 ({fmt(now_px)}), don't chase"
    sl_d, tp1_d, tp2_d = abs(plan["sl"] / now_px - 1), side * (plan["tp1"] / now_px - 1), side * (plan["tp2"] / now_px - 1)
    if tp1_d <= 2 * FEE:
        return None, "Take-profit now inside fees from the live price, skip"
    n = plan["margin"] * plan["lev"]; fees = n * 2 * FEE
    plan.update(entry=now_px, sl_pct=sl_d, tp1_pct=tp1_d, tp2_pct=tp2_d, rr=tp1_d / sl_d,
                loss=n * sl_d + fees, win1=n * tp1_d - fees, win2=n * tp2_d - fees)
    return plan, ("Reward to risk from the live price is only %.1f:1" % plan["rr"]) if plan["rr"] < 1.2 else None


def finalize(plan, sl, score):
    """Re-derive %, leverage and $ outcomes after the stop moved to real structure."""
    e, side = plan["entry"], (1 if plan["side"] == "LONG" else -1)
    sl_d = abs(1 - sl / e)
    tp1_d, tp2_d = side * (plan["tp1"] / e - 1), side * (plan["tp2"] / e - 1)
    lev = max(1, int(min(LEV_MAX, 1 / (1.8 * sl_d + 0.005))))
    if score < 65:
        lev = max(1, min(lev, int(lev * 0.6)))
    n = plan["margin"] * lev; fees = n * 2 * FEE
    rr, _ = G.net_rr(plan["side"], e, sl, plan["tp1"])
    plan.update(sl=sl, sl_pct=sl_d, tp1_pct=tp1_d, tp2_pct=tp2_d, rr=rr, lev=lev,
                loss=n * sl_d + fees, win1=n * tp1_d - fees, win2=n * tp2_d - fees, liq_pct=max(0.0, 1 / lev - 0.005))
    return plan


def apply_guards(sym, plan, hist, score, cfg, ledger):
    """Returns (plan or None, reason, htf). Order: structural stop -> min R:R -> HTF trend -> bias guard."""
    sl = G.structural_stop(plan["side"], plan["entry"], plan["sl"], list(hist["high"]), list(hist["low"]))
    plan = finalize(plan, sl, score)
    ok, rr, why = G.rr_ok(plan["side"], plan["entry"], plan["sl"], plan["tp1"], cfg["min_rr"])
    if not ok:
        return None, why, None
    try:
        htf = G.htf_trend(sym)
    except Exception as e:
        return None, f"Higher-timeframe check failed ({e}), no call", None
    ok, why = G.trend_allows(plan["side"], htf)
    if not ok:
        return None, why, htf
    ok, why = G.bias_guard(G.recent_sides(ledger, cfg["bias_window"]), plan["side"], htf,
                           cfg["bias_window"], cfg["bias_max_share"])
    if not ok:
        return None, why, htf
    return plan, None, htf


def fmt(p):
    return f"{p:,.2f}" if p >= 1 else f"{p:.6f}"


def main():
    pairs = []
    for p in (os.getenv("PAIRS") or "BTC:1h,ETH:1h,SOL:1h").replace(" ", "").split(","):
        if not p:
            continue
        sym, tf = (p.split(":") + ["1h"])[:2]
        sym = sym.upper()
        if not sym.endswith(("USDT", "USDC", "FDUSD")):
            sym += "USDT"
        pairs.append(f"{sym}:{tf.lower() or '1h'}")
    torch.set_num_threads(os.cpu_count() or 2)
    t0 = time.time()
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    pred = KronosPredictor(mdl, tok, device="cpu", max_context=512)
    now = datetime.now(IST).strftime("%d %b %I:%M %p")
    msg = [f"🐸🔮 Swanix Price Oracle (Kronos-small) · {now} IST",
           f"{PATHS} sampled futures each, next {PRED} candles.\n"]
    report = []
    global PLANS
    PLANS = []
    cfg = G.config()
    ledger = G.load(G.LEDGER_F, [])
    card = G.scorecard(ledger, cfg)
    POST = G.can_post(cfg, card)
    hit_txt = "n/a" if card["hit_rate"] is None else f"{card['hit_rate'] * 100:.0f}%"
    shadow_note = (f"🧪 SHADOW MODE: calls are logged and graded, not signals. Record so far: {card['window']} resolved, "
                   f"hit {hit_txt} ({card['gate_reason']}).") if not POST else None
    if shadow_note:
        msg.insert(1, shadow_note)
    for pr in pairs:
        sym, tf = pr.strip().upper().split(":") if ":" in pr else (pr.strip().upper(), "1h")
        tf = tf.lower()
        try:
            df = klines(sym, tf)
            hist = df.iloc[-LOOKBACK:]
            last = hist["close"].iloc[-1]
            c, yt = paths(pred, hist, PATHS, PRED)
            r = read(c, last)
            bt = backtest(pred, df, PRED) if BT_WINDOWS else None
            med = last * (1 + r["median"])
            score, label, vol, warns = confidence(r, bt, hist)
            score, warns = learned(sym, score, warns)
            label = "HIGH" if score >= 65 else "MEDIUM" if score >= 40 else "LOW"
            now_px = live_price(sym) or last
            drift = now_px / last - 1
            if r["regime"] == "CHOPPY" or label == "LOW":
                call = "NO TRADE (wait)"
            else:
                call = ("LONG bias" if r["up_share"] >= 0.7 else "SHORT bias")
            icon = "🟢" if r["regime"] == "TRENDING UP" else "🔴" if r["regime"] == "TRENDING DOWN" else "🟡"
            line = (f"{icon} {sym} {tf} · live {fmt(now_px)} (model read the {fmt(last)} candle close, {drift*100:+.2f}% since)\n"
                    f"• Call: {call} · Confidence {score}/100 ({label})\n"
                    f"• Regime: {r['regime']} ({r['agree']*100:.0f}% of paths agree, {r['up_share']*100:.0f}% end higher)\n"
                    f"• Median in {PRED} candles: {fmt(med)} ({r['median']*100:+.2f}%)\n"
                    f"• Range 10-90%: {fmt(last*(1+r['p10']))} to {fmt(last*(1+r['p90']))} (risk spread {r['spread']*100:.2f}%)")
            if bt:
                hr = f"{bt['hits']}/{bt['trades']} right" if bt["trades"] else "no confident calls"
                line += f"\n• Backtest last {bt['windows']} windows (fees in): {hr}, net {bt['pnl']*100:+.2f}%"
            plan, pw = risk_plan(call, last, c, hist, score)
            warns += pw
            if plan:
                plan, rw = reanchor(plan, now_px)
                if rw:
                    warns.append(rw)
                if plan is None and SCAN:
                    print(sym, "no setup:", rw); continue
            # model-quality filters first (scan only), so the paper ledger only holds real candidates
            bt_ok = bool(bt and bt["trades"] >= 4 and bt["hits"] / bt["trades"] >= 0.66 and bt["pnl"] > 0)   # proven on enough history
            if SCAN and not bt_ok:
                print(sym, "no setup: backtest too thin or weak", bt); continue
            if SCAN and not (plan and score >= SCAN_MIN and not any("skip" in w.lower() or "lost money" in w for w in warns)):
                print(sym, "no setup:", call, score); continue
            if plan:
                gplan, why, htf = apply_guards(sym, plan, hist, score, cfg, ledger)
                if gplan is None:
                    G.log_blocked({"ts": int(time.time()), "sym": sym, "tf": tf, "side": plan["side"], "reason": why,
                                   "entry": plan["entry"], "htf": {k: v.get("trend") for k, v in (htf or {}).items()}})
                    print(sym, "blocked:", why)
                    warns.append(why)
                    call = "NO TRADE (blocked by guard)"
                    if SCAN:
                        # paper-grade the blocked setup too (mode "blocked"): never posted, never counts toward the gate,
                        # but shows whether each guard is saving money or just killing winners (2026-10-26 deadlock fix)
                        if not G.is_dupe(ledger, sym, tf, plan["side"], time.time(), cfg["dedupe_hours"]):
                            ledger.append(G.new_call(sym, tf, plan["side"], plan["entry"], plan["sl"], plan["tp1"], plan["tp2"],
                                                     int(df["timestamps"].iloc[-1].value // 10**6), "blocked",
                                                     extra={"confidence": score, "block_reason": why,
                                                            "htf": {k: v.get("trend") for k, v in (htf or {}).items()}}))
                            G.save(G.LEDGER_F, ledger)
                        continue
                plan = gplan
            if plan and not POST:
                line += f"\n🧪 Shadow {plan['side']} call logged for grading (entry {fmt(plan['entry'])}, stop {fmt(plan['sl'])}, TP1 {fmt(plan['tp1'])}, R:R {plan['rr']:.1f}). Not a signal."
            if plan and POST:
                line += (f"\n🎯 Plan ({plan['side']}): entry ~{fmt(plan['entry'])} (live price)"
                         f"\n• Stop loss {fmt(plan['sl'])} ({plan['sl_pct']*100:.2f}% away)"
                         f"\n• TP1 {fmt(plan['tp1'])} ({plan['tp1_pct']*100:+.2f}%), TP2 {fmt(plan['tp2'])} ({plan['tp2_pct']*100:+.2f}%) · R:R {plan['rr']:.1f}"
                         f"\n• Size: ${plan['margin']:.0f} margin at {plan['lev']}x max (liq ~{plan['liq_pct']*100:.1f}% away, past the stop)"
                         f"\n• If stopped: -${plan['loss']:.2f} · TP1: +${plan['win1']:.2f} · TP2: +${plan['win2']:.2f} (fees in)"
                         f"\n• Take half at TP1, move stop to entry")
            if plan and POST:   # invalidation = the stop (the p10/p90 of the end price can sit on the wrong side of entry)
                line += f"\n• Invalid if a 1h candle closes {'below' if plan['side'] == 'LONG' else 'above'} {fmt(plan['sl'])}"
            if warns:
                line += "\n⚠️ " + "\n⚠️ ".join(warns)
            if plan and SCAN:   # every scan call that survives the guards goes into the grading ledger (shadow or live)
                if not G.is_dupe(ledger, sym, tf, plan["side"], time.time(), cfg["dedupe_hours"]):
                    ledger.append(G.new_call(sym, tf, plan["side"], plan["entry"], plan["sl"], plan["tp1"], plan["tp2"],
                                             int(df["timestamps"].iloc[-1].value // 10**6), "live" if POST else "shadow",
                                             extra={"confidence": score, "rr": round(plan["rr"], 2),
                                                    "htf": {k: v.get("trend") for k, v in (htf or {}).items()}}))
                    G.save(G.LEDGER_F, ledger)
            if SCAN:
                k = f"{sym}:{tf}:{plan['side']}"
                if time.time() - SENT.get(k, 0) < 6 * 3600:   # same setup already pitched in the last 6h
                    print(sym, "already sent"); continue
                SENT[k] = time.time()
                line = "🚨 TRADE SETUP\n" + line
            msg.append(line + "\n")
            if SCAN:
                PLANS.append({"sym": sym, "side": plan["side"], "entry": plan["entry"], "sl": plan["sl"], "tp1": plan["tp1"]})
            report.append({"pair": sym, "tf": tf, "last": last, **r, "backtest": bt, "confidence": score, "call": call, "warnings": warns, "plan": None,
                           "median_path": [float(v) for v in np.median(c, axis=0)],
                           "times": [str(t) for t in yt]})
            print(line, "\n")
        except Exception as e:
            print(sym, "failed:", e)
            if not SCAN:
                msg.append(f"⚠️ {sym} {tf}: skipped ({str(e)[:80]})\n")
    SENT_F.write_text(json.dumps({k: v for k, v in SENT.items() if time.time() - v < 86400}))
    if SCAN and len(msg) <= (3 if shadow_note else 2):
        print("scan: no strong setup right now, staying quiet")
        return
    msg.append(WATERMARK)
    text = "\n".join(msg)
    if SCAN:
        msg[0] = msg[0].replace("Swanix Price Oracle", "Swanix Trade Alert")
    (ROOT / "latest.json").write_text(json.dumps({"at": now, "pairs": report}, indent=1))
    if SCAN and not POST:
        (ROOT / "latest.json").write_text(json.dumps({"at": now, "pairs": report}, indent=1))
        print("SHADOW MODE: setups logged to calls.json for grading, nothing written to alerts.json (not posted).")
        # paper setups that passed every guard go to the owner's DM only (TELEGRAM_CHAT_ID), never the public channel
        try:
            from notify import telegram
            telegram(text.replace("Swanix Trade Alert", "Swanix PAPER Setup").replace("🚨 TRADE SETUP", "📝 PAPER SETUP (graded, not a signal)"))
        except Exception as e:
            print("paper DM failed:", e)
        print(text)
        return
    if SCAN:
        # Alerts go out through @swanixalertsbot: it reads forecast/alerts.json and broadcasts to every subscriber.
        f = ROOT / "alerts.json"
        old = json.loads(f.read_text()) if f.exists() else []
        for i, s_ in enumerate(msg[2:-1]):
            body = s_.strip().replace("🚨 TRADE SETUP\n", "")
            old.append({"id": f"{int(time.time())}-{abs(hash(body)) % 10**8}", "ts": int(time.time()),
                        "plan": PLANS[i] if i < len(PLANS) else None,
                        "text": "🚨 SWANIX TRADE ALERT 🐸\n\n" + body + "\n\n" + WATERMARK})
        f.write_text(json.dumps([a for a in old if time.time() - a["ts"] < 3 * 86400], indent=1, ensure_ascii=False))
        print(text)
        return
    try:
        from notify import telegram
        telegram(text)
    except Exception as e:
        print("notify failed:", e)
    print(text)


if __name__ == "__main__":
    main()
