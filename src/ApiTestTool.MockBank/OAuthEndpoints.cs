using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Certificates;
using Microsoft.Extensions.Options;

namespace ApiTestTool.MockBank;

/// <summary>
/// OAuth2 authorization server for the "OAuth" SCA approach: discovery, authorize (browser page or automatic
/// approval), token with PKCE S256 and refresh. Scope format: <c>AIS:&lt;consentId&gt;</c>.
/// </summary>
public static class OAuthEndpoints
{
    public const string HeadlessUserAgent = "ApiTestTool-HeadlessSca";

    public static void Map(WebApplication app)
    {
        app.MapGet("/.well-known/oauth-authorization-server", Discovery);
        app.MapGet("/.well-known/openid-configuration", Discovery);
        app.MapGet("/oauth/authorize", Authorize);
        app.MapPost("/oauth/authorize", DecideAsync);
        app.MapPost("/oauth/token", TokenAsync);
    }

    private static IResult Discovery(IOptions<MockBankOptions> options)
    {
        var o = options.Value;
        return Results.Json(new JsonObject
        {
            ["issuer"] = o.HttpsBaseUrl,
            ["authorization_endpoint"] = $"{o.HttpBaseUrl}/oauth/authorize",
            ["token_endpoint"] = $"{o.HttpsBaseUrl}/oauth/token",
            ["registration_endpoint"] = $"{o.HttpsBaseUrl}/oauth/register",
            ["scopes_supported"] = new JsonArray("AIS", "PIS"),
            ["response_types_supported"] = new JsonArray("code"),
            ["grant_types_supported"] = new JsonArray("authorization_code", "refresh_token"),
            ["token_endpoint_auth_methods_supported"] = new JsonArray("tls_client_auth"),
            ["ui_locales_supported"] = new JsonArray("ka", "en"),
            ["code_challenge_methods_supported"] = new JsonArray("S256"),
            ["mtls_endpoint_aliases"] = new JsonObject { ["token_endpoint"] = $"{o.HttpsBaseUrl}/oauth/token" },
            ["tls_client_certificate_bound_access_tokens"] = true,
        });
    }

    private sealed record AuthorizeRequest(string? ResponseType, string? ClientId, string? RedirectUri, string? Scope, string? State, string? CodeChallenge, string? CodeChallengeMethod);

    private static IResult Authorize(HttpContext http, MockState state, IOptions<MockBankOptions> options)
    {
        var q = http.Request.Query;
        var request = new AuthorizeRequest(q["response_type"], q["client_id"], q["redirect_uri"], q["scope"], q["state"], q["code_challenge"], q["code_challenge_method"]);

        if (!Uri.TryCreate(request.RedirectUri, UriKind.Absolute, out _))
            return ErrorPage("redirect_uri is missing or invalid");

        var (consent, error) = Validate(request, state);
        if (error is not null)
            return RedirectWithError(request, "invalid_request", error);

        var autoApprove = options.Value.AutoApprove switch
        {
            "always" => true,
            "never" => false,
            _ => http.Request.Headers.UserAgent.ToString().Contains(HeadlessUserAgent, StringComparison.Ordinal),
        };

        return autoApprove ? Approve(request, consent!, state) : LoginPage(request, consent!, state);
    }

    private static async Task<IResult> DecideAsync(HttpContext http, MockState state)
    {
        var f = await http.Request.ReadFormAsync(http.RequestAborted);
        var request = new AuthorizeRequest(f["response_type"], f["client_id"], f["redirect_uri"], f["scope"], f["state"], f["code_challenge"], f["code_challenge_method"]);

        var (consent, error) = Validate(request, state);
        if (error is not null)
            return RedirectWithError(request, "invalid_request", error);

        if (f["decision"] != "approve")
        {
            consent!.Status = "rejected";
            return RedirectWithError(request, "access_denied", "The PSU rejected the consent");
        }

        return Approve(request, consent!, state);
    }

