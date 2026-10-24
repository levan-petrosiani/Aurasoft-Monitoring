using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Running;

namespace ApiTestTool.Store;

public sealed record ImportResult(int Imported, int Skipped, IReadOnlyList<string> Problems);

/// <summary>
/// Loads existing run folders into the store: every folder with a <c>summary.json</c>. Portal folders also have a
/// <c>run.json</c> (status, request); CLI folders are named <c>&lt;BIC&gt;-&lt;yyyyMMdd-HHmmss&gt;</c>.
/// </summary>
public static class RunImporter
{
    public static async Task<ImportResult> ImportAsync(AqtStore store, IEnumerable<string> roots, bool replaceExisting, Action<string>? progress = null, CancellationToken cancellationToken = default)
    {
        int imported = 0, skipped = 0;
        var problems = new List<string>();

        // summary.json = a run with results; run.json alone = a portal run that was cancelled or crashed before results.
        var folders = roots
            .Where(Directory.Exists)
            .SelectMany(root => Directory.EnumerateFiles(root, "summary.json", SearchOption.AllDirectories)
                .Concat(Directory.EnumerateFiles(root, "run.json", SearchOption.AllDirectories)))
            .Select(Path.GetDirectoryName)
            .OfType<string>()
            .Distinct()
            .Order(StringComparer.Ordinal);

        foreach (var folder in folders)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                var (info, result) = Read(folder);
                if (!replaceExisting && await store.RunExistsAsync(info.Id, cancellationToken))
                {
                    skipped++;
                    continue;
                }

                await store.SaveRunAsync(info, result, cancellationToken);
                await store.AddEventAsync("import", "run.imported", $"Imported {info.Id} from {folder}", info.Id, cancellationToken: cancellationToken);
                imported++;
                progress?.Invoke($"{info.Id}  {info.BankBic}  {(result is null ? info.Status + ", no results" : $"{result.Scenarios.Count} scenario(s)")}");
            }
            catch (Exception ex) when (ex is JsonException or IOException or InvalidDataException)
            {
                problems.Add($"{folder}: {ex.Message}");
            }
        }

        return new ImportResult(imported, skipped, problems);
    }

    internal static (RunInfo Info, RunResult? Result) Read(string folder)
    {
        var summaryFile = Path.Combine(folder, "summary.json");
        var result = File.Exists(summaryFile)
            ? JsonSerializer.Deserialize<RunResult>(File.ReadAllText(summaryFile), RunMapper.Json) ?? throw new InvalidDataException("summary.json is empty")
            : null;

        var runFile = Path.Combine(folder, "run.json");
        var name = Path.GetFileName(folder);

        if (File.Exists(runFile) && JsonNode.Parse(File.ReadAllText(runFile)) is JsonObject record)
        {
            var id = (string?)record["id"] ?? name;
            var request = record["request"] as JsonObject;
            return (new RunInfo
            {
                Id = id,
                Source = "portal",
                Status = (string?)record["status"] ?? "Completed",
                BankBic = result?.BankBic ?? BicFromPortalId(id) ?? (string?)request?["bank"] ?? "UNKNOWN",
                BankName = (string?)record["bankName"] ?? result?.BankName,
                CreatedUtc = Date(record["createdUtc"]) ?? result?.StartedAtUtc ?? Directory.GetCreationTimeUtc(folder),
                StartedUtc = Date(record["startedUtc"]),
                FinishedUtc = Date(record["finishedUtc"]),
                Suites = request?["suites"] is JsonArray suites ? [.. suites.Select(s => (string?)s).OfType<string>()] : [],
                Error = (string?)record["error"],
                ReportDirectory = Path.GetFullPath(folder),
                OptionsJson = request?.ToJsonString(),
            }, result);
        }

        if (result is null)
            throw new InvalidDataException("neither summary.json nor a readable run.json");

        return (new RunInfo
        {
            Id = name,
            Source = "cli",
            Status = "Completed",
            BankBic = result.BankBic,
            BankName = result.BankName,
            CreatedUtc = result.StartedAtUtc,
            ReportDirectory = Path.GetFullPath(folder),
        }, result);
    }

    /// <summary>Portal run ids look like <c>yyyyMMdd-HHmmss-BIC-xxxx</c>.</summary>
    private static string? BicFromPortalId(string id) => id.Split('-') is { Length: 4 } parts ? parts[2] : null;

    private static DateTime? Date(JsonNode? node) =>
        (string?)node is { } s && DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var d) ? d : null;
}
