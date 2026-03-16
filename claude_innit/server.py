"""MCP server for Claude Innit."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.sync.markdown_sync import MarkdownSync
from claude_innit.tools import (
    get_context,
    search,
    remember,
    forget,
    save_session,
    check_integrity,
    list_memories,
    vault_index,
    vault_search,
    vault_related,
    vault_stats,
    federated_search,
    vault_tag,
)


class InnitServer:
    """MCP server wrapper with tool registration."""

    def __init__(
        self,
        server: Server,
        db: MemoryDatabase,
        sync: MarkdownSync,
        memories_dir: Path,
        vault_root: Optional[str] = None,
        extra_index_paths: Optional[list[str]] = None,
    ):
        self.server = server
        self.db = db
        self.sync = sync
        self.memories_dir = memories_dir
        self.vault_root = vault_root
        self.extra_index_paths = extra_index_paths or []
        self.embedding_store = EmbeddingStore(db)
        # Pre-load embedding model and matrix to avoid timeout on first query
        try:
            self.embedding_store.warm()
            self.embedding_store.load_matrix()
        except Exception:
            pass  # Embeddings optional — degrade gracefully if torch missing
        self._tools = self._define_tools()

    def _define_tools(self) -> list[Tool]:
        """Define all available tools."""
        return [
            Tool(
                name="get_context",
                description=(
                    "Load all persistent memory for this session. "
                    "Call this once at the start of every session before doing any other work. "
                    "Pass the project name to filter to relevant project and session memories."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "description": "Project name to filter context (e.g. 'claude-innit', 'my-app')",
                        },
                    },
                },
            ),
            Tool(
                name="search",
                description=(
                    "Find stored memories by keyword or concept. "
                    "Call this when you need information from past sessions that is not in the current get_context result. "
                    "Short keywords (1-3 words) use exact text match. Longer phrases use semantic/concept search."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query — use short keywords for exact match, longer phrases for concept recall",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["auto", "text", "semantic"],
                            "description": "Search method — auto (default) chooses based on query length",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="remember",
                description=(
                    "Store new information persistently across sessions. "
                    "Use for facts, decisions, preferences, or project state that should be recalled in future sessions. "
                    "Choose category: 'personal' for user preferences/identity, 'project' for per-project state, 'session' for session summaries."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The information to remember",
                        },
                        "category": {
                            "type": "string",
                            "enum": ["personal", "project", "session"],
                            "description": "Memory category",
                        },
                        "project": {
                            "type": "string",
                            "description": "Project name — required when category is 'project'",
                        },
                    },
                    "required": ["content", "category"],
                },
            ),
            Tool(
                name="forget",
                description=(
                    "Permanently delete a memory. "
                    "Requires the memory_id — use list_memories first to discover IDs. "
                    "Use when an improvement has been implemented, a fact is no longer true, or a memory is outdated."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of memory to delete (e.g. 'personal/a3f8c2d1') — get from list_memories or a prior remember call",
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
            Tool(
                name="save_session",
                description=(
                    "Save a summary of this session for future recall. "
                    "Call once at the end of a working session — not after each sub-task. "
                    "Include what was completed, what to do next, and any key decisions made."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Session summary (use format: LAST: [...] | NEXT: [...] | DECISIONS: [...])",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Topics covered in this session",
                        },
                        "project": {
                            "type": "string",
                            "description": "Project worked on",
                        },
                    },
                    "required": ["summary"],
                },
            ),
            Tool(
                name="list_memories",
                description=(
                    "List stored memories with their IDs, previews, and categories. "
                    "Call this to discover memory IDs before using forget(), or to audit what is stored. "
                    "Filter by category ('personal', 'project', 'session') or project name."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["personal", "project", "session"],
                            "description": "Filter by memory category",
                        },
                        "project": {
                            "type": "string",
                            "description": "Filter project memories by project name",
                        },
                    },
                },
            ),
            Tool(
                name="admin_sync",
                description=(
                    "Operator tool: Re-sync markdown files to database. "
                    "Not needed in normal sessions — only call if memories are out of sync after manual file edits."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="admin_check_integrity",
                description=(
                    "Operator tool: Check database health and repair issues. "
                    "Not needed in normal sessions — only call when experiencing search or sync failures. "
                    "Checks FTS index sync, orphaned embeddings, and SQLite integrity."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "auto_repair": {
                            "type": "boolean",
                            "description": "Automatically fix issues found (default: true)",
                        },
                    },
                },
            ),
            # --- OBF Vault Tools ---
            Tool(
                name="vault_index",
                description=(
                    "Index vault markdown files into the search database. "
                    "Call this to update the vault search index after file changes. "
                    "Skips unchanged files by default (hash-based). Use force=true to reindex everything."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault_root": {
                            "type": "string",
                            "description": "Path to the Obsidian vault root (overrides server default)",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Force reindex all files even if unchanged (default: false)",
                        },
                    },
                },
            ),
            Tool(
                name="vault_search",
                description=(
                    "Search vault files by keyword or concept. "
                    "Use scope to narrow: 'vault' for vault notes, 'configs' for framework config files, 'all' for everything."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["vault", "configs", "all"],
                            "description": "Search scope (default: all)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 20)",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["auto", "text", "semantic"],
                            "description": "Search method (default: auto — text for short queries, semantic for longer)",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by frontmatter status (e.g. 'active', 'archived')",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="vault_related",
                description=(
                    "Find notes related to a given note. "
                    "Uses semantic similarity if embeddings exist, otherwise falls back to filename-based FTS."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "note_path": {
                            "type": "string",
                            "description": "Full path to the source note",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max related notes to return (default: 10)",
                        },
                    },
                    "required": ["note_path"],
                },
            ),
            Tool(
                name="vault_stats",
                description=(
                    "Return vault health metrics: total notes, notes by module, notes by status, "
                    "inbox count, stale count, and index freshness."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="vault_rechunk",
                description=(
                    "Force re-chunk and re-embed all vault files. "
                    "Use when chunking parameters change or chunks seem stale."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="vault_tag",
                description=(
                    "Tag vault .md files (vault root only, not extra index paths) missing YAML frontmatter. "
                    "Phase 1: call without apply to preview untagged files grouped by folder. "
                    "Phase 2: call with apply=true and optional folder_defaults/file_overrides to write frontmatter. "
                    "Run vault_index after to update the search index."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vault_path": {
                            "type": "string",
                            "description": "Path to vault root (default: VAULT_ROOT env var)",
                        },
                        "apply": {
                            "type": "boolean",
                            "description": "True to write frontmatter, false for preview (default: false)",
                        },
                        "folder_defaults": {
                            "type": "object",
                            "description": 'Per-folder default overrides, e.g. {"Projects": {"status": "archived"}}',
                            "additionalProperties": {"type": "object"},
                        },
                        "file_overrides": {
                            "type": "object",
                            "description": 'Per-file overrides (relative path), e.g. {"Projects/old.md": {"status": "archived"}}',
                            "additionalProperties": {"type": "object"},
                        },
                    },
                },
            ),
            Tool(
                name="federated_search",
                description=(
                    "Search across vault, book library, and session memory simultaneously. "
                    "Results are merged using reciprocal rank fusion with source weighting. "
                    "Sources: vault (indexed vault files), books (book-library chapters), "
                    "sessions (claude-innit memories), portfolio (materialized portfolio docs)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["vault", "books", "sessions", "portfolio"],
                            },
                            "description": "Which sources to search (default: all)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results per source and in merged (default: 30)",
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    @property
    def name(self) -> str:
        return self.server.name

    def list_tools(self) -> list[Tool]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls with error boundary — never drops the MCP connection."""
        try:
            if name == "get_context":
                result = get_context(self.db, project=arguments.get("project"))
            elif name == "search":
                result = search(
                    self.db,
                    query=arguments["query"],
                    method=arguments.get("method", "auto"),
                    embedding_store=self.embedding_store,
                )
            elif name == "remember":
                result = remember(
                    self.db,
                    content=arguments["content"],
                    category=arguments["category"],
                    project=arguments.get("project"),
                    memories_dir=self.memories_dir,
                    embedding_store=self.embedding_store,
                )
            elif name == "forget":
                result = forget(
                    self.db, arguments["memory_id"], memories_dir=self.memories_dir
                )
            elif name == "save_session":
                result = save_session(
                    self.db,
                    summary=arguments["summary"],
                    topics=arguments.get("topics"),
                    project=arguments.get("project"),
                    memories_dir=self.memories_dir,
                )
            elif name == "admin_sync":
                result = self.sync.sync_all()
            elif name == "admin_check_integrity":
                result = check_integrity(
                    self.db,
                    auto_repair=arguments.get("auto_repair", True),
                )
            elif name == "list_memories":
                result = list_memories(
                    self.db,
                    category=arguments.get("category"),
                    project=arguments.get("project"),
                )
            # --- OBF Vault Tools ---
            elif name == "vault_index":
                vr = arguments.get("vault_root") or self.vault_root
                if not vr:
                    result = {
                        "error": "vault_root not configured — pass it as argument or set in server config"
                    }
                else:
                    # Create a dedicated DB connection for the thread to avoid
                    # sharing the main connection across threads (SQLite safety)
                    def _threaded_vault_index():
                        thread_db = MemoryDatabase(self.db.db_path)
                        try:
                            result = vault_index(
                                thread_db,
                                vault_root=vr,
                                extra_paths=self.extra_index_paths,
                                force=arguments.get("force", False),
                            )
                            cleaned = thread_db.cleanup_orphan_vault_embeddings()
                            result["orphan_embeddings_cleaned"] = cleaned
                            return result
                        finally:
                            thread_db.close()

                    result = await asyncio.to_thread(_threaded_vault_index)

                    # Generate chunk embeddings and reload matrix
                    if self.embedding_store:
                        try:
                            chunk_result = await asyncio.to_thread(
                                self.embedding_store.batch_store_chunk_embeddings,
                            )
                            result["chunks"] = chunk_result
                            matrix_count = await asyncio.to_thread(
                                self.embedding_store.load_matrix,
                            )
                            result["matrix_loaded"] = matrix_count
                        except ImportError:
                            result["embeddings"] = "skipped (numpy not installed)"
            elif name == "vault_search":
                try:
                    result = vault_search(
                        self.db,
                        query=arguments["query"],
                        scope=arguments.get("scope", "all"),
                        limit=arguments.get("limit", 20),
                        embedding_store=self.embedding_store,
                        method=arguments.get("method", "auto"),
                        status=arguments.get("status"),
                    )
                except ValueError as e:
                    return [
                        TextContent(type="text", text=json.dumps({"error": str(e)}))
                    ]
            elif name == "vault_related":
                result = vault_related(
                    self.db,
                    note_path=arguments["note_path"],
                    limit=arguments.get("limit", 10),
                    embedding_store=self.embedding_store,
                )
            elif name == "vault_stats":
                result = vault_stats(self.db, embedding_store=self.embedding_store)
            elif name == "vault_rechunk":
                if self.embedding_store:
                    try:
                        result = await asyncio.to_thread(
                            self.embedding_store.batch_store_chunk_embeddings,
                            force=True,
                        )
                        matrix_count = await asyncio.to_thread(
                            self.embedding_store.load_matrix,
                        )
                        result["matrix_reloaded"] = matrix_count
                    except ImportError:
                        result = {
                            "error": "numpy not installed — embeddings unavailable"
                        }
                else:
                    result = {"error": "No embedding store configured"}
            elif name == "vault_tag":
                vault_path = arguments.get("vault_path") or self.vault_root
                if not vault_path:
                    return [
                        TextContent(
                            type="text",
                            text="Error: vault_path required (set VAULT_ROOT or pass vault_path)",
                        )
                    ]
                result = await asyncio.to_thread(
                    vault_tag,
                    vault_path,
                    arguments.get("apply", False),
                    arguments.get("folder_defaults"),
                    arguments.get("file_overrides"),
                )
            elif name == "federated_search":
                result = federated_search(
                    self.db,
                    query=arguments["query"],
                    sources=arguments.get("sources"),
                    limit=arguments.get("limit", 30),
                    rrf_k=arguments.get("rrf_k", 60),
                    embedding_store=self.embedding_store,
                )
            else:
                result = {"error": f"Unknown tool: {name}"}
        except Exception as e:
            result = {"error": type(e).__name__, "message": str(e), "tool": name}

        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]


