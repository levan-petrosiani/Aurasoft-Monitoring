using System.Text.Json.Serialization;

namespace ApiTestTool.Engine.Running;

[JsonConverter(typeof(JsonStringEnumConverter<Outcome>))]
public enum Outcome
{
    /// <summary>All expectations met.</summary>
    Passed,

    /// <summary>The bank's API did not behave as expected.</summary>
    Failed,

    /// <summary>Not executed because an earlier step failed.</summary>
    Blocked,

    /// <summary>Not applicable: the bank does not support a required feature.</summary>
    Skipped,

    /// <summary>Problem on our side: configuration, certificate, unknown variable.</summary>
    Error,
}

public sealed class HttpExchange
{
    public required string Method { get; init; }

    public required string Url { get; init; }

    public required List<KeyValuePair<string, string>> RequestHeaders { get; init; }

    public string? RequestBody { get; init; }

    public int? StatusCode { get; set; }

    public List<KeyValuePair<string, string>> ResponseHeaders { get; set; } = [];

    public string? ResponseBody { get; set; }

    public long DurationMs { get; set; }

    /// <summary>Transport error (connection refused, TLS failure, timeout), if the request did not get a response.</summary>
    public string? Error { get; set; }

    public DateTime StartedAtUtc { get; init; }
}

public sealed class StepResult
{
    public required string Name { get; init; }

    public required string Kind { get; init; }

    public Outcome Outcome { get; set; }

    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<string> Failures { get; } = [];

    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<string> Warnings { get; } = [];

    /// <summary>Facts the step observed, e.g. negotiated TLS protocol or certificate expiry.</summary>
    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<string> Info { get; } = [];

    public HttpExchange? Exchange { get; set; }

    /// <summary>Further calls made by the step (e.g. discovery and token requests during SCA).</summary>
    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<HttpExchange> Exchanges { get; } = [];

    public long DurationMs { get; set; }

    /// <summary>The step could not be completed because of something on our side (tester, network to localhost, config).</summary>
    [JsonIgnore]
    public bool IsToolError { get; set; }
}

public sealed class ScenarioResult
{
    public required string Id { get; init; }

    public required string Title { get; init; }

    public required string Suite { get; init; }

    public required string Severity { get; init; }

    public string? SpecRef { get; init; }

    public string? Origin { get; init; }

    public string? Source { get; init; }

    public Outcome Outcome { get; set; }

    /// <summary>Short explanation for non-passed outcomes.</summary>
    public string? Reason { get; set; }

    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<StepResult> Steps { get; } = [];

    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<StepResult> Cleanup { get; } = [];

    public long DurationMs { get; set; }

    public IEnumerable<string> AllWarnings => Steps.Concat(Cleanup).SelectMany(s => s.Warnings);
}

public sealed class RunResult
{
    public required string ToolVersion { get; init; }

    public required string BankBic { get; init; }

    public required string BankName { get; init; }

    public required string Xs2aUrl { get; init; }

    public string? TppId { get; init; }

    public DateTime StartedAtUtc { get; init; }

    public DateTime FinishedAtUtc { get; set; }

    [JsonObjectCreationHandling(JsonObjectCreationHandling.Populate)]
    public List<ScenarioResult> Scenarios { get; } = [];

    public int Count(Outcome outcome) => Scenarios.Count(s => s.Outcome == outcome);

    /// <summary>Share of executed scenarios (passed + failed) that passed, in percent.</summary>
    public double PassRate
    {
        get
        {
            var executed = Count(Outcome.Passed) + Count(Outcome.Failed);
            return executed == 0 ? 0 : Math.Round(100.0 * Count(Outcome.Passed) / executed, 1);
        }
    }
}
