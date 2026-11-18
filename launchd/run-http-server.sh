#!/bin/bash
# Run the shared claude-innit MCP server (streamable HTTP on loopback) under
# launchd, logging every exit with its status, duration and last stderr line.
#
# Bash 3.2-safe: launchd runs /bin/bash. The server runs as a child (not
# `exec`) so this wrapper can record how it ended; SIGTERM/SIGINT from
# `launchctl bootout` are forwarded to it. See
# ~/.claude/references/wrapper-exit-status-logging.md.
#
# Env (all optional):
#   CLAUDE_INNIT_REPO   repo checkout to run from (default: this script's repo)
#   CLAUDE_INNIT_PYTHON interpreter (default: $CLAUDE_INNIT_REPO/.venv/bin/python)
#   CLAUDE_INNIT_LOG    log file (default: ~/Library/Logs/claude-innit/http-server.log)
#   CLAUDE_INNIT_PORT   passed through to the server (default 8767)

REPO="${CLAUDE_INNIT_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="${CLAUDE_INNIT_PYTHON:-$REPO/.venv/bin/python}"
LOG="${CLAUDE_INNIT_LOG:-$HOME/Library/Logs/claude-innit/http-server.log}"

mkdir -p "$(dirname "$LOG")" || exit 70
touch "$LOG" || exit 73

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "$(ts) [wrapper] $*" >>"$LOG"; }

if [ ! -x "$PY" ]; then
    log "FAILED rc=127 after 0s: interpreter not executable: $PY"
    exit 127
fi

export CLAUDE_INNIT_TRANSPORT=http
cd "$REPO" || { log "FAILED rc=66 after 0s: cannot cd to $REPO"; exit 66; }

# Byte offset where this run's output starts, so the exit line quotes this
# run's last stderr line rather than a previous run's.
START_BYTES=$(wc -c <"$LOG" | tr -d ' ')
T0=$(date +%s)
log "starting pid=$$ python=$PY repo=$REPO port=${CLAUDE_INNIT_PORT:-8767}"

"$PY" -m claude_innit.server >>"$LOG" 2>&1 &
CHILD=$!

STOP_SIG=""
forward() {
    STOP_SIG="$1"
    log "received $1, forwarding to server pid=$CHILD"
    kill -"$1" "$CHILD" 2>/dev/null
}
trap 'forward TERM' TERM
trap 'forward INT' INT

# `wait` returns early (rc>128) when a trapped signal arrives; keep waiting
# until the child has actually exited so its real status is what we log.
while :; do
    wait "$CHILD"
    RC=$?
    kill -0 "$CHILD" 2>/dev/null || break
done

SECS=$(( $(date +%s) - T0 ))
if [ "$RC" -eq 0 ]; then
    log "server exited ok rc=0 after ${SECS}s"
    exit 0
fi
# uvicorn shuts down gracefully on SIGTERM/SIGINT and then re-raises the
# signal, so a requested stop ends with 128+signo. That is a stop, not a crash.
if [ -n "$STOP_SIG" ] && { [ "$RC" -eq 143 ] || [ "$RC" -eq 130 ]; }; then
    log "server stopped on $STOP_SIG rc=$RC after ${SECS}s"
    exit 0
fi
ERR=$(tail -c +"$((START_BYTES + 1))" "$LOG" | grep -v '^[[:space:]]*$' | grep -v '\[wrapper\]' | tail -1)
log "server FAILED rc=$RC after ${SECS}s: ${ERR:-<no stderr>}"
exit "$RC"
