using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace ApiTestTool.Engine.Scenarios;

/// <summary>One test case, loaded from a YAML file in <c>scenarios/</c>.</summary>
public sealed class Scenario
{
    public required string Id { get; init; }

    public required string Title { get; init; }

    /// <summary>Suite name used for filtering, e.g. preflight, negative, ais, pis.</summary>
    public required string Suite { get; init; }

    /// <summary>Critical, Major, Minor or Info.</summary>
    public string Severity { get; init; } = "Major";

    /// <summary>Where in the standard the expected behaviour is defined.</summary>
    public string? SpecRef { get; init; }

    /// <summary>Where the scenario came from, e.g. the TestTool BadRequest method it was ported from.</summary>
    public string? Origin { get; init; }

    public string? Description { get; init; }

    /// <summary>Profile paths that must be present and truthy (e.g. <c>endpoints.discovery</c>), otherwise the scenario is skipped.</summary>
    public List<string>? Requires { get; init; }

    public required List<Step> Steps { get; init; }

    /// <summary>Steps that always run at the end (e.g. delete the consent). Their failures become warnings.</summary>
    public List<Step>? Cleanup { get; init; }

    [JsonIgnore]
    public string SourcePath { get; internal set; } = string.Empty;
}

/// <summary>A scenario step. Exactly one of <see cref="Request"/>, <see cref="Tls"/>, <see cref="LocalCertificates"/>, <see cref="Sca"/> is set.</summary>
public sealed class Step
{
    public string? Name { get; init; }

    public RequestSpec? Request { get; init; }

    public TlsSpec? Tls { get; init; }

    public LocalCertificatesSpec? LocalCertificates { get; init; }

    public ScaSpec? Sca { get; init; }

    /// <summary>Variables that must have been saved by earlier steps; otherwise this step is skipped (useful in cleanup).</summary>
    public List<string>? RunIf { get; init; }

    public Expectation? Expect { get; init; }

    /// <summary>Values to keep for later steps: variable name → JSON path (<c>$.consentId</c>) or <c>header:Location</c>.</summary>
    public Dictionary<string, string>? Save { get; init; }

    internal string Kind => Request is not null ? "request" : Tls is not null ? "tls" : Sca is not null ? "sca" : "localCertificates";

    internal int KindCount => (Request is null ? 0 : 1) + (Tls is null ? 0 : 1) + (LocalCertificates is null ? 0 : 1) + (Sca is null ? 0 : 1);

    internal string DisplayName(int index) => Name ?? Request switch
    {
        not null => $"{Request.Method.ToUpperInvariant()} {Request.Path}",
        null => $"step {index + 1} ({Kind})",
    };
}

public sealed class RequestSpec
{
    public string Method { get; init; } = "GET";

    /// <summary>Path relative to the target base URL; may contain a query string and {{variables}}.</summary>
    public required string Path { get; init; }

    /// <summary><c>xs2a</c> (default), <c>discovery</c> (path ignored, the discovery URL is used) or <c>absolute</c> (path is a full URL).</summary>
    public string Target { get; init; } = "xs2a";

    /// <summary>Named header set from <c>scenarios/_templates</c>.</summary>
    public string? HeadersTemplate { get; init; }

    /// <summary>Headers added on top of the template. A null value removes the header.</summary>
    public Dictionary<string, JsonNode?>? Headers { get; init; }

    public List<string>? RemoveHeaders { get; init; }

    /// <summary>Named JSON body from <c>scenarios/_templates</c>.</summary>
    public string? BodyTemplate { get; init; }

    /// <summary>JSON body; when a template is used, its properties are deep-merged over the template.</summary>
    public JsonNode? Body { get; init; }

    /// <summary>Dotted paths removed from the body after merging, e.g. <c>access</c> or <c>access.payments</c>.</summary>
    public List<string>? BodyRemove { get; init; }

    /// <summary>Body sent exactly as written (for malformed JSON tests). Overrides template and body.</summary>
    public string? BodyRaw { get; init; }

    /// <summary>normal, none, corruptSignature or wrongDigest.</summary>
    public string Signing { get; init; } = "normal";

