# Vault Tools — Manual Test Plan

**System**: Claude-Innit MCP Server (vault tools subset)
**Scope**: 6 vault-facing MCP tools — `vault_index`, `vault_search`, `vault_related`, `vault_stats`, `vault_rechunk`, `federated_search`
**Date created**: 2026-03-12

## Prerequisites

**Environment**:
- Claude-Innit MCP server running with `VAULT_ROOT` set to Obsidian vault
- `EXTRA_INDEX_PATHS` set (default: `~/Dev/_Lab:~/Dev/_Projects`)
- Embedding model loaded (`pip install -e ".[embeddings]"`)

**Test fixture** — use these exact invocations as baselines:

> **Vault with known content**: Run `vault_index()` first. The vault should have 800+ files across multiple modules (behavioral-studio, books, projects, portfolio, etc.)
>
> **Known file path**: Pick a file you know exists, e.g. a story in `behavioral-studio/Stories/`
>
> **Known query with results**: `"API migration"` (should hit vault notes and possibly book chapters)
>
> **Known query with no results**: `"xyznonexistent12345"`

---

## Part 1: Smoke Tests (Automated)

Run `pytest tests/test_vault_smoke.py -v` — all 15 tests should pass before proceeding to Part 2.

| Category | Count |
|----------|-------|
| vault_index | 3 |
| vault_search | 5 |
| vault_related | 2 |
| vault_stats | 1 |
| vault_rechunk | 1 |
| federated_search | 3 |

---

***Part 2: Quality Validation — RUN AS SEPARATE SESSION

EXECUTION RULE: After completing each tool's checks, append results to `test-results-vault-YYYY-MM-DD.md` in the project root. Format:
```
## [Tool Name] — PASS/FAIL
- [x] Assertion 1
- [ ] Assertion 3 — [actual value observed]
```
If context compacts, read the results file to find where you left off.

EXECUTION RULE: For each test, report only PASS/FAIL and the assertion results. Do NOT echo the full tool response. Only show full output on FAILURES to aid debugging.

---

### vault_index

Run: `vault_index()`

- [ ] Returns dict with all 6 expected keys: `indexed`, `updated`, `unchanged`, `removed`, `errors`, `duration_ms`
- [ ] All 6 values are integers
- [ ] `errors` is 0 (no indexing failures on a healthy vault)
- [ ] `duration_ms` is positive and < 120000 (under 2 minutes for full vault)
- [ ] `indexed + updated + unchanged` > 0 (at least some files processed)
- [ ] When called twice without changes: second run has `indexed == 0` and `unchanged > 0`

Run: `vault_index(force=true)`

