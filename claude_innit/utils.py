"""Shared utilities for claude-innit."""

import re

import yaml

_WORD_PATTERN = re.compile(r"\w+")


def sanitize_fts_query(query: str) -> str:
    """Sanitize a query for FTS5 — strips operators, quotes each word."""
    words = _WORD_PATTERN.findall(query)
    return " ".join(f'"{w}"' for w in words) if words else query


FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown text.

    Returns (frontmatter_dict, body_text). If no frontmatter found,
    returns ({}, original_text).
    """
    match = FRONTMATTER_PATTERN.match(text)
    if match:
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, text[match.end() :]
    return {}, text
