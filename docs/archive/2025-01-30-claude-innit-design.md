---
status: active
tags: []
type: note
created: '2026-01-30'
modified: '2026-01-30'
---

# Claude Innit Design

**Status:** Approved
**Date:** 2025-01-30

---

## Overview

Claude Innit is a unified memory context system for Claude that manages:
- **Personal context** - Identity, preferences, workflows
- **Project context** - Per-project state and decisions
- **Session continuity** - What happened in recent sessions

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Claude Innit                       │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐    ┌──────────────┐               │
│  │  Markdown    │───▶│   SQLite     │               │
│  │  Files       │    │   Database   │               │
│  │  (source)    │    │  (indexed)   │               │
│  └──────────────┘    └──────┬───────┘               │
│                             │                        │
│                             ▼                        │
│                      ┌──────────────┐               │
│                      │   Embeddings │               │
│                      │   (vectors)  │               │
│                      └──────┬───────┘               │
│                             │                        │
│                             ▼                        │
│                      ┌──────────────┐               │
│                      │  MCP Server  │               │
│                      └──────────────┘               │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### Data Flow

1. **Markdown files** = source of truth (human-editable)
2. **SQLite database** = indexed storage with FTS5 for text search
3. **Embeddings table** = semantic vectors for similarity search
4. **MCP server** = API layer for Claude to access all three

---

## Directory Structure

```
/Users/taylorstephens/_Lab/Claude-Innit/
├── README.md
├── pyproject.toml                 # Package config
├── claude_innit/
│   ├── __init__.py
│   ├── server.py                  # MCP server entry point
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py            # SQLite + FTS5
│   │   └── embeddings.py          # Vector storage
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── context.py             # get_context, sync
│   │   ├── search.py              # search (FTS + semantic)
│   │   ├── memory.py              # remember, forget
│   │   └── session.py             # save_session, update_project
│   └── sync/
│       ├── __init__.py
│       └── markdown_sync.py       # Markdown → DB sync
│
├── data/
│   ├── innit.db                   # SQLite database
│   └── memories/
│       ├── personal/
│       │   ├── identity.md
│       │   ├── preferences.md
│       │   └── workflows.md
│       ├── projects/
│       │   ├── _template.md
│       │   └── book-mcp-server.md
│       └── sessions/
│           ├── _index.md
│           └── 2025-01-30-architecture.md
│
└── tests/
    └── ...
```

---

## Database Schema

```sql
-- Core facts table
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    category TEXT,        -- 'personal', 'project', 'session'
    source_file TEXT,     -- path to markdown file
    content TEXT,         -- extracted text
    metadata JSON,        -- frontmatter as JSON
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Full-text search
CREATE VIRTUAL TABLE memories_fts USING fts5(content, category);

-- Vector embeddings
CREATE TABLE embeddings (
    memory_id TEXT PRIMARY KEY,
    embedding BLOB,       -- float32 array
    model TEXT            -- 'all-MiniLM-L6-v2'
);
```

---

## MCP Server Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `get_context()` | `working_dir?` | Load all relevant context for session start |
| `search(query)` | `query`, `method?` | Search memories (auto-selects FTS vs semantic) |
| `remember(content, category)` | `content`, `category`, `project?` | Store new memory |
| `forget(memory_id)` | `memory_id` | Remove a memory |
| `update_project(name, content)` | `name`, `content` | Update project context |
| `save_session(summary)` | `summary`, `topics?` | Save session summary |
| `sync()` | `force?` | Re-index markdown files into database |

### Smart Search Logic

```python
def search(query: str, method: str = "auto"):
    if method == "auto":
        # Short, specific queries → FTS
        if len(query.split()) <= 3:
            return fts_search(query)
        # Longer, conceptual queries → semantic
        return semantic_search(query)
    elif method == "text":
        return fts_search(query)
    elif method == "semantic":
        return semantic_search(query)
```

### get_context() Response

```json
{
  "personal": {
    "name": "Taylor Stephens",
    "role": "Program Manager with MBA",
    "preferences": ["concise responses", "business analogies"]
  },
  "project": {
    "name": "book-mcp-server",
    "status": "Pipeline functional, processing books",
    "recent_work": ["ProcessingAdapter", "MCP tools integration"]
  },
  "recent_sessions": [
    {"date": "2025-01-30", "topic": "Unified architecture implementation"}
  ]
}
```

---

## Session Lifecycle

```
┌─────────────────┐
│  Session Start  │
│  get_context()  │──────▶ Load personal + project + recent sessions
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  During Work    │
│  remember()     │──────▶ Capture important facts as they emerge
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Session End    │
│  save_session() │──────▶ Summarize what happened, update index
└─────────────────┘
```

### What Gets Remembered

| Trigger | Memory Created |
|---------|----------------|
| User states preference | `personal/preferences.md` updated |
| Major decision made | Added to project context |
| Bug fixed / feature added | Session summary |
| New project started | New `projects/{name}.md` created |

### Session Summary Format

```markdown
---
date: 2025-01-30
duration: ~2 hours
project: book-mcp-server
---

# Session: Unified Architecture Implementation

## What We Did
- Completed ProcessingAdapter integration
- Fixed MCP pipeline schema (added processing_result column)
- Processed and approved "101 Weird Ways to Make Money"

## Decisions Made
- Using markdown + SQLite + vectors hybrid for Claude Innit

## Open Items
- Claude Innit implementation pending
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| MCP Server | Python + mcp library |
| Database | SQLite with FTS5 |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) |
| File sync | watchdog (optional) or manual sync |

---

## Claude Code Integration

Add to `~/.claude/mcp_servers.json`:

```json
{
  "claude-innit": {
    "command": "python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "/Users/taylorstephens/_Lab/Claude-Innit"
  }
}
```

---

## Success Criteria

- [ ] `get_context()` returns personal + project + session context
- [ ] `search()` finds relevant memories via FTS and semantic search
- [ ] `remember()` persists facts to markdown and database
- [ ] `save_session()` creates session summary
- [ ] Markdown files sync to database on startup
- [ ] MCP server registers with Claude Code
