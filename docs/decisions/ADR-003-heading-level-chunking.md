---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-003: Heading-Level Chunking for Vault Content

**Date:** 2026-03-12 | **Status:** Implemented | **Supersedes:** —

## Decision

Vault files are split into chunks at `##`/`###` headings (`chunk_by_headings()` in `utils_chunking.py`), not at fixed token/character counts. H1 is treated as the document title, not a section boundary. Oversized sections fall back to paragraph splitting; sections smaller than `min_chunk_chars` merge into their neighbor; files smaller than `max_chunk_chars` stay a single chunk. Chunks are stored in `vault_chunks` with a foreign key to `vault_files`, and each chunk gets its own row in `vault_chunk_embeddings`.

## Why

The prior approach embedded whole files, which diluted relevance for large notes — a match buried in one section of a long file scored no better than a short, focused note. Splitting at heading boundaries respects the document's own structure instead of cutting mid-thought, and lets search surface the specific section that matched rather than the whole file.

## Rejected Alternatives

- **Fixed-size token/character chunking** — ignores document structure, can split mid-sentence or mid-list, produces chunks with no coherent topic.
- **Whole-file-only embeddings** (the prior approach) — this is exactly what heading-level chunking replaced; the legacy `vault_embeddings` table is deprecated and retained only for backward compatibility.

## Where in Code

- `claude_innit/utils_chunking.py` — `chunk_by_headings()`
- `claude_innit/db/database.py` — `vault_chunks` / `vault_chunk_embeddings` tables (FK-enforced)
- `docs/archive/2026-03-11-vault-search-quality.md` — Task 1 (schema), Task 2 (chunking utility)
- `ref/architecture.md` — "Heading-level chunking" section
