using System.Text.Json;
using System.Threading.Channels;
using ApiTestTool.Engine.Running;
using ApiTestTool.Store;

namespace ApiTestTool.Portal.Services;

/// <summary>
/// Writes portal runs and events to the PostgreSQL store in the background, one write at a time. Callers never
/// wait for the database and a database problem never affects a run: it is logged, and the run folder can be
/// imported later with <c>aqt db import</c>.
/// </summary>
public sealed class StoreRecorder(AqtStore store, ILogger<StoreRecorder> logger) : BackgroundService
{
    private readonly Channel<(string What, Func<CancellationToken, Task> Write)> _queue =
        Channel.CreateUnbounded<(string, Func<CancellationToken, Task>)>(new UnboundedChannelOptions { SingleReader = true });

    public AqtStore Store => store;

    /// <summary>Saves the run's current state (status, counts and, once finished, all results).</summary>
    public void SaveRun(PortalRun run, string reportDirectory)
    {
        // Snapshot now: the run object keeps changing while the write waits in the queue.
        var info = ToInfo(run, reportDirectory);
        var result = run.Record.Status is RunStatus.Queued or RunStatus.Running ? null : run.Result;
        Enqueue($"save run {run.Id}", ct => store.SaveRunAsync(info, result, ct));
    }

    public void Event(string type, string message, string? runId = null, string level = "Info", object? data = null) =>
        Enqueue($"event {type}", ct => store.AddEventAsync("portal", type, message, runId, level, data, ct));

    public void DeleteRun(string runId) =>
        Enqueue($"delete run {runId}", ct => store.DeleteRunAsync(runId, ct));

    /// <summary>Runs <paramref name="action"/> after every write queued so far (e.g. summarize once the run is saved).</summary>
    public void Then(string what, Action action) =>
        Enqueue(what, _ => { action(); return Task.CompletedTask; });

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await foreach (var (what, write) in _queue.Reader.ReadAllAsync(stoppingToken))
        {
            try
            {
                await write(stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogWarning(ex, "Store write failed ({What}); the run folder can be imported later with 'aqt db import'", what);
            }
        }
    }

    private void Enqueue(string what, Func<CancellationToken, Task> write) => _queue.Writer.TryWrite((what, write));

    internal static RunInfo ToInfo(PortalRun run, string reportDirectory)
    {
        var record = run.Record;
        return new RunInfo
        {
            Id = record.Id,
            Source = "portal",
            Status = record.Status.ToString(),
            BankBic = run.Result?.BankBic ?? (record.Id.Split('-') is { Length: 4 } parts ? parts[2] : record.Request.Bank),
            BankName = record.BankName,
            CreatedUtc = record.CreatedUtc,
            StartedUtc = record.StartedUtc,
            FinishedUtc = record.FinishedUtc,
            Suites = [.. record.Request.Suites],
            Error = record.Error,
            ReportDirectory = reportDirectory,
            OptionsJson = JsonSerializer.Serialize(record.Request, RunService.JsonOptions),
        };
    }

    internal static string Level(Outcome outcome) => outcome switch
    {
        Outcome.Failed or Outcome.Error => "Warning",
        _ => "Info",
    };
}
