using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Tests;

/// <summary>Checks the exact HTTP request sent to the Claude API (captured, no network, no key needed).</summary>
public class AnthropicRequestTests
{
    private const string CannedResponse = """
        {"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",
         "content":[{"type":"text","text":"OK"}],"stop_reason":"end_turn","stop_sequence":null,
         "usage":{"input_tokens":10,"output_tokens":2}}
        """;

    [Fact]
    public async Task Sends_model_effort_adaptive_thinking_and_the_default_refusal_fallback()
    {
        var capture = new CaptureHandler(CannedResponse);
        var options = new AiOptions { Anthropic = { ApiKey = "test-key" } };
        var factory = new ChatClientFactory(options, new InMemoryAuditSink(), anthropicHttp: new HttpClient(capture));

        var response = await factory.Create(AiFeatures.Summary).GetResponseAsync("Summarize", factory.ChatOptionsFor(AiFeatures.Summary));

        Assert.Equal("OK", response.Text);
        var body = JsonNode.Parse(capture.Body!)!;
        Assert.Equal("claude-opus-5-5", (string?)body["model"]);
        Assert.Equal(16000, (int?)body["max_tokens"]);
        Assert.Equal("default", (string?)body["fallbacks"]);
        Assert.Equal("medium", (string?)body["output_config"]?["effort"]);
        Assert.Equal("adaptive", (string?)body["thinking"]?["type"]);
        Assert.Contains("server-side-fallback-2026-07-01", capture.Headers["anthropic-beta"]);
        Assert.Contains("/v1/messages", capture.Url);
    }

    [Fact]
    public async Task Fallback_can_be_turned_off_and_effort_follows_configuration()
    {
        var capture = new CaptureHandler(CannedResponse);
        var options = new AiOptions { Anthropic = { ApiKey = "test-key", RefusalFallback = false } };
        options.Features[AiFeatures.Check] = new AiFeatureOptions { Effort = "high" };
        var factory = new ChatClientFactory(options, new InMemoryAuditSink(), anthropicHttp: new HttpClient(capture));

        await factory.Create(AiFeatures.Check).GetResponseAsync("ping", factory.ChatOptionsFor(AiFeatures.Check));

        var body = JsonNode.Parse(capture.Body!)!;
        Assert.Null(body["fallbacks"]);
        Assert.Equal("high", (string?)body["output_config"]?["effort"]);
        Assert.False(capture.Headers.TryGetValue("anthropic-beta", out var beta) && beta.Contains("server-side-fallback", StringComparison.Ordinal));
    }

    private sealed class CaptureHandler(string response) : HttpMessageHandler
    {
        public string? Body { get; private set; }

        public string? Url { get; private set; }

        public Dictionary<string, string> Headers { get; } = new(StringComparer.OrdinalIgnoreCase);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Url = request.RequestUri?.ToString();
            Body = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
            foreach (var h in request.Headers)
                Headers[h.Key] = string.Join(",", h.Value);

            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(response, Encoding.UTF8, "application/json") };
        }
    }
}
