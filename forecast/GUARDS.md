# Oracle guard rails (2026-10-21)

Added after five SHORT alerts in a row fired into a 1h grind-up (BTC went out at ~1.2:1).

| Guard | Rule |
|---|---|
| Shadow mode | **On by default.** Calls are logged to `calls.json` and graded, but nothing is written to `alerts.json` (so @swanixalertsbot broadcasts nothing). Posting needs BOTH `shadow: false` AND the gate open. |
| Gate | Over the last 30 resolved calls (min 20): hit rate ≥ 55% and expectancy > 0R. |
| Trend filter | No SHORT if 4h, 1D or the signal timeframe (1h) is trending up (close above a rising EMA50). No LONG if any is trending down. Unknown 4h/1D trend = no call. |
| Min R:R | Stop is widened past the last 12 closed candles' swing + 0.25 ATR, then net-of-fees R:R to TP1 must be ≥ 1.5 (hard floor since 2026-10-26, was 1.8; config can only raise it). |
| Closed candles | Only klines whose close time has passed are used (oracle, trend checks, grading). |
| Bias guard | If >80% of the last 10 calls are one direction and the trend opposes it, block that direction. |
| Grading | `learn.py` resolves each call from 5m candles: TP1 first = win, stop first (or same candle) = loss, neither in 24h = expired (marked to market). Writes `scorecard.json` and `learnings.json`. Blocked setups go to `blocked.json` (7 days). |

**Stats:** `python forecast/learn.py --summary` (or read `forecast/scorecard.json`; every oracle run prints it in the merge step).

**Turning shadow off:** set `"shadow": false` in `forecast/oracle_config.json` (or repo variable `ORACLE_SHADOW=0` under Settings → Secrets and variables → Actions → Variables). Even then nothing posts until the gate is open. `ORACLE_SHADOW=1` forces shadow back on.

**Tests:** `python -m unittest discover -s forecast/tests -v` · real-data dry run: `python forecast/replay_guards.py --sweep`.

## 2026-10-26: deadlock fix
The gate needed 20 graded calls, but the guards blocked every setup, so nothing was ever graded and the gate could never open.
- Scan setups that pass the model filters but get **blocked by a guard** are now paper-graded too (`mode: "blocked"` in `calls.json`). They never post and never count toward the gate. `scorecard.json → blocked_paper` shows, guard by guard, whether blocking them saved money.
- While in shadow mode, setups that pass **every** guard are DM'd to the owner (TELEGRAM_CHAT_ID) as "📝 PAPER SETUP". They're never sent to the public channel.
- Min R:R lowered to 1.5. The bias guard ignores blocked paper calls.
