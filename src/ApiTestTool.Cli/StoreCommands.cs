using ApiTestTool.Engine;
using ApiTestTool.Engine.Running;
using ApiTestTool.Store;

namespace ApiTestTool.Cli;

/// <summary><c>aqt db ...</c> and the optional store hook of <c>aqt run</c>.</summary>
internal static class StoreCommands
{
    /// <summary>Connection string for <c>aqt run</c>: <c>--db</c> or <c>AQT_DB</c>. Without one, runs are only written as files.</summary>
    public static string? RunConnection(CliArgs cli) => cli.Get("db") ?? StoreConnection.FromEnvironment();

    public static async Task<int> DbAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var sub = cli.Positional.FirstOrDefault() ?? "status";
        var connection = RunConnection(cli) ?? StoreConnection.DockerComposeDefault;
        if (RunConnection(cli) is null)
            Out.Muted($"No --db or {StoreConnection.EnvironmentVariable} set; using the docker-compose database (localhost:5435).");

        var store = new AqtStore(connection);
        try
        {
            switch (sub)
            {
                case "migrate":
                    await store.MigrateAsync(cancellationToken);
                    Out.Ok("Database schema is up to date");
                    return ExitCodes.Success;

                case "import":
                    var roots = cli.Positional.Skip(1).ToList();
                    if (roots.Count == 0)
                        roots = [Path.Combine(Directory.GetCurrentDirectory(), "reports")];

                    var result = await RunImporter.ImportAsync(store, roots, cli.Has("replace"), line => Out.Ok("imported  " + line), cancellationToken);
                    foreach (var problem in result.Problems)
                        Out.Error(problem);
                    Out.Muted($"{result.Imported} imported, {result.Skipped} already in the store (use --replace to overwrite), {result.Problems.Count} problem(s)");
                    return result.Problems.Count == 0 ? ExitCodes.Success : ExitCodes.ConfigurationError;

                case "status":
                    var status = await store.GetStatusAsync(cancellationToken);
                    if (status.PendingMigrations.Count > 0)
                        Out.Notice($"Pending migrations: {string.Join(", ", status.PendingMigrations)} - run 'aqt db migrate'");
                    Console.WriteLine($"runs {status.Runs} · scenario results {status.Scenarios} · exchanges {status.Exchanges} · events {status.Events} · AI calls {status.AiCalls}");
                    return ExitCodes.Success;

                default:
                    throw new ConfigurationException("Usage: aqt db migrate|import [folder...] [--replace]|status [--db <connection>]");
            }
        }
        catch (Exception ex) when (IsDatabaseProblem(ex))
        {
            Out.Error($"Database: {ex.GetBaseException().Message}. Is it running? (docker compose up -d)");
            return ExitCodes.ConfigurationError;
        }
    }

    /// <summary>
    /// Writes a finished CLI run to the store. A database problem never fails the test run: the reports on disk are
    /// complete, and the folder can be imported later with <c>aqt db import</c>.
    /// </summary>
    public static async Task SaveRunAsync(string connection, string runId, string reportDirectory, RunResult run, IReadOnlyList<string> suites, CancellationToken cancellationToken)
    {
        try
        {
            var store = new AqtStore(connection);
            await store.SaveRunAsync(new RunInfo
            {
                Id = runId,
                Source = "cli",
                Status = "Completed",
                BankBic = run.BankBic,
                BankName = run.BankName,
                CreatedUtc = run.StartedAtUtc,
                StartedUtc = run.StartedAtUtc,
                FinishedUtc = run.FinishedAtUtc,
                Suites = suites,
                ReportDirectory = Path.GetFullPath(reportDirectory),
            }, run, cancellationToken);
            await store.AddEventAsync("cli", "run.finished",
                $"{run.BankBic}: {run.Count(Outcome.Passed)} passed, {run.Count(Outcome.Failed)} failed, {run.Count(Outcome.Error)} errors",
                runId, run.Count(Outcome.Failed) + run.Count(Outcome.Error) > 0 ? "Warning" : "Info", cancellationToken: cancellationToken);
            Out.Muted($"  saved to the store as {runId}");
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // Any store problem (connection, retries exhausted, schema not migrated) is only a notice here.
            Out.Notice($"Could not save the run to the store ({ex.GetBaseException().Message}); import it later with 'aqt db import'.");
        }
    }

    /// <summary>Connection, retry, update or schema problems anywhere in the exception chain.</summary>
    private static bool IsDatabaseProblem(Exception ex)
    {
        for (var e = ex; e is not null; e = e.InnerException)
        {
            if (e is Npgsql.NpgsqlException or Microsoft.EntityFrameworkCore.DbUpdateException or Microsoft.EntityFrameworkCore.Storage.RetryLimitExceededException
                or System.Net.Sockets.SocketException or TimeoutException)
                return true;
        }

        return false;
    }
}
