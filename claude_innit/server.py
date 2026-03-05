"""MCP server for Claude Innit."""

import asyncio
import json
from pathlib import Path
from typing import Optional

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
)


class InnitServer:
    """MCP server wrapper with tool registration."""

    def __init__(self, server: Server, db: MemoryDatabase, sync: MarkdownSync, memories_dir: Path):
        self.server = server
        self.db = db
        self.sync = sync
        self.memories_dir = memories_dir
        self.embedding_store = EmbeddingStore(db)
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
                result = forget(self.db, arguments["memory_id"], memories_dir=self.memories_dir)
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
            else:
                result = {"error": f"Unknown tool: {name}"}
        except Exception as e:
            result = {"error": type(e).__name__, "message": str(e), "tool": name}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


def create_server(
    db_path: Path,
    memories_dir: Path,
) -> InnitServer:
    """Create and configure the MCP server."""
    server = Server("claude-innit")

    # Initialize database
    db = MemoryDatabase(db_path)
    sync = MarkdownSync(db_path, memories_dir, generate_embeddings=False)
    # Sync is deferred to main() background task — do not block here

    # Create wrapper
    innit_server = InnitServer(server, db, sync, memories_dir)

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
        pass  # Sync failure is non-fatal; server continues without it


async def main():
    """Run the MCP server."""
    # Default paths
    base_dir = Path(__file__).parent.parent
    db_path = base_dir / "data" / "innit.db"
    memories_dir = base_dir / "data" / "memories"

    innit_server = create_server(db_path, memories_dir)

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
