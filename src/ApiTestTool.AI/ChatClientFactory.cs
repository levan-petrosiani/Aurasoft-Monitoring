using Anthropic;
using ApiTestTool.Store.Masking;
using Microsoft.Extensions.AI;
using OllamaSharp;

namespace ApiTestTool.AI;

/// <summary>
/// Creates the chat client for a feature from configuration — Claude (Anthropic SDK) or a local model (Ollama) —
/// always wrapped in <see cref="AuditingChatClient"/>. Features depend on <see cref="IChatClient"/> only, so switching
/// provider is a configuration change.
/// </summary>
public sealed class ChatClientFactory(AiOptions options, IAiAuditSink audit, SensitiveDataMasker? masker = null, HttpClient? anthropicHttp = null)
{
    private readonly SensitiveDataMasker _masker = masker ?? SensitiveDataMasker.Default;
    private readonly Lazy<AnthropicClient> _anthropic = new(() =>
    {
        // No key configured: ANTHROPIC_API_KEY or the 'ant auth login' profile is used.
        var key = string.IsNullOrEmpty(options.Anthropic.ApiKey) ? null : options.Anthropic.ApiKey;
        return (key, anthropicHttp) switch
        {
            (null, null) => new AnthropicClient(),
            (null, { } http) => new AnthropicClient { HttpClient = http }, // tests and proxies
            ({ } k, null) => new AnthropicClient { ApiKey = k },
            ({ } k, { } http) => new AnthropicClient { ApiKey = k, HttpClient = http },
        };
    });

    public AiOptions Options => options;

    /// <summary>Tests: replaces the provider client (the audit wrapper is still applied).</summary>
    internal Func<AiFeatureOptions, IChatClient>? ProviderOverride { get; init; }

    /// <summary>Provider, model and limits a feature will use.</summary>
    public AiFeatureOptions Describe(string feature) => options.For(feature);

    public IChatClient Create(string feature, AiCallContext? context = null)
    {
        var settings = options.For(feature);
        var model = settings.Model!;
        IChatClient inner = ProviderOverride is { } fake ? fake(settings) : settings.Provider switch
        {
            // Beta endpoint: needed for the server-side refusal fallback.
            AiProviders.Anthropic => new AnthropicDefaultsChatClient(_anthropic.Value.Beta.AsIChatClient(model, settings.MaxOutputTokens), model, settings.Effort, options.Anthropic.RefusalFallback),
            AiProviders.Ollama => new OllamaDefaultsChatClient(new OllamaApiClient(
                new HttpClient { BaseAddress = new Uri(options.Ollama.Url), Timeout = TimeSpan.FromSeconds(options.Ollama.TimeoutSeconds) }, model), options.Ollama.ContextTokens),
            _ => throw new InvalidOperationException($"Unknown AI provider '{settings.Provider}' for feature '{feature}' (use {AiProviders.Anthropic} or {AiProviders.Ollama})"),
        };

        return new AuditingChatClient(inner, feature, settings.Provider!, model, audit, _masker, context);
    }

    /// <summary>
    /// Local embeddings (bge-m3 via Ollama) for clause and rule search. Not audited: nothing leaves the machine, and
    /// the inputs are public standards text and drafted rules.
    /// </summary>
    public IEmbeddingGenerator<string, Embedding<float>> CreateEmbeddingGenerator() =>
        EmbeddingOverride ?? new OllamaApiClient(
            new HttpClient { BaseAddress = new Uri(options.Ollama.Url), Timeout = TimeSpan.FromSeconds(options.Ollama.TimeoutSeconds) }, options.Ollama.EmbeddingModel);

    /// <summary>Tests: replaces the embedding generator.</summary>
    internal IEmbeddingGenerator<string, Embedding<float>>? EmbeddingOverride { get; init; }

    /// <summary>Default request options for a feature (output limit).</summary>
    public ChatOptions ChatOptionsFor(string feature) => new() { MaxOutputTokens = options.For(feature).MaxOutputTokens };
}
