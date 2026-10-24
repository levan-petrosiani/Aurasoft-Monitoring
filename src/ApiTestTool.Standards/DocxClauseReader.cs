using System.IO.Compression;
using System.Text;
using System.Xml.Linq;

namespace ApiTestTool.Standards;

/// <summary>
/// Splits a Word document into clauses using its heading styles (Heading1..Heading6). Word's automatic numbering is not
/// stored in the text, so numbers are computed from the heading levels the way Word numbers them (1, 1.1, 1.1.1, ...).
/// Every heading counts, including the "Contents" heading when its style is numbered (the Georgian guides number it as
/// chapter 1); only a heading that switches numbering off (<c>numId 0</c>) is unnumbered. Verified against the
/// documents' own cross-references ("see chapter 8.2.2").
/// </summary>
public static class DocxClauseReader
{
    private static readonly XNamespace W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

    public static List<Clause> Read(string path, string standard, string version, string language)
    {
        using var zip = ZipFile.OpenRead(path);
        var entry = zip.GetEntry("word/document.xml") ?? throw new InvalidDataException($"{path}: not a Word document (no word/document.xml)");
        XDocument doc;
        using (var stream = entry.Open())
            doc = XDocument.Load(stream);

        var clauses = new List<Clause>();
        var counters = new int[7];
        string? number = null, title = null;
        var body = new StringBuilder();

        void Flush()
        {
            if (number is not null)
            {
                clauses.Add(new Clause
                {
                    Standard = standard,
                    Version = version,
                    Number = number,
                    Title = title!,
                    Text = body.ToString().Trim(),
                    Order = clauses.Count,
                    Language = language,
                });
            }

            body.Clear();
        }

        foreach (var p in doc.Descendants(W + "body").Descendants(W + "p"))
        {
            var text = string.Concat(p.Descendants(W + "t").Select(t => t.Value)).Trim();
            if (text.Length == 0)
                continue;

            var level = HeadingLevel(p);
            if (level is { } l && !NumberingOff(p))
            {
                Flush();
                counters[l]++;
                for (var deeper = l + 1; deeper < counters.Length; deeper++)
                    counters[deeper] = 0;

                // A heading that skips a level (Heading3 directly under Heading1) still gets a full number: 2.0.1 → 2.1.1 like Word.
                for (var higher = 1; higher < l; higher++)
                    counters[higher] = Math.Max(counters[higher], 1);

                number = string.Join('.', counters[1..(l + 1)]);
                title = text;
                continue;
            }

            if (number is not null && !IsTocParagraph(p))
                body.Append(text).Append("\n\n");
        }

        Flush();
        return clauses;
    }

    private static int? HeadingLevel(XElement p)
    {
        var style = p.Element(W + "pPr")?.Element(W + "pStyle")?.Attribute(W + "val")?.Value;
        return style is not null && style.StartsWith("Heading", StringComparison.OrdinalIgnoreCase) && int.TryParse(style.AsSpan(7), out var level) && level is >= 1 and <= 6
            ? level
            : null;
    }

    private static bool NumberingOff(XElement p) =>
        p.Element(W + "pPr")?.Element(W + "numPr")?.Element(W + "numId")?.Attribute(W + "val")?.Value == "0";

    private static bool IsTocParagraph(XElement p) =>
        p.Element(W + "pPr")?.Element(W + "pStyle")?.Attribute(W + "val")?.Value is { } style && style.StartsWith("TOC", StringComparison.OrdinalIgnoreCase);
}
