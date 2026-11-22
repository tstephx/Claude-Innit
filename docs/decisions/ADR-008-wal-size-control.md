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

`checkpoint_truncate()` works in two steps on a short-lived connection of its own (opened read-write only, 2 s busy timeout):

1. `wal_checkpoint(PASSIVE)` copies the log back into the database without taking the write lock.
2. Only if step 1 copied every frame, `wal_checkpoint(TRUNCATE)` resets the `-wal` file to zero bytes.

A result that leaves the log un-reset is logged as a warning and never raised.

## Why

ADR-002 turned WAL mode on but nothing limited the `-wal` file. SQLite's automatic checkpoint is PASSIVE: it copies pages back but never shrinks the file, so the file stays at its high-water mark. With about 17 server processes holding the database open, the `-wal` file reached 13 GB, the size of the database itself (GitHub issue 6).

`journal_size_limit` is not a cap on its own. It only truncates the file on the first commit after the log has been reset, and the log cannot reset while a reader holds an older snapshot. The explicit checkpoints are what reset it.

The two-step form exists because a TRUNCATE checkpoint holds the WAL write lock for its whole copy. On a large un-copied log that would block every writer in every process, possibly past the 30 s statement timeout. PASSIVE does the copy without that lock, so the TRUNCATE that follows only has to copy what was committed since PASSIVE started. When a reader pins an older snapshot, TRUNCATE could not reset the log anyway, so it is skipped instead of holding the write lock while it waits.

Two limits remain:

- A reader on the newest snapshot lets PASSIVE finish, so TRUNCATE still runs and can hold the write lock for up to the 2 s busy timeout before it reports busy.
- The 2 s timeout bounds lock waits only. PASSIVE has no time bound: its first run on a very large log is I/O-bound and can take a long time, which delays the tool response or the shutdown that triggered it. Whatever other processes commit during that time is TRUNCATE's copy under the write lock.

## Alternatives Considered

- **`journal_size_limit` alone** — does nothing until the log resets, which the automatic PASSIVE checkpoint cannot guarantee with many connections.
- **A single `wal_checkpoint(TRUNCATE)`** — the first version of this change. Rejected because of the write-lock hold described above.
- **Checkpoint inside `close()`** — also in the first version. Removed: it hid a lock wait inside every close, ran twice for `vault_index`, and the server's long-lived connections are never closed anyway.
- **A periodic timer** — more moving parts than checkpointing at the points that write the most.

## Not Proven

The behavior with many live connections on the real database was not measured when this was written. 64 MiB and 2 s are named constants in `claude_innit/db/database.py`, chosen from the issue, not from load tests. How long the first PASSIVE checkpoint takes on a multi-gigabyte log, and how long writers actually stall behind TRUNCATE, were also not measured.

## Where in Code

- `claude_innit/db/database.py` — `JOURNAL_SIZE_LIMIT_BYTES`, `CHECKPOINT_BUSY_TIMEOUT_SECONDS`, `WAL_SIZE_WARN_BYTES`, `MemoryDatabase.checkpoint_truncate`, `MemoryDatabase.wal_size_bytes`
- `claude_innit/server.py` — `InnitServer.call_tool` (maintenance tools), `_background_sync`, `main`, `build_http_app` lifespan
- `tests/test_wal_limit.py`, `tests/test_wal_checkpoint_server.py`
