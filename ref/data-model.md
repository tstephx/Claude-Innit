---
status: active
tags: []
type: note
created: '2026-03-12'
modified: '2026-03-12'
---

# Data Model Reference

## Database Schema (`data/innit.db`)

### Memory Tables

#### `memories`

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

#### `memories_fts` (FTS5)

Content-shadowed FTS5 index over `memories`. Kept in sync via triggers:
- `memories_ai` — INSERT on memories → INSERT into FTS
- `memories_ad` — DELETE on memories → DELETE from FTS
- `memories_au` — UPDATE on memories → DELETE+INSERT in FTS

#### `embeddings`

```sql
CREATE TABLE embeddings (
    memory_id  TEXT PRIMARY KEY,
    embedding  BLOB,                   -- float32 array, 384 dims, little-endian bytes
    model      TEXT,                   -- "all-MiniLM-L6-v2"
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
```

---

### Vault Tables

#### `vault_files`

```sql
CREATE TABLE vault_files (
    file_id     INTEGER PRIMARY KEY,
    file_path   TEXT UNIQUE NOT NULL,  -- absolute path on disk
    filename    TEXT NOT NULL,         -- just the filename (e.g. "note.md")
    content     TEXT NOT NULL,         -- body text (frontmatter stripped)
    content_hash TEXT NOT NULL,        -- SHA-256 of raw file content (staleness detection)
    frontmatter JSON,                  -- parsed YAML frontmatter as JSON
    module      TEXT,                  -- lowercased top-level dir name (NULL for framework dirs)
    file_size   INTEGER,
    modified_at TIMESTAMP,            -- file mtime from disk
    indexed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `vault_files_fts` (FTS5)

```sql
CREATE VIRTUAL TABLE vault_files_fts USING fts5(
    file_path, filename, content,
    content='vault_files', content_rowid='file_id',
    tokenize='porter unicode61'
);
```

Kept in sync via triggers: `vault_files_ai`, `vault_files_ad`, `vault_files_au`.

#### `vault_chunks`

```sql
CREATE TABLE vault_chunks (
    chunk_id     INTEGER PRIMARY KEY,
    file_id      INTEGER NOT NULL,     -- FK to vault_files
    chunk_index  INTEGER NOT NULL,     -- 0-based position within file
    heading      TEXT,                 -- section heading (NULL for preamble/fallback)
    content      TEXT NOT NULL,        -- chunk text
    char_offset  INTEGER DEFAULT 0,   -- position in original file
    content_hash TEXT NOT NULL DEFAULT '',  -- parent file's hash (for staleness)
    FOREIGN KEY (file_id) REFERENCES vault_files(file_id),
    UNIQUE(file_id, chunk_index)
);
```

#### `vault_chunk_embeddings`

```sql
CREATE TABLE vault_chunk_embeddings (
    chunk_id   INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL,
    embedding  BLOB,                   -- float32 array, 384 dims
    model      TEXT,                   -- "all-MiniLM-L6-v2"
    FOREIGN KEY (chunk_id) REFERENCES vault_chunks(chunk_id),
    FOREIGN KEY (file_id) REFERENCES vault_files(file_id)
);
CREATE INDEX idx_vce_file_id ON vault_chunk_embeddings(file_id);
```

#### `vault_embeddings` (DEPRECATED)

```sql
-- Superseded by vault_chunk_embeddings (chunk-level).
-- Retained for backward compat. All read paths guard with try/except.
CREATE TABLE vault_embeddings (
    file_id   INTEGER PRIMARY KEY,
    embedding BLOB,
    model     TEXT,
    FOREIGN KEY (file_id) REFERENCES vault_files(file_id)
);
```

#### `chunk_config`

```sql
CREATE TABLE chunk_config (
    key   TEXT PRIMARY KEY,           -- "max_chunk_chars", "min_chunk_chars"
    value TEXT NOT NULL               -- stored as string
);
```

Tracks chunking parameters. If `max_chunk_chars` or `min_chunk_chars` change between runs, `batch_store_chunk_embeddings()` auto-triggers a force rechunk.

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

## Module Detection

Files in vault are assigned a `module` based on their top-level directory:

| Path | Module |
|------|--------|
| `<vault>/Notes/foo.md` | `notes` |
| `<vault>/Portfolio/bar.md` | `portfolio` |
| `<vault>/Daily/2026-01-01.md` | `NULL` (framework dir) |
| `<vault>/Inbox/capture.md` | `NULL` (framework dir) |
| `<vault>/Archive/old.md` | `NULL` (framework dir) |
| `<vault>/Claude-Memory/ctx.md` | `NULL` (framework dir) |
| `<vault>/root-file.md` | `NULL` (root level) |

Framework dirs: `daily`, `inbox`, `archive`, `claude-memory` (case-insensitive).
Dot-prefixed dirs (`.brain/`, `.claude/`) are excluded by `VaultIndexer.exclude_patterns`, not by module detection.

---

## SQLite Configuration

Enabled on every connection open:

```python
sqlite3.connect(db_path, timeout=30, check_same_thread=False)
PRAGMA journal_mode=WAL      # concurrent readers + single writer
PRAGMA synchronous=NORMAL    # safe with WAL, avoids fsync on every commit
PRAGMA foreign_keys=ON       # enforce FK constraints
```
