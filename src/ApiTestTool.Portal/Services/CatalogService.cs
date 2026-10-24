using ApiTestTool.Engine;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Scenarios;
using Microsoft.Extensions.Options;

namespace ApiTestTool.Portal.Services;

public sealed record BankEntry(string Key, BankProfile? Profile, string? Error)
{
    public string Bic => Profile?.Bank.Bic ?? Key;

    public string Name => Profile?.Bank.Name ?? Key;
}

/// <summary>Bank profiles and scenarios, read fresh from disk on every call so YAML edits show up without a restart.</summary>
public sealed class CatalogService
{
    public CatalogService(IOptions<PortalOptions> options, IWebHostEnvironment env)
    {
        var root = options.Value.Root;
        RootDirectory = string.IsNullOrWhiteSpace(root)
            ? AppContext.BaseDirectory
            : Path.GetFullPath(Path.Combine(env.ContentRootPath, root));
    }

    public string RootDirectory { get; }

    public string BanksDirectory => Path.Combine(RootDirectory, "banks");

    public string ScenariosDirectory => Path.Combine(RootDirectory, "scenarios");

    /// <summary>All profiles; a broken file is returned with its error instead of hiding the others.</summary>
    public List<BankEntry> GetBanks()
    {
        var result = new List<BankEntry>();
        foreach (var file in BankProfileLoader.List(BanksDirectory))
        {
            var key = Path.GetFileNameWithoutExtension(file);
            try
            {
                result.Add(new BankEntry(key, BankProfileLoader.LoadFile(file), null));
            }
            catch (ConfigurationException ex)
            {
                result.Add(new BankEntry(key, null, ex.Message));
            }
        }

        return result;
    }

    public BankProfile LoadBank(string key) => BankProfileLoader.Load(key, BanksDirectory);

    public List<Scenario> GetScenarios() => ScenarioLoader.LoadAll(ScenariosDirectory);

    public TemplateLibrary GetTemplates() => TemplateLibrary.Load(ScenariosDirectory);

    /// <summary>True for MockBank or another server on this machine.</summary>
    public static bool IsLocal(BankProfile profile) => Uri.TryCreate(profile.Endpoints.Xs2a, UriKind.Absolute, out var u) && u.IsLoopback;

    /// <summary>
    /// Automatic (headless) SCA is offered for local servers and for sandboxes whose profile declares
    /// <c>features.headlessSca: true</c> (their consent page only needs an "approve" click).
    /// </summary>
    public static bool AllowsHeadlessSca(BankProfile profile) =>
        IsLocal(profile) || profile.Features?["headlessSca"] is System.Text.Json.Nodes.JsonValue v && v.TryGetValue<bool>(out var on) && on;
}
