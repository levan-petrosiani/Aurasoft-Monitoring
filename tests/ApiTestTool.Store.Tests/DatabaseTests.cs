using ApiTestTool.Engine.Reporting;
using ApiTestTool.Store.Entities;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace ApiTestTool.Store.Tests;

/// <summary>Runs only when <c>AQT_TEST_DB</c> points to a PostgreSQL server (e.g. the docker-compose one).</summary>
public sealed class DatabaseFactAttribute : FactAttribute
{
    public DatabaseFactAttribute()
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("AQT_TEST_DB")))
            Skip = "Set AQT_TEST_DB (e.g. Host=localhost;Port=5435;Username=aqt;Password=aqt_dev) to run database tests";
    }
}

/// <summary>Creates a throw-away database per test class, migrates it, and drops it afterwards.</summary>
public sealed class TestDatabase : IAsyncLifetime
{
    private readonly string _name = "aqt_test_" + Guid.NewGuid().ToString("N")[..12];
    private string? _server;

    public AqtStore Store { get; private set; } = null!;

    public async Task InitializeAsync()
    {
        _server = Environment.GetEnvironmentVariable("AQT_TEST_DB");
        if (string.IsNullOrEmpty(_server))
            return;

        await using (var admin = new NpgsqlConnection(new NpgsqlConnectionStringBuilder(_server) { Database = "postgres" }.ConnectionString))
        {
            await admin.OpenAsync();
            await new NpgsqlCommand($"CREATE DATABASE {_name}", admin).ExecuteNonQueryAsync();
        }

        Store = new AqtStore(new NpgsqlConnectionStringBuilder(_server) { Database = _name }.ConnectionString);
        await Store.MigrateAsync();
    }

    public async Task DisposeAsync()
    {
        if (string.IsNullOrEmpty(_server))
            return;

        NpgsqlConnection.ClearAllPools();
        await using var admin = new NpgsqlConnection(new NpgsqlConnectionStringBuilder(_server) { Database = "postgres" }.ConnectionString);
        await admin.OpenAsync();
        await new NpgsqlCommand($"DROP DATABASE IF EXISTS {_name} WITH (FORCE)", admin).ExecuteNonQueryAsync();
    }
}

public class DatabaseTests(TestDatabase db) : IClassFixture<TestDatabase>
{
    [DatabaseFact]
    public async Task Saves_a_run_and_replaces_it_on_save_again()
    {
        var info = new RunInfo { Id = "TESTGE22-20261024-080000", Source = "cli", Status = "Completed", BankBic = "TESTGE22", Suites = ["ais"] };

        await db.Store.SaveRunAsync(info, TestRuns.Create());
        await db.Store.SaveRunAsync(info with { Status = "Completed" }, TestRuns.Create()); // idempotent

        await using var ctx = db.Store.CreateContext();
        var run = await ctx.Runs.Include(r => r.Scenarios).ThenInclude(s => s.Steps).ThenInclude(st => st.Exchanges).SingleAsync(r => r.Id == info.Id);
        Assert.Equal(2, run.Scenarios.Count);
        Assert.Equal(3, run.Scenarios.SelectMany(s => s.Steps).SelectMany(s => s.Exchanges).Count()); // 2 SCA + 1 request; cleanup has none
        Assert.Equal(["ais"], run.Suites);

        // Masked data is what reaches the database.
        var tokenRows = await ctx.Exchanges.CountAsync(e => e.RunId == info.Id && (e.ResponseBody!.Contains("SECRET") || e.RequestBody!.Contains("SECRET")));
        Assert.Equal(0, tokenRows);

        // jsonb headers are queryable.
        var authHeaders = await ctx.Database.SqlQueryRaw<string>(
            "select h->>1 as \"Value\" from exchanges, jsonb_array_elements(request_headers) h where run_id = {0} and h->>0 = 'Authorization'", info.Id).ToListAsync();
        Assert.All(authHeaders, v => Assert.Equal("Bearer ***(masked)", v));
    }

    [DatabaseFact]
    public async Task Delete_cascades_and_events_and_ai_calls_are_stored()
    {
        var info = new RunInfo { Id = "DEL-1", Source = "portal", Status = "Completed", BankBic = "TESTGE22" };
        await db.Store.SaveRunAsync(info, TestRuns.Create());
        await db.Store.AddEventAsync("portal", "run.finished", "done", info.Id, data: new { passed = 1 });
        await db.Store.AddAiCallAsync(new AiCallEntity
        {
            Id = Guid.NewGuid(), TimeUtc = DateTime.UtcNow, Feature = "check", Provider = "ollama", Model = "qwen3:8b",
            PromptSha256 = "x", Request = """[{"role":"user","text":"hi"}]""", Succeeded = true, RunId = info.Id,
        });

        Assert.True(await db.Store.DeleteRunAsync(info.Id));

        await using var ctx = db.Store.CreateContext();
        Assert.Equal(0, await ctx.ScenarioResults.CountAsync(s => s.RunId == info.Id));
        Assert.Equal(0, await ctx.Exchanges.CountAsync(e => e.RunId == info.Id));
        // History is kept: events and AI calls are not owned by the run row.
        Assert.Equal(1, await ctx.Events.CountAsync(e => e.RunId == info.Id));
        Assert.Equal(1, await ctx.AiCalls.CountAsync(c => c.RunId == info.Id));
    }

    [DatabaseFact]
    public async Task Import_skips_existing_runs_unless_replace_is_set()
    {
        var root = Directory.CreateTempSubdirectory("aqt-import-db-").FullName;
        try
        {
            ReportWriter.Write(TestRuns.Create(), Path.Combine(root, "IMPGE22-20261024-080000"), ["json"]);

            var first = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: false);
            var second = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: false);
            var third = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: true);

            Assert.Equal((1, 0), (first.Imported, first.Skipped));
            Assert.Equal((0, 1), (second.Imported, second.Skipped));
            Assert.Equal((1, 0), (third.Imported, third.Skipped));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
