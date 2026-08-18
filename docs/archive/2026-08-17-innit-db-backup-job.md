# innit.db Backup Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a consistency-safe, scheduled backup job for the live, WAL-mode `innit.db` (~5.4GB), independent of the general `backup-dev-to-whatbox.sh` sweep that deliberately excludes it.

**Architecture:** A standalone bash script (`scripts/backup_innit_db.sh`) uses `sqlite3 ... "VACUUM INTO"` to take a consistency-safe, compacted snapshot of the live DB, verifies it with `PRAGMA integrity_check`, prunes local snapshots beyond a retention count, and mirrors the retained local snapshot directory to Whatbox via `rsync --delete`. A dedicated launchd job runs it daily, independent of the weekly general sweep. All paths are env-var-overridable so the same script can be exercised in tests against a small fixture DB without touching the real 5.4GB file or the network.

**Tech Stack:** bash, `sqlite3` CLI (already present, `/usr/bin/sqlite3` 3.51.0), `rsync`, macOS `launchd`, pytest (existing repo test runner) for script-level tests via `subprocess`.

**Spec:** `docs/superpowers/specs/2026-08-17-innit-db-backup-job-kickoff-prompt.md`

## Global Constraints

- Do not re-add `innit.db` (or its backup snapshots) to `backup-dev-to-whatbox.sh`'s rsync scope — that exclusion is deliberate and stays in place (`~/Dev/scripts/backup-dev-to-whatbox.sh:45`).
- The backup mechanism must not require stopping the claude-innit MCP server — `VACUUM INTO` works against a live connection; a raw file copy does not.
- Snapshot correctness must be verified against the actual copy (`PRAGMA integrity_check` run on the snapshot file itself), never assumed from `VACUUM INTO` succeeding.
- The job runs on its own schedule (launchd), separate from `com.taylorstephens.backup-dev-to-whatbox`.
- Backup snapshot filenames must end in `.db` so the repo's existing `*.db` gitignore rule covers them without a new pattern.
- Canonical (non-worktree) paths: source DB at `$HOME/Dev/_Projects/Claude-Innit/data/innit.db`, script ultimately lives at `$HOME/Dev/_Projects/Claude-Innit/scripts/backup_innit_db.sh` post-merge — the launchd plist must reference the canonical path, not this worktree's path, since the worktree is torn down after merge.

## Decisions made this session (stated, not re-litigated)

- **Destination: both.** Local snapshot dir (`data/backups/` under the canonical repo) + `rsync --delete` mirror to Whatbox (`Dev-backup/innit-db-backups/`), matching the spec's "local snapshot directory, whatbox ... or both" option. Mirroring with `--delete` means Whatbox retention tracks local retention automatically — no separate remote pruning logic needed.
- **Retention: 3 local snapshots.** At ~5.4GB each that's ~16GB, well inside the 67GB currently free on `/`. Prevents unbounded local/remote growth without a separate cron cleanup job.
- **Schedule: daily, 4:00 AM.** The general sweep runs weekly (Sun 3:00 AM); this DB accumulates session data continuously, so a shorter cadence is warranted, offset an hour after the general sweep to avoid concurrent disk/network load.
- **Logs: project-local `logs/` dir** (`$HOME/Dev/_Projects/Claude-Innit/logs/`), matching the `whatbox/scripts-local` → `whatbox/logs/` convention for a project-owned job, rather than the shared `~/Dev/scripts/logs/` (which is specific to the general sweep script that lives there).

---

### Task 1: Backup script with integrity check and retention pruning

**Files:**
- Create: `scripts/backup_innit_db.sh`
- Create: `tests/test_backup_innit_db.py`
- Modify: `.gitignore` (add `logs/`)

