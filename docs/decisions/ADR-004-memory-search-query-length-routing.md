---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-004: Query-Length Routing for Memory Search

**Date:** 2026-01-30 | **Status:** Implemented | **Supersedes:** —

## Decision

The `search()` tool over `data/memories/` routes by query length when `method="auto"`: queries of 1-3 words go to FTS5 (fast, exact match), queries of 4+ words go to semantic search (`all-MiniLM-L6-v2` embeddings). Callers can force `method="text"` or `method="semantic"` to bypass routing. FTS queries are sanitized via `sanitize_fts_query()`, which strips FTS5 operators and quotes each word before the query reaches SQLite.

## Why

Short queries are almost always exact-term lookups ("UBS transfer", "fast-mail sieve") where FTS5's speed and precision win. Longer, conceptual queries ("what did we decide about vault chunking strategy") benefit from semantic similarity, where near-synonyms and paraphrases still match. A single fixed strategy would either miss exact terms (semantic-only) or miss conceptual matches (FTS-only).

## Rejected Alternatives

- **Always run semantic search** — slower, and worse than FTS5 for short exact-term lookups.
- **Always run FTS5** — misses paraphrased or conceptual queries entirely; no similarity signal.
- **User-specified method only (no auto-routing)** — pushes a judgment call onto every caller instead of picking a sane default; `method="text"`/`"semantic"` are still available as an escape hatch, so nothing is lost.

## Where in Code

- `claude_innit/tools/search.py` — routing logic
- `claude_innit/utils.py` — `sanitize_fts_query()`
- `docs/archive/2025-01-30-claude-innit-design.md` — "Smart Search Logic" section (original design)
- `CLAUDE.md` — "Search routing: 1-3 words → FTS5 (fast), 4+ → semantic (conceptual)"
