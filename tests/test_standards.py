"""Standards: PDF (TOC-driven) and Word readers, the quote verifier, the sources manifest."""

import os
import zipfile
from xml.sax.saxutils import escape

import pytest

from aqt.standards import StandardSources, check_quote, quote_occurs
from aqt.standards import docx_reader, pdf_reader
from conftest import REPO_ROOT

FURNITURE = [r"^NextGenPSD2 XS2A Framework", r"^Published by the Berlin Group", r"^\(ref\. License Notice"]


def pdf():
    return [
        # TOC page (>= 3 dotted leaders): a long title wrapped over two lines.
        (2, "Contents"),
        (2, "1 Introduction ............................................ 1"),
        (2, "6 Account Information Service ......................... 125"),
        (2, "6.3 Establish Account Information Consent Request ........ 140"),
        (2, "6.3.1 Account Information Consent Request ................. 142"),
        (2, "6.3.2 Get Status Request on Account Information Consent and a very long"),
        (2, "title wrapped .............................................. 150"),
        # Body: running header first, footer last (pdfplumber order).
        (3, "NextGenPSD2 XS2A Framework – Implementation Guidelines Introduction"),
        (3, "1 Introduction"),
        (3, "This document defines the interface."),
        (3, "See 6.3.1 for consents."),                      # a reference, not a heading
        (3, "Published by the Berlin Group under Creative Commons Attribution-NoDerivatives 4.0 Page 1"),
        (3, "(ref. License Notice for full license conditions)"),
        (4, "NextGenPSD2 XS2A Framework – Implementation Guidelines Account Information Service"),
        (4, "6 Account Information Service"),
        (4, "6.3 Establish Account Information Consent Request"),
        (4, "Intro text of 6.3."),
        (4, "6.3.1 Account Information Consent Request"),
        (4, "General text."),
        (4, "6.3.1.1 Consent Request on Dedicated Accounts"),
        (4, "The ASPSP shall reject invalid requests."),
        (4, "6.3.1.3 Not the next number so not a heading"),  # skips .2: stays text
        (4, "6.3.1.2 Consent Request on Account List"),
        (4, "If this function is supported, ..."),
        (4, "(ref. License Notice for full license conditions)"),
        (5, "6.3.2 Get Status Request on Account Information Consent and a very long"),
        (5, "title wrapped"),
        (5, "Status text."),
    ]


def test_parses_the_toc_including_wrapped_entries():
    toc = pdf_reader.parse_toc(line for page, line in pdf() if page == 2)
    assert [t.number for t in toc] == ["1", "6", "6.3", "6.3.1", "6.3.2"]
    assert (toc[4].title, toc[4].printed_page) == ("Get Status Request on Account Information Consent and a very long title wrapped", 150)


def test_finds_headings_in_order_drops_page_furniture_and_splits_deeper_sections():
    r = pdf_reader.read_lines(pdf(), "BG-IG", "1.3.16", "en", FURNITURE)
    assert r.problems == []
    assert [c.number for c in r.clauses] == ["1", "6", "6.3", "6.3.1", "6.3.1.1", "6.3.1.2", "6.3.2"]
    assert r.clauses[0].text == "This document defines the interface.\nSee 6.3.1 for consents."
    assert r.clauses[3].text == "General text."
    assert "6.3.1.3 Not the next number" in r.clauses[4].text   # only the expected next number is a heading
    assert r.clauses[6].text == "Status text."                     # wrapped heading line skipped
    assert not any("License Notice" in c.text or "Published by" in c.text for c in r.clauses)
    assert (r.clauses[4].parent, r.clauses[4].level, r.clauses[5].key) == ("6.3.1", 4, "BG-IG:1.3.16:6.3.1.2")


def test_reports_toc_entries_it_cannot_find():
    lines = [x for x in pdf() if x[1] != "6.3 Establish Account Information Consent Request"]
    assert any('6.3 "Establish' in p for p in pdf_reader.read_lines(lines, "BG-IG", "1.3.16", "en", FURNITURE).problems)


def test_lines_repeated_at_the_top_of_many_pages_are_running_headers():
    lines = [(p, line) for p in range(10, 16) for line in ("STRAY RUNNING SENTENCE", f"body {p}")]
    assert [line for _, line in pdf_reader.strip_page_furniture(lines, [])] == [f"body {p}" for p in range(10, 16)]


