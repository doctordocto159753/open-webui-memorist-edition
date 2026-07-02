from __future__ import annotations

import re

LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|[0-9۰-۹٠-٩]+[.)]|[A-Za-z][.)])\s+")
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
HEADING_LABEL_RE = re.compile(
    r"^\s*(?:[A-Z][A-Za-z0-9 _/-]{1,60}|[\u0600-\u06ff0-9 _/-]{2,60})\s*[:：]\s*$"
)
QUOTE_LINE_RE = re.compile(r"^\s*(?:>|[\"'“”«])")
FENCE_RE = re.compile(r"^\s*```")
URL_RE = re.compile(r"https?://|www\.")

SENTENCE_PUNCTUATION = {".", "!", "?", "؟", "۔", "؛"}
ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "e.g",
    "i.e",
    "etc",
    "vs",
    "fig",
    "no",
}

CONJOINING_HEADING_MAX_CHARS = 120
