using System.Security.Cryptography;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ApiTestTool.Standards;

/// <summary>One entry of <c>standards/sources.yaml</c>.</summary>
public sealed class StandardSource
{
    public string Id { get; set; } = "";

    public string Title { get; set; } = "";

    public string? Publisher { get; set; }

    public string Version { get; set; } = "";

    public string? Published { get; set; }

    /// <summary>en or ka.</summary>
    public string Language { get; set; } = "en";

    /// <summary>pdf or docx.</summary>
    public string Format { get; set; } = "pdf";

    public string Url { get; set; } = "";

    public string? Page { get; set; }

    /// <summary>Path below <c>standards/sources/</c>.</summary>
    public string File { get; set; } = "";

    public string Sha256 { get; set; } = "";

    public string? License { get; set; }

    public string? PageHeaderEnd { get; set; }

    /// <summary>Corrections for headings the document itself prints wrongly: clause number → title.</summary>
    public Dictionary<string, string> Titles { get; set; } = [];
}

/// <summary>Loads the manifest, downloads missing files (checking their hash) and reads them into clauses.</summary>
public sealed class StandardSources(string standardsDirectory)
{
    public string Directory => standardsDirectory;

    public IReadOnlyList<StandardSource> Load()
    {
        var path = Path.Combine(standardsDirectory, "sources.yaml");
        if (!System.IO.File.Exists(path))
            throw new FileNotFoundException($"No standards manifest at {path}");

        return new DeserializerBuilder().WithNamingConvention(CamelCaseNamingConvention.Instance).Build()
            .Deserialize<List<StandardSource>>(System.IO.File.ReadAllText(path)) ?? [];
    }

    public string PathOf(StandardSource source) => Path.Combine(standardsDirectory, "sources", source.File);

    /// <summary>Downloads the file if it is missing and checks its SHA-256. Returns what happened.</summary>
    public async Task<string> FetchAsync(StandardSource source, HttpClient http, CancellationToken cancellationToken = default)
    {
        var path = PathOf(source);
        if (System.IO.File.Exists(path))
        {
            Verify(source, path);
            return "present, hash ok";
        }

        System.IO.Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temp = path + ".download";
        await using (var target = System.IO.File.Create(temp))
        await using (var stream = await http.GetStreamAsync(source.Url, cancellationToken))
            await stream.CopyToAsync(target, cancellationToken);

        try
        {
            Verify(source, temp);
        }
        catch
        {
            System.IO.File.Delete(temp);
            throw;
        }

        System.IO.File.Move(temp, path);
        return "downloaded, hash ok";
    }

    public static string Hash(string path)
    {
        using var stream = System.IO.File.OpenRead(path);
        return Convert.ToHexStringLower(SHA256.HashData(stream));
    }

    /// <summary>Splits the downloaded file into clauses.</summary>
    public (List<Clause> Clauses, List<string> Problems) Read(StandardSource source)
    {
        var path = PathOf(source);
        Verify(source, path);
        var (clauses, problems) = source.Format switch
        {
            "pdf" => PdfClauseReader.Read(path, source.Id, source.Version, source.Language, source.PageHeaderEnd) is var r ? (r.Clauses, r.Problems) : default,
            "docx" => (DocxClauseReader.Read(path, source.Id, source.Version, source.Language), new List<string>()),
            _ => throw new InvalidDataException($"{source.Id}: unknown format '{source.Format}' (pdf or docx)"),
        };

        foreach (var (number, title) in source.Titles)
        {
            var index = clauses.FindIndex(c => c.Number == number);
            if (index < 0)
                problems.Add($"Title correction for {number}: no such clause");
            else
                clauses[index] = clauses[index] with { Title = title };
        }

        return (clauses, problems);
    }

    private static void Verify(StandardSource source, string path)
    {
        var actual = Hash(path);
        if (!string.Equals(actual, source.Sha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException($"{source.Id} {source.Version}: SHA-256 {actual} does not match the manifest ({source.Sha256}). The publisher may have changed the file: review it, then update sources.yaml.");
    }
}
