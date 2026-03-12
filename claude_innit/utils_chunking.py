"""Text chunking utilities for vault file embeddings."""

import re
from typing import Optional

# Default chunking parameters — stored in chunk_config for versioning
DEFAULT_MAX_CHUNK_CHARS = 1000
DEFAULT_MIN_CHUNK_CHARS = 100


def chunk_by_headings(
    content: str,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS,
) -> list[dict]:
    """Split markdown content into chunks at ## headings.

    Strategy:
    1. Split at ## (h2) and ### (h3) headings
    2. If a section is still > max_chunk_chars, split at paragraph breaks
    3. Tiny sections (< min_chunk_chars) get merged into the next section
    4. Files < max_chunk_chars stay as a single chunk
    5. No heading-based splits found -> fall back to paragraph splitting
    6. Code blocks are embedded as-is (not special-cased)

    Returns list of dicts:
        [{"chunk_index": 0, "heading": "Introduction",
          "content": "...", "char_offset": 0}, ...]
    """
    if not content or not content.strip():
        return []

    # Short files: single chunk
    if len(content) <= max_chunk_chars:
        return [
            {
                "chunk_index": 0,
                "heading": None,
                "content": content.strip(),
                "char_offset": 0,
            }
        ]

    # Split at ## or ### headings (not # which is typically the title)
    heading_pattern = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)

    sections = []
    last_end = 0
    last_heading = None

    for match in heading_pattern.finditer(content):
        text_before = content[last_end : match.start()].strip()
        if text_before:
            sections.append(
                {
                    "heading": last_heading,
                    "content": text_before,
                    "char_offset": last_end,
                }
            )
        last_heading = match.group(2).strip()
        last_end = match.end()

    # Capture text after the last heading
    remaining = content[last_end:].strip()
    if remaining:
        sections.append(
            {
                "heading": last_heading,
                "content": remaining,
                "char_offset": last_end,
            }
        )

    # No headings found -> fall back to paragraph splitting
    if len(sections) <= 1:
        return _split_by_paragraphs(content, max_chunk_chars)

    # Merge tiny sections into their neighbor
    merged = _merge_small_sections(sections, min_chunk_chars)

    # Split oversized sections at paragraph breaks
    final = []
    for section in merged:
        if len(section["content"]) > max_chunk_chars:
            sub_chunks = _split_by_paragraphs(
                section["content"],
                max_chunk_chars,
                base_heading=section["heading"],
            )
            for sc in sub_chunks:
                sc["char_offset"] += section["char_offset"]
            final.extend(sub_chunks)
        else:
            final.append(section)

    # Assign chunk indices
    for i, chunk in enumerate(final):
        chunk["chunk_index"] = i

    return final


def _split_by_paragraphs(
    text: str,
    max_chunk_chars: int,
    base_heading: Optional[str] = None,
) -> list[dict]:
    """Split text at double-newline paragraph boundaries.

    Tracks char_offset as cumulative character position in the
    original text (accounting for paragraph separators).
    """
    paragraphs = re.split(r"\n\n+", text.strip())
    chunks = []
    current = []
    current_len = 0
    # Track position in the original text
    running_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > max_chunk_chars and current:
            chunks.append(
                {
                    "heading": base_heading,
                    "content": "\n\n".join(current),
                    "char_offset": running_offset,
                }
            )
            running_offset += sum(len(p) for p in current) + 2 * (len(current) - 1)
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        chunks.append(
            {
                "heading": base_heading,
                "content": "\n\n".join(current),
                "char_offset": running_offset,
            }
        )

    for i, chunk in enumerate(chunks):
        chunk["chunk_index"] = i

    return chunks


def _merge_small_sections(sections: list[dict], min_chars: int) -> list[dict]:
    """Merge sections smaller than min_chars into their neighbor.

    When merging forward (small section into next), keeps the NEXT
    section's heading — the larger section is the semantically
    meaningful one. When merging a trailing buffer backward into the
    last section, keeps the last section's heading.
    """
    if not sections:
        return []

    merged = []
    buffer = None

    for section in sections:
        if buffer is not None:
            # Merge buffer into this section — keep THIS section's heading
            section = {
                "heading": section["heading"],
                "content": buffer["content"] + "\n\n" + section["content"],
                "char_offset": buffer["char_offset"],
            }
            buffer = None

        if len(section["content"]) < min_chars:
            buffer = section
        else:
            merged.append(section)

    # Trailing buffer: merge backward into last section
    if buffer is not None:
        if merged:
            merged[-1]["content"] += "\n\n" + buffer["content"]
        else:
            merged.append(buffer)

    return merged


def get_config_dict(
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS,
) -> dict:
    """Return a config dict suitable for chunk_config storage."""
    return {
        "max_chunk_chars": str(max_chunk_chars),
        "min_chunk_chars": str(min_chunk_chars),
    }
