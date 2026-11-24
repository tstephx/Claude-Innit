#!/usr/bin/env python3
"""
Shared constants and utilities for materialize_sessions.py and materialize_memories.py.
"""

import os
import re
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

INNIT_BASE = Path.home() / "Dev/_Projects/Claude-Innit/data/memories"
VAULT_ROOT = Path(
    os.environ.get("VAULT_ROOT", str(Path.home() / "Dev/Obsidian-Second-Brain"))
)
VAULT_SESSIONS = VAULT_ROOT / "6_System" / "Sessions"
VAULT_MEMORY = VAULT_ROOT / "6_System" / "Claude-Memory"

# ── Project → _PROJECT_CARD path map ──────────────────────────────────────────
# Maps project slug (from innit metadata) to vault-relative path (no .md extension).
# Used to generate [[wikilinks]] in the related: field.

PROJECT_CARD_MAP = {
    "book-ingestion": "1_Projects/book-ingestion-python/_PROJECT_CARD",
    "book-ingestion-python": "1_Projects/book-ingestion-python/_PROJECT_CARD",
    "book-mcp-server": "1_Projects/book-mcp-server/_PROJECT_CARD",
    "briefcase": "1_Projects/briefcase/_PROJECT_CARD",
    "career-coach-mcp": "1_Projects/career-coach-mcp/_PROJECT_CARD",
    "claude-cheatsheet": "1_Projects/claude-cheatsheet/_PROJECT_CARD",
    "claude-innit": "1_Projects/Claude-Innit/_PROJECT_CARD",
    "dev-scripts": "1_Projects/dev-scripts/_PROJECT_CARD",
    "document-intelligence": "1_Projects/document-intelligence/_PROJECT_CARD",
    "fast-mail": "1_Projects/fast-mail/_PROJECT_CARD",
    "my-mcp-portfolio": "1_Projects/my-mcp-portfolio/_PROJECT_CARD",
    "obsidian-brain-framework": "1_Projects/obsidian-brain-framework/_PROJECT_CARD",
    "periodical-parser": "1_Projects/periodical-parser/_PROJECT_CARD",
    "virtual-career-coach": "1_Projects/career-coach-mcp/_PROJECT_CARD",
    "website-portfolio": "1_Projects/website-portfolio/_PROJECT_CARD",
    "whatbox": "1_Projects/whatbox/_PROJECT_CARD",
    "write-pipeline": "1_Projects/write-pipeline/_PROJECT_CARD",
    # Repository copies with no repository left: their folders sit in the archive.
    "claude-workspace": "4_Archive/Repo-Copies/claude-workspace/_PROJECT_CARD",
    "whatbox-portfolio-mcp": "4_Archive/Repo-Copies/whatbox-portfolio-mcp/_PROJECT_CARD",
    "dotfiles": "4_Archive/Repo-Copies/claude-projects/dotfiles/_PROJECT_CARD",
    "claude-projects": "4_Archive/Repo-Copies/claude-projects/_PROJECT_CARD",
    # Aliases
    "obsidian-brain": "1_Projects/obsidian-brain-framework/_PROJECT_CARD",
    "obsidian-second-brain": "1_Projects/obsidian-brain-framework/_PROJECT_CARD",
    "action-tracker": "1_Projects/action-tracker/_PROJECT_CARD",
    "rss-news-server": "1_Projects/whatbox/rss-news-server/_PROJECT_CARD",
    "morning-reader": "1_Projects/whatbox/morning-reader/_PROJECT_CARD",
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


def _existing_card(vault_root: Path, rel: str) -> str | None:
    """Return vault-relative `rel` (no .md) in its on-disk casing if the note exists.

    Components are matched case-insensitively so the result is the same on
    case-sensitive and case-insensitive filesystems (mirrors sync_vault `_project_link`).
    """
    cur = vault_root
    parts = []
    components = rel.split("/")
    last = len(components) - 1
    for i, part in enumerate(components):
        want = f"{part}.md" if i == last else part
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        hit = (
            want
            if want in names
            else next((n for n in names if n.lower() == want.lower()), None)
        )
        if hit is None:
            return None
        cur = cur / hit
        parts.append(hit.removesuffix(".md") if i == last else hit)
    return "/".join(parts) if cur.is_file() else None


def resolve_card_path(project: str, vault_root: Path | None = None) -> str | None:
    """Vault-relative _PROJECT_CARD path (no .md) for a project slug, or None.

    Uses PROJECT_CARD_MAP, falling back to 1_Projects/<slug>/_PROJECT_CARD. Returns a
    path only when that card exists under the vault, so callers never emit a dead
    `related:` link (mirrors sync_vault `_card_related`, #159/#176).
    """
    if not project:
        return None
    root = vault_root if vault_root is not None else VAULT_ROOT
    rel = PROJECT_CARD_MAP.get(project) or PROJECT_CARD_MAP.get(
        project.lower(), f"1_Projects/{project}/_PROJECT_CARD"
    )
    return _existing_card(root, rel)