**Interfaces:**
- Produces: `scripts/backup_innit_db.sh`, invoked as `bash scripts/backup_innit_db.sh` with env vars `INNIT_DB_SRC`, `INNIT_BACKUP_DIR`, `INNIT_BACKUP_RETENTION`, `INNIT_BACKUP_REMOTE`, `INNIT_BACKUP_REMOTE_DIR`, `INNIT_BACKUP_LOG_DIR`, `INNIT_BACKUP_SKIP_REMOTE` (all optional, defaults below). Exit code 0 on success, non-zero on any failure (missing source, failed integrity check, rsync failure). Snapshot files are named `innit-<YYYYMMDD-HHMMSS>.db` inside `INNIT_BACKUP_DIR`.
- Consumes: nothing from other tasks (first task).

- [ ] **Step 1: Write the failing test**

Create `tests/test_backup_innit_db.py`:

```python
import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_innit_db.sh"


def _make_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _run_backup(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_script_is_executable():
    assert SCRIPT.exists()
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR


def test_backup_creates_verified_snapshot(tmp_path):
    src = tmp_path / "innit.db"
    _make_source_db(src)
    backup_dir = tmp_path / "backups"
    log_dir = tmp_path / "logs"

    result = _run_backup({
        "INNIT_DB_SRC": str(src),
        "INNIT_BACKUP_DIR": str(backup_dir),
        "INNIT_BACKUP_LOG_DIR": str(log_dir),
        "INNIT_BACKUP_SKIP_REMOTE": "1",
        "INNIT_BACKUP_RETENTION": "3",
    })

    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = list(backup_dir.glob("innit-*.db"))
    assert len(snapshots) == 1

    conn = sqlite3.connect(snapshots[0])
    row = conn.execute("SELECT val FROM t WHERE id = 1").fetchone()
    conn.close()
    assert row == ("hello",)


def test_backup_fails_when_source_missing(tmp_path):
    result = _run_backup({
        "INNIT_DB_SRC": str(tmp_path / "does-not-exist.db"),
        "INNIT_BACKUP_DIR": str(tmp_path / "backups"),
        "INNIT_BACKUP_LOG_DIR": str(tmp_path / "logs"),
        "INNIT_BACKUP_SKIP_REMOTE": "1",
    })
    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr)


def test_backup_prunes_old_snapshots(tmp_path):
    src = tmp_path / "innit.db"
    _make_source_db(src)
    backup_dir = tmp_path / "backups"
    log_dir = tmp_path / "logs"
    backup_dir.mkdir(parents=True)

    for i in range(3):
        fake = backup_dir / f"innit-2020010{i}-000000.db"
        _make_source_db(fake)
        old_time = time.time() - (1000 - i * 10)
        os.utime(fake, (old_time, old_time))

    result = _run_backup({
        "INNIT_DB_SRC": str(src),
        "INNIT_BACKUP_DIR": str(backup_dir),
        "INNIT_BACKUP_LOG_DIR": str(log_dir),
        "INNIT_BACKUP_SKIP_REMOTE": "1",
        "INNIT_BACKUP_RETENTION": "2",
    })

    assert result.returncode == 0, result.stdout + result.stderr
    remaining = sorted(backup_dir.glob("innit-*.db"))
    assert len(remaining) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backup_innit_db.py -v`
Expected: FAIL — `scripts/backup_innit_db.sh` does not exist yet (`test_script_is_executable` fails on `SCRIPT.exists()`; the others fail with `FileNotFoundError` from `subprocess.run`).

- [ ] **Step 3: Write the script**

Create `scripts/backup_innit_db.sh`:

```bash
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
# Run manually, or via the com.taylorstephens.innit-db-backup LaunchAgent.

set -euo pipefail

INNIT_DB_SRC="${INNIT_DB_SRC:-$HOME/Dev/_Projects/Claude-Innit/data/innit.db}"
INNIT_BACKUP_DIR="${INNIT_BACKUP_DIR:-$HOME/Dev/_Projects/Claude-Innit/data/backups}"
INNIT_BACKUP_RETENTION="${INNIT_BACKUP_RETENTION:-3}"
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

echo "Pruning local snapshots beyond retention ($INNIT_BACKUP_RETENTION)..." | tee -a "$LOG_FILE"
# shellcheck disable=SC2012
ls -1t "$INNIT_BACKUP_DIR"/innit-*.db 2>/dev/null | tail -n "+$((INNIT_BACKUP_RETENTION + 1))" | while IFS= read -r old; do
  echo "Removing old snapshot: $old" | tee -a "$LOG_FILE"
  rm -f "$old"
done

if [ "$INNIT_BACKUP_SKIP_REMOTE" != "1" ]; then
  echo "Syncing snapshots to $INNIT_BACKUP_REMOTE:$INNIT_BACKUP_REMOTE_DIR/..." | tee -a "$LOG_FILE"
  rsync -avz --delete "$INNIT_BACKUP_DIR/" "$INNIT_BACKUP_REMOTE:$INNIT_BACKUP_REMOTE_DIR/" 2>&1 | tee -a "$LOG_FILE"
else
  echo "Skipping remote sync (INNIT_BACKUP_SKIP_REMOTE=1)" | tee -a "$LOG_FILE"
fi

echo "=== innit.db backup completed: $(date) ===" | tee -a "$LOG_FILE"

find "$INNIT_BACKUP_LOG_DIR" -name "backup-innit-db-*.log" -mtime +30 -delete 2>/dev/null || true
```

Then: `chmod +x scripts/backup_innit_db.sh`

Add to `.gitignore` (after the existing `# Database` block):

```
# Backup job logs (project-local, not tracked)
logs/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_backup_innit_db.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/backup_innit_db.sh tests/test_backup_innit_db.py .gitignore
git commit -m "feat: add consistency-safe backup script for innit.db"
```

---

### Task 2: Dedicated launchd schedule

**Files:**
- Create: `~/Library/LaunchAgents/com.taylorstephens.innit-db-backup.plist` (outside the repo; machine-local config, not git-tracked)

**Interfaces:**
- Consumes: `scripts/backup_innit_db.sh` from Task 1, at its canonical post-merge path `$HOME/Dev/_Projects/Claude-Innit/scripts/backup_innit_db.sh`.
- Produces: a loaded launchd job labeled `com.taylorstephens.innit-db-backup`, running daily at 04:00.

- [ ] **Step 1: Write the plist**

Create `~/Library/LaunchAgents/com.taylorstephens.innit-db-backup.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.taylorstephens.innit-db-backup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/taylorstephens/Dev/_Projects/Claude-Innit/scripts/backup_innit_db.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>4</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/taylorstephens/Dev/_Projects/Claude-Innit/logs/backup-innit-db-launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/taylorstephens/Dev/_Projects/Claude-Innit/logs/backup-innit-db-launchd.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Validate the plist**

Run: `plutil -lint ~/Library/LaunchAgents/com.taylorstephens.innit-db-backup.plist`
Expected: `... OK`

- [ ] **Step 3: Load the job**

Run: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.taylorstephens.innit-db-backup.plist`
(If already loaded from a prior attempt: `launchctl bootout gui/$(id -u)/com.taylorstephens.innit-db-backup` first, then bootstrap.)

- [ ] **Step 4: Verify it's registered**

