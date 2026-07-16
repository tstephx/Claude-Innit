---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-005: Two-Level RRF Fusion for Vault and Federated Search

**Date:** 2026-03-09 | **Status:** Implemented | **Supersedes:** —

## Decision

Unlike memory `search()` (ADR-004), `vault_search(method="auto")` does not choose one method — it runs FTS5 and semantic search together and merges the two result sets via a mini reciprocal-rank-fusion (RRF: FTS weight 0.4, semantic weight 0.6, k=20), deduplicating to the best chunk per file. `federated_search()` wraps this as an inner leg and adds an outer RRF layer (k=60, equal weights) across vault, book-library, and session-memory sources. Output exposes `rrf_score` (the authoritative ranking) and `match_type` (`fts`/`semantic`/`hybrid`); raw `score`/`similarity` are stripped to avoid ambiguity.

## Why

Vault search serves broader, more varied queries than memory search — the same query can have both an exact-term match in one note and a conceptual match in another. Fusing both signals, rather than routing to one, surfaces both kinds of matches in a single ranked list instead of forcing a query-length heuristic to guess which one the user needs. Extending the same RRF pattern outward to `federated_search` reuses one fusion mechanism instead of inventing per-source merge logic.

## Rejected Alternatives

- **Query-length routing (same as memory search, ADR-004)** — vault queries don't split as cleanly into "exact" vs "conceptual" by word count; a short query can still want conceptual matches once vault content is broad and heterogeneous.
- **Return both result sets unmerged** — pushes ranking work onto the caller and produces duplicate/inconsistent ordering.
- **Simple score averaging instead of RRF** — FTS and cosine-similarity scores aren't on comparable scales; RRF ranks by position, which is scale-independent.

## Where in Code

- `claude_innit/tools/vault.py` — `vault_search()`, `_hybrid_merge()`
- `claude_innit/tools/federation.py` — `federated_search()`, outer RRF
- `docs/archive/2026-03-11-vault-search-quality.md` — Task 6 (hybrid vault search), Task 7 (federated hybrid upgrade)
- `ref/architecture.md` — "Hybrid vault search" and "Two-level RRF federation" sections
