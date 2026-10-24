using System.Text;
using System.Text.RegularExpressions;

namespace ApiTestTool.Standards;

/// <summary>
/// Splits a PDF standard into clauses using its table of contents as the list of sections: every TOC entry
/// ("6.3.1.2 Consent Request on Account List ....... 144") is then located, in order, as a heading line in the body.
/// Using the TOC avoids taking numbered list items or references ("cp. 6.3.3") for headings.
/// </summary>
public static partial class PdfClauseReader
{
    public sealed record TocEntry(string Number, string Title, int PrintedPage);

    public sealed record Result(List<Clause> Clauses, List<TocEntry> Toc, List<string> Problems);

    /// <param name="pageHeaderEnd">Text of the last line of each page's running header (the header lines are dropped).</param>
    public static Result Read(string path, string standard, string version, string language, string? pageHeaderEnd = null) =>
        Read([.. PdfText.Lines(path)], standard, version, language, pageHeaderEnd);

    internal static Result Read(IReadOnlyList<(int Page, string Line)> lines, string standard, string version, string language, string? pageHeaderEnd)
    {
        var problems = new List<string>();
        var tocPages = lines.GroupBy(l => l.Page).Where(g => g.Count(l => TocLeader().IsMatch(l.Line)) >= 3).Select(g => g.Key).ToHashSet();
        var toc = ParseToc(lines.Where(l => tocPages.Contains(l.Page)).Select(l => l.Line));

        var body = StripPageHeaders(lines.Where(l => !tocPages.Contains(l.Page) && l.Page > (tocPages.Count == 0 ? 0 : tocPages.Max())), pageHeaderEnd).ToList();

        // Locate each TOC entry as a heading, searching forward from the previous one.
        var headings = new List<(TocEntry Entry, int Index)>();
        var from = 0;
        foreach (var entry in toc)
        {
            var index = FindHeading(body, entry, from);
            if (index < 0)
            {
                problems.Add($"Heading {entry.Number} \"{entry.Title}\" (p. {entry.PrintedPage}) not found in the body");
                continue;
            }

            headings.Add((entry, index));
            from = index + 1;
        }

        var clauses = new List<Clause>();
        for (var i = 0; i < headings.Count; i++)
        {
            var (entry, start) = headings[i];
            var end = i + 1 < headings.Count ? headings[i + 1].Index : body.Count;

            // A long heading can wrap: skip continuation lines that are still part of the title.
            var textStart = start + 1;
            var headingText = body[start].Line;
            while (textStart < end && Normalize(headingText).Length < Normalize($"{entry.Number} {entry.Title}").Length
                   && Normalize($"{entry.Number} {entry.Title}").StartsWith(Normalize(headingText + " " + body[textStart].Line), StringComparison.Ordinal))
                headingText += " " + body[textStart++].Line;

            clauses.Add(new Clause
            {
                Standard = standard,
                Version = version,
                Number = entry.Number,
                Title = entry.Title,
                Text = string.Join('\n', body.Skip(textStart).Take(end - textStart).Select(l => l.Line)).Trim(),
                Order = clauses.Count,
                Page = entry.PrintedPage,
                Language = language,
            });
        }

        return new Result(SplitSubsections(clauses), toc, problems);
    }

    /// <summary>
    /// The TOC usually stops at three levels; deeper headings (6.3.1.2) are found inside their parent's text. Only the
    /// expected next child number is accepted (6.3.1.1, then 6.3.1.2, ...) on a short line starting with a capital,
    /// so references and numbered list items are not mistaken for headings. Recurses for deeper levels.
    /// </summary>
    internal static List<Clause> SplitSubsections(List<Clause> clauses)
    {
        var result = new List<Clause>();
        foreach (var clause in clauses)
            Split(clause, result);

        return [.. result.Select((c, i) => c with { Order = i })];
    }

    private static void Split(Clause clause, List<Clause> output)
    {
        var lines = clause.Text.Split('\n');
        var cuts = new List<(int Line, string Number, string Title)>();
        for (int i = 0, next = 1; i < lines.Length; i++)
        {
            var number = $"{clause.Number}.{next}";
            var line = lines[i];
            if (line.Length < 120 && line.StartsWith(number + " ", StringComparison.Ordinal) && line.Length > number.Length + 1 && char.IsUpper(line[number.Length + 1]))
            {
                cuts.Add((i, number, line[(number.Length + 1)..].Trim()));
                next++;
            }
        }

        if (cuts.Count == 0)
        {
            output.Add(clause);
            return;
        }

        output.Add(clause with { Text = string.Join('\n', lines[..cuts[0].Line]).Trim() });
        for (var k = 0; k < cuts.Count; k++)
        {
            var end = k + 1 < cuts.Count ? cuts[k + 1].Line : lines.Length;
            Split(new Clause
            {
                Standard = clause.Standard,
                Version = clause.Version,
                Number = cuts[k].Number,
                Title = cuts[k].Title,
                Text = string.Join('\n', lines[(cuts[k].Line + 1)..end]).Trim(),
                Page = clause.Page,
                Language = clause.Language,
            }, output);
        }
    }

    internal static List<TocEntry> ParseToc(IEnumerable<string> tocLines)
    {
        var entries = new List<TocEntry>();
        string? pending = null;

        foreach (var line in tocLines)
        {
            if (TocStart().IsMatch(line))
                pending = line;
            else if (pending is not null)
                pending += " " + line; // wrapped title or the leader line of a long title
            else
                continue;

            var m = TocLeader().Match(pending);
            if (!m.Success)
                continue;

            var start = TocStart().Match(pending);
            var title = pending[start.Length..m.Index].Trim();
            entries.Add(new TocEntry(start.Groups["n"].Value, Regex.Replace(title, @"\s+", " "), int.Parse(m.Groups["p"].Value, System.Globalization.CultureInfo.InvariantCulture)));
            pending = null;
        }

        return entries;
    }

    private static IEnumerable<(int Page, string Line)> StripPageHeaders(IEnumerable<(int Page, string Line)> lines, string? headerEnd)
    {
        if (headerEnd is null)
            return lines;

        // Drop everything on a page up to and including the header's last line (if the page has that line).
        return lines.GroupBy(l => l.Page).SelectMany(page =>
        {
            var list = page.ToList();
            var cut = list.FindIndex(l => l.Line.StartsWith(headerEnd, StringComparison.Ordinal));
            return cut < 0 ? list : list.Skip(cut + 1);
        });
    }

    private static int FindHeading(List<(int Page, string Line)> body, TocEntry entry, int from)
    {
        var prefix = entry.Number + " ";
        var title = Normalize(entry.Title);
        var probe = title[..Math.Min(title.Length, 20)];
        for (var i = from; i < body.Count; i++)
        {
            var line = body[i].Line;
            if (line.StartsWith(prefix, StringComparison.Ordinal) && Normalize(line[prefix.Length..]).StartsWith(probe, StringComparison.Ordinal))
                return i;
        }

        return -1;
    }

    private static string Normalize(string s) => Regex.Replace(s, @"\s+", " ").Trim();

    [GeneratedRegex(@"^(?<n>\d+(?:\.\d+)*)\s+")]
    private static partial Regex TocStart();

    [GeneratedRegex(@"\s*\.{4,}\s*(?<p>\d+)\s*$")]
    private static partial Regex TocLeader();
}