Run: `launchctl list | grep innit-db-backup`
Expected: a line containing `com.taylorstephens.innit-db-backup` (PID column may be `-` since it's calendar-scheduled, not running now).

- [ ] **Step 5: Commit**

The plist lives outside the repo (machine-local, matches the existing `com.taylorstephens.backup-dev-to-whatbox.plist` convention of not being git-tracked) — nothing to commit here. If the repo's script path changed during Task 1 review, re-verify the `ProgramArguments` path before moving to Task 3.

---

### Task 3: End-to-end verification and closeout

**Files:**
- None created/modified in this repo beyond what Tasks 1–2 already touched.
- Modify (in the `Obsidian-Second-Brain` vault repo, separately): move `Action-Tracker/Backlog/someday/2026-08-14-add-sqlite3-backup-job-for-innit-db.md` to `Action-Tracker/Backlog/completed/2026-08/week-33/`.

**Interfaces:**
- Consumes: `scripts/backup_innit_db.sh` (Task 1) and the loaded launchd job (Task 2).
- Produces: closed GitHub issue `tstephx/Claude-Innit#2`, closed Action-Tracker task, a released session claim.

- [ ] **Step 1: Run the real backup end-to-end (no env overrides — uses production defaults)**

Run: `bash /Users/taylorstephens/Dev/_Projects/Claude-Innit/scripts/backup_innit_db.sh`

This runs against the live 5.4GB `innit.db`, so it will take real time (minutes, not seconds) and needs ~5.4GB of free local disk for the new snapshot on top of any retained ones. Watch the log at `~/Dev/_Projects/Claude-Innit/logs/backup-innit-db-<date>.log` while it runs.

Expected: exit 0. Log shows `Integrity check passed: ok` and, unless `rsync` fails, a completed remote sync.

- [ ] **Step 2: Confirm the local snapshot**

Run: `ls -lh ~/Dev/_Projects/Claude-Innit/data/backups/`
Expected: one `innit-<timestamp>.db` file, roughly the size of the source DB (VACUUM INTO compacts, so it may be smaller, never larger).

- [ ] **Step 3: Confirm the remote copy landed**

Run: `ssh echobyte@cucumber.whatbox.ca "ls -lh Dev-backup/innit-db-backups/"`
Expected: the same `innit-<timestamp>.db` filename listed remotely.

- [ ] **Step 4: Re-run the automated tests once more for regression safety**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: full suite passes (307+ existing tests, plus the 4 new ones from Task 1).

- [ ] **Step 5: Close GitHub issue #2**

Run:
```bash
gh issue close 2 --repo tstephx/Claude-Innit --comment "$(cat <<'EOF'
Implemented: scripts/backup_innit_db.sh (VACUUM INTO + PRAGMA integrity_check, 3-snapshot retention).

- Destination: both — local (~/Dev/_Projects/Claude-Innit/data/backups/) and Whatbox (Dev-backup/innit-db-backups/, mirrored via rsync --delete)
- Schedule: com.taylorstephens.innit-db-backup launchd job, daily at 04:00
- Verified end-to-end: integrity check passed, snapshot confirmed at both destinations
EOF
)"
```

- [ ] **Step 6: Close the Action-Tracker task**

In the `Obsidian-Second-Brain` vault repo:
1. `cd` to the vault repo and run `git status` first — per the spec's caution, it commonly has 1000+ unrelated changed files from its own sync process. Only touch the one file below.
2. Edit `Action-Tracker/Backlog/someday/2026-08-14-add-sqlite3-backup-job-for-innit-db.md` frontmatter: set `completed:` to today's date, `priority: completed`, `status: done`.
3. Move it: `git mv Action-Tracker/Backlog/someday/2026-08-14-add-sqlite3-backup-job-for-innit-db.md Action-Tracker/Backlog/completed/2026-08/week-33/2026-08-14-add-sqlite3-backup-job-for-innit-db.md` (create the `week-33` directory first if it doesn't exist).
4. Commit only that one file: `git commit -m "chore: close add-sqlite3-backup-job-for-innit-db task"`.

- [ ] **Step 7: Release the session claim**

Run: `python3 ~/.claude/hooks/session_claims.py release --repo tstephx/Claude-Innit --issue 2 --outcome "closed via PR — script scripts/backup_innit_db.sh, daily launchd job, local+whatbox destination"`

- [ ] **Step 8: Final commit / PR**

If not already handled by the executing workflow, ensure Task 1's commit is pushed and a PR is opened for review (per this repo's normal git workflow) before or alongside closing the issue — closing the issue should reference the merged/mergeable state of the code, not leave the script only on a local branch.
