"""Tests for utils_chunking.py — heading-level text chunking."""

import pytest

from claude_innit.utils_chunking import (
    chunk_by_headings,
    get_config_dict,
    _split_by_paragraphs,
    _merge_small_sections,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_section(heading, content, char_offset=0):
    """Build a minimal section dict (without chunk_index)."""
    return {"heading": heading, "content": content, "char_offset": char_offset}


# ---------------------------------------------------------------------------
# 1. Empty / whitespace input -> empty list
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_string_returns_empty_list(self):
        assert chunk_by_headings("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert chunk_by_headings("   \n\n  \t  ") == []

    def test_newlines_only_returns_empty_list(self):
        assert chunk_by_headings("\n\n\n") == []


# ---------------------------------------------------------------------------
# 2. Short file (< max_chunk_chars) -> single chunk
# ---------------------------------------------------------------------------


class TestShortFile:
    def test_short_file_returns_single_chunk(self):
        content = "This is a short file.\n\nIt has two paragraphs."
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert len(result) == 1

    def test_short_file_chunk_index_is_zero(self):
        content = "Short content here."
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert result[0]["chunk_index"] == 0

    def test_short_file_char_offset_is_zero(self):
        content = "Short content here."
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert result[0]["char_offset"] == 0

    def test_short_file_content_is_stripped(self):
        content = "  Short content.  "
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert result[0]["content"] == "Short content."

    def test_short_file_heading_is_none(self):
        content = "Short content here."
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert result[0]["heading"] is None

    def test_exactly_max_chars_is_single_chunk(self):
        content = "x" * 1000
        result = chunk_by_headings(content, max_chunk_chars=1000)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 3. File with ## headings -> split at headings
# ---------------------------------------------------------------------------


class TestH2Headings:
    def _make_h2_doc(self):
        # Each section body is ~150 chars; total doc ~510 chars.
        # Use max_chunk_chars=300 so the full doc exceeds it, triggering heading splits.
        section_body = "word " * 30  # ~150 chars each
        return (
            f"## Introduction\n{section_body}\n\n"
            f"## Background\n{section_body}\n\n"
            f"## Conclusion\n{section_body}"
        )

    def test_h2_splits_into_multiple_chunks(self):
        doc = self._make_h2_doc()
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        assert len(result) >= 2

    def test_h2_headings_captured_without_prefix(self):
        doc = self._make_h2_doc()
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result]
        assert "Introduction" in headings
        assert "Background" in headings
        assert "Conclusion" in headings

    def test_h2_heading_has_no_hash_prefix(self):
        doc = self._make_h2_doc()
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        for chunk in result:
            if chunk["heading"] is not None:
                assert not chunk["heading"].startswith("#")


# ---------------------------------------------------------------------------
# 4. File with ### headings -> also split
# ---------------------------------------------------------------------------


class TestH3Headings:
    def _make_h3_doc(self):
        # Each section body ~150 chars; total ~510 chars — use max_chunk_chars=300.
        section_body = "word " * 30  # ~150 chars each
        return (
            f"### Alpha\n{section_body}\n\n"
            f"### Beta\n{section_body}\n\n"
            f"### Gamma\n{section_body}"
        )

    def test_h3_splits_into_multiple_chunks(self):
        doc = self._make_h3_doc()
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        assert len(result) >= 2

    def test_h3_headings_captured_correctly(self):
        doc = self._make_h3_doc()
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result]
        assert "Alpha" in headings
        assert "Beta" in headings
        assert "Gamma" in headings

    def test_mixed_h2_and_h3_both_split(self):
        section_body = "word " * 30  # ~150 chars each; total ~520 chars
        doc = (
            f"## Section One\n{section_body}\n\n"
            f"### Sub-section\n{section_body}\n\n"
            f"## Section Two\n{section_body}"
        )
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result]
        assert "Section One" in headings
        assert "Sub-section" in headings
        assert "Section Two" in headings