    /// <summary>Bearer token for the Authorization header.</summary>
    public string? Auth { get; init; }

    /// <summary>Send the request without Authorization when <see cref="Auth"/> refers to a variable that was never saved.</summary>
    public bool AuthOptional { get; init; }
}

public sealed class TlsSpec
{
    public string Target { get; init; } = "xs2a";

    /// <summary>Lowest acceptable protocol: Tls12 or Tls13.</summary>
    public string MinProtocol { get; init; } = "Tls12";

    public int WarnDaysBeforeExpiry { get; init; } = 30;

    public bool SendClientCertificate { get; init; } = true;
}

/// <summary>
/// Strong customer authentication through the bank's OAuth2 server (authorization code + PKCE):
/// discovery → authorize in a browser → callback on the redirect URI → token exchange (→ optional refresh).
/// </summary>
public sealed class ScaSpec
{
    /// <summary>Only <c>manual</c> for now: the tester logs in with a sandbox PSU in the system browser.</summary>
    public string Mode { get; init; } = "manual";

    /// <summary>Discovery URL or auth server base URL, e.g. <c>{{scaOAuthUrl}}</c> from <c>_links.scaOAuth</c>. Defaults to the profile's endpoints.discovery.</summary>
    public string? Discovery { get; init; }

    /// <summary>OAuth scope, e.g. <c>AIS:{{consentId}}</c> (Georgian XS2A: &lt;AIS|PIS&gt;:&lt;resource id&gt;).</summary>
    public required string Scope { get; init; }

    /// <summary>OAuth client_id. Defaults to our TPP id from the QWAC (<c>{{tpp.id}}</c>).</summary>
    public string? ClientId { get; init; }

    /// <summary>Defaults to the profile's tpp.redirectUri.</summary>
    public string? RedirectUri { get; init; }

    /// <summary>How long to wait for the tester to finish logging in.</summary>
    public int TimeoutSeconds { get; init; } = 300;

    /// <summary>Also exercise the refresh_token grant after the code exchange.</summary>
    public bool Refresh { get; init; }

    /// <summary>Variable that receives the access token.</summary>
    public string SaveAccessTokenAs { get; init; } = "token";

    /// <summary>Variable that receives the refresh token (when the bank issues one).</summary>
    public string SaveRefreshTokenAs { get; init; } = "refreshToken";

    /// <summary>Warn when the token's scope does not contain this value, e.g. the consent id.</summary>
    public string? ExpectScopeContains { get; init; }
}

public sealed class LocalCertificatesSpec
{
    public int WarnDaysBeforeExpiry { get; init; } = 30;

    public bool RequireQsealc { get; init; } = true;
}

public sealed class Expectation
{
    /// <summary>Expected HTTP status: a number or a list of numbers.</summary>
    public JsonNode? Status { get; init; }

    /// <summary>Response headers that must be present.</summary>
    public List<string>? Headers { get; init; }

    /// <summary>The response X-Request-ID must equal the request's.</summary>
    public bool EchoRequestId { get; init; }

    /// <summary>JSON path → expected value (compared as text, case-sensitive).</summary>
    public Dictionary<string, JsonNode?>? Json { get; init; }

    public List<string>? Exists { get; init; }

    public List<string>? NotExists { get; init; }

    /// <summary>Paths whose absence is reported as a warning, not a failure.</summary>
    public List<string>? WarnIfMissing { get; init; }

    /// <summary>
    /// Expected error code. Matched against <c>$.code</c> and <c>$.additionalErrors[*].code</c> (application/problem+json)
    /// and <c>$.tppMessages[*].code</c> (Berlin Group JSON). <c>"*"</c> accepts any code but requires one.
    /// </summary>
    public string? ErrorCode { get; init; }

    /// <summary>Expected media type prefix, e.g. application/json or application/problem+json.</summary>
    public string? ContentType { get; init; }

    public int? MaxDurationMs { get; init; }

    /// <summary>Overrides the profile's responseSignature for this step: required, optional or ignore.</summary>
    public string? ResponseSignature { get; init; }
}
