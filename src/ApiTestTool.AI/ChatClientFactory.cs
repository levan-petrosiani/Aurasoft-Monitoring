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
public sealed class ChatClientFactory(AiOptions options, IAiAuditSink audit, SensitiveDataMasker? masker = null)
{
    private readonly SensitiveDataMasker _masker = masker ?? SensitiveDataMasker.Default;
    private readonly Lazy<AnthropicClient> _anthropic = new(() => string.IsNullOrEmpty(options.Anthropic.ApiKey)
        ? new AnthropicClient() // ANTHROPIC_API_KEY or the 'ant auth login' profile
        : new AnthropicClient { ApiKey = options.Anthropic.ApiKey });

    public AiOptions Options => options;

    /// <summary>Provider, model and limits a feature will use.</summary>
    public AiFeatureOptions Describe(string feature) => options.For(feature);

    public IChatClient Create(string feature, AiCallContext? context = null)
    {
        var settings = options.For(feature);
        var model = settings.Model!;
        IChatClient inner = settings.Provider switch
        {
            AiProviders.Anthropic => _anthropic.Value.AsIChatClient(model),
            AiProviders.Ollama => new OllamaApiClient(new Uri(options.Ollama.Url), model),
            _ => throw new InvalidOperationException($"Unknown AI provider '{settings.Provider}' for feature '{feature}' (use {AiProviders.Anthropic} or {AiProviders.Ollama})"),
        };

        return new AuditingChatClient(inner, feature, settings.Provider!, model, audit, _masker, context);
    }

    /// <summary>Default request options for a feature (output limit).</summary>
    public ChatOptions ChatOptionsFor(string feature) => new() { MaxOutputTokens = options.For(feature).MaxOutputTokens };
}
