using ApiTestTool.Store.Masking;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Tests;

public class AiLayerTests
{
    [Fact]
    public void Defaults_send_drafting_to_claude_and_feature_overrides_win()
    {
        var options = new AiOptions();
        options.Features[AiFeatures.Summary] = new AiFeatureOptions { Provider = AiProviders.Ollama };
        options.Features[AiFeatures.Ask] = new AiFeatureOptions { Provider = AiProviders.Ollama, Model = "qwen3:8b" };

        Assert.Equal((AiProviders.Anthropic, "claude-opus-5-5"), (options.For(AiFeatures.RuleDrafting).Provider, options.For(AiFeatures.RuleDrafting).Model));
        Assert.Equal((AiProviders.Ollama, "qwen3:8b"), (options.For(AiFeatures.Summary).Provider, options.For(AiFeatures.Summary).Model));
        Assert.Equal("qwen3:8b", options.For(AiFeatures.Ask).Model);
        Assert.Equal((16000, "medium"), (options.For(AiFeatures.Summary).MaxOutputTokens, options.For(AiFeatures.Summary).Effort)); // defaults kept when not overridden
    }

    [Fact]
    public void Factory_builds_both_providers_wrapped_in_the_audit_client()
    {
        var options = new AiOptions { Anthropic = { ApiKey = "test-key-not-used" } };
        options.Features[AiFeatures.Summary] = new AiFeatureOptions { Provider = AiProviders.Ollama };
        var factory = new ChatClientFactory(options, new InMemoryAuditSink());

        Assert.IsType<AuditingChatClient>(factory.Create(AiFeatures.RuleDrafting));
        Assert.IsType<AuditingChatClient>(factory.Create(AiFeatures.Summary));

        options.Features[AiFeatures.Check] = new AiFeatureOptions { Provider = "openai" };
        Assert.Throws<InvalidOperationException>(() => factory.Create(AiFeatures.Check));
    }

    [Fact]
    public async Task Masks_outgoing_text_and_records_the_call()
    {
        var inner = new FakeChatClient(_ => new ChatResponse(new ChatMessage(ChatRole.Assistant, "fine"))
        {
            ModelId = "fake-1",
            Usage = new UsageDetails { InputTokenCount = 12, OutputTokenCount = 3 },
        });
        var audit = new InMemoryAuditSink();
        var client = new AuditingChatClient(inner, AiFeatures.Summary, "fake", "fake-1", audit, SensitiveDataMasker.Default, new AiCallContext("RUN-1", "tester"));

        var response = await client.GetResponseAsync("""{"access_token":"SECRET","iban":"GE29NB0000000101904917"}""");

        Assert.Equal("fine", response.Text);
        Assert.DoesNotContain("SECRET", inner.LastPrompt);
        Assert.DoesNotContain("GE29NB0000000101904917", inner.LastPrompt);

        var call = Assert.Single(audit.Calls);
        Assert.Equal((AiFeatures.Summary, "fake-1", "RUN-1", "tester", true), (call.Feature, call.Model, call.RunId, call.TriggeredBy, call.Succeeded));
        Assert.Equal((12L, 3L), (call.InputTokens, call.OutputTokens));
        Assert.DoesNotContain("SECRET", call.Request);
        Assert.Equal(64, call.PromptSha256.Length);
    }

    [Fact]
    public async Task Failed_calls_are_recorded_and_rethrown()
    {
        var audit = new InMemoryAuditSink();
        var client = new AuditingChatClient(new FakeChatClient(_ => throw new HttpRequestException("connection refused")),
            AiFeatures.Ask, "ollama", "gemma4:26b", audit, SensitiveDataMasker.Default);

        await Assert.ThrowsAsync<HttpRequestException>(() => client.GetResponseAsync("hello"));

        var call = Assert.Single(audit.Calls);
        Assert.False(call.Succeeded);
        Assert.Contains("connection refused", call.Error);
    }

    [Fact]
    public async Task Streaming_calls_are_recorded_once_complete()
    {
        var audit = new InMemoryAuditSink();
        var client = new AuditingChatClient(new FakeChatClient(_ => new ChatResponse(new ChatMessage(ChatRole.Assistant, "streamed answer"))),
            AiFeatures.Ask, "fake", "fake-1", audit, SensitiveDataMasker.Default);

        var text = "";
        await foreach (var update in client.GetStreamingResponseAsync("question"))
            text += update.Text;

        Assert.Equal("streamed answer", text);
        Assert.Equal("streamed answer", Assert.Single(audit.Calls).Response);
    }

    private sealed class FakeChatClient(Func<IList<ChatMessage>, ChatResponse> respond) : IChatClient
    {
        public string LastPrompt { get; private set; } = "";

        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
        {
            var list = messages.ToList();
            LastPrompt = string.Join("\n", list.Select(m => m.Text));
            return Task.FromResult(respond(list));
        }

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
        {
            var response = await GetResponseAsync(messages, options, cancellationToken);
            foreach (var update in response.ToChatResponseUpdates())
                yield return update;
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }
}
