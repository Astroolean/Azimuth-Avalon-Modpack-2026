"""Read mission XML with comment-only compatibility for decorative separators."""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET


def _comment_separators(data: bytes) -> bytes:
    """Normalize illegal hyphen runs only inside complete XML comments.

    This is an in-memory copy. Byte count and line positions stay unchanged.
    CDATA, processing instructions, quoted attributes and actual XML markup
    are never repaired. Any remaining error must pass through the strict parser.
    """
    edits: list[tuple[int, int, bytes]] = []
    cursor = 0
    while cursor < len(data):
        opening = data.find(b"<", cursor)
        if opening < 0:
            break
        if data.startswith(b"<!--", opening):
            start = opening + 4
            end = data.find(b"-->", start)
            if end < 0:
                break
            body = data[start:end]
            # A nested opener is a structural mistake, not a separator.
            if b"<!--" in body:
                return data
            if b"--" in body or body.endswith(b"-"):
                fixed = re.sub(rb"-{2,}", lambda match: b"=" * len(match[0]), body)
                if fixed.endswith(b"-"):
                    fixed = fixed[:-1] + b" "
                edits.append((start, end, fixed))
            cursor = end + 3
        elif data.startswith(b"<![CDATA[", opening):
            end = data.find(b"]]>", opening + 9)
            if end < 0:
                break
            cursor = end + 3
        elif data.startswith(b"<?", opening):
            end = data.find(b"?>", opening + 2)
            if end < 0:
                break
            cursor = end + 2
        elif data.startswith(b"<!", opening):
            # Economy readers reject DTD/entity declarations separately.
            return data
        else:
            cursor = opening + 1
            quote = None
            while cursor < len(data):
                value = data[cursor]
                if quote is not None:
                    if value == quote:
                        quote = None
                elif value in (34, 39):
                    quote = value
                elif value == 62:
                    cursor += 1
                    break
                cursor += 1
    if not edits:
        return data
    result = bytearray(data)
    for start, end, replacement in edits:
        result[start:end] = replacement
    return bytes(result)


def parse_mission_xml(data: bytes, label: str) -> ET.Element:
    """Parse strictly, tolerating only malformed non-gameplay comment hyphens."""
    try:
        return ET.fromstring(data)
    except ET.ParseError as original:
        compatible = _comment_separators(data)
        if compatible != data:
            try:
                return ET.fromstring(compatible)
            except ET.ParseError as remaining:
                raise ValueError(f"Could not read mission economy XML {label}: {remaining}") from remaining
        raise ValueError(f"Could not read mission economy XML {label}: {original}") from original