    private static (Consent? consent, string? error) Validate(AuthorizeRequest r, MockState state)
    {
        if (r.ResponseType != "code")
            return (null, "response_type must be 'code'");
        if (string.IsNullOrEmpty(r.ClientId))
            return (null, "client_id is required");
        if (string.IsNullOrEmpty(r.CodeChallenge) || r.CodeChallengeMethod != "S256")
            return (null, "PKCE with code_challenge_method=S256 is required");

        var consentId = ConsentIdFromScope(r.Scope);
        if (consentId is null)
            return (null, $"scope '{r.Scope}' must be AIS:<consentId>");
        if (!state.Consents.TryGetValue(consentId, out var consent) || consent.TppId != r.ClientId)
            return (null, "the consent in the scope is unknown for this client");
        if (consent.Status != "received")
            return (null, $"consent is '{consent.Status}', expected 'received'");
        if (consent.RedirectUri is not null && consent.RedirectUri != r.RedirectUri)
            return (null, "redirect_uri differs from the Client-Redirect-URI sent with the consent");

        return (consent, null);
    }

    private static IResult Approve(AuthorizeRequest r, Consent consent, MockState state)
    {
        var code = MockState.NewToken();
        state.Codes[code] = new AuthorizationCode(code, r.ClientId!, r.RedirectUri!, NormalizeScope(r.Scope!), consent.Id, r.CodeChallenge!, DateTime.UtcNow.AddMinutes(2));

        foreach (var a in state.Authorisations.Values.Where(a => a.ConsentId == consent.Id))
            a.ScaStatus = "psuAuthenticated";

        return Results.Redirect(AppendQuery(r.RedirectUri!, ("code", code), ("state", r.State)));
    }

    private static async Task<IResult> TokenAsync(HttpContext http, MockState state, IOptions<MockBankOptions> options)
    {
        var clientCert = http.Connection.ClientCertificate ?? await http.Connection.GetClientCertificateAsync(http.RequestAborted);
        var certTppId = clientCert is null ? null : Psd2CertificateInfo.GetOrganizationId(clientCert.SubjectName);
        if (certTppId is null)
            return Problems.OAuth("invalid_client", "Mutual TLS with the TPP's QWAC is required (tls_client_auth)", 401);

        var f = await http.Request.ReadFormAsync(http.RequestAborted);
        if (f["client_id"] != certTppId)
            return Problems.OAuth("invalid_client", $"client_id must be the QWAC's TPP id '{certTppId}'", 401);

        switch (f["grant_type"].ToString())
        {
            case "authorization_code":
                if (!state.Codes.TryGetValue(f["code"].ToString(), out var code) || code.Used || code.ExpiresUtc < DateTime.UtcNow)
                    return Problems.OAuth("invalid_grant", "Authorization code is unknown, expired or already used");
                if (code.ClientId != certTppId || code.RedirectUri != f["redirect_uri"])
                    return Problems.OAuth("invalid_grant", "client_id or redirect_uri does not match the authorization request");
                if (Pkce(f["code_verifier"].ToString()) != code.CodeChallenge)
                    return Problems.OAuth("invalid_grant", "code_verifier does not match code_challenge (PKCE)");

                code.Used = true;
                if (state.Consents.TryGetValue(code.ConsentId, out var consent) && consent.Status == "received")
                    consent.Status = "valid";
                foreach (var a in state.Authorisations.Values.Where(a => a.ConsentId == code.ConsentId))
                    a.ScaStatus = "finalised";

                return IssueTokens(state, options.Value, certTppId, code.ConsentId, code.Scope);

            case "refresh_token":
                if (!state.RefreshTokens.TryRemove(f["refresh_token"].ToString(), out var refresh) || refresh.ClientId != certTppId)
                    return Problems.OAuth("invalid_grant", "Refresh token is unknown or belongs to another client");
                if (!state.Consents.TryGetValue(refresh.ConsentId, out var c) || c.Status != "valid")
                    return Problems.OAuth("invalid_grant", "The consent is no longer valid");

                return IssueTokens(state, options.Value, certTppId, refresh.ConsentId, refresh.Scope);

            default:
                return Problems.OAuth("unsupported_grant_type", "Use authorization_code or refresh_token");
        }
    }

