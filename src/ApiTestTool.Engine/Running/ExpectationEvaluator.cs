using System.Text.Json.Nodes;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Signing;

namespace ApiTestTool.Engine.Running;

internal static class ExpectationEvaluator
{
    public static void Evaluate(Expectation? expect, HttpExchange exchange, JsonNode? json, string? requestId, byte[] responseBytes, RunContext ctx, StepResult result)
    {
        var status = exchange.StatusCode!.Value;
        var expectedStatuses = ReadStatuses(expect?.Status);

        // Status
        if (expectedStatuses.Count > 0)
        {
            if (!expectedStatuses.Contains(status))
                result.Failures.Add($"Status {status}, expected {string.Join(" or ", expectedStatuses)}");
        }
        else if (status >= 500)
        {
            result.Failures.Add($"Server error: status {status}");
        }

        if (expect is not null)
        {
            CheckHeaders(expect, exchange, requestId, result);
            CheckContentType(expect, exchange, result);
            CheckJson(expect, json, exchange, result);
            CheckErrorCode(expect, json, result);
        }

        // Response time
        var slowMs = expect?.MaxDurationMs ?? ctx.Profile.Limits.SlowResponseMs;
        if (exchange.DurationMs > slowMs)
            result.Warnings.Add($"Slow response: {exchange.DurationMs} ms (limit {slowMs} ms)");

        CheckResponseSignature(expect, exchange, responseBytes, ctx, result);
    }

    private static List<int> ReadStatuses(JsonNode? node) => node switch
    {
        null => [],
        JsonArray arr => arr.Select(n => n!.GetValue<long>()).Select(v => (int)v).ToList(),
        JsonValue v => [(int)v.GetValue<long>()],
        _ => throw new ConfigurationException("expect.status must be a number or a list of numbers"),
    };

    private static void CheckHeaders(Expectation expect, HttpExchange exchange, string? requestId, StepResult result)
    {
        foreach (var name in expect.Headers ?? [])
        {
            if (FindHeader(exchange, name) is null)
                result.Failures.Add($"Response header '{name}' is missing");
        }

        if (expect.EchoRequestId)
        {
            var echoed = FindHeader(exchange, "X-Request-ID");
            if (echoed is null)
                result.Failures.Add("Response X-Request-ID is missing");
            else if (requestId is not null && !string.Equals(echoed, requestId, StringComparison.OrdinalIgnoreCase))
                result.Failures.Add($"Response X-Request-ID '{echoed}' does not match request '{requestId}'");
        }
    }

    private static void CheckContentType(Expectation expect, HttpExchange exchange, StepResult result)
    {
        if (expect.ContentType is null)
            return;

        var actual = FindHeader(exchange, "Content-Type");
        if (actual is null || !actual.StartsWith(expect.ContentType, StringComparison.OrdinalIgnoreCase))
            result.Failures.Add($"Content-Type '{actual ?? "(none)"}', expected '{expect.ContentType}'");
    }

    private static void CheckJson(Expectation expect, JsonNode? json, HttpExchange exchange, StepResult result)
    {
        var needsJson = expect.Json is { Count: > 0 } || expect.Exists is { Count: > 0 } || expect.WarnIfMissing is { Count: > 0 };
        if (needsJson && json is null)
        {
            result.Failures.Add(string.IsNullOrEmpty(exchange.ResponseBody) ? "Response body is empty, JSON expected" : "Response body is not valid JSON");
            return;
        }

        foreach (var (path, expected) in expect.Json ?? [])
        {
            var matches = JsonPathLite.Select(json, path);
            var expectedText = JsonPathLite.AsText(expected);
            if (matches.Count == 0)
                result.Failures.Add($"{path} is missing, expected '{expectedText}'");
            else if (!matches.Any(m => JsonPathLite.AsText(m) == expectedText))
                result.Failures.Add($"{path} is '{string.Join("', '", matches.Select(JsonPathLite.AsText))}', expected '{expectedText}'");
        }

        foreach (var path in expect.Exists ?? [])
        {
            if (!JsonPathLite.Exists(json, path))
                result.Failures.Add($"{path} is missing");
        }

        foreach (var path in expect.NotExists ?? [])
        {
            if (json is not null && JsonPathLite.Exists(json, path))
                result.Failures.Add($"{path} must not be present");
        }

        foreach (var path in expect.WarnIfMissing ?? [])
        {
            if (!JsonPathLite.Exists(json, path))
                result.Warnings.Add($"{path} is missing");
        }
    }

    private static void CheckErrorCode(Expectation expect, JsonNode? json, StepResult result)
    {
        if (expect.ErrorCode is null)
            return;

        var codes = new[] { "$.code", "$.additionalErrors[*].code", "$.tppMessages[*].code" }
            .SelectMany(p => JsonPathLite.Select(json, p))
            .Select(JsonPathLite.AsText)
            .Where(c => c is not null && c != "null")
            .Distinct()
            .ToList();

        if (codes.Count == 0)
            result.Failures.Add(expect.ErrorCode == "*" ? "No error code in the response body" : $"No error code in the response body, expected '{expect.ErrorCode}'");
        else if (expect.ErrorCode != "*" && !codes.Contains(expect.ErrorCode, StringComparer.OrdinalIgnoreCase))
            result.Failures.Add($"Error code '{string.Join("', '", codes)}', expected '{expect.ErrorCode}'");
    }

    private static void CheckResponseSignature(Expectation? expect, HttpExchange exchange, byte[] responseBytes, RunContext ctx, StepResult result)
    {
        var mode = expect?.ResponseSignature ?? ctx.Profile.ResponseSignature;
        if (mode == "ignore")
            return;

        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in exchange.ResponseHeaders)
            headers[k] = v;

        var verification = JwsVerifier.VerifyResponse(headers, responseBytes, exchange.StatusCode!.Value, ctx.UtcNow());

        if (!verification.IsPresent)
        {
            if (mode == "required")
                result.Failures.Add("Response is not signed (X-Jws-Signature missing)");
            else
                result.Info.Add("Response is not signed");
            return;
        }

        // A signature that is present must be correct, whatever the mode.
        result.Failures.AddRange(verification.Errors.Select(e => "Response signature: " + e));
        result.Warnings.AddRange(verification.Warnings.Select(w => "Response signature: " + w));
        if (verification.IsValid)
            result.Info.Add($"Response signature valid (signer {verification.SignerOrganizationId ?? verification.SignerSubject})");
    }

    private static string? FindHeader(HttpExchange exchange, string name) =>
        exchange.ResponseHeaders.FirstOrDefault(h => h.Key.Equals(name, StringComparison.OrdinalIgnoreCase)).Value;
}
