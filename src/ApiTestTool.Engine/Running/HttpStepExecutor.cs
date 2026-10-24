using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Signing;

namespace ApiTestTool.Engine.Running;

internal static class HttpStepExecutor
{
    private const int MaxRecordedBodyChars = 256 * 1024;

    public static async Task ExecuteAsync(Step step, RunContext ctx, VariableResolver vars, StepResult result, CancellationToken cancellationToken)
    {
        var spec = step.Request!;
        var url = BuildUrl(spec, ctx, vars);
        var headers = BuildHeaders(spec, ctx, vars);
        var body = BuildBody(spec, ctx, vars);
        var bodyBytes = body is null ? [] : Encoding.UTF8.GetBytes(body);

        if (spec.Auth is not null)
        {
            var token = spec.AuthOptional ? vars.TryResolve(spec.Auth) : vars.Resolve(spec.Auth);
            if (token is not null)
                SetHeader(headers, "Authorization", "Bearer " + token);
            else
                result.Info.Add("Sent without Authorization: no token was obtained");
        }

        var mode = ParseSigningMode(spec.Signing);
        if (mode != SigningMode.None)
        {
            if (ctx.Signer is null)
            {
                result.Warnings.Add("Request sent unsigned: no QSealC configured in the profile (tpp.qsealc)");
            }
            else
            {
                var signature = ctx.Signer.Sign(headers, bodyBytes, ctx.UtcNow(), mode);
                SetHeader(headers, JwsSigner.DigestHeader, signature.Digest);
                SetHeader(headers, JwsSigner.JwsSignatureHeader, signature.JwsSignature);
            }
        }

        using var request = new HttpRequestMessage(new HttpMethod(spec.Method.ToUpperInvariant()), url)
        {
            Version = HttpVersion.Version11,
            VersionPolicy = HttpVersionPolicy.RequestVersionOrLower,
        };

        if (body is not null)
        {
            request.Content = new ByteArrayContent(bodyBytes);
            request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        }

        foreach (var (name, value) in headers)
        {
            if (!request.Headers.TryAddWithoutValidation(name, value) && request.Content is not null)
            {
                request.Content.Headers.Remove(name);
                request.Content.Headers.TryAddWithoutValidation(name, value);
            }
        }

        var exchange = new HttpExchange
        {
            Method = request.Method.Method,
            Url = url.ToString(),
            RequestHeaders = headers.Select(h => KeyValuePair.Create(h.Key, Mask(h.Key, h.Value))).ToList(),
            RequestBody = body,
            StartedAtUtc = ctx.UtcNow(),
        };
        result.Exchange = exchange;

        if (ctx.Profile.Limits.DelayBetweenRequestsMs > 0)
            await Task.Delay(ctx.Profile.Limits.DelayBetweenRequestsMs, cancellationToken).ConfigureAwait(false);

        var sw = Stopwatch.StartNew();
        byte[] responseBytes;
        HttpResponseMessage response;
        try
        {
            response = await ctx.HttpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
            responseBytes = await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !cancellationToken.IsCancellationRequested)
        {
            sw.Stop();
            exchange.DurationMs = sw.ElapsedMilliseconds;
            exchange.Error = Describe(ex);
            result.Failures.Add($"No response from {url.Host}: {exchange.Error}");
            return;
        }

        using (response)
        {
            sw.Stop();
            exchange.DurationMs = sw.ElapsedMilliseconds;
            exchange.StatusCode = (int)response.StatusCode;
            exchange.ResponseHeaders = response.Headers.Concat(response.Content.Headers)
                .Select(h => KeyValuePair.Create(h.Key, string.Join(", ", h.Value)))
                .ToList();

            var responseText = Encoding.UTF8.GetString(responseBytes);
            exchange.ResponseBody = responseText.Length > MaxRecordedBodyChars ? responseText[..MaxRecordedBodyChars] + "…(truncated)" : responseText;

            var json = TryParseJson(responseText, response.Content.Headers.ContentType?.MediaType);
            var requestId = headers.FirstOrDefault(h => h.Key.Equals("X-Request-ID", StringComparison.OrdinalIgnoreCase)).Value;

            ExpectationEvaluator.Evaluate(step.Expect, exchange, json, requestId, responseBytes, ctx, result);
            SaveVariables(step, exchange, json, vars, result);
        }
    }

    private static Uri BuildUrl(RequestSpec spec, RunContext ctx, VariableResolver vars)
    {
        switch (spec.Target)
        {
            case "discovery":
                var discovery = ctx.Profile.Endpoints.Discovery ?? throw new ConfigurationException("Step targets 'discovery' but endpoints.discovery is not set in the profile");
                return new Uri(vars.Resolve(discovery));

            case "absolute":
                return Uri.TryCreate(vars.Resolve(spec.Path), UriKind.Absolute, out var abs)
                    ? abs
                    : throw new ConfigurationException($"Path '{spec.Path}' is not an absolute URL");

            default:
                // "v2/consents" is relative to the XS2A base URL; "/0.8/v1/consents/…" (an href from the bank's _links)
                // is relative to the host, as in RFC 3986; absolute URLs are used as they are.
                var baseUrl = ctx.Profile.Endpoints.Xs2a.EndsWith('/') ? ctx.Profile.Endpoints.Xs2a : ctx.Profile.Endpoints.Xs2a + "/";
                return new Uri(new Uri(baseUrl), vars.Resolve(spec.Path));
        }
    }

