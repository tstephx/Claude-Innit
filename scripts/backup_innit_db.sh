#!/bin/bash
# backup_innit_db.sh — consistency-safe snapshot backup for innit.db
#
# innit.db is a live WAL-mode SQLite DB held open continuously by the
# claude-innit MCP server. A raw file copy fails checksum verification
# mid-transfer (rsync exit 23) and isn't consistency-safe regardless.
# VACUUM INTO is safe against a live connection and produces a compacted,
# single-file snapshot verified with PRAGMA integrity_check before it
# ever leaves this machine.
#
# Snapshots are NOT kept on this machine: INNIT_BACKUP_DIR is a staging area.
# rsync --remove-source-files deletes each snapshot locally once it is safely on
# whatbox, and retention (INNIT_BACKUP_RETENTION) is enforced on the server.
# If the upload fails, at most INNIT_BACKUP_LOCAL_RETENTION unsent snapshots
# (default 1) are kept locally and retried on the next run.
#
# Run manually, or via the com.taylorstephens.innit-db-backup LaunchAgent.

set -euo pipefail

INNIT_DB_SRC="${INNIT_DB_SRC:-$HOME/Dev/_Projects/Claude-Innit/data/innit.db}"
INNIT_BACKUP_DIR="${INNIT_BACKUP_DIR:-$HOME/Dev/_Projects/Claude-Innit/data/backups}"
INNIT_BACKUP_RETENTION="${INNIT_BACKUP_RETENTION:-3}"
INNIT_BACKUP_LOCAL_RETENTION="${INNIT_BACKUP_LOCAL_RETENTION:-1}"
INNIT_BACKUP_REMOTE="${INNIT_BACKUP_REMOTE:-echobyte@cucumber.whatbox.ca}"
INNIT_BACKUP_REMOTE_DIR="${INNIT_BACKUP_REMOTE_DIR:-Dev-backup/innit-db-backups}"
INNIT_BACKUP_LOG_DIR="${INNIT_BACKUP_LOG_DIR:-$HOME/Dev/_Projects/Claude-Innit/logs}"
INNIT_BACKUP_SKIP_REMOTE="${INNIT_BACKUP_SKIP_REMOTE:-0}"

mkdir -p "$INNIT_BACKUP_DIR" "$INNIT_BACKUP_LOG_DIR"

LOG_FILE="$INNIT_BACKUP_LOG_DIR/backup-innit-db-$(date +%Y-%m-%d).log"

ROTATE_SCRIPT="$HOME/Dev/scripts/rotate-log.sh"
if [ -f "$ROTATE_SCRIPT" ]; then
  # shellcheck disable=SC1090
  source "$ROTATE_SCRIPT"
  rotate_log "$INNIT_BACKUP_LOG_DIR/backup-innit-db-launchd.log"
fi

echo "=== innit.db backup started: $(date) ===" | tee -a "$LOG_FILE"

if [ ! -f "$INNIT_DB_SRC" ]; then
  echo "ERROR: source DB not found at $INNIT_DB_SRC" | tee -a "$LOG_FILE"
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT="$INNIT_BACKUP_DIR/innit-$TIMESTAMP.db"

echo "Creating VACUUM INTO snapshot: $SNAPSHOT" | tee -a "$LOG_FILE"
sqlite3 "$INNIT_DB_SRC" "VACUUM INTO '$SNAPSHOT';"

echo "Running PRAGMA integrity_check on snapshot..." | tee -a "$LOG_FILE"
INTEGRITY="$(sqlite3 "$SNAPSHOT" "PRAGMA integrity_check;")"
if [ "$INTEGRITY" != "ok" ]; then
  echo "FAILED integrity check: $INTEGRITY" | tee -a "$LOG_FILE"
  rm -f "$SNAPSHOT"
  exit 1
fi
echo "Integrity check passed: ok" | tee -a "$LOG_FILE"

echo "Pruning unsent local snapshots beyond $INNIT_BACKUP_LOCAL_RETENTION..." | tee -a "$LOG_FILE"
# shellcheck disable=SC2012
ls -1t "$INNIT_BACKUP_DIR"/innit-*.db 2>/dev/null | tail -n "+$((INNIT_BACKUP_LOCAL_RETENTION + 1))" | while IFS= read -r old; do
  echo "Removing old snapshot: $old" | tee -a "$LOG_FILE"
  rm -f "$old"
done

if [ "$INNIT_BACKUP_SKIP_REMOTE" != "1" ]; then
  echo "Syncing snapshots to $INNIT_BACKUP_REMOTE:$INNIT_BACKUP_REMOTE_DIR/..." | tee -a "$LOG_FILE"
  # Use the 1Password SSH agent (rotated whatbox key, 2026-09). The helper
  # checks the agent will sign BEFORE we connect, so a locked 1Password aborts
  # here instead of racking up failed logins (fail2ban). IdentityAgent=SSH_AUTH_SOCK
  # pins ssh to the helper's agent regardless of ~/.ssh/config.
  ENSURE_SSH_AGENT="${INNIT_ENSURE_SSH_AGENT:-$HOME/Dev/_Projects/whatbox/scripts-local/ensure-ssh-agent.sh}"
  # shellcheck disable=SC1090
  source "$ENSURE_SSH_AGENT" || exit 1
  WHATBOX_PUB="${WHATBOX_SSH_PUB:-$HOME/.ssh/id_ed25519_whatbox.pub}"
  SSH_OPTS=(-o IdentityAgent=SSH_AUTH_SOCK -o IdentitiesOnly=yes -i "$WHATBOX_PUB")
  # Upload every staged snapshot (incl. any left by a failed run); rsync
  # deletes each local file only after it has transferred successfully.
  rsync -avz --remove-source-files \
    -e "ssh ${SSH_OPTS[*]}" \
    "$INNIT_BACKUP_DIR"/innit-*.db "$INNIT_BACKUP_REMOTE:$INNIT_BACKUP_REMOTE_DIR/" 2>&1 | tee -a "$LOG_FILE"

  echo "Pruning remote snapshots beyond retention ($INNIT_BACKUP_RETENTION)..." | tee -a "$LOG_FILE"
  ssh "${SSH_OPTS[@]}" "$INNIT_BACKUP_REMOTE" \
    "cd '$INNIT_BACKUP_REMOTE_DIR' && ls -1t innit-*.db 2>/dev/null | tail -n +$((INNIT_BACKUP_RETENTION + 1)) | xargs -r rm -fv" \
    2>&1 | tee -a "$LOG_FILE"
else
  echo "Skipping remote sync (INNIT_BACKUP_SKIP_REMOTE=1)" | tee -a "$LOG_FILE"
fi

echo "=== innit.db backup completed: $(date) ===" | tee -a "$LOG_FILE"

find "$INNIT_BACKUP_LOG_DIR" -name "backup-innit-db-*.log" -mtime +30 -delete 2>/dev/null || true
