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
)


class InnitServer:
    """MCP server wrapper with tool registration."""

    def __init__(self, server: Server, db: MemoryDatabase, sync: MarkdownSync):
        self.server = server
        self.db = db
        self.sync = sync
        self.embedding_store = EmbeddingStore(db)
        self._tools = self._define_tools()

    def _define_tools(self) -> list[Tool]:
        """Define all available tools."""
        return [
            Tool(
                name="get_context",
                description="Load personal, project, and session context",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "description": "Optional project name to filter by",
                        },
                    },
                },
            ),
            Tool(
                name="search",
                description="Search memories using FTS or semantic search",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["auto", "text", "semantic"],
                            "description": "Search method (default: auto)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="remember",
                description="Store a new memory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Content to remember",
                        },
                        "category": {
                            "type": "string",
                            "enum": ["personal", "project", "session"],
                            "description": "Memory category",
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional project name",
                        },
                    },
                    "required": ["content", "category"],
                },
            ),
            Tool(
                name="forget",
                description="Remove a memory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of memory to remove",
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
            Tool(
                name="save_session",
                description="Save session summary",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Session summary",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Topics covered",
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
                name="sync",
                description="Re-sync markdown files to database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "description": "Force full resync",
                        },
                    },
                },
            ),
            Tool(
                name="check_integrity",
                description="Check database health and auto-repair issues (FTS index sync, orphaned embeddings, SQLite integrity)",
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
        """Handle tool calls."""
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
            )
        elif name == "forget":
            result = forget(self.db, arguments["memory_id"])
        elif name == "save_session":
            result = save_session(
                self.db,
                summary=arguments["summary"],
                topics=arguments.get("topics"),
                project=arguments.get("project"),
            )
        elif name == "sync":
            result = self.sync.sync_all()
        elif name == "check_integrity":
            result = check_integrity(
                self.db,
                auto_repair=arguments.get("auto_repair", True),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

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

    # Sync on startup if memories dir exists
    if memories_dir.exists():
        sync.sync_all()

    # Create wrapper
    innit_server = InnitServer(server, db, sync)

    # Register handlers
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return innit_server.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await innit_server.call_tool(name, arguments)

    return innit_server


async def main():
    """Run the MCP server."""
    # Default paths
    base_dir = Path(__file__).parent.parent
    db_path = base_dir / "data" / "innit.db"
    memories_dir = base_dir / "data" / "memories"

    innit_server = create_server(db_path, memories_dir)

    async with stdio_server() as (read_stream, write_stream):
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
