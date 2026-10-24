"""
Decides whether a quoted passage really occurs in a clause. This is what stops a drafted rule from citing words the
standard does not contain. Comparison ignores what text extraction and copying change — whitespace and line breaks,
hyphenation at line ends, typographic quotes and dashes, case — and nothing else. An ellipsis ("..." or "…") in the
quote splits it into parts that must all occur, in order.
"""

import re
import unicodedata

# Minimum characters (after normalisation) of each quoted part, so "the" or "MUST" cannot pass as a quote.
MIN_PART_LENGTH = 12

_QUOTES = dict.fromkeys(map(ord, "“”„«»‘’‚`'"), '"')
_DASHES = dict.fromkeys(map(ord, "–—−‐‑"), "-")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")
_HYPHEN_BREAK = re.compile(r"([^\W\d_])-\s*\n\s*([^\W\d_])")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).translate(_DASHES).replace("­", "").lower()
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # "unambi-\ngously" -> "unambigously"
    return _WHITESPACE.sub(" ", text).strip()


def check(quote: str, clause_text: str) -> str | None:
    """None when the quote occurs; otherwise the reason it does not."""
    text = normalize(clause_text)
    parts = [p for p in (normalize(x) for x in _ELLIPSIS.split(quote)) if p]
    if not parts:
        return "The quote is empty."

    start = 0
    for part in parts:
        if len(part) < MIN_PART_LENGTH:
            return f'The quoted part "{part}" is too short to identify a passage.'
        at = text.find(part, start)
        if at < 0:
            shown = part if len(part) <= 80 else part[:77] + "..."
            return f'"{shown}" does not occur in the clause{" after the previous quoted part" if start > 0 else ""}.'
        start = at + len(part)
    return None


def occurs(quote: str, clause_text: str) -> bool:
    return check(quote, clause_text) is None
