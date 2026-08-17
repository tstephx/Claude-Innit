# Add sqlite3 .backup/VACUUM INTO job for innit.db

## Where things stand

Tracked as `tstephx/Claude-Innit#2` and Action-Tracker task
`someday/2026-08-14-add-sqlite3-backup-job-for-innit-db.md`.

`innit.db` (`data/innit.db` in this repo, `~/Dev/_Projects/Claude-Innit/data/innit.db`)
is a live, WAL-mode SQLite DB (~5.6GB) held open continuously by the
claude-innit MCP server process. It's currently excluded entirely from
`backup-dev-to-whatbox.sh`'s rsync (`tstephx/scripts` PR #12, merged
2026-08-14) because raw file-copy of a live WAL DB fails checksum
verification mid-transfer (rsync exit 23) — and isn't a sound backup
strategy regardless, even when it happens to succeed. Flagged during that
PR's review as a deferred follow-up, source doc:
`2026-08-14-launchagent-remaining-jobs-triage-findings.md` (`tstephx/scripts`).

## What this session does

1. Write a backup script using `sqlite3 .backup` or `VACUUM INTO` — either
   is consistency-safe for a live WAL DB, unlike a raw file copy. Put it in
   this repo's `scripts/` directory alongside existing scripts.
2. Verify the snapshot is actually consistent: run `PRAGMA integrity_check`
   against the copy, not just against the assumption that `.backup`/`VACUUM
   INTO` guarantees it.
3. Decide and implement the destination: a local snapshot directory,
   whatbox (via the existing rsync infrastructure, now that the snapshot
   is a static file rather than a live WAL DB), or both.
4. Wire it into its own schedule (cron or launchd), separate from
   `backup-dev-to-whatbox.sh` — this DB needs its own cadence, not to be
   folded into the general sweep it was excluded from.
5. Test the whole path once end-to-end: run the backup, confirm the
   snapshot's integrity check passes, confirm it lands at the chosen
   destination.
6. Close `#2` with the script path, schedule, and destination summarized
   in the closing comment.
7. Close out the Action-Tracker task: stamp `completed:`, `priority:
   completed`, `status: done`, move to
   `Action-Tracker/Backlog/completed/<YYYY-MM>/week-<NN>/` in the
   `Obsidian-Second-Brain` vault repo, commit only that one file (check
   `git status` there first — it commonly has 1000+ unrelated changed
   files from its own sync process).

## Constraints carried over

- Don't re-add `innit.db` to `backup-dev-to-whatbox.sh`'s rsync scope —
  that exclusion was deliberate (checksum failures on a live WAL DB), this
  task builds a separate, correct mechanism instead.
- The claude-innit MCP server holds this DB open continuously — confirm
  the backup approach doesn't require stopping the server (`.backup` and
  `VACUUM INTO` both work against a live connection; a raw file copy does
  not, which is the whole reason this task exists).

## Caution

Re-derive current state before trusting anything above: `gh issue view 2
--repo tstephx/Claude-Innit` to confirm it's still open, and check whether
`backup-dev-to-whatbox.sh` (`tstephx/scripts`) still excludes `innit.db`
the same way — the exclusion mechanism may have changed since 2026-08-14.
Re-check `data/innit.db`'s current size before running the first full
backup (was ~5.6GB as of this prompt — a `.backup`/`VACUUM INTO` snapshot
of that size takes real time and disk).

Run the `concurrent-session-preflight` skill before claiming this issue.