def create_server(
    db_path: Path,
    memories_dir: Path,
    vault_root: Optional[str] = None,
    extra_index_paths: Optional[list[str]] = None,
) -> InnitServer:
    """Create and configure the MCP server."""
    server = Server("claude-innit")

    # Initialize database
    db = MemoryDatabase(db_path)
    sync = MarkdownSync(db_path, memories_dir, generate_embeddings=False)
    # Sync is deferred to main() background task — do not block here

    # Create wrapper
    innit_server = InnitServer(
        server,
        db,
        sync,
        memories_dir,
        vault_root=vault_root,
        extra_index_paths=extra_index_paths,
    )

    # Register handlers
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return innit_server.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await innit_server.call_tool(name, arguments)

    return innit_server


async def _background_sync(sync: MarkdownSync) -> None:
    """Run sync in background after server is accepting connections."""
    try:
        await asyncio.to_thread(sync.sync_all)
    except Exception:
        logger.debug("Background sync failed", exc_info=True)


async def main():
    """Run the MCP server."""
    import os

    # Default paths
    base_dir = Path(__file__).parent.parent
    db_path = base_dir / "data" / "innit.db"
    memories_dir = base_dir / "data" / "memories"
    vault_root = os.environ.get(
        "VAULT_ROOT", str(Path.home() / "Dev" / "Obsidian-Second-Brain")
    )

    # Extra directories to index alongside the vault (markdown files only)
    extra_index_paths = (
        os.environ.get("EXTRA_INDEX_PATHS", "").split(":")
        if os.environ.get("EXTRA_INDEX_PATHS")
        else [
            str(Path.home() / "Dev" / "_Lab"),
            str(Path.home() / "Dev" / "_Projects"),
        ]
    )

    innit_server = create_server(
        db_path,
        memories_dir,
        vault_root=vault_root,
        extra_index_paths=extra_index_paths,
    )

    async with stdio_server() as (read_stream, write_stream):
        # Defer sync to background — don't block initialize handshake
        if memories_dir.exists():
            asyncio.create_task(_background_sync(innit_server.sync))

        await innit_server.server.run(
            read_stream,
            write_stream,
            innit_server.server.create_initialization_options(),
        )


def main_sync():
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