- [ ] `updated` equals total file count (force reindexes everything)
- [ ] `indexed` is 0 (files already exist, so they're updates not new)

---

### vault_search (4 tools — highest value, test all methods)

**FTS search**
Run: `vault_search(query="API migration", method="text")`

- [ ] Returns a list (not dict, not error)
- [ ] Each result has `file_path`, `filename`, `content`, `module` keys
- [ ] At least 1 result contains "API Migration" in `filename` or `content`
- [ ] Results have `score` field (float, 0-1 range)
- [ ] Results are ordered by score descending
- [ ] Does NOT return results from excluded dirs (`node_modules/`, `.git/`, `__pycache__/`)

**Semantic search**
Run: `vault_search(query="leading cross-functional teams through organizational change", method="semantic")`

- [ ] Returns a list (not error)
- [ ] Each result has `file_path`, `filename`, `similarity`, `matched_heading` keys
- [ ] Results are conceptually relevant (not just keyword matches — e.g., leadership/change content)
- [ ] `similarity` values are all >= 0.35 (min_similarity threshold)
- [ ] Does NOT return identical results as FTS for the same query (different ranking expected)

**Hybrid (auto) search**
Run: `vault_search(query="stakeholder alignment and communication strategy", method="auto")`

- [ ] Returns a list with `rrf_score` and `match_type` on each result
- [ ] `match_type` values are one of: `fts`, `semantic`, `hybrid`
- [ ] At least 1 result has `match_type == "hybrid"` (found by both methods)
- [ ] Raw `score` and `similarity` fields are stripped (not present)
- [ ] Results ordered by `rrf_score` descending

**Scope filtering**
Run: `vault_search(query="config", scope="configs")`

- [ ] All results have `module == null` (framework dirs only, not content modules)
- [ ] Does NOT return files from content modules like `behavioral-studio`

---

### vault_related

Run: `vault_related(note_path="<path to API Migration.md>")`

- [ ] Returns a list of related notes
- [ ] Does NOT include the source note itself in results
- [ ] Results have `file_path` and `filename` fields
- [ ] Related notes are topically relevant (e.g., other stories, leadership content)
- [ ] With `limit=3`, returns at most 3 results

Run: `vault_related(note_path="/nonexistent/path.md")`

- [ ] Returns empty list `[]` (not an error/crash)

---

### vault_stats

Run: `vault_stats()`

- [ ] Returns dict with all expected keys: `total_notes`, `by_module`, `by_status`, `inbox_count`, `stale_count`, `index_age_seconds`, `last_indexed`, `embeddings`
- [ ] `total_notes` is a positive integer matching approximate vault file count
- [ ] `by_module` is a dict — keys are module names (lowercase), values are positive ints
- [ ] `by_module` values sum to <= `total_notes` (some files have no module)
- [ ] `by_status` is a dict — keys are status strings from frontmatter
- [ ] `inbox_count` is an int >= 0
- [ ] `stale_count` is an int >= 0
- [ ] `index_age_seconds` is a positive float (not -1.0, meaning index exists)
- [ ] `last_indexed` is an ISO datetime string
- [ ] `embeddings` is a dict with keys: `total_files`, `chunk_embeddings`, `legacy_embeddings`, `model`, `mode`, `self_test`
- [ ] `embeddings.self_test` is `"pass"` when embedding store is configured

---

### vault_rechunk

Run: `vault_rechunk()`

- [ ] Returns a dict (not crash)
- [ ] When embedding store is configured: has `chunks_processed` or similar count key
- [ ] Has `matrix_reloaded` key with an integer (number of embeddings in matrix)
- [ ] When run after `vault_index`, matrix count > 0

---

### federated_search

**All sources**
Run: `federated_search(query="behavioral interview preparation")`

- [ ] Returns dict with keys: `vault`, `books`, `sessions`, `merged`
- [ ] `vault` is a list — each item has `source == "vault"`
- [ ] `books` is a list — each item has `source == "books"`, `title`, `author`, `snippet`
- [ ] `sessions` is a list — each item has `source == "sessions"`, `snippet`
- [ ] `merged` is a list sorted by `rrf_score` descending
- [ ] `merged` items from vault have `file_path` and `filename` fields
- [ ] Book results include snippet (not full chapter content — **regression check for truncation**)

**Source filtering**
Run: `federated_search(query="conflict", sources=["vault", "sessions"])`

- [ ] Result has `vault` and `sessions` keys
- [ ] Result does NOT have `books` key
- [ ] `merged` only contains items with `source` in `["vault", "sessions"]`

**Portfolio source**
Run: `federated_search(query="DSP", sources=["portfolio"])`

- [ ] Result has `portfolio` key
- [ ] Portfolio results have `source == "portfolio"`
- [ ] Portfolio results only include files with `module == "portfolio"`

---

***Part 3: Edge Cases — RUN AS SEPARATE SESSION

---

### Input validation (10 tests)

```
vault_search(query="")                          — returns empty list, not crash
vault_search(query="a")                         — returns results (single-char query)
vault_search(query='test "OR" AND NOT drop')    — FTS operators sanitized, no crash
vault_search(query="x" * 10000)                 — very long query, returns gracefully
vault_search(query="test", method="invalid")    — returns empty list (unrecognized method)
vault_search(query="test", scope="invalid")     — handles gracefully (no crash)
vault_search(query="test", limit=0)             — returns empty list
vault_search(query="test", limit=-1)            — handles gracefully
vault_related(note_path="")                     — returns empty list
vault_related(note_path="/nonexistent/file.md") — returns empty list
```

### Semantic without embedding store (2 tests)

```
vault_search(query="test", method="semantic")   — raises ValueError with clear message (no embedding store)
federated_search(query="test", sources=["vault"]) without embeddings — falls back to FTS, no crash
```

### Concurrency / state (3 tests)

```
vault_index() then vault_index()                — second run is fast (hash-based skip)
vault_index(force=true) during search           — search still works (WAL mode)
vault_stats() on empty database                 — returns total_notes=0, index_age=-1.0
```

---

## Test Run Log

| Date | Tester | Smoke (pytest) | Part 2 | Part 3 | Notes |
|------|--------|----------------|--------|--------|-------|
| | | /15 pass | /42 | /15 | |
