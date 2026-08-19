---
name: db-schema
description: "Map of this project's SQLite schema. Use before answering any question about tables, columns, embeddings, or 'why is this table empty' — including anything touching claude_innit/db/."
---

# Database Schema Map

Orientation only. This tells you where the schema lives and what will mislead
you. It is not a table reference — read the source for that.

## Canonical source

The schema is `claude_innit/db/database.py`, method `_create_tables()`
(lines ~29-160). All `CREATE TABLE IF NOT EXISTS` — there are no ALTER TABLE
migrations in this codebase, so `.schema` on any installed DB matches the
source exactly (unlike two-phase-migration projects).

The live database is at `<repo>/data/innit.db`, resolved in
`claude_innit/server.py:626` as `Path(__file__).parent.parent / "data" / "innit.db"`.
No env var overrides this path. (A stray 0-byte `memory.db` at the repo root
was an unreferenced leftover, unrelated to this path — deleted 2026-08-14,
book-mcp-server#8.)

To verify a specific column actually exists:

    sqlite3 data/innit.db "PRAGMA table_info(vault_chunks)"

## Known traps

- **`vault_embeddings` is a dead table, not a deprecated-but-live one.** The
  in-source comment (`database.py:98-100`) calls it superseded by
  `vault_chunk_embeddings` (chunk-level) and says read paths guard for its
  absence. In fact its writer, `batch_store_vault_embeddings()`
  (`embeddings.py:340`), is never called anywhere in the codebase — the live
  DB has 0 rows in `vault_embeddings` and 118,973 in `vault_chunk_embeddings`.
  Don't debug "why is vault_embeddings empty" as a bug; it's expected. All
  file-level vault embedding data lives in `vault_chunk_embeddings`, keyed by
  `chunk_id`/`file_id`, not `vault_embeddings`.

- **Two embedding tables exist for two different granularities.** `embeddings`
  (memory-level, keyed by `memory_id`) is small and active (~42 rows). Vault
  content embeddings are chunk-level in `vault_chunk_embeddings`, not
  file-level — a `file_id` maps to many `chunk_id`s via `vault_chunks`.

- **`insert_memory()` uses explicit UPDATE/INSERT, never `INSERT OR REPLACE`**
  (`database.py:167-202`) — deliberately, to preserve rowid for the FTS5
  external-content table. If you're about to write a raw SQL upsert against
  `memories`, don't use `INSERT OR REPLACE`; it corrupts the FTS5 index (see
  commit `fa4b4be`).

## What this file is not

Not a table reference. Do not paste table definitions here — read them from
`claude_innit/db/database.py`. This file tells you where to look and what
will mislead you.
