#!/usr/bin/env bash
#
# run_daily.sh — local cron wrapper that runs the TQQQ/SQQQ rotation playbook once.
#
# It runs Claude Code headless, pointed at DAILY_RUN.md, with ONLY the Bash tool
# and the robinhood-trading MCP tools enabled. Claude reuses the OAuth token you
# set up interactively via `/mcp` (stored in ~/.claude.json / your keychain).
#
# SETUP (one time, on the always-on machine that will run cron):
#   1. Install Claude Code and clone this repo. Check out the branch below.
#   2. Run `claude` interactively once, do `/mcp`, authenticate `robinhood-trading`.
#      (This stores the OAuth token that headless runs will reuse + auto-refresh.)
#   3. Make sure `python3`, `git`, and `claude` are on PATH for cron (see README).
#   4. chmod +x robinhood_trader/run_daily.sh
#   5. Add the crontab line from README.md (Scheduling section).
#
set -euo pipefail

# --- Config -------------------------------------------------------------------
BRANCH="claude/robinhood-trading-wO8ot"
# Repo root = parent of the dir this script lives in.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/robinhood_trader/logs"
mkdir -p "$LOG_DIR"
TODAY="$(date +%F)"
LOG="$LOG_DIR/run_$TODAY.log"

log() { echo "[$(date '+%F %T %Z')] $*" | tee -a "$LOG"; }

# --- US market holiday guard (cron already restricts to Mon–Fri) --------------
# Update this list yearly. On these dates the market is closed; skip the run.
HOLIDAYS_2026="2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 \
2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25"
case " $HOLIDAYS_2026 " in
  *" $TODAY "*) log "Market holiday ($TODAY). Skipping."; exit 0 ;;
esac

# --- Sync repo so we inherit yesterday's state.json ---------------------------
cd "$REPO_DIR"
log "Pulling latest $BRANCH ..."
git checkout "$BRANCH" >>"$LOG" 2>&1
git pull --ff-only origin "$BRANCH" >>"$LOG" 2>&1 || log "WARN: git pull failed; using local state."

# --- Run the playbook headless ------------------------------------------------
PROMPT="Execute robinhood_trader/DAILY_RUN.md for today (${TODAY}). Run the full \
playbook end to end: read the account, run signal_engine.py with the live equity, \
reconcile holdings to the target by placing any required rotation order in account \
855664652, then update robinhood_trader/state.json and commit & push it to ${BRANCH}. \
Finish with a one-line summary: equity, regime, target, action taken. If the \
robinhood-trading MCP is not authenticated, or data fetch fails, DO NOT TRADE — \
print a line beginning with 'NEEDS_ATTENTION:' explaining why, and stop."

log "Invoking Claude Code headless ..."
set +e
claude -p "$PROMPT" \
  --permission-mode bypassPermissions \
  --allowedTools "Bash,mcp__robinhood-trading__*" \
  --output-format text >>"$LOG" 2>&1
STATUS=$?
set -e

# --- Detect failures / re-auth needed -----------------------------------------
if [ $STATUS -ne 0 ]; then
  log "ERROR: claude exited $STATUS. Likely MCP auth/refresh failure — re-run \`claude\` + /mcp to re-authenticate robinhood-trading."
  # Optional alert: uncomment to email yourself (requires `mail` configured).
  # tail -n 40 "$LOG" | mail -s "[robinhood-trader] run FAILED $TODAY" you@example.com
  exit $STATUS
fi

if grep -q "NEEDS_ATTENTION:" "$LOG"; then
  log "Playbook flagged NEEDS_ATTENTION — review today's log: $LOG"
  # tail -n 40 "$LOG" | mail -s "[robinhood-trader] NEEDS ATTENTION $TODAY" you@example.com
  exit 0
fi

log "Done."
