---
status: active
tags: [project/claude-innit, format/reference]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# Architecture Decision Records (ADR) Index

Permanent decision log for Claude-Innit. Each ADR captures a single architectural choice, the context that drove it, the alternatives considered, and where it lives in code. ADRs are append-only — when a decision changes, write a new ADR that supersedes the old one.

**Last verified:** 2026-07-16

---

## Index

| ADR | Title | Date | Status | Supersedes |
| --- | --- | --- | --- | --- |
| [ADR-001](ADR-001-markdown-first-storage.md) | Markdown-First Storage with SQLite as a Rebuildable Index | 2026-01-30 | Implemented | — |
| [ADR-002](ADR-002-wal-mode-concurrent-sqlite.md) | WAL Mode for Concurrent SQLite Access | 2026-03-04 | Implemented | — |
| [ADR-003](ADR-003-heading-level-chunking.md) | Heading-Level Chunking for Vault Content | 2026-03-12 | Implemented | — |
| [ADR-004](ADR-004-memory-search-query-length-routing.md) | Query-Length Routing for Memory Search | 2026-01-30 | Implemented | — |
| [ADR-005](ADR-005-hybrid-rrf-vault-federated-search.md) | Two-Level RRF Fusion for Vault and Federated Search | 2026-03-09 | Implemented | — |
| [ADR-006](ADR-006-path-prefixed-module-detection.md) | Path-Prefixed Module Detection for Extra-Path Files | 2026-03-16 | Implemented | — |
| [ADR-007](ADR-007-vault-tag-two-phase-mcp-tool.md) | vault_tag as an MCP Tool with Two-Phase Preview/Apply | 2026-03-16 | Implemented | — |

---

## One-Line Summaries

**ADR-001: Markdown-First Storage** — `data/memories/` is the source of truth; `data/innit.db` is a rebuildable derived index (`admin_sync`). `forget()` deletes the markdown file before the DB row so a later sync can't resurrect it.

**ADR-002: WAL Mode for Concurrent SQLite Access** — All connections run `PRAGMA journal_mode=WAL` to eliminate `SQLITE_BUSY` under concurrent read/write from the server, background sync, and `vault_index`.

**ADR-003: Heading-Level Chunking** — Vault files split into chunks at `##`/`###` headings (paragraph fallback for oversized sections) instead of whole-file embeddings or fixed-size chunks, so search surfaces the specific section that matched.

**ADR-004: Query-Length Routing for Memory Search** — `search()` over `data/memories/` routes 1-3 word queries to FTS5 and 4+ word queries to semantic search — a single either/or heuristic, distinct from vault search's fusion approach (ADR-005).

**ADR-005: Two-Level RRF Fusion for Vault and Federated Search** — `vault_search` runs FTS5 and semantic together and merges via mini-RRF (FTS 0.4 / semantic 0.6, k=20); `federated_search` adds an outer RRF layer (k=60) across vault, books, and sessions. Chosen over query-length routing because vault content is too heterogeneous for a word-count heuristic to pick the right method.

**ADR-006: Path-Prefixed Module Detection** — Extra-index-path files (`_Lab`, `_Projects`) get path-prefixed module names (`lab/project-name`) instead of bare folder names, fixing 8,128 files that showed as "unassigned" and avoiding folder-name collisions across roots.

**ADR-007: vault_tag as an MCP Tool** — Frontmatter tagging is a callable MCP tool with a two-phase preview/apply flow and folder + file-level override granularity, not a one-off backfill script — so Claude can tag new files mid-session, not just historical ones.

---

## How to Add a New ADR

1. Pick the next number: ADR-NNN
2. Use a descriptive kebab-case slug: `ADR-NNN-{slug}.md`
3. Format: `# ADR-NNN: Title` heading + `**Date:** YYYY-MM-DD | **Status:** Proposed/Implemented | **Supersedes:** —` line + Decision / Why / Rejected Alternatives / Where in Code sections
4. **Update this README** with a row in the index table and a one-line summary
5. If superseding an existing ADR, mark the old one with `**Status:** Superseded by ADR-NNN` and link to the new one

## See Also

- [`ref/architecture.md`](../../ref/architecture.md) — full architecture reference
- [`CLAUDE.md`](../../CLAUDE.md) — project-level instructions, key design decisions, common footguns
- [`docs/archive/`](../archive/) — executed implementation plans these ADRs were extracted from
