namespace ApiTestTool.AI;

/// <summary>Names of the AI features; each one has its own provider/model setting.</summary>
public static class AiFeatures
{
    public const string Summary = "summary";
    public const string Ask = "ask";
    public const string RuleDrafting = "ruleDrafting";
    public const string ScenarioDrafting = "scenarioDrafting";

    /// <summary><c>aqt ai check</c>: a one-line call to prove the provider is reachable.</summary>
    public const string Check = "check";

    public static readonly string[] All = [Summary, Ask, RuleDrafting, ScenarioDrafting, Check];
}

public static class AiProviders
{
    public const string Anthropic = "anthropic";
    public const string Ollama = "ollama";
}

/// <summary>Configuration section <c>ApiTestTool:AI</c>.</summary>
public sealed class AiOptions
{
    public const string Section = "ApiTestTool:AI";

    public AnthropicOptions Anthropic { get; set; } = new();

    public OllamaOptions Ollama { get; set; } = new();

    /// <summary>Per-feature overrides; features not listed use <see cref="Defaults"/>.</summary>
    public Dictionary<string, AiFeatureOptions> Features { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// Built-in choices (docs/ai/PLAN.md §4): drafting reads only public standards → Claude; summary and ask see run
    /// data → Claude by default, switchable to Ollama to keep data on the machine.
    /// </summary>
    public static IReadOnlyDictionary<string, AiFeatureOptions> Defaults { get; } = new Dictionary<string, AiFeatureOptions>(StringComparer.OrdinalIgnoreCase)
    {
        [AiFeatures.Summary] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 16000, Effort = "medium" },
        [AiFeatures.Ask] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 16000, Effort = "medium" },
        [AiFeatures.RuleDrafting] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 32000, Effort = "high" },
        [AiFeatures.ScenarioDrafting] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 32000, Effort = "high" },
        [AiFeatures.Check] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 1000, Effort = "low" },
    };

    /// <summary>
    /// A copy in which <paramref name="feature"/> uses <paramref name="provider"/> (e.g. a "Local" button). A configured
    /// model belongs to the configured provider, so switching provider uses the new provider's default model.
    /// </summary>
    public AiOptions WithProvider(string feature, string provider)
    {
        var copy = new AiOptions { Anthropic = Anthropic, Ollama = Ollama, Features = new(Features, StringComparer.OrdinalIgnoreCase) };
        var current = Features.GetValueOrDefault(feature);
        copy.Features[feature] = new AiFeatureOptions
        {
            Provider = provider,
            Model = current?.Provider == provider ? current.Model : null,
            MaxOutputTokens = current?.MaxOutputTokens,
            Effort = current?.Effort,
        };
        return copy;
    }

    /// <summary>Effective settings of a feature: configured values over the defaults, model defaulting per provider.</summary>
    public AiFeatureOptions For(string feature)
    {
        var defaults = Defaults.TryGetValue(feature, out var d) ? d : new AiFeatureOptions { Provider = AiProviders.Anthropic };
        Features.TryGetValue(feature, out var configured);

        var provider = configured?.Provider ?? defaults.Provider ?? AiProviders.Anthropic;
        return new AiFeatureOptions
        {
            Provider = provider,
            Model = configured?.Model ?? (provider == AiProviders.Ollama ? Ollama.DefaultModel : Anthropic.DefaultModel),
            MaxOutputTokens = configured?.MaxOutputTokens ?? defaults.MaxOutputTokens,
            Effort = configured?.Effort ?? defaults.Effort,
        };
    }
}

public sealed class AiFeatureOptions
{
    /// <summary>anthropic or ollama.</summary>
    public string? Provider { get; set; }

    public string? Model { get; set; }

    public int? MaxOutputTokens { get; set; }

    /// <summary>Claude only: low | medium | high | xhigh | max (adaptive thinking depth and token spend).</summary>
    public string? Effort { get; set; }
}

public sealed class AnthropicOptions
{
    /// <summary>Leave empty to use <c>ANTHROPIC_API_KEY</c> (or an <c>ant auth login</c> profile). Never commit a key.</summary>
    public string? ApiKey { get; set; }

    public string DefaultModel { get; set; } = "claude-opus-5-5";

    /// <summary>
    /// If Claude declines a request for policy reasons, let the API re-run it on Anthropic's recommended fallback
    /// model (<c>fallbacks: "default"</c>). Security-testing material (signatures, tokens, auth flows) can trip
    /// classifiers, so this is on by default.
    /// </summary>
    public bool RefusalFallback { get; set; } = true;
}

public sealed class OllamaOptions
{
    public string Url { get; set; } = "http://localhost:11434";

    /// <summary>
    /// qwen3:8b (5 GB): a TBC run summarized in ~35 s with the right root cause; gemma4:26b (17 GB) was as accurate
    /// but took ~190 s (NOTE.md step 15). Bigger models can be chosen per feature.
    /// </summary>
    public string DefaultModel { get; set; } = "qwen3:8b";

    /// <summary>Local models can take minutes (loading a 17 GB model, slow generation); HttpClient's default is 100 s.</summary>
    public int TimeoutSeconds { get; set; } = 600;

    /// <summary>
    /// Context window sent as <c>num_ctx</c>. Ollama's default is small and it cuts longer prompts silently, which would
    /// drop most of a long clause. 16k fits the largest clauses and still runs on a 24 GB Mac with qwen3:8b.
    /// </summary>
    public int ContextTokens { get; set; } = 16384;

    /// <summary>Local embedding model for clause and rule search (1024 dimensions, multilingual incl. Georgian).</summary>
    public string EmbeddingModel { get; set; } = "bge-m3";
}
