using ApiTestTool.AI;
using ApiTestTool.AI.Summaries;
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
            "summarize" => await SummarizeAsync(cli, cancellationToken),
            _ => throw new ConfigurationException("""
                Usage: aqt ai check [--provider anthropic|ollama] [--model name] [--db <connection>]
                       aqt ai summarize <run-id|latest> [--bank BIC] [--provider anthropic|ollama] [--model name] [--show-input]
                """),
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

    /// <summary>Summarizes a stored run (the store is required: the summary is read from and written to it).</summary>
    private static async Task<int> SummarizeAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var connection = StoreCommands.RunConnection(cli) ?? StoreConnection.DockerComposeDefault;
        var store = new AqtStore(connection);

        var target = cli.Positional.Skip(1).FirstOrDefault() ?? "latest";
        var runId = target.Equals("latest", StringComparison.OrdinalIgnoreCase)
            ? await store.GetLatestRunIdAsync(cli.Get("bank"), cancellationToken) ?? throw new ConfigurationException("The store has no runs yet (aqt db import, or run with --db)")
            : target;

        var options = new AiOptions();
        if (cli.Get("provider") is not null || cli.Get("model") is not null)
            options.Features[AiFeatures.Summary] = new AiFeatureOptions { Provider = cli.Get("provider"), Model = cli.Get("model") };

        var factory = new ChatClientFactory(options, new StoreAuditSink(store, Out.Notice), store.Masker);
        var settings = factory.Describe(AiFeatures.Summary);

        if (cli.Has("show-input"))
        {
            var run = await store.GetRunAsync(runId, cancellationToken) ?? throw new ConfigurationException($"Run '{runId}' is not in the store");
            Out.Muted("--- input sent to the model ---");
            Console.WriteLine(RunDigest.Build(run, await store.GetPreviousRunAsync(run, cancellationToken)).Text);
            Out.Muted("--- end of input ---");
        }

        Out.Muted($"Summarizing {runId} with {settings.Provider} {settings.Model} ...");
        var result = await new RunSummarizer(store, factory).SummarizeAsync(runId, $"{Environment.UserName} (aqt ai summarize)", cancellationToken);
        var e = result.Entity;

        Console.WriteLine();
        if (result.Summary is { } summary)
            Console.WriteLine(summary.ToText());
        else
            Out.Error($"No summary ({e.Status}): {e.Text}");

        foreach (var note in e.ValidationNotes)
            Out.Notice("check: " + note);
        Out.Muted($"{e.Provider} {e.Model} · {e.DurationMs} ms · stored in run_summaries ({e.Id})");
        return result.Summary is null ? ExitCodes.TestsFailed : ExitCodes.Success;
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
