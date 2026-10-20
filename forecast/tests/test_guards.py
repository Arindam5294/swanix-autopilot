"""Offline tests for forecast/guards.py:  python -m unittest discover -s forecast/tests -v"""
import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import guards as G

H = 3600


def series(start, step, n):
    return [start * (1 + step) ** i for i in range(n)]


def k(t, o, h, l, c, close_t=None):          # binance kline row
    return [t * 1000, str(o), str(h), str(l), str(c), "0", (close_t or t + 299) * 1000]


UP = {"4h": {"trend": "up"}, "1d": {"trend": "up"}}
DOWN = {"4h": {"trend": "down"}, "1d": {"trend": "down"}}


class Trend(unittest.TestCase):
    def test_grind_up_is_up_and_blocks_shorts(self):
        closes = series(80000, 0.002, 120)       # slow grind higher, like BTC into the bad shorts
        t = G.tf_trend(closes)
        self.assertEqual(t["trend"], "up")
        ok, why = G.trend_allows("SHORT", {"4h": t, "1d": {"trend": "flat"}})
        self.assertFalse(ok); self.assertIn("SHORT blocked", why)
        self.assertTrue(G.trend_allows("LONG", {"4h": t, "1d": {"trend": "flat"}})[0])

    def test_downtrend_blocks_longs(self):
        t = G.tf_trend(series(100, -0.003, 120))
        self.assertEqual(t["trend"], "down")
        self.assertFalse(G.trend_allows("LONG", {"4h": t, "1d": t})[0])
        self.assertTrue(G.trend_allows("SHORT", {"4h": t, "1d": t})[0])

    def test_either_timeframe_up_blocks_short(self):
        self.assertFalse(G.trend_allows("SHORT", {"4h": {"trend": "flat"}, "1d": {"trend": "up"}})[0])

    def test_signal_tf_grind_up_blocks_short_even_if_4h_down(self):
        # the real 21 Oct pattern: 4h/1D down, 1h grinding up above a rising EMA50
        h1 = G.tf_trend(series(0.0796, 0.0012, 120))
        self.assertEqual(h1["trend"], "up")
        self.assertFalse(G.trend_allows("SHORT", {"4h": {"trend": "down"}, "1d": {"trend": "down"}, "1h": h1})[0])

    def test_unknown_trend_blocks(self):
        self.assertFalse(G.trend_allows("LONG", {"4h": G.tf_trend([1, 2, 3]), "1d": {"trend": "up"}})[0])


class ClosedCandles(unittest.TestCase):
    def test_forming_candle_dropped(self):
        now = 1_000_000
        rows = [k(now - 7200, 1, 1, 1, 1, now - 3601), k(now - 3600, 1, 1, 1, 1, now - 1), k(now, 1, 1, 1, 1, now + 3599)]
        out = G.closed_only(rows, now * 1000)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(int(r[6]) < now * 1000 for r in out))


class RewardRisk(unittest.TestCase):
    def test_btc_alert_1_2_to_1_rejected(self):          # the real 21 Oct BTC short
        ok, rr, why = G.rr_ok("SHORT", 82578.0, 83120.73, 81943.32, 1.8)
        self.assertFalse(ok); self.assertLess(rr, 1.8)

    def test_good_rr_passes_and_floor_is_hard(self):
        ok, rr, _ = G.rr_ok("LONG", 100, 99, 103, 1.8)
        self.assertTrue(ok); self.assertGreater(rr, 1.8)
        self.assertFalse(G.rr_ok("LONG", 100, 99, 101.5, 1.8)[0])

    def test_bad_geometry_rejected(self):              # stop on the wrong side of entry
        self.assertFalse(G.rr_ok("SHORT", 100, 99, 97, 1.8)[0])

    def test_structural_stop_only_widens(self):
        highs = [101, 102, 104, 103]; lows = [99, 98, 99, 100]
        sl = G.structural_stop("SHORT", 100, 101.0, highs, lows)
        self.assertGreater(sl, 104)                     # past the swing high
        self.assertEqual(G.structural_stop("SHORT", 100, 110.0, highs, lows), 110.0)
        self.assertLess(G.structural_stop("LONG", 100, 99.5, highs, lows), 98)

    def test_doge_style_short_fails_after_real_stop(self):
        # DOGE 21 Oct: model stop 0.98% away but the 1h swing high sat higher -> real stop wider -> R:R collapses
        highs = [0.0812, 0.0816, 0.0823, 0.0819, 0.0815, 0.0813]
        lows = [0.0806, 0.0809, 0.0811, 0.0810, 0.0808, 0.0809]
        sl = G.structural_stop("SHORT", 0.08133, 0.082128, highs, lows)
        self.assertFalse(G.rr_ok("SHORT", 0.08133, sl, 0.079613, 1.8)[0])


