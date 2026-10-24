using System.Text.Json;
using System.Text.Json.Serialization;
using ApiTestTool.Engine.Running;
using ApiTestTool.Store.Entities;
using ApiTestTool.Store.Masking;

namespace ApiTestTool.Store;

/// <summary>Turns the engine's <see cref="RunResult"/> into store rows, masking and capping exchanges on the way.</summary>
internal static class RunMapper
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static RunEntity ToEntity(RunInfo info, RunResult? result, SensitiveDataMasker masker, int maxBodyChars)
    {
        var run = new RunEntity
        {
            Id = info.Id,
            Source = info.Source,
            Status = info.Status,
            BankBic = info.BankBic,
            BankName = info.BankName ?? result?.BankName,
            Xs2aUrl = result?.Xs2aUrl,
            TppId = result?.TppId,
            ToolVersion = result?.ToolVersion,
            CreatedUtc = Utc(info.CreatedUtc),
            StartedUtc = Utc(info.StartedUtc ?? result?.StartedAtUtc),
            FinishedUtc = Utc(info.FinishedUtc ?? (result is { FinishedAtUtc: var f } && f != default ? f : null)),
            Suites = [.. info.Suites],
            Error = info.Error,
            ReportDirectory = info.ReportDirectory,
            OptionsJson = info.OptionsJson,
        };

        if (result is null)
            return run;

        run.Passed = result.Count(Outcome.Passed);
        run.Failed = result.Count(Outcome.Failed);
        run.Errors = result.Count(Outcome.Error);
        run.Blocked = result.Count(Outcome.Blocked);
        run.Skipped = result.Count(Outcome.Skipped);
        run.PassRate = result.PassRate;

        var ordinal = 0;
        foreach (var s in result.Scenarios)
        {
            var scenario = new ScenarioResultEntity
            {
                RunId = info.Id,
                Ordinal = ordinal++,
                ScenarioId = s.Id,
                Title = s.Title,
                Suite = s.Suite,
                Severity = s.Severity,
                SpecRef = s.SpecRef,
                Origin = s.Origin,
                Outcome = s.Outcome.ToString(),
                Reason = s.Reason,
                DurationMs = s.DurationMs,
            };

            var stepOrdinal = 0;
            foreach (var (step, isCleanup) in s.Steps.Select(st => (st, false)).Concat(s.Cleanup.Select(st => (st, true))))
            {
                var stepEntity = new StepResultEntity
                {
                    RunId = info.Id,
                    Ordinal = stepOrdinal++,
                    IsCleanup = isCleanup,
                    Name = step.Name,
                    Kind = step.Kind,
                    Outcome = step.Outcome.ToString(),
                    Failures = [.. step.Failures],
                    Warnings = [.. step.Warnings],
                    Info = [.. step.Info],
                    DurationMs = step.DurationMs,
                };

                var exchangeOrdinal = 0;
                foreach (var exchange in (step.Exchange is null ? [] : new[] { step.Exchange }).Concat(step.Exchanges))
                    stepEntity.Exchanges.Add(ToEntity(info.Id, exchangeOrdinal++, exchange, masker, maxBodyChars));

                scenario.Steps.Add(stepEntity);
            }

            run.Scenarios.Add(scenario);
        }

        return run;
    }

    private static ExchangeEntity ToEntity(string runId, int ordinal, HttpExchange x, SensitiveDataMasker masker, int maxBodyChars)
    {
        var (requestBody, requestCut) = SensitiveDataMasker.Cap(masker.MaskBody(x.RequestBody), maxBodyChars);
        var (responseBody, responseCut) = SensitiveDataMasker.Cap(masker.MaskBody(x.ResponseBody), maxBodyChars);

        return new ExchangeEntity
        {
            RunId = runId,
            Ordinal = ordinal,
            Method = x.Method,
            Url = masker.MaskUrl(x.Url),
            StatusCode = x.StatusCode,
            DurationMs = x.DurationMs,
            Error = x.Error,
            StartedUtc = Utc(x.StartedAtUtc),
            RequestHeaders = HeadersJson(masker.MaskHeaders(x.RequestHeaders)),
            RequestBody = requestBody,
            ResponseHeaders = HeadersJson(masker.MaskHeaders(x.ResponseHeaders)),
            ResponseBody = responseBody,
            Truncated = requestCut || responseCut,
        };
    }

    /// <summary>Headers as [[name, value], ...] — keeps order and repeated names.</summary>
    private static string HeadersJson(List<KeyValuePair<string, string>> headers) =>
        JsonSerializer.Serialize(headers.Select(h => new[] { h.Key, h.Value }), Json);

    private static DateTime Utc(DateTime value) => value.Kind switch
    {
        DateTimeKind.Utc => value,
        DateTimeKind.Local => value.ToUniversalTime(),
        _ => DateTime.SpecifyKind(value, DateTimeKind.Utc),
    };

    private static DateTime? Utc(DateTime? value) => value is { } v ? Utc(v) : null;
}
