using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace ApiTestTool.Standards;

/// <summary>
/// Decides whether a quoted passage really occurs in a clause. This is what stops a drafted rule from citing words the
/// standard does not contain. Comparison ignores what text extraction and copying change — whitespace and line breaks,
/// hyphenation at line ends, typographic quotes and dashes, case — and nothing else. An ellipsis ("..." or "…") in the
/// quote splits it into parts that must all occur, in order.
/// </summary>
public static partial class QuoteVerifier
{
    /// <summary>Minimum characters (after normalisation) of each quoted part, so "the" or "MUST" cannot pass as a quote.</summary>
    public const int MinPartLength = 12;

    public static bool Occurs(string quote, string clauseText) => Check(quote, clauseText) is null;

    /// <summary>Null when the quote occurs; otherwise the reason it does not.</summary>
    public static string? Check(string quote, string clauseText)
    {
        var text = Normalize(clauseText);
        var parts = Ellipsis().Split(quote).Select(Normalize).Where(p => p.Length > 0).ToList();
        if (parts.Count == 0)
            return "The quote is empty.";

        var from = 0;
        foreach (var part in parts)
        {
            if (part.Length < MinPartLength)
                return $"The quoted part \"{part}\" is too short to identify a passage.";

            var at = text.IndexOf(part, from, StringComparison.Ordinal);
            if (at < 0)
                return $"\"{Shorten(part)}\" does not occur in the clause{(from > 0 ? " after the previous quoted part" : "")}.";
            from = at + part.Length;
        }

        return null;
    }

    public static string Normalize(string s)
    {
        var sb = new StringBuilder(s.Length);
        foreach (var ch in s.Normalize(NormalizationForm.FormKC))
        {
            sb.Append(ch switch
            {
                '“' or '”' or '„' or '«' or '»' or '‘' or '’' or '‚' or '`' or '\'' => '"',
                '–' or '—' or '−' or '‐' or '‑' => '-',
                '­' => '\0', // soft hyphen
                _ => char.ToLower(ch, CultureInfo.InvariantCulture),
            });
        }

        var text = sb.ToString().Replace("\0", "", StringComparison.Ordinal);
        text = HyphenBreak().Replace(text, "$1$2"); // "unambi-\ngously" → "unambiguously"
        return Whitespace().Replace(text, " ").Trim();
    }

    private static string Shorten(string s) => s.Length <= 80 ? s : s[..77] + "...";

    [GeneratedRegex(@"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")]
    private static partial Regex Ellipsis();

    [GeneratedRegex(@"(\p{L})-\s*\n\s*(\p{L})")]
    private static partial Regex HyphenBreak();

    [GeneratedRegex(@"\s+")]
    private static partial Regex Whitespace();
}
