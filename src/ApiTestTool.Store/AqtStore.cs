using System.Text.Json;
using ApiTestTool.Engine.Running;
using ApiTestTool.Store.Entities;
using ApiTestTool.Store.Masking;
using Microsoft.EntityFrameworkCore;

namespace ApiTestTool.Store;

/// <summary>What is known about a run besides its results (who started it, how, where the reports are).</summary>
public sealed record RunInfo
{
    public required string Id { get; init; }

    /// <summary>cli or portal.</summary>
    public required string Source { get; init; }

    /// <summary>Queued, Running, Completed, Failed or Cancelled.</summary>
    public required string Status { get; init; }

    public required string BankBic { get; init; }

    public string? BankName { get; init; }

    public DateTime CreatedUtc { get; init; }

    public DateTime? StartedUtc { get; init; }

    public DateTime? FinishedUtc { get; init; }

    public IReadOnlyList<string> Suites { get; init; } = [];

    public string? Error { get; init; }

    public string? ReportDirectory { get; init; }

    /// <summary>Run options as JSON (e.g. the portal's request).</summary>
    public string? OptionsJson { get; init; }
}

public sealed class StoreOptions
{
    /// <summary>Request/response bodies longer than this are cut before they are stored.</summary>
    public int MaxBodyChars { get; set; } = 64 * 1024;

    public MaskingOptions Masking { get; set; } = new();
}

public sealed record StoreStatus(int Runs, int Scenarios, int Exchanges, int Events, int AiCalls, IReadOnlyList<string> PendingMigrations);

/// <summary>
/// Writes runs, events and AI calls to PostgreSQL. Every method opens its own short-lived context, so one instance
/// can be shared (singleton in the portal, one per command in the CLI). All data is masked before it is written.
/// </summary>
public sealed class AqtStore
{
    private readonly DbContextOptions<AqtDbContext> _options;
    private readonly StoreOptions _storeOptions;
    private readonly SensitiveDataMasker _masker;

    public AqtStore(string connectionString, StoreOptions? options = null)
        : this(AqtDbContext.CreateOptions(connectionString), options)
    {
    }

    public AqtStore(DbContextOptions<AqtDbContext> options, StoreOptions? storeOptions = null)
    {
        _options = options;
        _storeOptions = storeOptions ?? new StoreOptions();
        _masker = new SensitiveDataMasker(_storeOptions.Masking);
    }

    public SensitiveDataMasker Masker => _masker;

    public AqtDbContext CreateContext() => new(_options);

    /// <summary>Creates or upgrades the schema.</summary>
    public async Task MigrateAsync(CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        await db.Database.MigrateAsync(cancellationToken);
    }

    /// <summary>Inserts or replaces a run with all its scenario/step/exchange rows.</summary>
    public async Task SaveRunAsync(RunInfo info, RunResult? result, CancellationToken cancellationToken = default)
    {
        var entity = RunMapper.ToEntity(info, result, _masker, _storeOptions.MaxBodyChars);

        await using var db = CreateContext();
        var strategy = db.Database.CreateExecutionStrategy();
        await strategy.ExecuteAsync(async () =>
        {
            await using var tx = await db.Database.BeginTransactionAsync(cancellationToken);
            await db.Runs.Where(r => r.Id == info.Id).ExecuteDeleteAsync(cancellationToken);
            db.Runs.Add(entity);
            await db.SaveChangesAsync(cancellationToken);
            await tx.CommitAsync(cancellationToken);
        });
        db.ChangeTracker.Clear();
    }

    public async Task<bool> RunExistsAsync(string id, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Runs.AnyAsync(r => r.Id == id, cancellationToken);
    }

    public async Task<bool> DeleteRunAsync(string id, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Runs.Where(r => r.Id == id).ExecuteDeleteAsync(cancellationToken) > 0;
    }

    public async Task AddEventAsync(string source, string type, string message, string? runId = null, string level = "Info", object? data = null, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        db.Events.Add(new EventEntity
        {
            TimeUtc = DateTime.UtcNow,
            RunId = runId,
            Source = source,
            Type = type,
            Level = level,
            Message = message,
            Data = data is null ? null : JsonSerializer.Serialize(data, RunMapper.Json),
        });
        await db.SaveChangesAsync(cancellationToken);
    }

    public async Task AddAiCallAsync(AiCallEntity call, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        db.AiCalls.Add(call);
        await db.SaveChangesAsync(cancellationToken);
    }

    /// <summary>A run with scenarios, steps and exchanges (read-only, ordered as executed).</summary>
    public async Task<RunEntity?> GetRunAsync(string id, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var run = await db.Runs.AsNoTracking()
            .Include(r => r.Scenarios).ThenInclude(s => s.Steps).ThenInclude(st => st.Exchanges)
            .AsSplitQuery()
            .SingleOrDefaultAsync(r => r.Id == id, cancellationToken);

        if (run is not null)
        {
            run.Scenarios = [.. run.Scenarios.OrderBy(s => s.Ordinal)];
            foreach (var s in run.Scenarios)
            {
                s.Steps = [.. s.Steps.OrderBy(st => st.Ordinal)];
                foreach (var st in s.Steps)
                    st.Exchanges = [.. st.Exchanges.OrderBy(e => e.Ordinal)];
            }
        }

        return run;
    }

    /// <summary>The newest run id, optionally for one bank.</summary>
    public async Task<string?> GetLatestRunIdAsync(string? bankBic = null, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Runs.AsNoTracking()
            .Where(r => bankBic == null || r.BankBic == bankBic)
            .OrderByDescending(r => r.CreatedUtc)
            .Select(r => r.Id)
            .FirstOrDefaultAsync(cancellationToken);
    }

    /// <summary>The bank's previous run that has results (for "what changed since last time"), with its scenario outcomes.</summary>
    public async Task<RunEntity?> GetPreviousRunAsync(RunEntity run, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.Runs.AsNoTracking()
            .Include(r => r.Scenarios)
            .Where(r => r.BankBic == run.BankBic && r.Id != run.Id && r.CreatedUtc < run.CreatedUtc && r.Scenarios.Any())
            .OrderByDescending(r => r.CreatedUtc)
            .FirstOrDefaultAsync(cancellationToken);
    }

    public async Task AddRunSummaryAsync(RunSummaryEntity summary, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        db.RunSummaries.Add(summary);
        await db.SaveChangesAsync(cancellationToken);
    }

    public async Task<RunSummaryEntity?> GetLatestRunSummaryAsync(string runId, CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        return await db.RunSummaries.AsNoTracking()
            .Where(s => s.RunId == runId)
            .OrderByDescending(s => s.CreatedUtc)
            .FirstOrDefaultAsync(cancellationToken);
    }

    public async Task<StoreStatus> GetStatusAsync(CancellationToken cancellationToken = default)
    {
        await using var db = CreateContext();
        var pending = (await db.Database.GetPendingMigrationsAsync(cancellationToken)).ToList();
        if (pending.Count > 0 && !(await db.Database.GetAppliedMigrationsAsync(cancellationToken)).Any())
            return new StoreStatus(0, 0, 0, 0, 0, pending);

        return new StoreStatus(
            await db.Runs.CountAsync(cancellationToken),
            await db.ScenarioResults.CountAsync(cancellationToken),
            await db.Exchanges.CountAsync(cancellationToken),
            await db.Events.CountAsync(cancellationToken),
            await db.AiCalls.CountAsync(cancellationToken),
            pending);
    }
}
