---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-11-22'
modified: '2026-11-22'
---

# ADR-008: WAL Size Control (journal_size_limit + Explicit Checkpoints)

**Date:** 2026-11-22 | **Status:** Implemented | **Supersedes:** — (extends [ADR-002](ADR-002-wal-mode-concurrent-sqlite.md))

## Decision

Every connection sets `PRAGMA journal_size_limit` (64 MiB) in addition to the PRAGMAs in ADR-002. The server also runs an explicit checkpoint, `MemoryDatabase.checkpoint_truncate()`, at four points: after each maintenance tool (`vault_index`, `vault_rechunk`, `admin_sync`), after the startup background sync, at stdio shutdown, and at HTTP shutdown. `MemoryDatabase.close()` does not checkpoint.

`checkpoint_truncate()` works in up to two steps on a short-lived connection of its own (opened read-write only, 2 s busy timeout):

1. `wal_checkpoint(PASSIVE)` copies the log back into the database without taking the write lock.
2. Only if step 1 copied every frame and the `-wal` file is larger than `journal_size_limit`, `wal_checkpoint(TRUNCATE)` resets the file to zero bytes.

A checkpoint that was held back is logged as a warning and never raised. A file left under the limit is the normal result, not a warning.

After a maintenance tool the checkpoint runs as a background task: the tool's result is returned, and the maintenance lock released, without waiting for it. The startup-sync and shutdown checkpoints are awaited in place; shutdown first waits for a maintenance-tool checkpoint still running (the startup-sync checkpoint is not tracked, so a process stopped during its startup sync can run two at once; the second reports busy at once).

## Why

ADR-002 turned WAL mode on but nothing limited the `-wal` file. SQLite's automatic checkpoint is PASSIVE: it copies pages back but never shrinks the file, so the file stays at its high-water mark. With about 17 server processes holding the database open, the `-wal` file reached 13 GB, the size of the database itself (GitHub issue 6).

`journal_size_limit` is not a cap on its own. It only truncates the file on the first commit after the log has been reset, and the log cannot reset while a reader holds an older snapshot. The explicit checkpoints are what reset it.

The two-step form exists because a TRUNCATE checkpoint holds the WAL write lock for its whole copy. On a large un-copied log that would block every writer in every process, possibly past the 30 s statement timeout. PASSIVE does the copy without that lock, so the TRUNCATE that follows only has to copy what was committed since PASSIVE started. When a reader pins an older snapshot, TRUNCATE could not reset the log anyway, so it is skipped instead of holding the write lock while it waits.

TRUNCATE is also skipped while the file is under `journal_size_limit`. The startup-sync and shutdown checkpoints run at every session start and exit, and a TRUNCATE that meets a reader on the newest snapshot holds the write lock for its whole busy timeout: measured on a test database (SQLite 3.54), it waited the full timeout and a write on another connection completed only after it gave up. Under the limit there is nothing to gain from that: `journal_size_limit` already cuts the file back to the limit when a writer next restarts the log, on connections that set it (every `MemoryDatabase` connection does). So the write lock is taken only when the file has actually outgrown the limit.

Two limits remain:

- When the file is over the limit, a reader on the newest snapshot lets PASSIVE finish, so TRUNCATE runs and holds the write lock for up to the 2 s busy timeout before it reports busy. Every writer in every process waits that long.
- The 2 s timeout bounds lock waits only. PASSIVE has no time bound: its first run on a very large log is I/O-bound and can take a long time. That delays a shutdown, or the end of the startup sync, but not a tool response. Whatever any connection commits during that time is TRUNCATE's copy under the write lock; since the maintenance lock is released before the checkpoint ends, that includes the next maintenance tool in the same process.

## Alternatives Considered

- **`journal_size_limit` alone** — does nothing until the log resets, which the automatic PASSIVE checkpoint cannot guarantee with many connections.
- **A single `wal_checkpoint(TRUNCATE)`** — the first version of this change. Rejected because of the write-lock hold described above.
- **Checkpoint inside `close()`** — also in the first version. Removed: it hid a lock wait inside every close, ran twice for `vault_index`, and the server's long-lived connections are never closed anyway.
- **A periodic timer** — more moving parts than checkpointing at the points that write the most.
- **Awaiting the checkpoint inside the maintenance tool call** — the second version. Rejected: the result of a finished batch waited behind a checkpoint with no time bound, so a client timeout could lose it.
- **TRUNCATE on every checkpoint** — also the second version. Rejected because of the write-lock wait at every session start and exit, described above.

## Not Proven

The behavior with many live connections on the real database was not measured when this was written. 64 MiB and 2 s are named constants in `claude_innit/db/database.py`, chosen from the issue, not from load tests. How long the first PASSIVE checkpoint takes on a multi-gigabyte log was not measured. What pins the log on the real database is not known either: while something does, no checkpoint can reset it, and the sign is the busy warning in the log (two overlapping checkpoints log the same warning). Processes started before this change keep the old behavior until they restart, because the PRAGMA is per connection.

## Where in Code

- `claude_innit/db/database.py` — `JOURNAL_SIZE_LIMIT_BYTES`, `CHECKPOINT_BUSY_TIMEOUT_SECONDS`, `WAL_SIZE_WARN_BYTES`, `MemoryDatabase.checkpoint_truncate`, `MemoryDatabase.wal_size_bytes`
- `claude_innit/server.py` — `InnitServer.call_tool` (maintenance tools), `_background_sync`, `main`, `build_http_app` lifespan
- `tests/test_wal_limit.py`, `tests/test_wal_checkpoint_server.py`
