using ApiTestTool.AI;
using ApiTestTool.Engine;
using ApiTestTool.Store;
using Microsoft.Extensions.AI;

namespace ApiTestTool.Cli;

/// <summary><c>aqt ai ...</c></summary>
internal static class AiCommands
{
    public static async Task<int> AiAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var sub = cli.Positional.FirstOrDefault() ?? "";
        return sub switch
        {
            "check" => await CheckAsync(cli, cancellationToken),
            _ => throw new ConfigurationException("Usage: aqt ai check [--provider anthropic|ollama] [--model name] [--db <connection>]"),
        };
    }

    /// <summary>Sends a one-line prompt to each provider to prove the key / local server and the audit log work.</summary>
    private static async Task<int> CheckAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var providers = cli.Get("provider") is { } p ? [p] : new[] { AiProviders.Anthropic, AiProviders.Ollama };
        var (audit, where) = AuditSink(cli);
        var ok = true;

        foreach (var provider in providers)
        {
            var options = new AiOptions();
            options.Features[AiFeatures.Check] = new AiFeatureOptions { Provider = provider, Model = cli.Get("model") };
            var factory = new ChatClientFactory(options, audit);
            var settings = factory.Describe(AiFeatures.Check);

            try
            {
                var client = factory.Create(AiFeatures.Check, new AiCallContext(TriggeredBy: $"{Environment.UserName} (aqt ai check)"));
                var started = DateTime.UtcNow;
                var response = await client.GetResponseAsync("Reply with exactly: OK", factory.ChatOptionsFor(AiFeatures.Check), cancellationToken);
                var ms = (long)(DateTime.UtcNow - started).TotalMilliseconds;
                Out.Ok($"{provider,-9} {settings.Model,-18} {ms} ms  in {response.Usage?.InputTokenCount} / out {response.Usage?.OutputTokenCount} tokens  → {response.Text.Trim()}");
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                ok = false;
                Out.Error($"{provider,-9} {settings.Model,-18} {Hint(provider, ex)}");
            }
        }

        Out.Muted($"Audit records written to {where}.");
        return ok ? ExitCodes.Success : ExitCodes.ConfigurationError;
    }

    private static (IAiAuditSink Sink, string Where) AuditSink(CliArgs cli) =>
        StoreCommands.RunConnection(cli) is { } db
            ? (new StoreAuditSink(new AqtStore(db), Out.Notice), "the ai_calls table")
            : (new InMemoryAuditSink(), $"memory only (set --db or {StoreConnection.EnvironmentVariable} to keep them)");

    private static string Hint(string provider, Exception ex)
    {
        var message = ex.GetBaseException().Message;
        return provider switch
        {
            AiProviders.Anthropic when message.Contains("401") || message.Contains("auth", StringComparison.OrdinalIgnoreCase) || message.Contains("API key", StringComparison.OrdinalIgnoreCase)
                => $"no valid key: set ANTHROPIC_API_KEY (or run 'ant auth login'). {message}",
            AiProviders.Ollama when ex is HttpRequestException
                => $"Ollama is not reachable at http://localhost:11434: start it with 'ollama serve'. {message}",
            _ => message,
        };
    }
}