    private static List<KeyValuePair<string, string>> BuildHeaders(RequestSpec spec, RunContext ctx, VariableResolver vars)
    {
        var headers = new List<KeyValuePair<string, string>>();

        if (spec.HeadersTemplate is not null)
        {
            foreach (var (name, value) in ctx.Templates.GetHeaders(spec.HeadersTemplate))
            {
                if (value is not null)
                    SetHeader(headers, name, vars.Resolve(JsonPathLite.AsText(value) ?? string.Empty));
            }
        }

        foreach (var (name, value) in spec.Headers ?? [])
        {
            if (value is null)
                headers.RemoveAll(h => h.Key.Equals(name, StringComparison.OrdinalIgnoreCase));
            else
                SetHeader(headers, name, vars.Resolve(JsonPathLite.AsText(value) ?? string.Empty));
        }

        foreach (var name in spec.RemoveHeaders ?? [])
            headers.RemoveAll(h => h.Key.Equals(name, StringComparison.OrdinalIgnoreCase));

        return headers;
    }

    private static string? BuildBody(RequestSpec spec, RunContext ctx, VariableResolver vars)
    {
        if (spec.BodyRaw is not null)
            return vars.Resolve(spec.BodyRaw);

        if (spec.BodyTemplate is null && spec.Body is null)
            return null;

        JsonNode? node = spec.BodyTemplate is null ? null : ctx.Templates.GetBody(spec.BodyTemplate);
        if (spec.Body is not null)
            node = node is JsonObject target && spec.Body is JsonObject patch ? DeepMerge(target, patch) : spec.Body.DeepClone();

        node = vars.Resolve(node);

        foreach (var path in spec.BodyRemove ?? [])
            RemovePath(node, path);

        return node?.ToJsonString() ?? "null";
    }

    /// <summary>Merges <paramref name="patch"/> into <paramref name="target"/>; objects merge recursively, everything else replaces (including null).</summary>
    internal static JsonObject DeepMerge(JsonObject target, JsonObject patch)
    {
        foreach (var (key, value) in patch)
        {
            if (value is JsonObject patchObj && target[key] is JsonObject targetObj)
                DeepMerge(targetObj, patchObj);
            else
                target[key] = value?.DeepClone();
        }

        return target;
    }

    private static void RemovePath(JsonNode? root, string dottedPath)
    {
        var parts = dottedPath.Split('.');
        var node = root;
        for (var i = 0; i < parts.Length - 1 && node is not null; i++)
            node = node is JsonObject o ? o[parts[i]] : null;

        if (node is JsonObject parent)
            parent.Remove(parts[^1]);
    }

    private static void SaveVariables(Step step, HttpExchange exchange, JsonNode? json, VariableResolver vars, StepResult result)
    {
        foreach (var (name, source) in step.Save ?? [])
        {
            string? value;
            if (source.StartsWith("header:", StringComparison.OrdinalIgnoreCase))
            {
                var header = source[7..].Trim();
                value = exchange.ResponseHeaders.FirstOrDefault(h => h.Key.Equals(header, StringComparison.OrdinalIgnoreCase)).Value;
            }
            else
            {
                var matches = JsonPathLite.Select(json, source);
                value = matches.Count == 0 ? null : JsonPathLite.AsText(matches[0]);
            }

            if (value is null)
                result.Failures.Add($"Cannot save '{name}': {source} not found in the response");
            else
                vars.Save(name, value);
        }
    }

    internal static JsonNode? TryParseJson(string text, string? mediaType)
    {
        var trimmed = text.AsSpan().TrimStart();
        var looksLikeJson = mediaType?.Contains("json", StringComparison.OrdinalIgnoreCase) == true
                            || trimmed.StartsWith("{") || trimmed.StartsWith("[");
        if (!looksLikeJson || trimmed.IsEmpty)
            return null;

        try
        {
            return JsonNode.Parse(text);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static void SetHeader(List<KeyValuePair<string, string>> headers, string name, string value)
    {
        var index = headers.FindIndex(h => h.Key.Equals(name, StringComparison.OrdinalIgnoreCase));
        if (index >= 0)
            headers[index] = KeyValuePair.Create(name, value);
        else
            headers.Add(KeyValuePair.Create(name, value));
    }

    private static SigningMode ParseSigningMode(string value) => value switch
    {
        "none" => SigningMode.None,
        "corruptSignature" => SigningMode.CorruptSignature,
        "wrongDigest" => SigningMode.WrongDigest,
        _ => SigningMode.Normal,
    };

    private static string Mask(string name, string value) =>
        name.Equals("Authorization", StringComparison.OrdinalIgnoreCase) && value.Length > 15 ? value[..15] + "…(masked)" : value;

    internal static string Describe(Exception ex)
    {
        if (ex is TaskCanceledException)
            return "timeout";

        // Inner exceptions often repeat the outer message; keep only messages that add something.
        var messages = new List<string>();
        for (var e = ex; e is not null; e = e.InnerException)
        {
            var message = e.Message.Trim();
            if (!messages.Any(m => m.Contains(message, StringComparison.Ordinal)))
                messages.Add(message);
        }

        return string.Join(" -> ", messages);
    }
}
