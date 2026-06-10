#!/usr/bin/env python3
"""
Signal engine for the leveraged Nasdaq trend-rotation strategy.

Design decisions (see README.md for full rationale):
  * QQQ (the unleveraged Nasdaq-100 ETF) is the SIGNAL proxy. We compute every
    indicator on QQQ and only TRADE the leveraged children TQQQ / SQQQ. Computing
    MAs/RSI on the 3x ETFs themselves is noisier and distorted by volatility decay.
  * Daily bars only. This is an end-of-day rotation system, not an intraday one,
    which also keeps a cash account clear of good-faith / settlement violations.
  * The engine is PURE: it fetches data, reads state, and prints a decision as
    JSON. It never places orders. Order placement is done by the Claude session
    that runs DAILY_RUN.md, which alone holds the Robinhood MCP credentials.

Usage:
    python3 signal_engine.py [--equity 171.27] [--state state.json]

Exit code is always 0 on a successful decision; non-zero only on data failure
(so the playbook can refuse to trade on stale/missing data rather than guess).
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# --- Strategy parameters (aggressive posture, chosen by account owner) ---------
SMA_FAST = 50          # trend-confirmation MA
SMA_SLOW = 200         # primary regime MA
RSI_LEN = 14
RSI_OVERBOUGHT = 80    # don't ENTER TQQQ above this (blow-off risk) -> wait in cash
RSI_OVERSOLD = 20      # don't ENTER SQQQ below this (oversold bounce risk) -> wait in cash
DRAWDOWN_HALT = 0.30   # halt all trading if equity falls 30% below high-water mark
DEPLOY_FRACTION = 0.98 # fraction of account value to deploy into the target ETF

LONG_SYMBOL = "TQQQ"   # 3x long Nasdaq-100
SHORT_SYMBOL = "SQQQ"  # 3x inverse Nasdaq-100 (bought long in a cash account)
SIGNAL_SYMBOL = "QQQ"  # signal proxy

YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch_daily_closes(symbol, rng="2y"):
    """Return (dates, closes) of daily closing prices for `symbol` from Yahoo."""
    last_err = None
    for host in YAHOO_HOSTS:
        url = (f"https://{host}/v8/finance/chart/{symbol}"
               f"?range={rng}&interval=1d&includePrePost=false")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = json.load(resp)
            result = payload["chart"]["result"][0]
            ts = result["timestamp"]
            closes = result["indicators"]["quote"][0]["close"]
            # Drop any null bars (Yahoo occasionally returns gaps).
            dates, vals = [], []
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                dates.append(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"))
                vals.append(float(c))
            if len(vals) >= SMA_SLOW + 5:
                return dates, vals
            last_err = f"insufficient bars ({len(vals)}) from {host}"
        except (urllib.error.URLError, KeyError, ValueError, TypeError) as e:
            last_err = f"{host}: {e}"
            continue
    raise RuntimeError(f"could not fetch {symbol} data: {last_err}")


def sma(values, length):
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def rsi_wilder(values, length=RSI_LEN):
    """Classic Wilder's RSI on closing prices."""
    if len(values) < length + 1:
        return None
    gains, losses = [], []
    for i in range(1, length + 1):
        chg = values[i] - values[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length
    for i in range(length + 1, len(values)):
        chg = values[i] - values[i - 1]
        gain = max(chg, 0.0)
        loss = max(-chg, 0.0)
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"high_water_mark": 0.0, "halted": False, "last_target": "CASH", "history": []}


def decide(equity, state):
    dates, closes = fetch_daily_closes(SIGNAL_SYMBOL)
    price = closes[-1]
    s_fast = sma(closes, SMA_FAST)
    s_slow = sma(closes, SMA_SLOW)
    rsi = rsi_wilder(closes)

    # --- Circuit breaker (high-water-mark drawdown halt) ----------------------
    hwm = max(float(state.get("high_water_mark", 0.0)), float(equity or 0.0))
    halted = bool(state.get("halted", False))
    dd = 0.0 if hwm == 0 else (hwm - float(equity or 0.0)) / hwm
    if equity is not None and hwm > 0 and dd >= DRAWDOWN_HALT:
        halted = True

    if halted:
        return {
            "asof": dates[-1], "signal_symbol": SIGNAL_SYMBOL,
            "qqq_close": round(price, 2), "sma50": round(s_fast, 2),
            "sma200": round(s_slow, 2), "rsi14": round(rsi, 1),
            "regime": "HALTED", "target": "CASH", "deploy_fraction": 0.0,
            "high_water_mark": round(hwm, 2), "drawdown_pct": round(dd * 100, 1),
            "halted": True,
            "reason": (f"CIRCUIT BREAKER: drawdown {dd*100:.1f}% >= {DRAWDOWN_HALT*100:.0f}% "
                       f"from high-water mark ${hwm:.2f}. Trading halted; liquidate to cash. "
                       f"Manual reset required (set halted=false in state.json)."),
        }

    # --- Regime classification on QQQ -----------------------------------------
    bullish = price > s_slow and price > s_fast
    bearish = price < s_slow and price < s_fast

    if bullish:
        if rsi >= RSI_OVERBOUGHT:
            target, regime = "CASH", "BULL_OVERBOUGHT"
            reason = (f"QQQ {price:.2f} > 50MA {s_fast:.2f} > 200MA but RSI {rsi:.1f} "
                      f">= {RSI_OVERBOUGHT}: blow-off risk, wait in cash for pullback.")
        else:
            target, regime = LONG_SYMBOL, "BULL"
            reason = (f"QQQ {price:.2f} > 50MA {s_fast:.2f} and > 200MA {s_slow:.2f}, "
                      f"RSI {rsi:.1f} healthy: long {LONG_SYMBOL}.")
    elif bearish:
        if rsi <= RSI_OVERSOLD:
            target, regime = "CASH", "BEAR_OVERSOLD"
            reason = (f"QQQ {price:.2f} < 50MA {s_fast:.2f} < 200MA but RSI {rsi:.1f} "
                      f"<= {RSI_OVERSOLD}: oversold bounce risk, wait in cash.")
        else:
            target, regime = SHORT_SYMBOL, "BEAR"
            reason = (f"QQQ {price:.2f} < 50MA {s_fast:.2f} and < 200MA {s_slow:.2f}, "
                      f"RSI {rsi:.1f}: downtrend, hold inverse {SHORT_SYMBOL}.")
    else:
        target, regime = "CASH", "CHOP"
        reason = (f"QQQ {price:.2f} between 50MA {s_fast:.2f} and 200MA {s_slow:.2f}: "
                  f"mixed/chop zone, stay in cash to avoid leveraged-ETF decay whipsaw.")

    return {
        "asof": dates[-1], "signal_symbol": SIGNAL_SYMBOL,
        "qqq_close": round(price, 2), "sma50": round(s_fast, 2),
        "sma200": round(s_slow, 2), "rsi14": round(rsi, 1),
        "regime": regime, "target": target,
        "deploy_fraction": DEPLOY_FRACTION if target != "CASH" else 0.0,
        "high_water_mark": round(hwm, 2), "drawdown_pct": round(dd * 100, 1),
        "halted": False, "reason": reason,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=None,
                    help="Current total account value (for drawdown circuit breaker).")
    ap.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "state.json"))
    args = ap.parse_args()
    try:
        state = load_state(args.state)
        decision = decide(args.equity, state)
    except Exception as e:  # data failure -> non-zero so the playbook refuses to trade
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
