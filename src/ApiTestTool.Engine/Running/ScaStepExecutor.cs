using System.Text.Json.Nodes;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Scenarios;

namespace ApiTestTool.Engine.Running;

/// <summary>
/// Manual SCA through the bank's OAuth2 server (authorization code + PKCE). Failures of the bank's auth server are
/// <c>Failed</c>; problems on our side (no callback in time, port in use, missing configuration) are <c>Error</c>.
/// </summary>
internal static class ScaStepExecutor
{
    public static async Task ExecuteAsync(Step step, RunContext ctx, VariableResolver vars, StepResult result, CancellationToken cancellationToken)
    {
        var spec = step.Sca!;
        var oauth = new OAuthClient(ctx.HttpClient, ctx.UtcNow);

        var discovery = spec.Discovery is not null ? vars.Resolve(spec.Discovery)
            : ctx.Profile.Endpoints.Discovery ?? throw new ConfigurationException("sca.discovery is not set and the profile has no endpoints.discovery");
        var scope = vars.Resolve(spec.Scope);
        var clientId = spec.ClientId is not null ? vars.Resolve(spec.ClientId)
            : ctx.TppId ?? throw new ConfigurationException("sca needs a client_id: configure a QWAC with an organizationIdentifier or set sca.clientId");
        var redirectUri = vars.Resolve(spec.RedirectUri ?? ctx.Profile.Tpp.RedirectUri);
        var quirks = OAuthQuirks.From(ctx.Profile.Quirks);

        // 1. Discovery
        var metadata = await oauth.GetMetadataAsync(discovery, cancellationToken).ConfigureAwait(false);
        Record(result, metadata.Exchange);
        if (!CheckCall(metadata, "Discovery document", result))
            return;

        var authorizationEndpoint = Text(metadata.Json, "authorization_endpoint");
        var tokenEndpoint = Text(metadata.Json, "token_endpoint");
        if (authorizationEndpoint is null || tokenEndpoint is null)
        {
            result.Failures.Add("Discovery document has no authorization_endpoint and/or token_endpoint");
            return;
        }

        if (metadata.Json?["code_challenge_methods_supported"] is JsonArray methods && !methods.Any(m => m?.ToString() == Pkce.Method))
            result.Warnings.Add("Discovery document does not list S256 in code_challenge_methods_supported");

        // 2. Authorize in the browser, wait for the callback
        var verifier = Pkce.CreateVerifier();
        var state = Pkce.CreateState(scope.Split(':')[0]);
        var authorizeUrl = OAuthClient.BuildAuthorizeUrl(authorizationEndpoint, clientId, redirectUri, scope, state, Pkce.CreateChallenge(verifier), quirks);
        result.Info.Add($"Authorize URL: {authorizeUrl}");

        OAuthCallback callback;
        await using (var listener = OAuthCallbackListener.Start(redirectUri))
        {
            ctx.Notify($"Log in with a sandbox PSU to authorise '{scope}'. Waiting up to {spec.TimeoutSeconds} s for {redirectUri} ...");
            ctx.Notify($"If no browser opened, open: {authorizeUrl}");
            await ctx.OpenBrowser(authorizeUrl, cancellationToken).ConfigureAwait(false);

            try
            {
                callback = await listener.WaitAsync(TimeSpan.FromSeconds(spec.TimeoutSeconds), cancellationToken).ConfigureAwait(false);
            }
            catch (TimeoutException ex)
            {
                result.Failures.Add($"{ex.Message} — SCA was not completed in the browser");
                result.IsToolError = true;
                return;
            }
        }

        if (callback.Error is not null)
        {
            result.Failures.Add($"Auth server redirected with error '{callback.Error}'{(callback.ErrorDescription is null ? "" : $": {callback.ErrorDescription}")}");
            return;
        }

        if (!string.Equals(callback.State, state, StringComparison.Ordinal))
        {
            result.Failures.Add($"Callback state '{callback.State}' does not match the state we sent");
            return;
        }

        if (string.IsNullOrEmpty(callback.Code))
        {
            result.Failures.Add("Callback has no authorization code");
            return;
        }

        // 3. Code → token
        var token = await oauth.ExchangeCodeAsync(tokenEndpoint, clientId, callback.Code, redirectUri, verifier, cancellationToken).ConfigureAwait(false);
        Record(result, token.Exchange);
        if (!CheckCall(token, "Token request", result) || !CheckTokenResponse(token.Json, spec, vars, result, scope, "Access token received"))
            return;

        // 4. Optional refresh
        if (!spec.Refresh)
            return;

        if (!vars.Saved.TryGetValue(spec.SaveRefreshTokenAs, out var refreshToken))
        {
            result.Warnings.Add("Refresh not tested: the token response has no refresh_token");
            return;
        }

        var refreshed = await oauth.RefreshAsync(tokenEndpoint, clientId, refreshToken, cancellationToken).ConfigureAwait(false);
        Record(result, refreshed.Exchange);
        if (CheckCall(refreshed, "Refresh token request", result))
            CheckTokenResponse(refreshed.Json, spec, vars, result, scope, "Access token refreshed");
    }

    private static bool CheckCall(OAuthCallResult call, string what, StepResult result)
    {
        if (call.Exchange.Error is not null)
        {
            result.Failures.Add($"{what}: no response from {new Uri(call.Exchange.Url).Host}: {call.Exchange.Error}");
            return false;
        }

        if (!call.IsSuccess)
        {
            var error = Text(call.Json, "error");
            var description = Text(call.Json, "error_description");
            result.Failures.Add($"{what}: status {call.Exchange.StatusCode}{(error is null ? "" : $", error '{error}'")}{(description is null ? "" : $": {description}")}");
            return false;
        }

        if (call.Json is not JsonObject)
        {
            result.Failures.Add($"{what}: response is not a JSON object");
            return false;
        }

        return true;
    }

    private static bool CheckTokenResponse(JsonNode? json, ScaSpec spec, VariableResolver vars, StepResult result, string scope, string label)
    {
        var accessToken = Text(json, "access_token");
        if (string.IsNullOrEmpty(accessToken))
        {
            result.Failures.Add("Token response has no access_token");
            return false;
        }

        vars.Save(spec.SaveAccessTokenAs, accessToken);

        if (Text(json, "refresh_token") is { Length: > 0 } refresh)
            vars.Save(spec.SaveRefreshTokenAs, refresh);

        var tokenType = Text(json, "token_type");
        if (!string.Equals(tokenType, "Bearer", StringComparison.OrdinalIgnoreCase))
            result.Warnings.Add($"token_type is '{tokenType ?? "(missing)"}', expected 'Bearer'");

        if (json?["expires_in"] is null)
            result.Warnings.Add("Token response has no expires_in");

        var expectInScope = spec.ExpectScopeContains is null ? scope.Split(':').Last() : vars.Resolve(spec.ExpectScopeContains);
        if (Text(json, "scope") is { } grantedScope && !grantedScope.Contains(expectInScope, StringComparison.Ordinal))
            result.Warnings.Add($"Granted scope '{grantedScope}' does not contain '{expectInScope}'");

        result.Info.Add($"{label} ({(json?["expires_in"] is { } exp ? $"expires in {exp} s" : "no expiry")})");
        return true;
    }

    private static void Record(StepResult result, HttpExchange exchange)
    {
        if (result.Exchange is null)
            result.Exchange = exchange;
        else
            result.Exchanges.Add(exchange);
    }

    private static string? Text(JsonNode? json, string name) =>
        json is JsonObject obj && obj[name] is { } value ? JsonPathLite.AsText(value) : null;
}
