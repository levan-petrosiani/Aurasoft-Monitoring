using System.Collections.Concurrent;
using System.Text.Json;
using ApiTestTool.AI;
using ApiTestTool.AI.Summaries;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.Options;

namespace ApiTestTool.Portal.Services;

/// <summary>Portal settings of the run summary (section <c>ApiTestTool:AI:Summary</c>).</summary>
public sealed class SummaryOptions
{
    /// <summary>Summarize every completed run automatically (costs a model call per run when the provider is Claude).</summary>
    public bool AutoSummarize { get; set; }

    /// <summary>Provider for automatic summaries; empty = the configured summary provider.</summary>
    public string? AutoProvider { get; set; }
}

/// <summary>AI run summaries for the Run page: start one (Claude or local), read the latest, one at a time per run.</summary>
public sealed class SummaryService(AqtStore store, IOptions<AiOptions> aiOptions, IOptions<SummaryOptions> summaryOptions, ILogger<SummaryService> logger)
{
    private readonly ConcurrentDictionary<string, string> _running = new();

    public SummaryOptions Options => summaryOptions.Value;

    /// <summary>Raised when a summary starts or finishes (run id).</summary>
    public event Action<string>? Changed;

    /// <summary>Provider currently summarizing the run, or null.</summary>
    public string? RunningProvider(string runId) => _running.TryGetValue(runId, out var p) ? p : null;

    /// <summary>Model a provider would use for summaries, for button labels.</summary>
    public string ModelFor(string provider) => WithProvider(provider).For(AiFeatures.Summary).Model!;

    public string DefaultProvider => aiOptions.Value.For(AiFeatures.Summary).Provider!;

    public Task<RunSummaryEntity?> GetLatestAsync(string runId, CancellationToken cancellationToken = default) =>
        store.GetLatestRunSummaryAsync(runId, cancellationToken);

    public static RunSummary? Parse(RunSummaryEntity entity) =>
        entity.SummaryJson is null ? null : JsonSerializer.Deserialize<RunSummary>(entity.SummaryJson, new JsonSerializerOptions(JsonSerializerDefaults.Web));

    /// <summary>Summarizes in the background; returns false if this run is already being summarized.</summary>
    public bool Start(string runId, string provider, string triggeredBy)
    {
        if (!_running.TryAdd(runId, provider))
            return false;

        Changed?.Invoke(runId);
        _ = Task.Run(async () =>
        {
            try
            {
                var factory = new ChatClientFactory(WithProvider(provider), new StoreAuditSink(store, m => logger.LogWarning("{Message}", m)), store.Masker);
                await new RunSummarizer(store, factory).SummarizeAsync(runId, triggeredBy);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Summary of run {RunId} failed", runId);
            }
            finally
            {
                _running.TryRemove(runId, out _);
                Changed?.Invoke(runId);
            }
        });
        return true;
    }

    private AiOptions WithProvider(string provider)
    {
        var source = aiOptions.Value;
        var options = new AiOptions
        {
            Anthropic = source.Anthropic,
            Ollama = source.Ollama,
            Features = new(source.Features, StringComparer.OrdinalIgnoreCase),
        };

        var current = source.Features.GetValueOrDefault(AiFeatures.Summary);
        options.Features[AiFeatures.Summary] = new AiFeatureOptions
        {
            Provider = provider,
            // A configured model belongs to the configured provider; switching provider uses that provider's default.
            Model = current?.Provider == provider ? current.Model : null,
            MaxOutputTokens = current?.MaxOutputTokens,
            Effort = current?.Effort,
        };
        return options;
    }
}
