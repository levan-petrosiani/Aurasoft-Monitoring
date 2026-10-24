using ApiTestTool.Store;
using Npgsql;

namespace ApiTestTool.Tests.Shared;

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
