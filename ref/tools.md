---
status: active
tags: [project/claude-innit, format/reference]
type: note
created: '2026-03-12'
modified: '2026-03-12'
---

# MCP Tools Reference

Claude-Innit exposes 14 tools: 6 memory tools, 6 vault tools, and 2 operator-only admin tools.

---

## Memory Tools

### `get_context`

Load all persistent memory for a session. **Call once at session start before any other work.**

```json
{ "project": "claude-innit" }
```

Returns: `{ "personal": [...], "project": [...], "recent_sessions": [...] }`

- `project` filters project memories by `metadata.name` and sessions by `metadata.project`
- If no project-specific sessions exist, falls back to all recent sessions (last 5)
- Personal memories are always returned unfiltered

---

### `search`

Find stored memories by keyword or concept. Use when `get_context` didn't surface what you need.

```json
{ "query": "adapter pattern", "method": "auto" }
```

- `method: "auto"` → 1-3 words use FTS5 (exact), 4+ words use semantic (concept)
- `method: "text"` → force FTS5 phrase search
- `method: "semantic"` → force semantic search (requires model load)
- Semantic results filtered at `min_similarity=0.35` — low-relevance results are excluded
- FTS queries are sanitized via `sanitize_fts_query()` — strips operators, quotes each word

---

### `remember`

Store new information persistently across sessions.

```json
{
  "content": "Taylor prefers dark mode and concise responses",
  "category": "personal"
}
```

```json
{
  "content": "Using adapter pattern for book ingestion pipeline",
  "category": "project",
  "project": "book-mcp-server"
}
```

- Categories: `personal` (identity/prefs), `project` (per-project state), `session` (summaries)
- Returns `{ "success": true, "memory_id": "personal/a3f8c2d1" }`
- Memory ID is needed for `forget()` — save it or use `list_memories` later

---

### `forget`

Permanently delete a memory. **Use `list_memories` first to get the ID.**

```json
{ "memory_id": "personal/a3f8c2d1" }
```

- Deletes both the DB record AND the markdown file — durable across server restarts
- Without `memories_dir` (should not happen in production), returns `{ "success": true, "warning": "..." }`
- Returns `{ "success": true }` on clean delete

---

### `list_memories`

List stored memories with IDs and previews. The discovery tool for `forget()`.

```json
{ "category": "personal" }
```

```json
{ "project": "claude-innit" }
```

Returns: `[{ "id": "personal/a3f8c2d1", "preview": "First 80 chars...", "category": "personal", "updated_at": "..." }]`

- No filter → returns all memories (up to 100)
- `project` filter implicitly scopes to `category: "project"`
- `category` + `project` together: `project` takes priority

---

### `save_session`

Save a session summary for future recall. **Call once at session end — not after each sub-task.**

```json
{
  "summary": "LAST: implemented auth module | NEXT: add refresh token | DECISIONS: used JWT",
  "topics": ["auth", "JWT"],
  "project": "my-app"
}
```

Returns: `{ "success": true, "session_id": "sessions/2026-03-04-120000" }`

---

## Vault Tools (OBF)

### `vault_index`

Index vault markdown files into the search database. Scans vault root + extra paths.

```json
{ "force": false }
```

- Skips unchanged files (content hash comparison) unless `force: true`
- Detects module from top-level directory name (lowercased)
- Excludes framework dirs (Daily, Inbox, Archive, Claude-Memory) from module assignment
- Cleans up DB entries for files deleted from disk
- Extra paths configured via `EXTRA_INDEX_PATHS` env var (default: `~/Dev/_Lab:~/Dev/_Projects`)
- Returns: `{ "indexed": N, "updated": N, "unchanged": N, "removed": N, "errors": N, "duration_ms": N }`

---

### `vault_search`

Hybrid FTS + semantic search over vault files.

```json
{ "query": "API migration", "method": "auto", "scope": "all", "limit": 20 }
```

- `method: "auto"` → runs BOTH FTS and semantic, merges with mini-RRF (FTS=0.4, semantic=0.6, k=20). Falls back to FTS-only if no embedding store.
- `method: "text"` → FTS only
- `method: "semantic"` → semantic only (raises ValueError if no embedding store)
- `scope: "all"` (default), `"vault"`, or `"configs"` (framework dirs only)
- Semantic search delegates to `EmbeddingStore.search_chunks()` — deduplicates by file, returns best chunk per file
- Returns list of dicts with `rrf_score`, `match_type` (fts/semantic/hybrid), `file_path`, `filename`, `module`

---

### `vault_related`

Find notes related to a given note path.

```json
{ "note_path": "/path/to/note.md", "limit": 10 }
```

- Tries semantic search first (first 500 chars of note content)
- Falls back to FTS using filename words if no embeddings
- Excludes the source note from results

---

### `vault_stats`

Return vault health metrics.

```json
{}
```

Returns:
```json
{
  "total_notes": 1234,
  "by_module": {"notes": 500, "portfolio": 50},
  "by_status": {"draft": 200, "ready": 100},
  "inbox_count": 15,
  "stale_count": 30,
  "index_age_seconds": 3600.0,
  "last_indexed": "2026-03-12T15:00:00",
  "embeddings": {"total": 5000, "self_test": "pass"}
}
```

---

### `vault_rechunk`

Force re-chunk all vault files and regenerate embeddings.

```json
{}
```

- Deletes all existing chunks and chunk embeddings
- Re-runs heading-level chunking (`utils_chunking.py`) on every indexed file
- Generates new embeddings for each chunk
- Reloads the embedding matrix
- Returns: `{ "files_processed": N, "chunks_created": N, "embeddings_generated": N, "errors": N }`

---

### `federated_search`

Two-level RRF fusion across multiple sources.

```json
{ "query": "API migration", "sources": ["vault", "books", "sessions"], "limit": 30 }
```

- Sources: `vault` (hybrid FTS+semantic), `books` (book-library DB), `sessions` (memory sessions), `portfolio` (vault module filter)
- Inner: vault hybrid search (k=20)
- Outer: RRF fusion across all sources (k=60, equal weights)
- Returns: `{ "vault": [...], "books": [...], "sessions": [...], "merged": [...] }`

---

## Operator Tools

These are for debugging/maintenance only. Not needed in normal sessions.

### `admin_sync`

Re-sync markdown files to database. Call if memories are out of sync after manual file edits.

```json
{}
```

Returns sync stats: `{ "synced": N, "errors": N, "skipped": N }`

- Files starting with `_` are skipped (templates, indexes)
- Runs automatically in background on server start — rarely needed manually

---

### `admin_check_integrity`

Check database health and repair issues. Call when search or sync is behaving unexpectedly.

```json
{ "auto_repair": true }
```

Checks:
1. SQLite structural integrity (`PRAGMA integrity_check`)
2. FTS index sync for memories and vault (count comparison, rebuilds if off)
3. Orphaned embeddings (memory embeddings, vault embeddings, chunk embeddings)

Returns: `{ "status": "healthy|repaired|unhealthy", "memories": N, "issues": [...], "repairs": [...] }`
