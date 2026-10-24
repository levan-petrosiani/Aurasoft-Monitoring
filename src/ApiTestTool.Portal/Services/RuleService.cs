using System.Collections.Concurrent;
using ApiTestTool.AI;
using ApiTestTool.AI.Rules;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.Options;

namespace ApiTestTool.Portal.Services;

/// <summary>
/// Standards and rules for the portal: clause search, drafting rules in the background (Claude or local), and the
/// review actions. Review actions go through <see cref="RuleBook"/>, which records the reviewer and every change.
/// </summary>
public sealed class RuleService(AqtStore store, IOptions<AiOptions> aiOptions, IOptions<PortalOptions> portalOptions, IWebHostEnvironment env, ILogger<RuleService> logger)
{
    private readonly ConcurrentDictionary<string, string> _drafting = new();
    private readonly ConcurrentDictionary<string, RuleDraftResult> _lastDraft = new();

    public AqtStore Store => store;

    public RuleBook Book { get; } = new(store);

    /// <summary>Raised when drafting for a clause starts or finishes, or a rule changes (clause key or rule id).</summary>
    public event Action<string>? Changed;

    public string? DraftingProvider(string clauseKey) => _drafting.TryGetValue(clauseKey, out var p) ? p : null;

    public RuleDraftResult? LastDraft(string clauseKey) => _lastDraft.TryGetValue(clauseKey, out var r) ? r : null;

    public string ModelFor(string provider) => aiOptions.Value.WithProvider(AiFeatures.RuleDrafting, provider).For(AiFeatures.RuleDrafting).Model!;

    /// <summary>Hybrid clause search; falls back to words only when the local embedding model is not reachable.</summary>
    public async Task<List<ClauseHit>> SearchAsync(string query, string? standard, CancellationToken cancellationToken = default)
    {
        float[]? vector = null;
        try
        {
            var generator = new ChatClientFactory(aiOptions.Value, new InMemoryAuditSink()).CreateEmbeddingGenerator();
            vector = (await generator.GenerateAsync([query], cancellationToken: cancellationToken))[0].Vector.ToArray();
        }
        catch (HttpRequestException ex)
        {
            logger.LogInformation("Clause search by words only: {Message}", ex.Message);
        }

        return await store.SearchClausesAsync(query, vector, standard, 15, cancellationToken);
    }

    public bool StartDraft(string clauseKey, string provider, string triggeredBy)
    {
        if (!_drafting.TryAdd(clauseKey, provider))
            return false;

        Changed?.Invoke(clauseKey);
        _ = Task.Run(async () =>
        {
            try
            {
                var factory = new ChatClientFactory(aiOptions.Value.WithProvider(AiFeatures.RuleDrafting, provider),
                    new StoreAuditSink(store, m => logger.LogWarning("{Message}", m)), store.Masker);
                _lastDraft[clauseKey] = await new RuleDrafter(store, factory).DraftAsync(clauseKey, triggeredBy);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Drafting rules for {Clause} failed", clauseKey);
                _lastDraft[clauseKey] = new RuleDraftResult(clauseKey, [], null, "failed", ex.Message, 0);
            }
            finally
            {
                _drafting.TryRemove(clauseKey, out _);
                Changed?.Invoke(clauseKey);
            }
        });
        return true;
    }

    /// <summary>Runs a review action and reports its error message instead of throwing (shown next to the rule).</summary>
    public async Task<string?> ReviewAsync(string ruleId, Func<RuleBook, Task<RuleEntity>> action)
    {
        try
        {
            await action(Book);
            Changed?.Invoke(ruleId);
            return null;
        }
        catch (InvalidOperationException ex)
        {
            return ex.Message;
        }
    }

    /// <summary>Folder the approved rules are exported to (the repository's rules/ in development).</summary>
    public string RulesDirectory
    {
        get
        {
            var root = portalOptions.Value.Root;
            var baseDir = string.IsNullOrEmpty(root) ? AppContext.BaseDirectory : Path.GetFullPath(Path.Combine(env.ContentRootPath, root));
            return Path.Combine(baseDir, "rules");
        }
    }

    public Task<(int Written, int Removed)> ExportAsync() => Book.ExportAsync(RulesDirectory);
}
