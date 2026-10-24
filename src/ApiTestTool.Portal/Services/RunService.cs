using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using System.Threading.Channels;
using ApiTestTool.Engine;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Reporting;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Scenarios;
using Microsoft.Extensions.Options;

namespace ApiTestTool.Portal.Services;

/// <summary>
/// Executes runs one at a time (manual SCA listens on one fixed localhost callback port), keeps live progress for
/// the UI, and stores each run in its own folder: run.json, summary.json, report.html, junit.xml.
/// </summary>
public sealed class RunService : BackgroundService
{
    public static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
    };

    private readonly Channel<PortalRun> _queue = Channel.CreateUnbounded<PortalRun>();
    private readonly ConcurrentDictionary<string, PortalRun> _runs = new();
    private readonly CatalogService _catalog;
    private readonly MockBankService _mockBank;
    private readonly ILogger<RunService> _logger;
    private readonly StoreRecorder? _store;
    private readonly SummaryService? _summaries;

    /// <param name="store">Set when a database is configured (<c>ConnectionStrings:Aqt</c>); runs are then also written to PostgreSQL.</param>
    public RunService(CatalogService catalog, MockBankService mockBank, IOptions<PortalOptions> options, IWebHostEnvironment env, ILogger<RunService> logger, StoreRecorder? store = null, SummaryService? summaries = null)
    {
        _catalog = catalog;
        _mockBank = mockBank;
        _logger = logger;
        _store = store;
        _summaries = summaries;
        DataDirectory = Path.GetFullPath(Path.Combine(env.ContentRootPath, options.Value.DataDirectory));
        Directory.CreateDirectory(DataDirectory);
        LoadHistory();
    }

    public string DataDirectory { get; }

    /// <summary>Raised when a run is added or changes status (not for every progress tick).</summary>
    public event Action? RunsChanged;

    public IReadOnlyList<PortalRun> Runs => [.. _runs.Values.OrderByDescending(r => r.Record.CreatedUtc)];

    public PortalRun? Get(string id) => _runs.TryGetValue(id, out var run) ? run : null;

    public string RunDirectory(string id) => Path.Combine(DataDirectory, id);

    public PortalRun Enqueue(RunRequest request)
    {
        var profile = _catalog.LoadBank(request.Bank); // validates the bank before queueing
        var record = new RunRecord
        {
            Id = $"{DateTime.UtcNow:yyyyMMdd-HHmmss}-{profile.Bank.Bic}-{Guid.NewGuid().ToString()[..4]}",
            Request = request,
            Status = RunStatus.Queued,
            CreatedUtc = DateTime.UtcNow,
            BankName = profile.Bank.Name,
        };

        var run = new PortalRun(record);
        _runs[run.Id] = run;
        Save(run);
        _store?.SaveRun(run, RunDirectory(run.Id));
        _store?.Event("run.queued", $"{record.BankName} ({profile.Bank.Bic}) queued", run.Id, data: request);
        _queue.Writer.TryWrite(run);
        RunsChanged?.Invoke();
        return run;
    }

    public void Cancel(string id)
    {
        if (Get(id) is { IsActive: true } run)
            run.Cancellation.Cancel();
    }

    public bool Delete(string id)
    {
        if (Get(id) is not { IsActive: false } run)
            return false;

        _runs.TryRemove(run.Id, out _);
        var dir = RunDirectory(id);
        if (Directory.Exists(dir))
            Directory.Delete(dir, recursive: true);
        _store?.DeleteRun(id);
        _store?.Event("run.deleted", $"Run {id} deleted by the user", id);
        RunsChanged?.Invoke();
        return true;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await foreach (var run in _queue.Reader.ReadAllAsync(stoppingToken))
        {
            if (run.Cancellation.IsCancellationRequested)
            {
                Finish(run, RunStatus.Cancelled, "Cancelled before it started");
                continue;
            }

            using var linked = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken, run.Cancellation.Token);
            try
            {
                await ExecuteRunAsync(run, linked.Token);
            }
            catch (OperationCanceledException)
            {
                Finish(run, RunStatus.Cancelled, stoppingToken.IsCancellationRequested ? "Portal stopped" : "Cancelled by user");
            }
            catch (ConfigurationException ex)
            {
                run.AddLog("ERROR: " + ex.Message);
                Finish(run, RunStatus.Failed, ex.Message);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Run {RunId} crashed", run.Id);
                run.AddLog("ERROR: " + ex.Message);
                Finish(run, RunStatus.Failed, $"{ex.GetType().Name}: {ex.Message}");
            }
        }
    }

    private async Task ExecuteRunAsync(PortalRun run, CancellationToken cancellationToken)
    {
        var request = run.Record.Request;
        run.Record.Status = RunStatus.Running;
        run.Record.StartedUtc = DateTime.UtcNow;
        Save(run);
        _store?.SaveRun(run, RunDirectory(run.Id));
        _store?.Event("run.started", $"Run started ({run.Record.Request.Bank})", run.Id);
        RunsChanged?.Invoke();

        var profile = _catalog.LoadBank(request.Bank);
        var all = _catalog.GetScenarios();
        var scenarios = ScenarioLoader.Filter(all, request.Suites, request.ScenarioIds);
        if (scenarios.Count == 0)
            throw new ConfigurationException("No scenarios match the selected suites/scenarios");

        // Same rule as the CLI: with the gate on, connectivity is always checked first.
        if (request.PreflightGate && !scenarios.Any(IsPreflight))
            scenarios = [.. all.Where(IsPreflight), .. scenarios];

        run.TotalScenarios = scenarios.Count;
        run.AddLog($"{profile.Bank.Name} ({profile.Bank.Bic}) — {profile.Endpoints.Xs2a} — {scenarios.Count} scenario(s)");

        if (request.UseMockBank)
        {
            if (!CatalogService.IsLocal(profile))
                throw new ConfigurationException("The built-in MockBank can only be used with a localhost profile such as MOCK");

            await _mockBank.EnsureRunningAsync(cancellationToken);
            run.AddLog($"MockBank is running at {_mockBank.Xs2aUrl}");
        }

        using var ctx = RunContext.Create(profile, _catalog.GetTemplates());
        ctx.Notify = run.AddLog;
        ctx.OpenBrowser = request.AutoSca && CatalogService.AllowsHeadlessSca(profile)
            ? HeadlessAuthorizer.Create(message => run.AddLog("ERROR: " + message))
            : (url, _) =>
            {
                // Manual SCA: the tester opens the link from the run page (on the machine the portal runs on).
                run.LoginUrl = url;
                run.NotifyChanged();
                return Task.CompletedTask;
            };

        if (ctx.TppId is not null)
            run.AddLog($"TPP id: {ctx.TppId}");

        var runner = new ScenarioRunner(ctx) { PreflightGate = request.PreflightGate };
        runner.ScenarioStarted += s =>
        {
            run.CurrentScenario = $"{s.Id} — {s.Title}";
            run.LoginUrl = null;
            run.NotifyChanged();
        };
        runner.ScenarioCompleted += s =>
        {
            run.LoginUrl = null;
            run.AddCompleted(s);
            _store?.Event("scenario.completed", $"{s.Id} {s.Outcome}{(s.Reason is null ? "" : ": " + s.Reason)}", run.Id, StoreRecorder.Level(s.Outcome),
                new { scenario = s.Id, outcome = s.Outcome.ToString(), s.DurationMs });
        };

        var result = await runner.RunAsync(scenarios, cancellationToken);
        run.Result = result;
        run.CurrentScenario = null;
        ReportWriter.Write(result, RunDirectory(run.Id), ReportWriter.SupportedFormats);

        Finish(run, RunStatus.Completed, null);
        run.AddLog($"Finished: {result.Count(Outcome.Passed)} passed, {result.Count(Outcome.Failed)} failed, {result.Count(Outcome.Error)} errors — pass rate {result.PassRate.ToString(CultureInfo.InvariantCulture)}%");
    }

    private void Finish(PortalRun run, RunStatus status, string? error)
    {
        var record = run.Record;
        record.Status = status;
        record.Error = error;
        record.FinishedUtc = DateTime.UtcNow;
        run.CurrentScenario = null;
        run.LoginUrl = null;

        if (run.Result is { } r)
        {
            record.Passed = r.Count(Outcome.Passed);
            record.Failed = r.Count(Outcome.Failed);
            record.Errors = r.Count(Outcome.Error);
            record.Blocked = r.Count(Outcome.Blocked);
            record.Skipped = r.Count(Outcome.Skipped);
            record.PassRate = r.PassRate;
        }

        Save(run);
        _store?.SaveRun(run, RunDirectory(run.Id));
        _store?.Event($"run.{status.ToString().ToLowerInvariant()}",
            error ?? $"{record.Passed} passed, {record.Failed} failed, {record.Errors} errors, {record.Skipped} skipped",
            run.Id, status == RunStatus.Completed && record.Failed + record.Errors == 0 ? "Info" : status == RunStatus.Failed ? "Error" : "Warning");

        if (status == RunStatus.Completed && _summaries is { Options.AutoSummarize: true } summaries)
        {
            var provider = summaries.Options.AutoProvider is { Length: > 0 } p ? p : summaries.DefaultProvider;
            _store?.Then($"auto-summary {run.Id}", () => summaries.Start(run.Id, provider, "auto (portal)"));
        }
        run.NotifyChanged();
        RunsChanged?.Invoke();
    }

    private void Save(PortalRun run)
    {
        var dir = RunDirectory(run.Id);
        Directory.CreateDirectory(dir);
        File.WriteAllText(Path.Combine(dir, "run.json"), JsonSerializer.Serialize(run.Record, JsonOptions));
    }

    private void LoadHistory()
    {
        foreach (var dir in Directory.EnumerateDirectories(DataDirectory))
        {
            try
            {
                var recordFile = Path.Combine(dir, "run.json");
                if (!File.Exists(recordFile))
                    continue;

                var record = JsonSerializer.Deserialize<RunRecord>(File.ReadAllText(recordFile), JsonOptions);
                if (record is null)
                    continue;

                // A run that was in progress when the portal stopped will never finish.
                if (record.Status is RunStatus.Queued or RunStatus.Running)
                {
                    record.Status = RunStatus.Cancelled;
                    record.Error = "The portal stopped while this run was in progress";
                }

                var summaryFile = Path.Combine(dir, "summary.json");
                var result = File.Exists(summaryFile) ? JsonSerializer.Deserialize<RunResult>(File.ReadAllText(summaryFile), JsonOptions) : null;
                _runs[record.Id] = new PortalRun(record, result);
            }
            catch (Exception ex) when (ex is JsonException or IOException)
            {
                _logger.LogWarning(ex, "Skipping unreadable run folder {Folder}", dir);
            }
        }
    }

    private static bool IsPreflight(Scenario s) => s.Suite.Equals(ScenarioRunner.PreflightSuite, StringComparison.OrdinalIgnoreCase);
}
