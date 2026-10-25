"""
Splits a Word document into clauses using its heading styles (Heading1..Heading6). Word's automatic numbering is not
stored in the text, so numbers are computed from the heading levels the way Word numbers them (1, 1.1, 1.1.1, ...).
Every heading counts, including the "Contents" heading when its style is numbered (the Georgian guides number it as
chapter 1); only a heading that switches numbering off (numId 0) is unnumbered. Verified against the documents' own
published PDF ("1 სარჩევი", 7.6.1 PartyIdentification, 8.2.3, 8.3.2).
"""

import zipfile
from xml.etree import ElementTree

from .clause import Clause

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _style(p) -> str | None:
    style = p.find(f"{_W}pPr/{_W}pStyle")
    return style.get(f"{_W}val") if style is not None else None


def _heading_level(p) -> int | None:
    style = _style(p)
    if style and style.lower().startswith("heading") and style[7:].isdigit() and 1 <= int(style[7:]) <= 6:
        return int(style[7:])
    return None


def _numbering_off(p) -> bool:
    num_id = p.find(f"{_W}pPr/{_W}numPr/{_W}numId")
    return num_id is not None and num_id.get(f"{_W}val") == "0"


def _is_toc(p) -> bool:
    style = _style(p)
    return bool(style) and style.upper().startswith("TOC")


def read(path: str, standard: str, version: str, language: str) -> list[Clause]:
    with zipfile.ZipFile(path) as z:
        try:
            xml = z.read("word/document.xml")
        except KeyError as ex:
            raise ValueError(f"{path}: not a Word document (no word/document.xml)") from ex
    body = ElementTree.fromstring(xml).find(f"{_W}body")

    clauses: list[Clause] = []
    counters = [0] * 7
    number = title = None
    text: list[str] = []

    def flush():
        if number is not None:
            clauses.append(Clause(standard, version, number, title, "".join(text).strip(), language, order=len(clauses)))
        text.clear()

    for p in body.iter(f"{_W}p"):
        content = "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()
        if not content:
            continue

        level = _heading_level(p)
        if level is not None and not _numbering_off(p):
            flush()
            counters[level] += 1
            for deeper in range(level + 1, len(counters)):
                counters[deeper] = 0
            # A heading that skips a level (Heading3 directly under Heading1) still gets a full number.
            for higher in range(1, level):
                counters[higher] = max(counters[higher], 1)
            number = ".".join(str(c) for c in counters[1:level + 1])
            title = content
            continue

        if number is not None and not _is_toc(p):
            text.append(content + "\n\n")

    flush()
    return clauses
