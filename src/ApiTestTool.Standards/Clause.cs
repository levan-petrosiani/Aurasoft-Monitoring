namespace ApiTestTool.Standards;

/// <summary>One numbered section of a standard, e.g. BG-IG 6.3.1.2 "Consent Request on Account List ...".</summary>
public sealed record Clause
{
    /// <summary>Standard id, e.g. <c>BG-IG</c>, <c>GE-XS2A</c>.</summary>
    public required string Standard { get; init; }

    public required string Version { get; init; }

    /// <summary>Section number as printed in the document, e.g. <c>6.3.1.2</c>.</summary>
    public required string Number { get; init; }

    public required string Title { get; init; }

    /// <summary>Body text of the section only (not its sub-sections), paragraphs separated by blank lines.</summary>
    public required string Text { get; init; }

    /// <summary>Heading depth: 1 for "6", 2 for "6.3", ...</summary>
    public int Level => Number.Count(c => c == '.') + 1;

    /// <summary>Number of the enclosing section, or null at the top level.</summary>
    public string? Parent => Number.LastIndexOf('.') is var i and > 0 ? Number[..i] : null;

    /// <summary>Position in the document, for stable ordering.</summary>
    public int Order { get; init; }

    /// <summary>First page of the section (PDF sources).</summary>
    public int? Page { get; init; }

    /// <summary>ISO language of the text: en, ka.</summary>
    public required string Language { get; init; }

    /// <summary>Stable key: <c>BG-IG:1.3.16:6.3.1.2</c>.</summary>
    public string Key => $"{Standard}:{Version}:{Number}";
}