# ---------------------------------------------------------------------------
# 5. Large section with no headings -> paragraph fallback
# ---------------------------------------------------------------------------


class TestParagraphFallback:
    def test_no_headings_falls_back_to_paragraphs(self):
        # Build content > max_chunk_chars with paragraph boundaries
        para = "word " * 60  # ~300 chars per paragraph
        content = f"{para}\n\n{para}\n\n{para}\n\n{para}"
        result = chunk_by_headings(content, max_chunk_chars=500, min_chunk_chars=50)
        assert len(result) > 1

    def test_paragraph_fallback_produces_sequential_chunk_indices(self):
        para = "word " * 60
        content = f"{para}\n\n{para}\n\n{para}\n\n{para}"
        result = chunk_by_headings(content, max_chunk_chars=500, min_chunk_chars=50)
        indices = [c["chunk_index"] for c in result]
        assert indices == list(range(len(result)))

    def test_oversized_section_splits_into_sub_chunks(self):
        # A document with one heading but an oversized body
        para = "word " * 60  # ~300 chars per para
        large_body = f"{para}\n\n{para}\n\n{para}\n\n{para}"
        doc = f"## Big Section\n{large_body}\n\n## Small\n{'x' * 200}"
        result = chunk_by_headings(doc, max_chunk_chars=500, min_chunk_chars=50)
        # "Big Section" should generate more than one chunk
        big_chunks = [c for c in result if c["heading"] == "Big Section"]
        assert len(big_chunks) > 1


# ---------------------------------------------------------------------------
# 6. Tiny sections (< min_chunk_chars) -> merged into neighbor
# ---------------------------------------------------------------------------


class TestMergeSmallSections:
    def test_tiny_section_merged_forward(self):
        """A tiny leading section should merge into the next section."""
        small = make_section("Tiny", "hi", char_offset=0)
        large = make_section("Big", "word " * 50, char_offset=10)
        merged = _merge_small_sections([small, large], min_chars=100)
        # Should end up as one section after merge
        assert len(merged) == 1

    def test_merged_section_keeps_next_heading(self):
        """When tiny merges forward, the NEXT section's heading wins."""
        small = make_section("Tiny", "hi", char_offset=0)
        large = make_section("Big", "word " * 50, char_offset=10)
        merged = _merge_small_sections([small, large], min_chars=100)
        assert merged[0]["heading"] == "Big"

    def test_tiny_content_is_included_in_merged_section(self):
        """Tiny section's content should appear in the merged output."""
        small = make_section("Tiny", "unique_tiny_text", char_offset=0)
        large = make_section("Big", "word " * 50, char_offset=30)
        merged = _merge_small_sections([small, large], min_chars=100)
        assert "unique_tiny_text" in merged[0]["content"]

    def test_all_large_sections_pass_through(self):
        """Sections above min_chars should not be merged or dropped."""
        sections = [
            make_section("A", "word " * 30, char_offset=0),
            make_section("B", "word " * 30, char_offset=200),
            make_section("C", "word " * 30, char_offset=400),
        ]
        merged = _merge_small_sections(sections, min_chars=50)
        assert len(merged) == 3

    def test_empty_sections_list_returns_empty(self):
        assert _merge_small_sections([], min_chars=100) == []

    def test_chunk_by_headings_merges_tiny_sections(self):
        """End-to-end: a tiny section between two large ones gets merged."""
        large_body = "word " * 40  # ~200 chars
        tiny_body = "tiny"  # 4 chars — well below min_chunk_chars=100
        doc = (
            f"## Big One\n{large_body}\n\n"
            f"## Tiny\n{tiny_body}\n\n"
            f"## Big Two\n{large_body}"
        )
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=100)
        # Tiny section should have been merged; we expect fewer chunks than headings
        assert len(result) < 3


# ---------------------------------------------------------------------------
# 7. Trailing tiny section -> merged backward
# ---------------------------------------------------------------------------


