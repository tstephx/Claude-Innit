"""Property-based tests using Hypothesis.

Fuzzes search inputs, chunking parameters, and limit boundaries
to catch edge cases that hand-written tests miss.
"""

import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from claude_innit.db.database import MemoryDatabase
from claude_innit.utils import sanitize_fts_query, parse_frontmatter
from claude_innit.utils_chunking import chunk_by_headings, _split_by_paragraphs


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

# Single-char alphabet for FTS adversarial testing
_fts_chars = list(set(string.printable) | {"'", '"', "*", "(", ")"})
fts_adversarial = st.text(
    alphabet=st.sampled_from(_fts_chars),
    min_size=0,
    max_size=500,
)

# Markdown-like text (single chars only — no multi-char "\n\n")
_md_chars = list(
    set(string.ascii_letters + string.digits + " \t\n") | {"#", "-", "*", "`"}
)
markdown_text = st.text(
    alphabet=st.sampled_from(_md_chars),
    min_size=0,
    max_size=5000,
)

# Realistic markdown with actual heading patterns
heading_levels = st.sampled_from(["## ", "### "])
word = st.text(string.ascii_lowercase, min_size=1, max_size=20)
paragraph = st.lists(word, min_size=1, max_size=10).map(lambda ws: " ".join(ws))
section = st.tuples(heading_levels, word, paragraph).map(
    lambda t: f"{t[0]}{t[1]}\n\n{t[2]}"
)
structured_markdown = st.lists(section, min_size=0, max_size=10).map(
    lambda ss: "\n\n".join(ss)
)


# ---------------------------------------------------------------------------
# sanitize_fts_query — should never produce invalid FTS5 syntax
# ---------------------------------------------------------------------------


class TestSanitizeFtsQueryProperty:
    @given(query=fts_adversarial)
    @settings(max_examples=200)
    def test_never_raises(self, query):
        """sanitize_fts_query handles any string without raising."""
        result = sanitize_fts_query(query)
        assert isinstance(result, str)

    @given(query=fts_adversarial)
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_result_is_valid_fts5(self, tmp_path, query):
        """Sanitized query never causes FTS5 OperationalError."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory("prop/1", "personal", "test content for search", metadata={})

        # This should never raise sqlite3.OperationalError
        results = db.fts_search(query)
        assert isinstance(results, list)

    @given(query=st.text(min_size=0, max_size=10000))
    @settings(max_examples=100)
    def test_very_long_queries(self, query):
        """Very long queries don't crash sanitize_fts_query."""
        result = sanitize_fts_query(query)
        assert isinstance(result, str)


class TestSanitizeFtsQueryOutput:
    """Output-format tests to kill mutation survivors."""

    def test_single_word_quoted(self):
        """Single word gets double-quoted."""
        assert sanitize_fts_query("hello") == '"hello"'

    def test_multiple_words_space_joined(self):
        """Multiple words are space-joined, each quoted."""
        assert sanitize_fts_query("hello world") == '"hello" "world"'

    def test_operators_stripped_words_extracted(self):
        """FTS5 operators stripped, only \\w+ words survive."""
        assert sanitize_fts_query('hello AND "world"') == '"hello" "AND" "world"'

    def test_no_words_returns_original(self):
        """Input with no \\w+ matches returns original string."""
        assert sanitize_fts_query("***") == "***"

    def test_word_pattern_extracts_alphanumeric(self):
        """Pattern matches alphanumeric + underscore sequences."""
        result = sanitize_fts_query("a_1 b-2 c.3")
        assert '"a_1"' in result
        assert '"b"' in result
        assert '"c"' in result


class TestParseFrontmatterOutput:
    """Output tests for parse_frontmatter to kill mutation survivors."""

    def test_extracts_yaml_dict(self):
        """Frontmatter YAML becomes a dict."""
        text = "---\ntitle: Hello\nstatus: draft\n---\nBody text."
        fm, body = parse_frontmatter(text)
        assert fm == {"title": "Hello", "status": "draft"}
        assert body == "Body text."

    def test_no_frontmatter_returns_empty_dict(self):
        """No frontmatter returns ({}, original text)."""
        text = "Just body text, no frontmatter."
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_invalid_yaml_returns_empty_dict(self):
        """Invalid YAML in frontmatter returns {} for metadata."""
        text = "---\n[invalid: yaml: :\n---\nBody."
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == "Body."

    def test_empty_frontmatter_returns_empty_dict(self):
        """Empty frontmatter block returns {}."""
        text = "---\n\n---\nBody."
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == "Body."

    def test_body_starts_after_closing_dashes(self):
        """Body text is everything after the closing --- delimiter."""
        text = "---\nkey: value\n---\nLine 1\nLine 2"
        fm, body = parse_frontmatter(text)
        assert fm == {"key": "value"}
        assert body == "Line 1\nLine 2"

    def test_frontmatter_group1_is_yaml_content(self):
        """The regex captures YAML content in group(1), not group(2)."""
        text = "---\ntitle: Test\n---\nBody."
        fm, body = parse_frontmatter(text)
        assert fm["title"] == "Test"


