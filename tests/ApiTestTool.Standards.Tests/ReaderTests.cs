using System.IO.Compression;
using System.Text;

namespace ApiTestTool.Standards.Tests;

public class PdfClauseReaderTests
{
    private const string Header = "(ref. License Notice for full license conditions)";

    private static List<(int, string)> Pdf() =>
    [
        // TOC pages (≥3 dotted leaders): wrapped entries, a long title on two lines.
        (2, "Contents"),
        (2, "1 Introduction ............................................ 1"),
        (2, "6 Account Information Service ......................... 125"),
        (2, "6.3 Establish Account Information Consent Request ........ 140"),
        (2, "6.3.1 Account Information Consent Request ................. 142"),
        (2, "6.3.2 Get Status Request on Account Information Consent and a very long"),
        (2, "title wrapped .............................................. 150"),
        // body
        (3, "NextGenPSD2 XS2A Framework – Implementation Guidelines  Introduction"),
        (3, "Published by the Berlin Group ... Page 1"),
        (3, Header),
        (3, "1 Introduction"),
        (3, "This document defines the interface."),
        (3, "See 6.3.1 for consents."),                      // a reference, not a heading
        (4, "NextGenPSD2 XS2A Framework – Implementation Guidelines  Account Information Service"),
        (4, Header),
        (4, "6 Account Information Service"),
        (4, "6.3 Establish Account Information Consent Request"),
        (4, "Intro text of 6.3."),
        (4, "6.3.1 Account Information Consent Request"),
        (4, "General text."),
        (4, "6.3.1.1 Consent Request on Dedicated Accounts"),
        (4, "The ASPSP shall reject invalid requests."),
        (4, "6.3.1.3 Not the next number so not a heading"),  // skips .2: stays text
        (4, "6.3.1.2 Consent Request on Account List"),
        (4, "If this function is supported, ..."),
        (5, Header),
        (5, "6.3.2 Get Status Request on Account Information Consent and a very long"),
        (5, "title wrapped"),
        (5, "Status text."),
    ];

    [Fact]
    public void Parses_the_toc_including_wrapped_entries()
    {
        var toc = PdfClauseReader.ParseToc(Pdf().Where(l => l.Item1 == 2).Select(l => l.Item2));

        Assert.Equal(["1", "6", "6.3", "6.3.1", "6.3.2"], toc.Select(t => t.Number));
        Assert.Equal(("Get Status Request on Account Information Consent and a very long title wrapped", 150), (toc[4].Title, toc[4].PrintedPage));
    }

    [Fact]
    public void Finds_headings_in_order_drops_page_headers_and_splits_deeper_sections()
    {
        var r = PdfClauseReader.Read(Pdf(), "BG-IG", "1.3.16", "en", "(ref. License Notice");

        Assert.Empty(r.Problems);
        Assert.Equal(["1", "6", "6.3", "6.3.1", "6.3.1.1", "6.3.1.2", "6.3.2"], r.Clauses.Select(c => c.Number));
        Assert.Equal("This document defines the interface.\nSee 6.3.1 for consents.", r.Clauses[0].Text);
        Assert.Equal("General text.", r.Clauses[3].Text);
        Assert.Contains("6.3.1.3 Not the next number", r.Clauses[4].Text); // only the expected next number is a heading
        Assert.Equal("Status text.", r.Clauses[6].Text);                   // wrapped heading line skipped
        Assert.DoesNotContain(r.Clauses, c => c.Text.Contains("License Notice", StringComparison.Ordinal));
        Assert.Equal(("6.3.1", 4), (r.Clauses[4].Parent, r.Clauses[4].Level));
        Assert.Equal("BG-IG:1.3.16:6.3.1.2", r.Clauses[5].Key);
    }

    [Fact]
    public void Reports_toc_entries_it_cannot_find()
    {
        var lines = Pdf().Where(l => l.Item2 != "6.3 Establish Account Information Consent Request").ToList();

        var r = PdfClauseReader.Read(lines, "BG-IG", "1.3.16", "en", "(ref. License Notice");

        Assert.Contains(r.Problems, p => p.Contains("6.3 \"Establish", StringComparison.Ordinal));
    }
}

public class DocxClauseReaderTests
{
    [Fact]
    public void Numbers_headings_like_word_including_a_numbered_contents_heading()
    {
        var path = Docx(
            ("Title", "Guide title"),
            ("Heading1", "სარჩევი"),
            ("TOC1", "1 სარჩევი ....... 1"),
            ("Heading1", "დოკუმენტის შესახებ"),
            ("", "პირველი აბზაცი."),
            ("Heading2", "ქვეთავი"),
            ("", "აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა."),
            ("Heading3!", "Unnumbered heading"),           // numbering switched off: stays text of 2.1
            ("Heading1", "მესამე თავი"),
            ("Heading3", "Skipped a level"));

        try
        {
            var clauses = DocxClauseReader.Read(path, "GE-XS2A", "0.8.RC3", "ka");

            Assert.Equal(["1", "2", "2.1", "3", "3.1.1"], clauses.Select(c => c.Number));
            Assert.Equal("", clauses[0].Text);                 // TOC lines are not clause text
            Assert.Equal("პირველი აბზაცი.", clauses[1].Text);
            Assert.Contains("Unnumbered heading", clauses[2].Text);
            Assert.All(clauses, c => Assert.Equal("ka", c.Language));
        }
        finally
        {
            File.Delete(path);
        }
    }

