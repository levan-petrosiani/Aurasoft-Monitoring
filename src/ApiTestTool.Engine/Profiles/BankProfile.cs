using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace ApiTestTool.Engine.Profiles;

/// <summary>
/// Everything the tool needs to know about one bank's sandbox: endpoints, our TPP certificates, supported features
/// and test data. One YAML file per bank in <c>banks/</c>.
/// </summary>
public sealed class BankProfile
{
    public required BankInfo Bank { get; init; }

    public required EndpointSettings Endpoints { get; init; }

    public TppSettings Tpp { get; init; } = new();

    /// <summary>What the bank supports. Scenarios declare <c>requires</c> paths into this object and are skipped otherwise.</summary>
    public JsonObject? Features { get; init; }

    /// <summary>Free-form test data (PSUs, IBANs, ...), available in scenarios as <c>{{vars.*}}</c>.</summary>
    public JsonObject? Variables { get; init; }

    /// <summary>Bank-specific deviations, e.g. extra OAuth parameters.</summary>
    public JsonObject? Quirks { get; init; }

    public LimitSettings Limits { get; init; } = new();

    /// <summary>Default for checking the bank's response signatures: <c>required</c>, <c>optional</c> (verify when present) or <c>ignore</c>.</summary>
    public string ResponseSignature { get; init; } = "optional";

    /// <summary>Path of the YAML file this profile was loaded from.</summary>
    [JsonIgnore]
    public string SourcePath { get; internal set; } = string.Empty;

    /// <summary>The raw profile document, used to evaluate scenario <c>requires</c> paths.</summary>
    [JsonIgnore]
    public JsonNode? Raw { get; internal set; }
}

public sealed class BankInfo
{
    public required string Bic { get; init; }

    public required string Name { get; init; }

    /// <summary>Specification the bank claims to implement (informational for now).</summary>
    public string? Spec { get; init; }
}

public sealed class EndpointSettings
{
    /// <summary>XS2A sandbox base URL, e.g. https://psd2-xs2a-sandbox.bank.ge/</summary>
    public required string Xs2a { get; init; }

    /// <summary>OAuth2/OIDC discovery document of the bank's auth server.</summary>
    public string? Discovery { get; init; }
}

public sealed class TppSettings
{
    /// <summary>QWAC used for mutual TLS.</summary>
    public CertificateSettings? Qwac { get; init; }

    /// <summary>QSealC used for request signatures (X-Jws-Signature).</summary>
    public CertificateSettings? Qsealc { get; init; }

    /// <summary>Redirect URI registered at the bank, sent as TPP-Redirect-URI.</summary>
    public string RedirectUri { get; init; } = "http://localhost:5199/callback";

    /// <summary>Reject server certificates that do not chain to a trusted root. Disable only for sandboxes with test CAs.</summary>
    public bool ValidateServerCertificate { get; init; } = true;
}

public sealed class CertificateSettings
{
    /// <summary>PFX file, relative to the profile file.</summary>
    public string? File { get; init; }

    /// <summary>PFX password. Prefer <see cref="PasswordEnv"/> so passwords stay out of files.</summary>
    public string? Password { get; init; }

    /// <summary>Environment variable that holds the PFX password.</summary>
    public string? PasswordEnv { get; init; }

    /// <summary>Windows certificate store, e.g. CurrentUser/My.</summary>
    public string? Store { get; init; }

    public string? Thumbprint { get; init; }

    /// <summary>
    /// Generate a throw-away self-signed certificate for this run instead of loading one. Only useful against
    /// test servers that accept any certificate, such as ApiTestTool.MockBank.
    /// </summary>
    public SelfSignedSettings? SelfSigned { get; init; }
}

public sealed class SelfSignedSettings
{
    /// <summary>PSD2 authorisation number put in the organizationIdentifier (OID 2.5.4.97), i.e. our TPP id.</summary>
    public string OrganizationId { get; init; } = "PSDGE-NBG-MOCKTPP";

    public string CommonName { get; init; } = "ApiTestTool Mock TPP";
}

public sealed class LimitSettings
{
    public int TimeoutSeconds { get; init; } = 30;

    /// <summary>Pause between requests, to stay within the bank's rate limits.</summary>
    public int DelayBetweenRequestsMs { get; init; }

    /// <summary>Response time above which a warning is reported.</summary>
    public int SlowResponseMs { get; init; } = 3000;

    /// <summary>
    /// Retry of transient failures. Off by default, because for quality monitoring an outage is a finding;
    /// useful for sandboxes that inject random failures on purpose. Every retry is reported as a warning.
    /// </summary>
    public RetrySettings? Retry { get; init; }
}

public sealed class RetrySettings
{
    /// <summary>Total attempts including the first one (2 = one retry).</summary>
    public int Attempts { get; init; } = 3;

    public int DelayMs { get; init; } = 500;

    /// <summary>Statuses treated as transient. Connection errors and timeouts are always transient.</summary>
    public List<int> Statuses { get; init; } = [502, 503, 504];
}