# ---------------------------------------------------------------------------
# chunk_by_headings — invariants that should hold for any input
# ---------------------------------------------------------------------------


class TestChunkByHeadingsProperty:
    @given(content=markdown_text)
    @settings(max_examples=200)
    def test_never_raises(self, content):
        """chunk_by_headings handles any text without raising."""
        result = chunk_by_headings(content)
        assert isinstance(result, list)

    @given(content=markdown_text)
    @settings(max_examples=200)
    def test_chunk_indices_are_sequential(self, content):
        """Chunk indices are always 0, 1, 2, ... in order."""
        chunks = chunk_by_headings(content)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    @given(content=markdown_text)
    @settings(max_examples=200)
    def test_all_chunks_have_required_fields(self, content):
        """Every chunk has chunk_index, heading, content, char_offset."""
        chunks = chunk_by_headings(content)
        for chunk in chunks:
            assert "chunk_index" in chunk
            assert "heading" in chunk
            assert "content" in chunk
            assert "char_offset" in chunk
            assert isinstance(chunk["chunk_index"], int)
            assert isinstance(chunk["char_offset"], int)
            assert isinstance(chunk["content"], str)

    @given(content=markdown_text)
    @settings(max_examples=200)
    def test_no_empty_content_chunks(self, content):
        """No chunk should have empty content."""
        chunks = chunk_by_headings(content)
        for chunk in chunks:
            assert len(chunk["content"]) > 0

    @given(content=structured_markdown)
    @settings(max_examples=100)
    def test_structured_markdown_produces_chunks(self, content):
        """Structured markdown with headings produces at least one chunk."""
        assume(len(content.strip()) > 0)
        chunks = chunk_by_headings(content)
        assert len(chunks) >= 1

    @given(
        content=structured_markdown,
        max_chars=st.integers(min_value=50, max_value=2000),
    )
    @settings(max_examples=100)
    def test_max_chunk_chars_respected(self, content, max_chars):
        """No chunk exceeds max_chunk_chars by more than 3x (paragraph granularity)."""
        assume(len(content.strip()) > 0)
        chunks = chunk_by_headings(content, max_chunk_chars=max_chars)
        for chunk in chunks:
            # Paragraphs are atomic — a single large paragraph can exceed max_chars
            if len(chunk["content"]) > max_chars * 3:
                pytest.fail(
                    f"Chunk too large: {len(chunk['content'])} chars "
                    f"(max_chars={max_chars})"
                )

    @given(content=st.text(min_size=0, max_size=100))
    @settings(max_examples=100)
    def test_short_content_single_chunk(self, content):
        """Content shorter than max_chunk_chars produces at most 1 chunk."""
        assume(len(content.strip()) > 0)
        chunks = chunk_by_headings(content, max_chunk_chars=10000)
        assert len(chunks) == 1

    @given(content=st.from_regex(r"^\s*$", fullmatch=True))
    @settings(max_examples=50)
    def test_whitespace_only_returns_empty(self, content):
        """Whitespace-only content returns []."""
        chunks = chunk_by_headings(content)
        assert chunks == []


# ---------------------------------------------------------------------------
# _split_by_paragraphs — invariants
# ---------------------------------------------------------------------------


class TestSplitByParagraphsProperty:
    @given(text=st.text(min_size=1, max_size=3000))
    @settings(max_examples=100)
    def test_never_raises(self, text):
        """_split_by_paragraphs handles any non-empty text."""
        result = _split_by_paragraphs(text, max_chunk_chars=500)
        assert isinstance(result, list)

    @given(
        text=st.text(min_size=1, max_size=3000),
        max_chars=st.integers(min_value=10, max_value=2000),
    )
    @settings(max_examples=100)
    def test_indices_sequential(self, text, max_chars):
        """Paragraph chunks always have sequential indices."""
        chunks = _split_by_paragraphs(text, max_chunk_chars=max_chars)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# vault_search limit fuzzing
# ---------------------------------------------------------------------------


class TestVaultSearchLimitProperty:
    @given(limit=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=100)
    def test_limit_never_crashes(self, limit):
        """vault_search with any integer limit never crashes."""
        from claude_innit.tools.vault import vault_search

        with tempfile.TemporaryDirectory() as td:
            db = MemoryDatabase(Path(td) / "test.db")
            db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1")
            result = vault_search(db, "test", limit=limit, method="text")
            assert isinstance(result, list)
            assert len(result) <= 100

    @given(limit=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=100)
    def test_federated_limit_never_crashes(self, limit):
        """federated_search with any integer limit never crashes."""
        from claude_innit.tools.federation import federated_search

        with tempfile.TemporaryDirectory() as td:
            db = MemoryDatabase(Path(td) / "test.db")
            db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1")
            result = federated_search(db, "test", limit=limit)
            assert isinstance(result, dict)
            assert "merged" in result
            assert len(result["merged"]) <= 100
