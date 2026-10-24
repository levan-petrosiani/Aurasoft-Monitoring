using Anthropic.Models.Beta.Messages;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI;

/// <summary>
/// Applies the per-feature Claude settings to every request: effort (adaptive thinking depth) and, when enabled,
/// the server-side refusal fallback (<c>fallbacks: "default"</c> with the <c>server-side-fallback-2026-07-01</c> beta).
/// Settings a caller already put on <see cref="ChatOptions"/> are left alone.
/// </summary>
internal sealed class AnthropicDefaultsChatClient(IChatClient inner, string model, string? effort, bool refusalFallback) : DelegatingChatClient(inner)
{
    internal const string FallbackBeta = "server-side-fallback-2026-07-01";

    public override Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
        base.GetResponseAsync(messages, Apply(options), cancellationToken);

    public override IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
        base.GetStreamingResponseAsync(messages, Apply(options), cancellationToken);

    private ChatOptions Apply(ChatOptions? options)
    {
        var o = options?.Clone() ?? new ChatOptions();
        if (o.Reasoning is null && ParseEffort(effort) is { } e)
            o.Reasoning = new ReasoningOptions { Effort = e };

        if (refusalFallback && o.RawRepresentationFactory is null)
        {
            // The adapter fills model, messages and limits into this object; we only add what ChatOptions can't express.
            o.RawRepresentationFactory = _ => new MessageCreateParams
            {
                Model = model,
                MaxTokens = o.MaxOutputTokens ?? 16000,
                Messages = [],
                Betas = [FallbackBeta],
                Fallbacks = new Default(),
            };
        }

        return o;
    }

    internal static ReasoningEffort? ParseEffort(string? value) => value?.ToLowerInvariant() switch
    {
        null or "" => null,
        "low" => ReasoningEffort.Low,
        "medium" => ReasoningEffort.Medium,
        "high" => ReasoningEffort.High,
        "xhigh" or "extrahigh" => ReasoningEffort.ExtraHigh,
        "max" => ReasoningEffort.ExtraHigh, // ChatOptions has no "max"; the closest level is sent
        _ => throw new InvalidOperationException($"Unknown effort '{value}' (use low, medium, high, xhigh)"),
    };
}
