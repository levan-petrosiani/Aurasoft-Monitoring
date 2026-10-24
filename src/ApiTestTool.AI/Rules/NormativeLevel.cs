using System.Text.RegularExpressions;

namespace ApiTestTool.AI.Rules;

/// <summary>
/// Which requirement levels the wording of a quote supports (RFC 2119 keywords in English, their usual equivalents in
/// the Georgian guides). Used to question a drafted level, not to decide it: "MUST NOT" and "is not allowed" are MUST.
/// </summary>
public static partial class NormativeLevel
{
    public static IReadOnlySet<string> Supported(string quote)
    {
        var levels = new HashSet<string>();
        if (Must().IsMatch(quote))
            levels.Add("MUST");
        if (Should().IsMatch(quote))
            levels.Add("SHOULD");
        if (May().IsMatch(quote))
            levels.Add("MAY");
        return levels;
    }

    /// <summary>A note when the drafted level is not backed by the quote's wording; null when it is.</summary>
    public static string? Question(string level, string quote)
    {
        var supported = Supported(quote);
        if (supported.Contains(level))
            return null;

        return supported.Count == 0
            ? $"The quote has no requirement keyword (must/shall/should/may or Georgian equivalent); check that it is normative and that {level} is right."
            : $"Drafted as {level}, but the quote's wording suggests {string.Join(" or ", supported.Order())}.";
    }

    [GeneratedRegex(@"\b(must|shall|required|mandatory|has to|have to|is not allowed|not permitted)\b|აუცილებელ|უნდა|სავალდებულო|ვალდებულ|დაუშვებელ|აკრძალულ", RegexOptions.IgnoreCase)]
    private static partial Regex Must();

    [GeneratedRegex(@"\b(should|recommended)\b|უმჯობესია|რეკომენდებ|სასურველია", RegexOptions.IgnoreCase)]
    private static partial Regex Should();

    [GeneratedRegex(@"\b(may|optional|optionally|can)\b|შეიძლება|შესაძლებელ|უფლება აქვს|არასავალდებულო", RegexOptions.IgnoreCase)]
    private static partial Regex May();
}