class Bias(unittest.TestCase):
    def test_all_shorts_into_uptrend_blocked(self):
        ok, why = G.bias_guard(["SHORT"] * 5, "SHORT", UP)
        self.assertFalse(ok); self.assertIn("Bias guard", why)

    def test_same_streak_with_trend_is_fine(self):
        self.assertTrue(G.bias_guard(["SHORT"] * 10, "SHORT", DOWN)[0])

    def test_opposite_call_allowed(self):
        self.assertTrue(G.bias_guard(["SHORT"] * 10, "LONG", UP)[0])

    def test_80_percent_is_not_over_80(self):
        self.assertTrue(G.bias_guard(["SHORT"] * 8 + ["LONG"] * 2, "SHORT", UP)[0])
        self.assertFalse(G.bias_guard(["SHORT"] * 9 + ["LONG"], "SHORT", UP)[0])


class Grading(unittest.TestCase):
    def call(self, side="LONG"):
        return G.new_call("TESTUSDT", "1h", side, 100.0, 98.0 if side == "LONG" else 102.0,
                          104.0 if side == "LONG" else 96.0, 106.0, None, "shadow", now=1_000_000)

    def test_win(self):
        c = G.grade_call(self.call(), [k(1_000_000, 100, 101, 99, 100.5), k(1_000_300, 100.5, 104.5, 100, 104)], now=1_000_600)
        self.assertEqual(c["status"], "win"); self.assertGreater(c["r"], 1.5)

    def test_loss_and_same_candle_is_loss(self):
        c = G.grade_call(self.call("SHORT"), [k(1_000_000, 100, 102.5, 99, 102)], now=1_000_600)
        self.assertEqual((c["status"], c["r"]), ("loss", -1.0))
        c = G.grade_call(self.call(), [k(1_000_000, 100, 105, 97, 100)], now=1_000_600)
        self.assertEqual(c["status"], "loss")

    def test_open_then_expired(self):
        rows = [k(1_000_000 + i * 300, 100, 100.5, 99.5, 100.2) for i in range(10)]
        self.assertEqual(G.grade_call(self.call(), rows, now=1_000_000 + 3000)["status"], "open")
        c = G.grade_call(self.call(), rows, now=1_000_000 + G.HORIZON + 1)
        self.assertEqual(c["status"], "expired"); self.assertLess(abs(c["r"]), 1)

    def test_candle_before_call_ignored(self):
        c = G.grade_call(self.call(), [k(999_700, 100, 110, 90, 100)], now=1_000_100)
        self.assertEqual(c["status"], "open")


class Gate(unittest.TestCase):
    cfg = dict(G.DEFAULTS)

    def ledger(self, wins, losses):
        L = []
        for i in range(wins + losses):
            L.append({"id": str(i), "ts": i, "resolved_at": i, "sym": "X", "side": "LONG",
                      "status": "win" if i < wins else "loss", "r": 1.9 if i < wins else -1.0})
        return L

    def test_needs_20_resolved(self):
        self.assertFalse(G.scorecard(self.ledger(19, 0), self.cfg)["gate_open"])

    def test_needs_55pct_and_positive_expectancy(self):
        self.assertFalse(G.scorecard(self.ledger(10, 10), self.cfg)["gate_open"])     # 50%
        self.assertTrue(G.scorecard(self.ledger(11, 9), self.cfg)["gate_open"])       # 55%, +0.6R

    def test_shadow_flag_blocks_even_when_gate_open(self):
        L = self.ledger(15, 5)
        self.assertFalse(G.can_post(dict(self.cfg, shadow=True), ledger=L))
        self.assertTrue(G.can_post(dict(self.cfg, shadow=False), ledger=L))
        self.assertFalse(G.can_post(dict(self.cfg, shadow=False), ledger=self.ledger(5, 15)))

    def test_default_config_is_shadow_and_min_rr_floor(self):
        import os
        os.environ.pop("ORACLE_SHADOW", None)
        c = G.config()
        self.assertTrue(c["shadow"]); self.assertGreaterEqual(c["min_rr"], 1.8)
        os.environ["ORACLE_MIN_RR"] = "1.2"
        self.assertEqual(G.config()["min_rr"], 1.8)
        os.environ.pop("ORACLE_MIN_RR")


if __name__ == "__main__":
    unittest.main()
