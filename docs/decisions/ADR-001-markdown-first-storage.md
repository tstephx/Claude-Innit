---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-001: Markdown-First Storage with SQLite as a Rebuildable Index

**Date:** 2026-01-30 | **Status:** Implemented | **Supersedes:** —

## Decision

`data/memories/` (markdown files) is the source of truth for all memory content. `data/innit.db` (SQLite, with FTS5 + embeddings tables) is a derived index that can be rebuilt from markdown at any time via `admin_sync`. Every write path keeps this ordering: `forget()` deletes the markdown file first, then the DB row — if DB deletion fails, the file is already gone so a later sync cannot resurrect it.

## Why

Markdown-first makes memory content human-readable and hand-editable outside the tool. It also makes database corruption a non-event: delete `data/innit.db`, call `admin_sync`, and every memory reconstitutes from the files. A DB-first design would make the index itself the thing that must never be lost.

## Rejected Alternatives

- **SQLite as the source of truth** — corruption or schema drift would mean permanent data loss with no recovery path; also not diffable or hand-editable.
- **Flat files with no index (grep-only)** — no fast retrieval, no FTS5/semantic ranking, doesn't scale past a handful of files.

## Where in Code

- `data/memories/` — source markdown, organized by category (`personal/`, `projects/`, `sessions/`)
- `claude_innit/sync/markdown_sync.py` — markdown → DB sync engine (`sync_all`, runs async on startup)
- `claude_innit/tools/memory.py` — `remember()`/`forget()` write ordering
- `ref/architecture.md` — "Markdown-first storage" section, `forget()` data-flow diagram
- `docs/archive/2025-01-30-claude-innit-design.md` — original architecture doc
