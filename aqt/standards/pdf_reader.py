"""
Splits a PDF standard into clauses using its table of contents as the list of sections: every TOC entry
("6.3.1.2 Consent Request on Account List ....... 144") is then located, in order, as a heading line in the body.
Using the TOC avoids taking numbered list items or references ("cp. 6.3.3") for headings. Sections deeper than the TOC
(6.3.1.2 when the TOC stops at 6.3.1) are found inside their parent, accepting only the expected next number.
"""

import re
from dataclasses import dataclass, field

from .clause import Clause

_TOC_START = re.compile(r"^(?P<n>\d+(?:\.\d+)*)\s+")
_TOC_LEADER = re.compile(r"\s*\.{4,}\s*(?P<p>\d+)\s*$")
_WS = re.compile(r"\s+")


@dataclass
class TocEntry:
    number: str
    title: str
    printed_page: int


@dataclass
class ReadResult:
    clauses: list[Clause]
    toc: list[TocEntry]
    problems: list[str] = field(default_factory=list)


def pdf_lines(path: str) -> list[tuple[int, str]]:
    """Text lines of a PDF, page by page. Compared with the C# tool's PdfPig text on BG-IG 1.3.16: same 182 clauses in the
    same order; prose is the same, tables and figures differ in places (median similarity 0.97), and every quote of the
    rules drafted so far still verifies."""
    import pdfplumber  # optional dependency (the `ai` extra)

    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # Rotated labels inside figures come out as single letters ("d m o d e l"); keep upright text only.
            upright = page.filter(lambda obj: obj.get("object_type") != "char" or obj.get("upright", True))
            text = upright.extract_text() or ""
            lines.extend((page.page_number, line.strip()) for line in text.split("\n") if line.strip())
    return lines


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def parse_toc(toc_lines) -> list[TocEntry]:
    entries, pending = [], None
    for line in toc_lines:
        if _TOC_START.match(line):
            pending = line
        elif pending is not None:
            pending += " " + line  # wrapped title or the leader line of a long title
        else:
            continue

        m = _TOC_LEADER.search(pending)
        if not m:
            continue
        start = _TOC_START.match(pending)
        entries.append(TocEntry(start.group("n"), _norm(pending[start.end():m.start()]), int(m.group("p"))))
        pending = None
    return entries


# A line in the first lines of at least this many pages, identical each time, is a running header (e.g. the stray
# sentence glued into the BG-IG chapter 6 running head).
_REPEATED_HEADER_PAGES = 5
_TOP_LINES = 4


def strip_page_furniture(lines, patterns: list[str]):
    """Drops running headers and footers wherever the extractor puts them on the page: lines matching the manifest's
    patterns, and lines repeated verbatim at the top of many pages. (PdfPig returned them first on the page,
    pdfplumber returns the footer last, so position cannot be relied on.)"""
    compiled = [re.compile(p) for p in patterns]
    by_page: dict[int, list[str]] = {}
    for page, line in lines:
        by_page.setdefault(page, []).append(line)

    counts: dict[str, int] = {}
    for page_lines in by_page.values():
        for line in set(page_lines[:_TOP_LINES]):
            counts[line] = counts.get(line, 0) + 1
    repeated = {line for line, n in counts.items() if n >= _REPEATED_HEADER_PAGES}

    result = []
    for page, page_lines in by_page.items():
        for i, line in enumerate(page_lines):
            if any(p.search(line) for p in compiled) or (i < _TOP_LINES and line in repeated):
                continue
            result.append((page, line))
    return result


def _find_heading(body, entry: TocEntry, start: int) -> int:
    prefix = entry.number + " "
    probe = _norm(entry.title)[:20]
    for i in range(start, len(body)):
        line = body[i][1]
        if line.startswith(prefix) and _norm(line[len(prefix):]).startswith(probe):
            return i
    return -1


def read_lines(lines: list[tuple[int, str]], standard: str, version: str, language: str, furniture: list[str] | None = None) -> ReadResult:
    problems = []
    pages: dict[int, list[str]] = {}
    for page, line in lines:
        pages.setdefault(page, []).append(line)
    toc_pages = {p for p, ls in pages.items() if sum(bool(_TOC_LEADER.search(l)) for l in ls) >= 3}
    toc = parse_toc(line for page, line in lines if page in toc_pages)

    last_toc = max(toc_pages) if toc_pages else 0
    body = strip_page_furniture([(p, l) for p, l in lines if p not in toc_pages and p > last_toc], furniture or [])

    headings, start = [], 0
    for entry in toc:
        index = _find_heading(body, entry, start)
        if index < 0:
            problems.append(f'Heading {entry.number} "{entry.title}" (p. {entry.printed_page}) not found in the body')
            continue
        headings.append((entry, index))
        start = index + 1

    clauses = []
    for i, (entry, begin) in enumerate(headings):
        end = headings[i + 1][1] if i + 1 < len(headings) else len(body)
        # A long heading can wrap: skip continuation lines that are still part of the title.
        text_start, heading_text = begin + 1, body[begin][1]
        full = _norm(f"{entry.number} {entry.title}")
        while (text_start < end and len(_norm(heading_text)) < len(full)
               and full.startswith(_norm(heading_text + " " + body[text_start][1]))):
            heading_text += " " + body[text_start][1]
            text_start += 1
        clauses.append(Clause(standard, version, entry.number, entry.title,
                              "\n".join(l for _, l in body[text_start:end]).strip(), language,
                              order=len(clauses), page=entry.printed_page))

    return ReadResult(split_subsections(clauses), toc, problems)


def read(path: str, standard: str, version: str, language: str, furniture: list[str] | None = None) -> ReadResult:
    return read_lines(pdf_lines(path), standard, version, language, furniture)


def split_subsections(clauses: list[Clause]) -> list[Clause]:
    result: list[Clause] = []
    for clause in clauses:
        _split(clause, result)
    return [c.with_(order=i) for i, c in enumerate(result)]


def _split(clause: Clause, output: list[Clause]):
    lines = clause.text.split("\n")
    cuts, expected = [], 1
    for i, line in enumerate(lines):
        number = f"{clause.number}.{expected}"
        rest = line[len(number) + 1:]
        if len(line) < 120 and line.startswith(number + " ") and rest[:1].isupper():
            cuts.append((i, number, rest.strip()))
            expected += 1

    if not cuts:
        output.append(clause)
        return

    output.append(clause.with_(text="\n".join(lines[:cuts[0][0]]).strip()))
    for k, (line_index, number, title) in enumerate(cuts):
        end = cuts[k + 1][0] if k + 1 < len(cuts) else len(lines)
        _split(Clause(clause.standard, clause.version, number, title, "\n".join(lines[line_index + 1:end]).strip(),
                      clause.language, page=clause.page), output)
