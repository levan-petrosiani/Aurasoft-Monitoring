namespace ApiTestTool.Store.Entities;

/// <summary>One execution of scenarios against one bank (CLI, portal or imported from a report folder).</summary>
public sealed class RunEntity
{
    /// <summary>The run folder name, e.g. <c>20261022-173008-TBCBGE22-fa2e</c> (portal) or <c>MOCKGE22-20261020-120426</c> (CLI).</summary>
    public required string Id { get; set; }

    /// <summary>cli, portal or import.</summary>
    public required string Source { get; set; }

    /// <summary>Queued, Running, Completed, Failed or Cancelled.</summary>
    public required string Status { get; set; }

    public required string BankBic { get; set; }

    public string? BankName { get; set; }

    public string? Xs2aUrl { get; set; }

    public string? TppId { get; set; }

    public string? ToolVersion { get; set; }

    public DateTime CreatedUtc { get; set; }

    public DateTime? StartedUtc { get; set; }

    public DateTime? FinishedUtc { get; set; }

    public int Passed { get; set; }

    public int Failed { get; set; }

    public int Errors { get; set; }

    public int Blocked { get; set; }

    public int Skipped { get; set; }

    public double PassRate { get; set; }

    /// <summary>Suites requested for the run (empty = all).</summary>
    public List<string> Suites { get; set; } = [];

    /// <summary>Run-level error (configuration problem, crash, cancellation).</summary>
    public string? Error { get; set; }

    /// <summary>Folder with report.html / summary.json / junit.xml, if known.</summary>
    public string? ReportDirectory { get; set; }

    /// <summary>Options the run was started with (portal request), as JSON.</summary>
    public string? OptionsJson { get; set; }

    public List<ScenarioResultEntity> Scenarios { get; set; } = [];
}

public sealed class ScenarioResultEntity
{
    public long Id { get; set; }

    public required string RunId { get; set; }

    /// <summary>Position in the run.</summary>
    public int Ordinal { get; set; }

    public required string ScenarioId { get; set; }

    public required string Title { get; set; }

    public required string Suite { get; set; }

    public required string Severity { get; set; }

    public string? SpecRef { get; set; }

    public string? Origin { get; set; }

    public required string Outcome { get; set; }

    public string? Reason { get; set; }

    public long DurationMs { get; set; }

    public RunEntity? Run { get; set; }

    public List<StepResultEntity> Steps { get; set; } = [];
}

public sealed class StepResultEntity
{
    public long Id { get; set; }

    public long ScenarioResultId { get; set; }

    public required string RunId { get; set; }

    public int Ordinal { get; set; }

    /// <summary>True for cleanup steps (their failures are warnings).</summary>
    public bool IsCleanup { get; set; }

    public required string Name { get; set; }

    /// <summary>request, tls, sca or localCertificates.</summary>
    public required string Kind { get; set; }

    public required string Outcome { get; set; }

    public List<string> Failures { get; set; } = [];

    public List<string> Warnings { get; set; } = [];

    public List<string> Info { get; set; } = [];

    public long DurationMs { get; set; }

    public ScenarioResultEntity? Scenario { get; set; }

    public List<ExchangeEntity> Exchanges { get; set; } = [];
}

/// <summary>One HTTP request/response. Secrets are masked and bodies are size-capped before they are stored.</summary>
public sealed class ExchangeEntity
{
    public long Id { get; set; }

    public long StepResultId { get; set; }

    public required string RunId { get; set; }

    public int Ordinal { get; set; }

    public required string Method { get; set; }

    public required string Url { get; set; }

    public int? StatusCode { get; set; }

    public long DurationMs { get; set; }

    /// <summary>Transport error, if no response was received.</summary>
    public string? Error { get; set; }

    public DateTime StartedUtc { get; set; }

    /// <summary>Headers as a JSON array of [name, value] pairs (jsonb).</summary>
    public string RequestHeaders { get; set; } = "[]";

    public string? RequestBody { get; set; }

    public string ResponseHeaders { get; set; } = "[]";

    public string? ResponseBody { get; set; }

    /// <summary>A body was cut to the configured maximum size.</summary>
    public bool Truncated { get; set; }

    public StepResultEntity? Step { get; set; }
}

/// <summary>Something that happened: run queued/started/finished, scenario completed, import, AI call failed, ...</summary>
public sealed class EventEntity
{
    public long Id { get; set; }

    public DateTime TimeUtc { get; set; }

    public string? RunId { get; set; }

    /// <summary>cli, portal, import, ai.</summary>
    public required string Source { get; set; }

    /// <summary>Dotted name, e.g. <c>run.started</c>, <c>scenario.completed</c>.</summary>
    public required string Type { get; set; }

    /// <summary>Info, Warning or Error.</summary>
    public required string Level { get; set; }

    public required string Message { get; set; }

    /// <summary>Extra details as JSON (jsonb), optional.</summary>
    public string? Data { get; set; }
}

/// <summary>Audit record of one model call: what was sent (masked), what came back, cost drivers, who asked.</summary>
public sealed class AiCallEntity
{
    public Guid Id { get; set; }

    public DateTime TimeUtc { get; set; }

    /// <summary>summary, ask, ruleDrafting, scenarioDrafting, check.</summary>
    public required string Feature { get; set; }

    /// <summary>anthropic or ollama.</summary>
    public required string Provider { get; set; }

    public required string Model { get; set; }

    public string? RunId { get; set; }

    /// <summary>User or process that triggered the call.</summary>
    public string? TriggeredBy { get; set; }

    /// <summary>SHA-256 of the request messages, to spot identical prompts.</summary>
    public required string PromptSha256 { get; set; }

    /// <summary>The (already masked) messages sent, as JSON (jsonb).</summary>
    public required string Request { get; set; }

    public string? Response { get; set; }

    public long? InputTokens { get; set; }

    public long? OutputTokens { get; set; }

    public long DurationMs { get; set; }

    public bool Succeeded { get; set; }

    public string? Error { get; set; }
}
