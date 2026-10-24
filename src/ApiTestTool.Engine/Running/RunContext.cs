using System.Security.Cryptography.X509Certificates;
using ApiTestTool.Engine.Certificates;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Signing;

namespace ApiTestTool.Engine.Running;

/// <summary>Shared state for one run against one bank.</summary>
public sealed class RunContext : IDisposable
{
    private readonly bool _ownsHttpClient;

    public RunContext(BankProfile profile, TemplateLibrary templates, X509Certificate2? qwac, X509Certificate2? qsealc, HttpClient? httpClient = null, Func<DateTime>? utcNow = null)
    {
        Profile = profile;
        Templates = templates;
        Qwac = qwac;
        Qsealc = qsealc;
        QwacInfo = qwac is null ? null : Psd2CertificateInfo.From(qwac);
        QsealcInfo = qsealc is null ? null : Psd2CertificateInfo.From(qsealc);
        Signer = qsealc is null ? null : new JwsSigner(qsealc);
        UtcNow = utcNow ?? (() => DateTime.UtcNow);

        _ownsHttpClient = httpClient is null;
        HttpClient = httpClient ?? Xs2aHttpClient.Create(profile, qwac);
    }

    public BankProfile Profile { get; }

    public TemplateLibrary Templates { get; }

    public X509Certificate2? Qwac { get; }

    public X509Certificate2? Qsealc { get; }

    public Psd2CertificateInfo? QwacInfo { get; }

    public Psd2CertificateInfo? QsealcInfo { get; }

    public JwsSigner? Signer { get; }

    public HttpClient HttpClient { get; }

    public Func<DateTime> UtcNow { get; }

    /// <summary>Opens the authorization URL for manual SCA. Replaceable in tests.</summary>
    public Func<string, CancellationToken, Task> OpenBrowser { get; set; } = SystemBrowser.OpenAsync;

    /// <summary>Messages the tester must see while a step waits for them (e.g. "log in at …").</summary>
    public Action<string> Notify { get; set; } = _ => { };

    /// <summary>Our TPP id (organizationIdentifier of the QWAC).</summary>
    public string? TppId => QwacInfo?.OrganizationId;

    /// <summary>Loads the profile's certificates and creates the context. Missing certificates are allowed (preflight reports them).</summary>
    public static RunContext Create(BankProfile profile, TemplateLibrary templates)
    {
        var baseDir = Path.GetDirectoryName(profile.SourcePath) ?? Directory.GetCurrentDirectory();
        var qwac = profile.Tpp.Qwac is null ? null : CertificateLoader.Load(profile.Tpp.Qwac, baseDir, "tpp.qwac");
        var qsealc = profile.Tpp.Qsealc is null ? null : CertificateLoader.Load(profile.Tpp.Qsealc, baseDir, "tpp.qsealc");
        return new RunContext(profile, templates, qwac, qsealc);
    }

    public void Dispose()
    {
        if (_ownsHttpClient)
            HttpClient.Dispose();
        Qwac?.Dispose();
        Qsealc?.Dispose();
    }
}
