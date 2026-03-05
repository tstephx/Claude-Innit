# MCP Tools Reference

Claude-Innit exposes 8 tools. 6 are conversational (used during sessions), 2 are operator-only admin tools.

---

## Conversational Tools

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
- FTS queries are phrase-wrapped to prevent injection (`"OR AND NOT"` is safe)

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
2. FTS index sync (count comparison, rebuilds if off)
3. Orphaned embeddings (no matching memory row)

Returns: `{ "status": "healthy|repaired|unhealthy", "memories": N, "issues": [...], "repairs": [...] }`
