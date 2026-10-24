using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Running;

namespace ApiTestTool.Engine.OAuth;

/// <summary>Result of one call to the bank's auth server, recorded like any other exchange.</summary>
public sealed record OAuthCallResult(HttpExchange Exchange, JsonNode? Json)
{
    public bool IsSuccess => Exchange.StatusCode is >= 200 and < 300;
}

/// <summary>
/// The OAuth2 pieces the TestTool's OAuth2ClientService provides, over the run's mTLS HttpClient:
/// discovery metadata, the authorization URL (PKCE), code exchange and refresh at the token endpoint.
/// </summary>
public sealed class OAuthClient
{
    private const string WellKnownOAuth = "/.well-known/oauth-authorization-server";

    private readonly HttpClient _http;
    private readonly Func<DateTime> _utcNow;

    public OAuthClient(HttpClient http, Func<DateTime>? utcNow = null)
    {
        _http = http;
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>
    /// A discovery URL without "/.well-known" gets "/.well-known/oauth-authorization-server" appended, as in
    /// OAuth2ClientService. XS2A returns such base URLs in <c>_links.scaOAuth</c>.
    /// </summary>
    public static string NormalizeDiscoveryUrl(string url)
    {
        if (url.Contains("/.well-known", StringComparison.OrdinalIgnoreCase))
            return url;

        return url.TrimEnd('/') + WellKnownOAuth;
    }

    public Task<OAuthCallResult> GetMetadataAsync(string discoveryUrl, CancellationToken cancellationToken)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, NormalizeDiscoveryUrl(discoveryUrl));
        return SendAsync(request, null, cancellationToken);
    }

    /// <summary>Authorization URL with PKCE. Parameter order follows Psd2Session.GetOAuthUrlAsync.</summary>
    public static string BuildAuthorizeUrl(string authorizationEndpoint, string clientId, string redirectUri, string scope, string state, string codeChallenge, OAuthQuirks quirks)
    {
        var query = new List<KeyValuePair<string, string>>
        {
            new("redirect_uri", redirectUri),
            new("response_type", "code"),
            new("client_id", clientId),
            new("code_challenge", codeChallenge),
            new("code_challenge_method", Pkce.Method),
            new("state", state),
            new("scope", scope + (quirks.ScopeSuffix ?? string.Empty)),
        };

        if (quirks.Nonce is not null)
            query.Add(new("nonce", quirks.Nonce));
        if (quirks.UiLocales is not null)
            query.Add(new("ui_locales", quirks.UiLocales));
        foreach (var (key, value) in quirks.ExtraParams)
            query.Add(new(key, value));

        var builder = new UriBuilder(authorizationEndpoint);
        var existing = builder.Query.TrimStart('?');
        builder.Query = string.Join('&', new[] { existing }.Where(e => e.Length > 0).Concat(query.Select(p => $"{Uri.EscapeDataString(p.Key)}={Uri.EscapeDataString(p.Value)}")));
        return builder.Uri.AbsoluteUri;
    }

    public Task<OAuthCallResult> ExchangeCodeAsync(string tokenEndpoint, string clientId, string code, string redirectUri, string codeVerifier, CancellationToken cancellationToken) =>
        PostFormAsync(tokenEndpoint,
        [
            new("client_id", clientId),
            new("grant_type", "authorization_code"),
            new("code", code),
            new("redirect_uri", redirectUri),
            new("code_verifier", codeVerifier),
        ], cancellationToken);

    public Task<OAuthCallResult> RefreshAsync(string tokenEndpoint, string clientId, string refreshToken, CancellationToken cancellationToken) =>
        PostFormAsync(tokenEndpoint,
        [
            new("client_id", clientId),
            new("grant_type", "refresh_token"),
            new("refresh_token", refreshToken),
        ], cancellationToken);

    private Task<OAuthCallResult> PostFormAsync(string url, List<KeyValuePair<string, string>> form, CancellationToken cancellationToken)
    {
        var body = string.Join('&', form.Select(p => $"{Uri.EscapeDataString(p.Key)}={Uri.EscapeDataString(p.Value)}"));
        var request = new HttpRequestMessage(HttpMethod.Post, url)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/x-www-form-urlencoded"),
        };

        // Never keep the authorization code or refresh token in reports.
        var recorded = string.Join('&', form.Select(p => p.Key is "code" or "refresh_token" or "code_verifier" ? $"{p.Key}=***" : $"{p.Key}={p.Value}"));
        return SendAsync(request, recorded, cancellationToken);
    }

    private async Task<OAuthCallResult> SendAsync(HttpRequestMessage request, string? recordedBody, CancellationToken cancellationToken)
    {
        using (request)
        {
            request.Version = HttpVersion.Version11;
            request.VersionPolicy = HttpVersionPolicy.RequestVersionOrLower;

            var exchange = new HttpExchange
            {
                Method = request.Method.Method,
                Url = request.RequestUri!.ToString(),
                RequestHeaders = request.Content?.Headers.Select(h => KeyValuePair.Create(h.Key, string.Join(", ", h.Value))).ToList() ?? [],
                RequestBody = recordedBody,
                StartedAtUtc = _utcNow(),
            };

            var sw = Stopwatch.StartNew();
            try
            {
                using var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
                var text = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                exchange.DurationMs = sw.ElapsedMilliseconds;
                exchange.StatusCode = (int)response.StatusCode;
                exchange.ResponseHeaders = response.Headers.Concat(response.Content.Headers)
                    .Select(h => KeyValuePair.Create(h.Key, string.Join(", ", h.Value)))
                    .ToList();

                var json = HttpStepExecutor.TryParseJson(text, response.Content.Headers.ContentType?.MediaType);
                exchange.ResponseBody = MaskTokens(json) ?? text;
                return new OAuthCallResult(exchange, json);
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !cancellationToken.IsCancellationRequested)
            {
                exchange.DurationMs = sw.ElapsedMilliseconds;
                exchange.Error = HttpStepExecutor.Describe(ex);
                return new OAuthCallResult(exchange, null);
            }
        }
    }

    /// <summary>Token responses are recorded with the token values shortened.</summary>
    private static string? MaskTokens(JsonNode? json)
    {
        if (json is not JsonObject obj || !obj.ContainsKey("access_token"))
            return null;

        var copy = (JsonObject)obj.DeepClone();
        foreach (var key in new[] { "access_token", "refresh_token", "id_token" })
        {
            if (copy[key]?.ToString() is { Length: > 0 } value)
                copy[key] = value.Length > 12 ? value[..12] + "…(masked)" : "***";
        }

        return copy.ToJsonString();
    }
}
