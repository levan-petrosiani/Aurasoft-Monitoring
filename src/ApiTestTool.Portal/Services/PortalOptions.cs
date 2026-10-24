namespace ApiTestTool.Portal.Services;

public sealed class PortalOptions
{
    public const string Section = "ApiTestTool";

    /// <summary>
    /// Folder with <c>banks/</c> and <c>scenarios/</c> (and <c>certs/</c>). Relative paths are resolved against the
    /// content root. Empty = the copies next to the portal binaries.
    /// </summary>
    public string? Root { get; set; }

    /// <summary>Where run results are stored (one folder per run). Relative to the content root.</summary>
    public string DataDirectory { get; set; } = "data/runs";

    /// <summary>MockBank's XS2A URL; the portal can host MockBank itself on this port.</summary>
    public string MockBankXs2aUrl { get; set; } = "https://localhost:7443/";
}