class TestTrailingTinySection:
    def test_trailing_tiny_merged_backward(self):
        """A tiny trailing section should merge into the preceding large one."""
        large = make_section("Big", "word " * 50, char_offset=0)
        tiny = make_section("Trail", "end", char_offset=300)
        merged = _merge_small_sections([large, tiny], min_chars=100)
        assert len(merged) == 1

    def test_trailing_tiny_content_present_in_last_section(self):
        large = make_section("Big", "word " * 50, char_offset=0)
        tiny = make_section("Trail", "trailing_unique", char_offset=300)
        merged = _merge_small_sections([large, tiny], min_chars=100)
        assert "trailing_unique" in merged[0]["content"]

    def test_trailing_tiny_keeps_large_section_heading(self):
        """When merging backward, the large section's heading is retained."""
        large = make_section("BigHeading", "word " * 50, char_offset=0)
        tiny = make_section("SmallHeading", "end", char_offset=300)
        merged = _merge_small_sections([large, tiny], min_chars=100)
        assert merged[0]["heading"] == "BigHeading"

    def test_only_tiny_section_not_dropped(self):
        """A single tiny section (no neighbor) should still be returned."""
        tiny = make_section("Alone", "small", char_offset=0)
        merged = _merge_small_sections([tiny], min_chars=100)
        assert len(merged) == 1
        assert "small" in merged[0]["content"]


# ---------------------------------------------------------------------------
# 8. get_config_dict returns string values
# ---------------------------------------------------------------------------


class TestGetConfigDict:
    def test_returns_dict(self):
        result = get_config_dict(1000, 100)
        assert isinstance(result, dict)

    def test_max_chunk_chars_is_string(self):
        result = get_config_dict(1000, 100)
        assert isinstance(result["max_chunk_chars"], str)

    def test_min_chunk_chars_is_string(self):
        result = get_config_dict(1000, 100)
        assert isinstance(result["min_chunk_chars"], str)

    def test_values_match_inputs(self):
        result = get_config_dict(2000, 200)
        assert result["max_chunk_chars"] == "2000"
        assert result["min_chunk_chars"] == "200"

    def test_defaults_are_strings(self):
        result = get_config_dict()
        assert result["max_chunk_chars"] == "1000"
        assert result["min_chunk_chars"] == "100"

    def test_contains_expected_keys_only(self):
        result = get_config_dict(500, 50)
        assert set(result.keys()) == {"max_chunk_chars", "min_chunk_chars"}


# ---------------------------------------------------------------------------
# 9. chunk_index is sequential starting at 0
# ---------------------------------------------------------------------------


class TestChunkIndex:
    def test_single_chunk_index_is_zero(self):
        result = chunk_by_headings("short", max_chunk_chars=1000)
        assert result[0]["chunk_index"] == 0

    def test_multiple_chunks_sequential_from_zero(self):
        section_body = "word " * 30
        doc = (
            f"## Alpha\n{section_body}\n\n"
            f"## Beta\n{section_body}\n\n"
            f"## Gamma\n{section_body}"
        )
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        indices = [c["chunk_index"] for c in result]
        assert indices == list(range(len(result)))

    def test_paragraph_fallback_chunk_indices_sequential(self):
        para = "word " * 60
        content = "\n\n".join([para] * 6)
        result = chunk_by_headings(content, max_chunk_chars=400, min_chunk_chars=50)
        indices = [c["chunk_index"] for c in result]
        assert indices == list(range(len(result)))

    def test_no_duplicate_chunk_indices(self):
        section_body = "word " * 30
        doc = "\n\n".join([f"## Section {i}\n{section_body}" for i in range(5)])
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        indices = [c["chunk_index"] for c in result]
        assert len(indices) == len(set(indices))


# ---------------------------------------------------------------------------
# 10. char_offset tracks position in original text
# ---------------------------------------------------------------------------