    private static IResult IssueTokens(MockState state, MockBankOptions options, string clientId, string consentId, string scope)
    {
        var access = new AccessToken(MockState.NewToken(), clientId, consentId, scope, DateTime.UtcNow.AddSeconds(options.AccessTokenLifetimeSeconds));
        var refresh = new RefreshToken(MockState.NewToken(), clientId, consentId, scope);
        state.AccessTokens[access.Token] = access;
        state.RefreshTokens[refresh.Token] = refresh;

        return Results.Json(new JsonObject
        {
            ["access_token"] = access.Token,
            ["token_type"] = "Bearer",
            ["expires_in"] = options.AccessTokenLifetimeSeconds,
            ["refresh_token"] = refresh.Token,
            ["scope"] = scope,
        });
    }

    /// <summary>"AIS:&lt;id&gt;" possibly followed by extra scopes such as "openid".</summary>
    private static string? ConsentIdFromScope(string? scope)
    {
        var first = NormalizeScope(scope ?? string.Empty);
        var parts = first.Split(':', 2);
        return parts.Length == 2 && parts[0] is "AIS" or "ais" && Guid.TryParse(parts[1], out _) ? parts[1] : null;
    }

    private static string NormalizeScope(string scope) => scope.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? string.Empty;

    private static string Pkce(string verifier) =>
        Convert.ToBase64String(SHA256.HashData(Encoding.ASCII.GetBytes(verifier))).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static IResult RedirectWithError(AuthorizeRequest r, string error, string description) =>
        Uri.TryCreate(r.RedirectUri, UriKind.Absolute, out _)
            ? Results.Redirect(AppendQuery(r.RedirectUri!, ("error", error), ("error_description", description), ("state", r.State)))
            : ErrorPage(description);

    private static string AppendQuery(string url, params (string key, string? value)[] items)
    {
        var query = string.Join('&', items.Where(i => i.value is not null).Select(i => $"{i.key}={Uri.EscapeDataString(i.value!)}"));
        return url + (url.Contains('?') ? "&" : "?") + query;
    }

    private static IResult ErrorPage(string message) =>
        Results.Content($"<!doctype html><meta charset=utf-8><title>MockBank error</title><body style=\"font-family:system-ui;margin:40px\"><h2>MockBank: authorization error</h2><p>{WebUtility.HtmlEncode(message)}</p></body>", "text/html", statusCode: 400);

    private static IResult LoginPage(AuthorizeRequest r, Consent consent, MockState state)
    {
        static string E(string? s) => WebUtility.HtmlEncode(s ?? string.Empty);

        var hidden = string.Concat(new (string, string?)[]
        {
            ("response_type", r.ResponseType), ("client_id", r.ClientId), ("redirect_uri", r.RedirectUri), ("scope", r.Scope),
            ("state", r.State), ("code_challenge", r.CodeChallenge), ("code_challenge_method", r.CodeChallengeMethod),
        }.Select(h => $"<input type=hidden name=\"{h.Item1}\" value=\"{E(h.Item2)}\">"));

        var accounts = string.Concat(state.Accounts.Select(a => $"<li>{E(a.Name)} — {E(a.Iban)} ({E(a.Currency)})</li>"));

        return Results.Content($$"""
            <!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <title>MockBank login</title>
            <style>
              body { font: 15px/1.5 system-ui, sans-serif; background: #f4f6f8; margin: 0; padding: 32px 16px; color: #1d1d1f; }
              .card { max-width: 460px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
              h1 { font-size: 20px; margin: 0 0 4px; } .muted { color: #6e6e73; font-size: 13px; }
              button { font: inherit; padding: 10px 18px; border-radius: 8px; border: 0; cursor: pointer; margin-right: 8px; }
              .approve { background: #1a7f37; color: #fff; } .deny { background: #e5e5ea; }
              code { background: #f0f0f3; padding: 1px 4px; border-radius: 4px; }
            </style></head><body><div class="card">
            <h1>MockBank — confirm access</h1>
            <p class="muted">Sandbox PSU <b>testuser1</b> is logged in (no password needed in the mock).</p>
            <p><b>{{E(r.ClientId)}}</b> asks for <code>{{E(consent.ConsentType)}}</code> access
               ({{E(string.Join(", ", consent.Rights))}}) until {{consent.ValidTo:yyyy-MM-dd}} to:</p>
            <ul>{{accounts}}</ul>
            <form method="post" action="/oauth/authorize">{{hidden}}
              <button class="approve" name="decision" value="approve">Approve</button>
              <button class="deny" name="decision" value="deny">Deny</button>
            </form>
            </div></body></html>
            """, "text/html");
    }
}
