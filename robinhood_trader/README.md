# Leveraged Nasdaq Trend-Rotation Trader (TQQQ / SQQQ)

An automated end-of-day rotation system that trades **TQQQ** (3x long Nasdaq-100) and
**SQQQ** (3x inverse) in a Robinhood account, using **QQQ as the signal proxy** for all
moving-average and RSI calculations.

> ⚠️ **READ THIS FIRST — risk disclosure.** This trades **3x leveraged ETFs with real
> money**. These instruments can lose value extraordinarily fast and suffer volatility
> decay over time. This is **not** a guaranteed or even expected path to "doubling money
> every year" — that target was explicitly rejected during design because engineering for
> it means engineering for ruin. The realistic goal here is a *disciplined, risk-limited*
> trend system that survives drawdowns. **You can lose a large fraction of this account.**
> Only the owner authorized this, only in the designated account, with a hard drawdown
> circuit breaker. Nothing here is financial advice.

## Account
Trades **only** account `855664652` ("Agentic") — the single account flagged
`agentic_allowed=true`. Every other account is off-limits to automation per Robinhood policy.

## Strategy (aggressive posture)
Signals computed on **QQQ** daily closes; trades executed in **TQQQ / SQQQ**:

| QQQ condition | RSI(14) filter | Target |
|---|---|---|
| Price > 50-MA **and** > 200-MA | RSI < 80 | **TQQQ** (long, 98% deployed) |
| Price > 50-MA **and** > 200-MA | RSI ≥ 80 | CASH (blow-off risk) |
| Price < 50-MA **and** < 200-MA | RSI > 20 | **SQQQ** (inverse, 98% deployed) |
| Price < 50-MA **and** < 200-MA | RSI ≤ 20 | CASH (oversold-bounce risk) |
| In between MAs (chop) | — | CASH (avoid decay whipsaw) |

**Circuit breaker:** if total equity falls **≥30% below its high-water mark**, the system
liquidates to cash and **halts**. It stays halted until a human sets `halted: false` in
`state.json`. This is the single most important safety mechanism — do not disable it.

Parameters live at the top of `signal_engine.py` (`SMA_FAST`, `SMA_SLOW`, `RSI_LEN`,
`RSI_OVERBOUGHT`, `RSI_OVERSOLD`, `DRAWDOWN_HALT`, `DEPLOY_FRACTION`).

## Files
| File | Purpose |
|---|---|
| `signal_engine.py` | Pure signal computation. Fetches QQQ daily bars (Yahoo), computes MAs/RSI, applies the circuit breaker, prints a JSON decision. Places **no** orders. |
| `DAILY_RUN.md` | The playbook a scheduled Claude session follows to read the account, run the engine, place rotation orders via the Robinhood MCP, and persist state. |
| `state.json` | Persisted high-water mark, halt flag, last target, and run history. Committed each day so the next run inherits it. |

## How it actually runs (important)
Orders go through the `robinhood-trading` **MCP server**, which is OAuth-authenticated to
*this* Claude session. A normal cron job **cannot** place these orders — it has no MCP
credentials. So "run every trading day" means a **recurring Claude Code session** that
opens `DAILY_RUN.md` and executes it.

### Scheduling (you set this up)
In **Claude Code on the web** (https://claude.com/code), create a **scheduled session /
trigger** on this repo + branch (`claude/robinhood-trading-wO8ot`) with a prompt like:

> "Execute robinhood_trader/DAILY_RUN.md for today. Re-authenticate the robinhood-trading
> MCP if needed, run the full playbook, place any required rotation order in account
> 855664652, update and push state.json, then report."

Schedule it for **trading days at ~3:30 PM ET** (before the 4 PM close, so dollar/fractional
orders are accepted and the daily bar is near-final). Set the environment's network policy
to allow `*.finance.yahoo.com` (data) in addition to the Robinhood MCP host.
See https://code.claude.com/docs/en/claude-code-on-the-web for trigger/schedule setup.

> **Re-auth note:** the MCP OAuth token may expire between sessions. If a scheduled run
> finds the Robinhood tools unauthenticated, it must call `authenticate` and surface the
> URL — which needs you to approve in a browser. Plan to re-approve periodically.

## Manual run / testing
```bash
# See today's signal without trading (omit --equity to skip the circuit-breaker check):
python3 robinhood_trader/signal_engine.py --equity 171.27
```

## Status
- **2026-06-10:** Live. First order placed — BUY TQQQ $167.84 (queued, fills next open).
  Regime BULL (QQQ 716.07 > 50MA 670.65 > 200MA 622.57, RSI 53.6). High-water mark $171.27.
