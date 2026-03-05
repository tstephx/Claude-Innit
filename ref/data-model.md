# Data Model Reference

## Database Schema (`data/innit.db`)

### `memories` table

```sql
CREATE TABLE memories (
    id          TEXT PRIMARY KEY,      -- e.g. "personal/a3f8c2d1" or "personal/identity.md"
    category    TEXT NOT NULL,         -- "personal" | "project" | "session"
    source_file TEXT,                  -- relative path to markdown file (if synced)
    content     TEXT NOT NULL,         -- full text content
    metadata    JSON,                  -- frontmatter fields as JSON
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`metadata` JSON fields by category:
- `personal`: arbitrary frontmatter
- `project`: `{"name": "project-name"}` — used for filtering in `get_context` and `list_memories`
- `session`: `{"project": "project-name", "topics": [...], "date": "YYYY-MM-DD"}`

### `memories_fts` virtual table (FTS5)

Content-shadowed FTS5 index over `memories`. Kept in sync via triggers:
- `memories_ai` — INSERT on memories → INSERT into FTS
- `memories_ad` — DELETE on memories → DELETE from FTS
- `memories_au` — UPDATE on memories → DELETE+INSERT in FTS

### `embeddings` table

```sql
CREATE TABLE embeddings (
    memory_id  TEXT PRIMARY KEY,
    embedding  BLOB,                   -- float32 array, 384 dims, little-endian bytes
    model      TEXT,                   -- "all-MiniLM-L6-v2"
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
```

---

## Markdown File Format

All memory files use YAML frontmatter:

```markdown
---
category: personal
---

I prefer dark mode and concise responses.
```

```markdown
---
category: project
project: book-mcp-server
---

Using adapter pattern for book ingestion pipeline.
```

```markdown
---
category: session
project: claude-innit
topics:
  - mcp-optimization
  - reliability
date: 2026-03-04
---

LAST: implemented auth module | NEXT: add refresh token | DECISIONS: used JWT
```

---

## Memory Directory Layout

```
data/memories/
├── personal/
│   ├── identity.md          # who the user is
│   ├── preferences.md       # tools, style, workflow prefs
│   └── workflows.md         # how they work
├── projects/
│   ├── _template.md         # skipped by sync (starts with _)
│   └── <project-name>.md    # per-project state (hand-edited)
└── sessions/
    ├── _index.md            # skipped by sync
    └── 2026-03-04-120000.md # auto-created by save_session
```

**Sync rules:**
- Files starting with `_` are skipped (templates, indexes)
- Category detected from directory: `personal/` → `personal`, `projects/` → `project`, `sessions/` → `session`
- All other subdirectories → `unknown` category

---

## SQLite Configuration

Enabled on every connection open:

```python
sqlite3.connect(db_path, timeout=30, check_same_thread=False)
PRAGMA journal_mode=WAL      # concurrent readers + single writer
PRAGMA synchronous=NORMAL    # safe with WAL, avoids fsync on every commit
```
