using System.Text.Json.Serialization;
using ApiTestTool.Engine.Running;

namespace ApiTestTool.Portal.Services;

[JsonConverter(typeof(JsonStringEnumConverter<RunStatus>))]
public enum RunStatus
{
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

/// <summary>What the user asked for on the "New run" page.</summary>
public sealed class RunRequest
{
    public string Bank { get; set; } = string.Empty;

    public List<string> Suites { get; set; } = [];

    public List<string> ScenarioIds { get; set; } = [];

    /// <summary>Host MockBank in the portal for this run (MOCK profile only).</summary>
    public bool UseMockBank { get; set; }

    /// <summary>Complete SCA without a browser (auto-approving test servers only).</summary>
    public bool AutoSca { get; set; }

    public bool PreflightGate { get; set; } = true;
}

/// <summary>Stored next to the reports as run.json; together with summary.json it describes a run.</summary>
public sealed class RunRecord
{
    public required string Id { get; init; }

    public required RunRequest Request { get; init; }

    public RunStatus Status { get; set; }

    public DateTime CreatedUtc { get; init; }

    public DateTime? StartedUtc { get; set; }

    public DateTime? FinishedUtc { get; set; }

    public string? Error { get; set; }

    public string? BankName { get; set; }

    public int Passed { get; set; }

    public int Failed { get; set; }

    public int Errors { get; set; }

    public int Blocked { get; set; }

    public int Skipped { get; set; }

    public double PassRate { get; set; }
}

/// <summary>A run as the UI sees it: the persisted record plus live progress while it executes.</summary>
public sealed class PortalRun
{
    private readonly Lock _gate = new();
    private readonly List<string> _log = [];
    private readonly List<ScenarioResult> _completed = [];

    public PortalRun(RunRecord record, RunResult? result = null)
    {
        Record = record;
        Result = result;
        if (result is not null)
            _completed.AddRange(result.Scenarios);
    }

    public RunRecord Record { get; }

    public string Id => Record.Id;

    public RunResult? Result { get; internal set; }

    public CancellationTokenSource Cancellation { get; } = new();

    /// <summary>Scenario being executed right now.</summary>
    public string? CurrentScenario { get; internal set; }

    public int TotalScenarios { get; internal set; }

    /// <summary>Authorize URL the tester must open (manual SCA) while a step waits for the login.</summary>
    public string? LoginUrl { get; internal set; }

    public event Action? Changed;

    public IReadOnlyList<string> Log
    {
        get { lock (_gate) return [.. _log]; }
    }

    public IReadOnlyList<ScenarioResult> Completed
    {
        get { lock (_gate) return [.. _completed]; }
    }

    public bool IsActive => Record.Status is RunStatus.Queued or RunStatus.Running;

    internal void AddLog(string message)
    {
        lock (_gate)
            _log.Add($"{DateTime.Now:HH:mm:ss}  {message}");
        NotifyChanged();
    }

    internal void AddCompleted(ScenarioResult result)
    {
        lock (_gate)
            _completed.Add(result);
        NotifyChanged();
    }

    internal void NotifyChanged() => Changed?.Invoke();
}
