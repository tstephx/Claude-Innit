---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-002: WAL Mode for Concurrent SQLite Access

**Date:** 2026-03-04 | **Status:** Implemented | **Supersedes:** —

## Decision

All SQLite connections run in WAL journal mode (`PRAGMA journal_mode=WAL`) with `PRAGMA synchronous=NORMAL` and a 30s connection timeout. `MarkdownSync` opens its own dedicated `MemoryDatabase` connection, separate from the server's connection — the two are never in the same transaction.

## Why

The server's connection serves reads while the background sync task writes on startup and `vault_index` writes from a `asyncio.to_thread()` connection. Under the default `DELETE` journal mode, concurrent access threw `SQLITE_BUSY`. WAL allows concurrent readers alongside a single writer, which eliminates `SQLITE_BUSY` in practice.

## Rejected Alternatives

- **Default DELETE journal mode** — this was the pre-existing behavior and is what caused the `SQLITE_BUSY` failures being fixed; kept as the negative case in `test_wal_mode_enabled` (`assert row[0] == "wal"`, not `"delete"`).
- **Application-level lock/mutex around all DB access** — would still serialize reads behind writes and adds complexity the WAL PRAGMA avoids entirely.

## Where in Code

- `claude_innit/db/database.py` — `MemoryDatabase.__init__` PRAGMA statements
- `tests/test_database.py::test_wal_mode_enabled`
- `docs/archive/2026-03-04-mcp-optimizations.md` — Task 1
- `ref/architecture.md` — "Two-Connection Architecture" section
