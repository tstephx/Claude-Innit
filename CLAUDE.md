# CLAUDE.md - Claude-Innit Memory System

## Session Initialization (READ FIRST)

**DO NOT scan directories on startup.** This is a focused MCP server project.

### Quick Start Protocol
1. Run `get_context(project="claude-innit")` for project memory
2. Check `git status` and `git log -3` for recent state
3. Ask the user what they want to work on

---

## Project Purpose

MCP server that gives Claude persistent memory across sessions. Three memory categories (personal, project, session), dual search (FTS5 + semantic), markdown-first storage.

---

## Project Structure (No Scanning Needed)

```
Claude-Innit/
├── claude_innit/             # Source code
│   ├── server.py             # Main MCP server entry point
│   ├── tools/                # MCP tool implementations
│   │   ├── context.py        # get_context tool
│   │   ├── memory.py         # remember, forget tools
│   │   ├── search.py         # search tool
│   │   ├── session.py        # save_session tool
│   │   └── maintenance.py    # sync, check_integrity tools
│   ├── db/                   # Database operations
│   │   ├── database.py       # SQLite + FTS5 operations
│   │   └── embeddings.py     # Sentence-transformer embeddings
│   └── sync/                 # Markdown sync engine
│       └── sync_engine.py    # Bidirectional markdown ↔ DB sync
│
├── data/                     # Runtime data
│   ├── innit.db              # SQLite database (FTS5 + embeddings)
│   └── memories/             # Markdown files (SOURCE OF TRUTH)
│       ├── personal/         # Identity, preferences
│       ├── project/          # Per-project context
│       └── sessions/         # Session summaries
│
├── tests/                    # Test suite (19 tests)
│   ├── test_database.py
│   ├── test_embeddings.py
│   ├── test_server.py
│   ├── test_sync.py
│   └── test_tools.py
│
├── docs/                     # Design documents
├── scripts/                  # Utility scripts
├── pyproject.toml            # Package config
└── README.md                 # Installation guide
```

---

## MCP Tools (7 total)

| Tool | Purpose | Example |
|------|---------|---------|
| `get_context` | Load memories for session start | `get_context(project="my-project")` |
| `search` | Find memories (auto-routes FTS5/semantic) | `search("Python preferences")` |
| `remember` | Store new memory | `remember(content, category, project)` |
| `forget` | Remove a memory | `forget(memory_id)` |
| `save_session` | Save session summary | `save_session(summary, project, topics)` |
| `sync` | Re-sync markdown to database | `sync(force=True)` |
| `check_integrity` | Verify and repair database | `check_integrity(auto_repair=True)` |

---

## Key Technical Decisions

- **Markdown-first**: Files in `data/memories/` are source of truth; DB is index
- **Smart search routing**: 1-3 words → FTS5 (fast), 4+ words → semantic (conceptual)
- **Lazy embedding load**: Model only loads when semantic search is needed
- **all-MiniLM-L6-v2**: 384-dim embeddings, fast and good quality

---

## Development Commands

```bash
# Run tests
pytest tests/ -v

# Run specific test file
pytest tests/test_tools.py -v

# Run MCP server directly
python -m claude_innit.server

# Install in development mode
pip install -e .
```

---

## Common Tasks

### Add a new MCP tool
1. Create tool function in `claude_innit/tools/`
2. Register in `claude_innit/tools/__init__.py`
3. Add to server in `claude_innit/server.py`
4. Write tests in `tests/test_tools.py`

### Modify database schema
1. Update `claude_innit/db/database.py`
2. Update sync engine if markdown format changes
3. Run `sync(force=True)` to rebuild

### Debug memory issues
1. Check `data/memories/` for markdown files
2. Run `check_integrity(auto_repair=True)`
3. Check `data/innit.db` with sqlite3 if needed

---

## Git Workflow

- Branch: `main`
- Commit style: `feat:`, `fix:`, `test:`, `refactor:`
- TDD: Write tests before implementation

---

## Memory Integration

This project IS claude-innit, so use it for its own context:
- `get_context(project="claude-innit")` - Load project memory
- `remember(content, category="project", project="claude-innit")` - Save decisions

---

*Last updated: 2026-02-06*
