namespace ApiTestTool.Store;

/// <summary>Where the store's connection string comes from when no configuration system is involved (CLI, dotnet ef).</summary>
public static class StoreConnection
{
    /// <summary>Environment variable with the PostgreSQL connection string.</summary>
    public const string EnvironmentVariable = "AQT_DB";

    /// <summary>Matches docker-compose.yml (local development only).</summary>
    public const string DockerComposeDefault = "Host=localhost;Port=5435;Database=aqt;Username=aqt;Password=aqt_dev";

    public static string? FromEnvironment() =>
        Environment.GetEnvironmentVariable(EnvironmentVariable) is { Length: > 0 } value ? value : null;
}