class TestCharOffset:
    def test_single_chunk_offset_is_zero(self):
        result = chunk_by_headings("short content", max_chunk_chars=1000)
        assert result[0]["char_offset"] == 0

    def test_second_chunk_offset_greater_than_first(self):
        para = "word " * 60
        content = "\n\n".join([para] * 4)
        result = chunk_by_headings(content, max_chunk_chars=500, min_chunk_chars=50)
        if len(result) >= 2:
            assert result[1]["char_offset"] > result[0]["char_offset"]

    def test_offsets_are_non_negative(self):
        section_body = "word " * 30
        doc = f"## First\n{section_body}\n\n## Second\n{section_body}"
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        for chunk in result:
            assert chunk["char_offset"] >= 0

    def test_offsets_are_integers(self):
        section_body = "word " * 30
        doc = f"## First\n{section_body}\n\n## Second\n{section_body}"
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        for chunk in result:
            assert isinstance(chunk["char_offset"], int)


# ---------------------------------------------------------------------------
# 11. Heading text captured correctly (not the ## prefix)
# ---------------------------------------------------------------------------


class TestHeadingCapture:
    def test_h2_heading_has_no_hashes(self):
        # Two sections each ~150 chars body; use a second heading section to exceed
        # max_chunk_chars=300 so heading-based splitting activates.
        body = "word " * 30
        doc = f"## My Heading\n{body}\n\n## Second Heading\n{body}"
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result if c["heading"]]
        assert all("##" not in h for h in headings)

    def test_h3_heading_has_no_hashes(self):
        body = "word " * 30
        doc = f"### Deep Heading\n{body}\n\n### Another\n{body}"
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result if c["heading"]]
        assert all("#" not in h for h in headings)

    def test_heading_text_matches_source(self):
        body = "word " * 30
        doc = f"## Exact Title Text\n{body}\n\n## Another Title\n{body}"
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result if c["heading"]]
        assert "Exact Title Text" in headings
        assert "Another Title" in headings

    def test_heading_whitespace_stripped(self):
        body = "word " * 30
        doc = f"##   Spaced Heading   \n{body}\n\n## Normal\n{body}"
        result = chunk_by_headings(doc, max_chunk_chars=300, min_chunk_chars=50)
        headings = [c["heading"] for c in result if c["heading"]]
        assert "Spaced Heading" in headings

    def test_h1_heading_does_not_split(self):
        """# (h1) headings should NOT trigger a split — only ## and ###."""
        body = "word " * 30
        doc = f"# Title\n{body}\n\n# Another Title\n{body}"
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        # h1 headings do not split; falls through to paragraph fallback or single chunk
        for chunk in result:
            assert chunk["heading"] is None or not chunk["heading"].startswith("#")


# ---------------------------------------------------------------------------
# Additional: output dict schema validation
# ---------------------------------------------------------------------------


class TestOutputSchema:
    REQUIRED_KEYS = {"chunk_index", "heading", "content", "char_offset"}

    def test_all_chunks_have_required_keys(self):
        section_body = "word " * 30
        doc = f"## A\n{section_body}\n\n## B\n{section_body}"
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        for chunk in result:
            assert self.REQUIRED_KEYS.issubset(chunk.keys()), (
                f"Missing keys: {self.REQUIRED_KEYS - chunk.keys()}"
            )

    def test_short_file_chunk_has_required_keys(self):
        result = chunk_by_headings("short text", max_chunk_chars=1000)
        assert self.REQUIRED_KEYS.issubset(result[0].keys())

    def test_content_is_non_empty_string(self):
        section_body = "word " * 30
        doc = f"## A\n{section_body}\n\n## B\n{section_body}"
        result = chunk_by_headings(doc, max_chunk_chars=2000, min_chunk_chars=50)
        for chunk in result:
            assert isinstance(chunk["content"], str)
            assert len(chunk["content"]) > 0
