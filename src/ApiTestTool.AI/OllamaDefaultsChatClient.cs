using Microsoft.Extensions.AI;
using OllamaSharp;
using OllamaSharp.Models;

namespace ApiTestTool.AI;

/// <summary>Sets the context window (<c>num_ctx</c>) on every Ollama request unless the caller already did.</summary>
internal sealed class OllamaDefaultsChatClient(IChatClient inner, int contextTokens) : DelegatingChatClient(inner)
{
    public override Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
        base.GetResponseAsync(messages, Apply(options), cancellationToken);

    public override IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
        base.GetStreamingResponseAsync(messages, Apply(options), cancellationToken);

    private ChatOptions Apply(ChatOptions? options)
    {
        var o = options?.Clone() ?? new ChatOptions();
        if (o.AdditionalProperties?.ContainsKey(OllamaOption.NumCtx.Name) != true)
            o.AddOllamaOption(OllamaOption.NumCtx, contextTokens);
        return o;
    }
}