    /// <summary>A minimal .docx: paragraphs with a style; a style ending in "!" gets numbering switched off (numId 0).</summary>
    private static string Docx(params (string Style, string Text)[] paragraphs)
    {
        var body = new StringBuilder();
        foreach (var (style, text) in paragraphs)
        {
            var off = style.EndsWith('!');
            var name = style.TrimEnd('!');
            var pPr = name.Length == 0 ? "" : $"<w:pPr><w:pStyle w:val=\"{name}\"/>{(off ? "<w:numPr><w:numId w:val=\"0\"/></w:numPr>" : "")}</w:pPr>";
            body.Append($"<w:p>{pPr}<w:r><w:t>{System.Security.SecurityElement.Escape(text)}</w:t></w:r></w:p>");
        }

        var path = Path.Combine(Path.GetTempPath(), $"aqt-{Guid.NewGuid():N}.docx");
        using var zip = ZipFile.Open(path, ZipArchiveMode.Create);
        using (var writer = new StreamWriter(zip.CreateEntry("word/document.xml").Open()))
            writer.Write($"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>{body}</w:body></w:document>");
        return path;
    }
}

public class QuoteVerifierTests
{
    private const string Clause = """
        The ASPSP shall reject the request with "FORMAT_ERROR" if the validUntil date is in the past. This is not unambi-
        gously required by [EBA-RTS]. It is up to the ASPSP to implement the appropriate
        requirements on customer authentication.
        """;

    [Theory]
    [InlineData("The ASPSP shall reject the request with “FORMAT_ERROR” if the validUntil date is in the past.")] // typographic quotes
    [InlineData("the aspsp shall reject the request")]                                                           // case
    [InlineData("This is not unambigously required by [EBA-RTS].")]                                              // hyphenation at a line break (the PDF's own spelling)
    [InlineData("It is up to the ASPSP to implement the appropriate requirements on customer authentication.")] // line breaks
    [InlineData("The ASPSP shall reject the request ... if the validUntil date is in the past.")]               // ellipsis
    public void Accepts_what_extraction_and_copying_change(string quote) =>
        Assert.Null(QuoteVerifier.Check(quote, Clause));

    [Theory]
    [InlineData("The ASPSP must reject the request", "does not occur")]                          // one word changed
    [InlineData("This is not unambiguously required by [EBA-RTS].", "does not occur")]          // a "corrected" typo is not verbatim
    [InlineData("if the validUntil date is in the past ... The ASPSP shall reject", "after the previous")] // wrong order
    [InlineData("shall", "too short")]
    [InlineData("", "empty")]
    public void Rejects_words_the_clause_does_not_contain(string quote, string reason) =>
        Assert.Contains(reason, QuoteVerifier.Check(quote, Clause));

    [Fact]
    public void Works_for_georgian_text()
    {
        const string clause = "იმ შემთხვევაში, თუ აღნიშნული ველის მნიშვნელობა აღემატება მოთხოვნიდან 90 დღეს, აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID.";

        Assert.True(QuoteVerifier.Occurs("აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID", clause));
        Assert.False(QuoteVerifier.Occurs("აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა FORMAT_ERROR", clause));
    }
}

public class StandardSourcesTests
{
    [Fact]
    public void Loads_the_manifest_and_refuses_a_file_whose_hash_changed()
    {
        var dir = Directory.CreateTempSubdirectory("aqt-std-").FullName;
        try
        {
            File.WriteAllText(Path.Combine(dir, "sources.yaml"), """
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
                """);
            var sources = new StandardSources(dir);
            var source = Assert.Single(sources.Load());
            Assert.Equal(("GE-TEST", "Fixed title"), (source.Id, source.Titles["2"]));

            Directory.CreateDirectory(Path.GetDirectoryName(sources.PathOf(source))!);
            File.WriteAllText(sources.PathOf(source), "not the published file");
            var ex = Assert.Throws<InvalidDataException>(() => sources.Read(source));
            Assert.Contains("does not match the manifest", ex.Message);
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public void The_shipped_manifest_is_valid()
    {
        var root = AppContext.BaseDirectory;
        while (root is not null && !File.Exists(Path.Combine(root, "standards", "sources.yaml")))
            root = Path.GetDirectoryName(root);
        Assert.NotNull(root);

        var sources = new StandardSources(Path.Combine(root, "standards")).Load();

        Assert.Contains(sources, s => s.Id == "BG-IG" && s.Version == "1.3.16");
        Assert.All(sources, s =>
        {
            Assert.Matches("^[0-9a-f]{64}$", s.Sha256);
            Assert.Contains(s.Format, new[] { "pdf", "docx" });
            Assert.Contains(s.Language, new[] { "en", "ka" });
        });
    }
}
