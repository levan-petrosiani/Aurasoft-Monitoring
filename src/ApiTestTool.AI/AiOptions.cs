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
        [AiFeatures.Summary] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 4000 },
        [AiFeatures.Ask] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 8000 },
        [AiFeatures.RuleDrafting] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 16000 },
        [AiFeatures.ScenarioDrafting] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 16000 },
        [AiFeatures.Check] = new() { Provider = AiProviders.Anthropic, MaxOutputTokens = 200 },
    };

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
        };
    }
}

public sealed class AiFeatureOptions
{
    /// <summary>anthropic or ollama.</summary>
    public string? Provider { get; set; }

    public string? Model { get; set; }

    public int? MaxOutputTokens { get; set; }
}

public sealed class AnthropicOptions
{
    /// <summary>Leave empty to use <c>ANTHROPIC_API_KEY</c> (or an <c>ant auth login</c> profile). Never commit a key.</summary>
    public string? ApiKey { get; set; }

    public string DefaultModel { get; set; } = "claude-opus-5-5";
}

public sealed class OllamaOptions
{
    public string Url { get; set; } = "http://localhost:11434";

    public string DefaultModel { get; set; } = "gemma4:26b";
}
