#!/usr/bin/env python3
"""
Shared constants and utilities for materialize_sessions.py and materialize_memories.py.
"""

import re
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

INNIT_BASE = Path.home() / "Dev/_Projects/Claude-Innit/data/memories"
VAULT_ROOT = Path.home() / "Dev/Obsidian-Second-Brain"
VAULT_SESSIONS = VAULT_ROOT / "Sessions"
VAULT_MEMORY = VAULT_ROOT / "Claude-Memory"

# ── Project → _PROJECT_CARD path map ──────────────────────────────────────────
# Maps project slug (from innit metadata) to vault-relative path (no .md extension).
# Used to generate [[wikilinks]] in the related: field.

PROJECT_CARD_MAP = {
    "api-dashboard": "Projects/api-dashboard/_PROJECT_CARD",
    "book-ingestion": "Projects/mcp (category)/mcp-books (category)/book-ingestion-python/_PROJECT_CARD",
    "book-ingestion-python": "Projects/mcp (category)/mcp-books (category)/book-ingestion-python/_PROJECT_CARD",
    "book-mcp-server": "Projects/mcp (category)/mcp-books (category)/book-mcp-server/_PROJECT_CARD",
    "briefcase": "Projects/mcp (category)/briefcase/_PROJECT_CARD",
    "career-coach-mcp": "Projects/mcp (category)/career-coach-mcp/_PROJECT_CARD",
    "claude-cheatsheet": "Projects/claude-cheatsheet/_PROJECT_CARD",
    "claude-innit": "Projects/mcp (category)/Claude-Innit/_PROJECT_CARD",
    "claude-workspace": "Projects/claude-workspace/_PROJECT_CARD",
    "dev-scripts": "Projects/dev-scripts/_PROJECT_CARD",
    "document-intelligence": "Projects/taylor-work (category)/document-intelligence/_PROJECT_CARD",
    "fast-mail": "Projects/mcp (category)/fast-mail/_PROJECT_CARD",
    "my-mcp-portfolio": "Projects/mcp (category)/my-mcp-portfolio/_PROJECT_CARD",
    "obsidian-brain-framework": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "periodical-parser": "Projects/whatbox-server/periodical-parser/_PROJECT_CARD",
    "virtual-career-coach": "Projects/mcp (category)/career-coach-mcp/_PROJECT_CARD",
    "website-portfolio": "Projects/whatbox-server/website-portfolio/_PROJECT_CARD",
    "whatbox": "Projects/whatbox-server/whatbox/_PROJECT_CARD",
    "whatbox-portfolio-mcp": "Projects/mcp (category)/whatbox-portfolio-mcp/_PROJECT_CARD",
    "write-pipeline": "Projects/whatbox-server/whatbox/morning-reader/_PROJECT_CARD",
    # Aliases
    "_lab": "Projects/lab-docs/_PROJECT_CARD",
    "obsidian-brain": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "obsidian-second-brain": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "dotfiles": "Projects/dotfiles/_PROJECT_CARD",
    "action-tracker": "Projects/action-tracker/_PROJECT_CARD",
    "claude-projects": "Projects/claude-projects/_PROJECT_CARD",
    "rss-news-server": "Projects/whatbox-server/whatbox/rss-news-server/_PROJECT_CARD",
    "morning-reader": "Projects/whatbox-server/whatbox/morning-reader/_PROJECT_CARD",
}


# ── Utilities ─────────────────────────────────────────────────────────────────


def slugify(project: str) -> str:
    """Normalize project slug: lowercase, replace non-alphanumeric with -, strip leading -."""
    return re.sub(r"[^a-z0-9-]", "-", project.lower()).strip("-")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (fields, body). Fields are raw strings; body is the content after ---."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    fields: dict = {}
    current_key = None
    current_list: list[str] = []

    for line in raw.splitlines():
        list_match = re.match(r"^- (.+)$", line)
        kv_match = re.match(r"^(\w[\w-]*):\s*(.*)", line)

        if list_match and current_key:
            current_list.append(list_match.group(1).strip("'\""))
        elif kv_match:
            if current_key and current_list:
                fields[current_key] = current_list
                current_list = []
            current_key = kv_match.group(1)
            val = kv_match.group(2).strip().strip("'\"")
            if val:
                fields[current_key] = val
                current_key = None
        else:
            if current_key and current_list:
                fields[current_key] = current_list
                current_list = []
            current_key = None

    if current_key and current_list:
        fields[current_key] = current_list

    return fields, body


def strip_frontmatter_body(text: str) -> str:
    """Return just the body content, stripping frontmatter if present."""
    _, body = parse_frontmatter(text)
    return body.strip()


def resolve_card_path(project: str) -> str:
    """Look up the _PROJECT_CARD path for a project slug, with fallback."""
    return PROJECT_CARD_MAP.get(project, f"Projects/{project}/_PROJECT_CARD")