def _docx(tmp_path, paragraphs):
    """A minimal .docx: (style, text); a style ending in "!" switches numbering off (numId 0)."""
    body = []
    for style, text in paragraphs:
        off, name = style.endswith("!"), style.rstrip("!")
        ppr = f'<w:pPr><w:pStyle w:val="{name}"/>{"<w:numPr><w:numId w:val=\"0\"/></w:numPr>" if off else ""}</w:pPr>' if name else ""
        body.append(f"<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>")
    path = tmp_path / "guide.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   + "".join(body) + "</w:body></w:document>")
    return str(path)


def test_word_numbering_counts_the_contents_heading_like_word(tmp_path):
    path = _docx(tmp_path, [("Title", "Guide"), ("Heading1", "სარჩევი"), ("TOC1", "1 სარჩევი ....... 1"),
                            ("Heading1", "დოკუმენტის შესახებ"), ("", "პირველი აბზაცი."), ("Heading2", "ქვეთავი"),
                            ("", "აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა."), ("Heading3!", "Unnumbered heading"),
                            ("Heading1", "მესამე თავი"), ("Heading3", "Skipped a level")])
    clauses = docx_reader.read(path, "GE-XS2A", "0.8.RC3", "ka")
    assert [c.number for c in clauses] == ["1", "2", "2.1", "3", "3.1.1"]
    assert clauses[0].text == "" and clauses[1].text == "პირველი აბზაცი."
    assert "Unnumbered heading" in clauses[2].text


CLAUSE = """The ASPSP shall reject the request with "FORMAT_ERROR" if the validUntil date is in the past. This is not unambi-
gously required by [EBA-RTS]. It is up to the ASPSP to implement the appropriate
requirements on customer authentication."""


@pytest.mark.parametrize("quote", [
    "The ASPSP shall reject the request with “FORMAT_ERROR” if the validUntil date is in the past.",  # typographic quotes
    "the aspsp shall reject the request",                                                              # case
    "This is not unambigously required by [EBA-RTS].",                                                 # hyphenation (the PDF's spelling)
    "It is up to the ASPSP to implement the appropriate requirements on customer authentication.",    # line breaks
    "The ASPSP shall reject the request ... if the validUntil date is in the past.",                  # ellipsis
])
def test_quote_verifier_accepts_what_extraction_and_copying_change(quote):
    assert check_quote(quote, CLAUSE) is None


@pytest.mark.parametrize("quote,reason", [
    ("The ASPSP must reject the request", "does not occur"),
    ("This is not unambiguously required by [EBA-RTS].", "does not occur"),  # a "corrected" typo is not verbatim
    ("if the validUntil date is in the past ... The ASPSP shall reject", "after the previous"),
    ("shall", "too short"),
    ("", "empty"),
])
def test_quote_verifier_rejects_words_the_clause_does_not_contain(quote, reason):
    assert reason in check_quote(quote, CLAUSE)


def test_quote_verifier_georgian():
    clause = "იმ შემთხვევაში, თუ აღნიშნული ველის მნიშვნელობა აღემატება მოთხოვნიდან 90 დღეს, აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID."
    assert quote_occurs("აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID", clause)
    assert not quote_occurs("აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა FORMAT_ERROR", clause)


def test_manifest_loads_and_refuses_a_file_whose_hash_changed(tmp_path):
    (tmp_path / "sources.yaml").write_text("""
- id: GE-TEST
  title: Test guide
  version: "1.0"
  language: ka
  format: docx
  url: https://example.invalid/test.docx
  file: ge-test/1.0/test.docx
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
  titles:
    "2": Fixed title
""", encoding="utf-8")
    sources = StandardSources(str(tmp_path))
    source = sources.load()[0]
    assert (source.id, source.titles["2"]) == ("GE-TEST", "Fixed title")
    os.makedirs(os.path.dirname(sources.path_of(source)))
    with open(sources.path_of(source), "w") as f:
        f.write("not the published file")
    with pytest.raises(ValueError, match="does not match the manifest"):
        sources.read(source)


def test_the_shipped_manifest_is_valid():
    sources = StandardSources(os.path.join(REPO_ROOT, "standards")).load()
    assert any(s.id == "BG-IG" and s.version == "1.3.16" for s in sources)
    for s in sources:
        assert len(s.sha256) == 64 and s.format in ("pdf", "docx") and s.language in ("en", "ka")
