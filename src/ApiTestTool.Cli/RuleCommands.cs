using System.Text.Json;
using ApiTestTool.AI;
using ApiTestTool.AI.Rules;
using ApiTestTool.AI.Standards;
using ApiTestTool.Engine;
using ApiTestTool.Standards;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;

namespace ApiTestTool.Cli;

/// <summary><c>aqt standards ...</c> and <c>aqt rules ...</c> (Phase 2: rules drafted from the standards).</summary>
internal static class RuleCommands
{
    public static async Task<int> StandardsAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var paths = Paths.Resolve(cli);
        var sources = new StandardSources(paths.Standards);
        var sub = cli.Positional.FirstOrDefault() ?? "list";

        switch (sub)
        {
            case "fetch":
            {
                using var http = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
                var ok = true;
                foreach (var source in Selected(sources, cli))
                {
                    try
                    {
                        Out.Ok($"{source.Id,-9} {source.Version,-9} {await sources.FetchAsync(source, http, cancellationToken)}");
                    }
                    catch (Exception ex) when (ex is HttpRequestException or InvalidDataException or IOException)
                    {
                        ok = false;
                        Out.Error($"{source.Id} {source.Version}: {ex.Message}");
                    }
                }

                return ok ? ExitCodes.Success : ExitCodes.ConfigurationError;
            }

            case "ingest":
            {
                var store = Store(cli);
                var ingestor = new StandardsIngestor(store, sources, Factory(store, cli));
                foreach (var source in Selected(sources, cli))
                {
                    var r = await ingestor.IngestAsync(source, embed: !cli.Has("no-embed"), Out.Muted, cancellationToken);
                    Out.Ok($"{r.Standard,-9} {r.Version,-9} {r.Clauses} clauses, {r.Embedded} embedded, {r.DurationMs / 1000.0:0.0} s");
                    foreach (var p in r.Problems)
                        Out.Notice(p);
                }

                return ExitCodes.Success;
            }

            case "list":
            {
                var docs = await Store(cli).ListStandardsAsync(cancellationToken);
                if (docs.Count == 0)
                    Out.Muted("No standards in the store yet: aqt standards fetch && aqt standards ingest");
                foreach (var d in docs)
                    Console.WriteLine($"{d.Id,-9} {d.Version,-9} {d.Language}  {d.ClauseCount,4} clauses  {d.Title}");
                return ExitCodes.Success;
            }

            case "search":
            {
                var query = string.Join(' ', cli.Positional.Skip(1));
                if (query.Length == 0)
                    throw new ConfigurationException("Usage: aqt standards search \"<text>\" [--standard ID]");

                var store = Store(cli);
                float[]? vector = null;
                try
                {
                    vector = (await Factory(store, cli).CreateEmbeddingGenerator().GenerateAsync([query], cancellationToken: cancellationToken))[0].Vector.ToArray();
                }
                catch (HttpRequestException)
                {
                    Out.Muted("(Ollama not reachable: searching by words only)");
                }

                foreach (var hit in await store.SearchClausesAsync(query, vector, cli.Get("standard"), 10, cancellationToken))
                {
                    var how = (hit.ByWords, hit.ByMeaning) switch { (true, true) => "words+meaning", (true, false) => "words", _ => "meaning" };
                    Console.WriteLine($"{hit.Clause.Key,-28} {hit.Clause.Title[..Math.Min(70, hit.Clause.Title.Length)],-70} {how}");
                }

                return ExitCodes.Success;
            }

            case "show":
            {
                var key = cli.Positional.Skip(1).FirstOrDefault() ?? throw new ConfigurationException("Usage: aqt standards show <clause-key>  (e.g. BG-IG:1.3.16:6.3.1.2)");
                var store = Store(cli);
                var clause = await store.GetClauseAsync(key, cancellationToken) ?? throw new ConfigurationException($"No clause {key}");
                foreach (var a in await store.GetAncestorsAsync(clause, cancellationToken))
                    Out.Muted($"{a.Number} {a.Title}");
                Out.Header($"{clause.Number} {clause.Title}{(clause.Page is { } p ? $"  (p. {p})" : "")}");
                Console.WriteLine(clause.Text);
                foreach (var r in await store.ListRulesAsync(clauseKey: key, cancellationToken: cancellationToken))
                    Out.Muted($"rule {r.Id} [{r.Status}] {r.Level}: {r.Requirement}");
                return ExitCodes.Success;
            }

            default:
                throw new ConfigurationException("Usage: aqt standards fetch|ingest [--standard ID] [--no-embed]|list|search \"<text>\"|show <clause-key>");
        }
    }

    public static async Task<int> RulesAsync(CliArgs cli, CancellationToken cancellationToken)
    {
        var sub = cli.Positional.FirstOrDefault() ?? "list";
        var store = Store(cli);
        var book = new RuleBook(store);
        var id = cli.Positional.Skip(1).FirstOrDefault();

        switch (sub)
        {
            case "draft":
            {
                var key = id ?? throw new ConfigurationException("Usage: aqt rules draft <clause-key> [--provider anthropic|ollama] [--model name]");
                var factory = Factory(store, cli);
                var settings = factory.Describe(AiFeatures.RuleDrafting);
                Out.Muted($"Drafting rules for {key} with {settings.Provider} {settings.Model} ...");

                var result = await new RuleDrafter(store, factory).DraftAsync(key, $"{Environment.UserName} (aqt rules draft)", cancellationToken);
                if (result.Status != "ok")
                {
                    Out.Error(result.Error ?? result.Status);
                    return ExitCodes.TestsFailed;
                }

                foreach (var r in result.Rules)
                    Print(r);
                if (!string.IsNullOrWhiteSpace(result.Skipped))
                    Out.Muted("not drafted: " + result.Skipped);
                Out.Muted($"{result.Rules.Count(r => r.Status == "draft")} draft(s) for review, {result.Rules.Count(r => r.Status == "invalid")} rejected by the checks · {result.DurationMs / 1000.0:0.0} s");
                return ExitCodes.Success;
            }

            case "list":
                foreach (var r in await store.ListRulesAsync(cli.Get("status"), cli.Get("standard"), cancellationToken: cancellationToken))
                    Console.WriteLine($"{r.Id,-14} {r.Status,-9} {r.Level,-6} {r.Service,-6} {r.Standard} {r.ClauseNumber,-9} {r.Title}");
                return ExitCodes.Success;

            case "show":
            {
                var rule = await store.GetRuleAsync(id ?? throw new ConfigurationException("Usage: aqt rules show <id>"), cancellationToken) ?? throw new ConfigurationException($"No rule {id}");
                Print(rule);
                foreach (var h in await store.GetRuleHistoryAsync(rule.Id, cancellationToken))
                    Out.Muted($"  {h.TimeUtc:yyyy-MM-dd HH:mm} {h.Action,-9} by {h.Reviewer}{(h.Note is null ? "" : ": " + h.Note)}");
                return ExitCodes.Success;
            }

            case "approve" or "reject" or "reopen":
            {
                var reviewer = cli.Get("by") ?? throw new ConfigurationException($"Usage: aqt rules {sub} <id> --by <your name> [--note text]");
                var rule = sub switch
                {
                    "approve" => await book.ApproveAsync(id!, reviewer, cli.Get("note"), cancellationToken),
                    "reject" => await book.RejectAsync(id!, reviewer, cli.Get("note") ?? "", cancellationToken),
                    _ => await book.ReopenAsync(id!, reviewer, cli.Get("note"), cancellationToken),
                };
                Out.Ok($"{rule.Id} is now {rule.Status}");
                return ExitCodes.Success;
            }

            case "export":
            {
                var (written, removed) = await book.ExportAsync(Paths.Resolve(cli).Rules, cancellationToken);
                Out.Ok($"{written} approved rule(s) written to rules/, {removed} stale file(s) removed");
                return ExitCodes.Success;
            }

            default:
                throw new ConfigurationException("Usage: aqt rules draft|list|show|approve|reject|reopen|export");
        }
    }

    private static void Print(RuleEntity r)
    {
        Console.WriteLine();
        var line = $"{r.Id}  [{r.Status}]  {r.Level} · {r.Service} · {r.Standard} {r.ClauseNumber}  {r.Title}";
        if (r.Status == "invalid")
            Out.Error(line);
        else
            Out.Ok(line);
        Console.WriteLine($"   requirement: {r.Requirement}");
        Console.WriteLine($"   quote: \"{r.Quote}\"");
        if (r.QuoteEnglish is not null)
            Console.WriteLine($"   quote (EN, AI translation): \"{r.QuoteEnglish}\"");
        if (r.Operation is not null)
            Console.WriteLine($"   operation: {r.Operation}");
        if (r.CheckJson is not null && JsonSerializer.Deserialize<RuleCheck>(r.CheckJson, new JsonSerializerOptions(JsonSerializerDefaults.Web)) is { } c)
            Console.WriteLine($"   check: {c.Kind} — {c.Description}{(c.ExpectStatus.Count > 0 ? $" [status {string.Join("/", c.ExpectStatus)}]" : "")}{(c.ExpectErrorCodes.Count > 0 ? $" [{string.Join("/", c.ExpectErrorCodes)}]" : "")}");
        if (r.Notes is not null)
            Console.WriteLine($"   notes: {r.Notes}");
        foreach (var n in r.CheckNotes)
            Out.Muted("   check: " + n);
    }

    private static IEnumerable<StandardSource> Selected(StandardSources sources, CliArgs cli) =>
        sources.Load().Where(s => cli.Get("standard") is not { } only || s.Id.Equals(only, StringComparison.OrdinalIgnoreCase));

    private static AqtStore Store(CliArgs cli) => new(StoreCommands.RunConnection(cli) ?? StoreConnection.DockerComposeDefault);

    private static ChatClientFactory Factory(AqtStore store, CliArgs cli)
    {
        var options = new AiOptions();
        if (cli.Get("provider") is not null || cli.Get("model") is not null)
            options.Features[AiFeatures.RuleDrafting] = new AiFeatureOptions { Provider = cli.Get("provider"), Model = cli.Get("model") };
        return new ChatClientFactory(options, new StoreAuditSink(store, Out.Notice), store.Masker);
    }
}
