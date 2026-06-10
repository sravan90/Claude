# DAILY_RUN — Leveraged Nasdaq Trend Rotation (TQQQ / SQQQ)

> This file is the **playbook a scheduled Claude Code session runs every trading day.**
> Robinhood orders are placed through the `robinhood-trading` MCP tools, which only
> the Claude session can call — a plain cron cannot. So the "schedule" is a
> recurring Claude web session pointed at this file (see README.md → Scheduling).

**Account:** `855664652` ("Agentic", cash account — the ONLY `agentic_allowed=true` account).
**Never trade any other account.**

Run this **once per trading day, during regular market hours (9:30 AM–4 PM ET)** —
ideally ~15–30 min before the close (≈3:30 PM ET) so signals use a near-final daily bar
and dollar/fractional orders are accepted (they only execute in regular hours).

---

## Steps

### 1. Read current account state
- `get_portfolio(account_number="855664652")` → record `total_value` as **equity**.
- `get_equity_positions(account_number="855664652")` → record current holding
  (symbol + quantity). This is the **source of truth** for what we currently hold.

### 2. Compute today's target signal
Run the engine with the live equity (for the drawdown circuit breaker):

```bash
cd robinhood_trader && python3 signal_engine.py --equity <equity_from_step_1>
```

It prints JSON with `target` ∈ {`TQQQ`, `SQQQ`, `CASH`}, plus `regime`, `reason`,
and `halted`. **If the engine exits non-zero (data failure), DO NOT TRADE** — log it
and stop. Trading blind on stale data is worse than skipping a day.

### 3. Reconcile holdings to the target
Let `current` = the symbol currently held (TQQQ, SQQQ, or none/CASH).
Let `target` = engine output.

- **If `current == target`:** nothing to do. (For TQQQ/SQQQ, a held position needs no
  top-up unless it's drifted far from `deploy_fraction` — don't churn.)
- **If `current != target`:**
  1. **Exit** the current ETF if held: `get_equity_quotes` for the spread, then
     `review_equity_order` → `place_equity_order` with **side=sell**, `type=market`,
     `quantity=<full position quantity>`, `market_hours=regular_hours`.
     Wait for it to fill (poll `get_equity_orders`), because this is a **cash account** —
     buying-power from the sale must be available before the next buy.
  2. **Enter** the new target if it's an ETF (not CASH): re-read `get_portfolio` for
     freed buying power, then `review_equity_order` → `place_equity_order` with
     **side=buy**, `type=market`, `dollar_amount = round(buying_power * 0.98, 2)`,
     `market_hours=regular_hours`. Use a fresh UUID `ref_id`.
  3. **If target == CASH:** just exit; hold cash, place no buy.

> **Cash-account discipline:** never sell a position and rebuy a *different* one with the
> SAME unsettled proceeds twice in one day — that risks a good-faith violation. One
> rotation per day max. If a sale hasn't settled and you'd need those funds, wait a day.

### 4. Update state and persist
Edit `robinhood_trader/state.json`:
- `high_water_mark = max(old high_water_mark, equity_from_step_1)`
- `halted` = the engine's `halted` value (stays true once tripped until a human resets it)
- `last_target = target`, `last_run = <today>`
- Append a `history` entry: date, equity, regime, target, action taken, order_id(s),
  and the qqq_close/sma50/sma200/rsi14 from the engine.

Then commit and push so the next day's session inherits the state:
```bash
git add robinhood_trader/state.json && \
git commit -m "daily run <date>: <regime> -> <target>" && \
git push -u origin claude/robinhood-trading-wO8ot
```

### 5. Report
One concise message: equity, regime, signal, action taken (or "no change"),
drawdown vs high-water mark, and whether the circuit breaker is armed/tripped.

---

## Circuit breaker (hard rule)
If the engine returns `halted: true` (equity fell ≥30% below the high-water mark),
the target is forced to **CASH**: liquidate any holding and place no new buys. The system
stays halted until a human edits `state.json` to set `halted: false`. **Do not auto-reset it.**

## Guardrails (never violate)
- Only ever trade account `855664652`.
- Only ever trade `TQQQ`, `SQQQ`, or hold cash. No other symbols, no options, no margin.
- Max one rotation per day. Never average down into a losing leveraged position beyond
  the engine's `deploy_fraction`.
- If anything is ambiguous, data is stale, or an order behaves unexpectedly, STOP and
  report — do not improvise new strategy logic.
