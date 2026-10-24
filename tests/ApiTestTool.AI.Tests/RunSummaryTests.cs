using System.Text.Json;
using ApiTestTool.AI.Summaries;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Tests;

public class RunSummaryTests
{
    private const string SpecRef = "NextGenPSD2 IG (ENG) 6.3.1.2 Consent Request on Account List; 14.11.1 (p. 144)";

    private static RunEntity Run(string id, DateTime created, params (string Id, string Outcome)[] scenarios) => new()
    {
        Id = id,
        Source = "cli",
        Status = "Completed",
        BankBic = "TESTGE22",
        CreatedUtc = created,
        Scenarios = [.. scenarios.Select((s, i) => new ScenarioResultEntity
        {
            RunId = id, Ordinal = i, ScenarioId = s.Id, Title = s.Id, Suite = "negative", Severity = "Major", Outcome = s.Outcome,
            SpecRef = s.Id == "NEG-CON-005" ? SpecRef : null,
            Reason = s.Outcome == "Failed" ? "Error code 'FORMAT_ERROR', expected 'PARAMETER_NOT_SUPPORTED'" : null,
            Steps = s.Outcome == "Failed"
                ? [new StepResultEntity
                {
                    RunId = id, Name = "POST consent", Kind = "request", Outcome = "Failed", Failures = ["Error code 'FORMAT_ERROR', expected 'PARAMETER_NOT_SUPPORTED'"],
                    Exchanges = [new ExchangeEntity { RunId = id, Method = "POST", Url = "https://bank/v2/consents/account-access", StatusCode = 400, ResponseBody = """{"code":"FORMAT_ERROR"}""" }],
                }]
                : [],
        })],
    };

    [Fact]
    public void Digest_lists_problems_with_evidence_and_computes_changes_in_code()
    {
        var previous = Run("PREV", new DateTime(2026, 10, 21), ("PRE-001", "Passed"), ("NEG-CON-005", "Blocked"), ("AIS-001", "Passed"));
        var current = Run("CUR", new DateTime(2026, 10, 22), ("PRE-001", "Passed"), ("NEG-CON-005", "Failed"), ("AIS-002", "Skipped"));

        var digest = RunDigest.Build(current, previous);

        Assert.Equal(["NEG-CON-005"], digest.ProblemScenarioIds);
        Assert.Equal([new OutcomeChange("NEG-CON-005", "Blocked", "Failed")], digest.Changes); // AIS-001/002 were not run both times
        Assert.Contains("FORMAT_ERROR", digest.Text);
        Assert.Contains("POST https://bank/v2/consents/account-access → 400", digest.Text);
        Assert.Contains("PASSED: PRE-001", digest.Text);
        Assert.Contains(SpecRef, digest.SpecRefs);
    }

    [Fact]
    public void Parses_answers_wrapped_in_think_blocks_and_code_fences()
    {
        var summary = RunSummary.Parse("""
            <think>The user wants JSON {not this}</think>
            ```json
            {"headline":"h","health":"degraded","findings":[],"attention":[]}
            ```
            """);

        Assert.Equal(("h", "degraded"), (summary.Headline, summary.Health));
        Assert.Throws<JsonException>(() => RunSummary.Parse("Sorry, I cannot help with that."));
    }

    [Fact]
    public void Validation_drops_unknown_scenarios_canonicalises_clauses_and_flags_unexplained_failures()
    {
        var digest = RunDigest.Build(Run("CUR", DateTime.UtcNow, ("NEG-CON-005", "Failed"), ("NEG-CON-006", "Failed")), null);
        var summary = new RunSummary
        {
            Headline = "h",
            Health = "Degraded",
            Findings =
            [
                new() { Title = "wrong code", Side = "BANK", Severity = "major", Scenarios = ["NEG-CON-005", "NEG-CON-999"], SpecRef = "NextGenPSD2 IG 6.3.1.2, 14.11.1" },
                new() { Title = "made up", Side = "bank", Severity = "Minor", Scenarios = [], SpecRef = "PSD2 RTS Article 36" },
            ],
        };

        var notes = summary.Validate(digest);

        Assert.Equal("degraded", summary.Health);
        Assert.Equal(("bank", "Major"), (summary.Findings[0].Side, summary.Findings[0].Severity));
        Assert.Equal(["NEG-CON-005"], summary.Findings[0].Scenarios);
        Assert.Equal(SpecRef, summary.Findings[0].SpecRef); // shortened citation → the run's exact reference
        Assert.Equal("", summary.Findings[1].SpecRef);    // not in the run → cleared
        Assert.Contains(notes, n => n.Contains("NEG-CON-999"));
        Assert.Contains(notes, n => n.Contains("PSD2 RTS Article 36"));
        Assert.Contains(notes, n => n.Contains("Not explained by any finding: NEG-CON-006"));
        Assert.Equal("No earlier run with results for this bank.", summary.Comparison);
    }

    [Fact]
    public void Findings_about_passed_scenarios_become_warnings_and_the_comparison_is_written_in_code()
    {
        var previous = Run("PREV", new DateTime(2026, 10, 21), ("PRE-002", "Passed"), ("PRE-003", "Failed"), ("NEG-CON-005", "Blocked"));
        var digest = RunDigest.Build(Run("CUR", new DateTime(2026, 10, 22), ("PRE-002", "Passed"), ("PRE-003", "Passed"), ("NEG-CON-005", "Failed")), previous);
        var summary = new RunSummary
        {
            Headline = "h",
            Health = "degraded",
            Findings =
            [
                new() { Title = "certificate not trusted", Scenarios = ["PRE-002"] },
                new() { Title = "wrong code", Scenarios = ["NEG-CON-005"] },
            ],
            Comparison = "Previously failed NEG-CON-005 is now Failed", // a model's wrong wording is replaced
        };

        summary.Validate(digest);

        Assert.Equal(["wrong code"], summary.Findings.Select(f => f.Title));
        Assert.Equal(["PRE-002: certificate not trusted"], summary.Attention);
        Assert.Contains("Blocked → Failed: NEG-CON-005", summary.Comparison);
        Assert.Contains("Failed → Passed: PRE-003", summary.Comparison);
    }

    [Theory]
    [InlineData("6.3.1.2", true)]
    [InlineData("IG 14.11.1", true)]
    [InlineData("6.3.1.9", false)]
    [InlineData("Berlin Group spec", false)]
    public void Clause_numbers_must_all_appear_in_a_run_reference(string cited, bool backed) =>
        Assert.Equal(backed, RunSummary.MatchSpecRef(cited, [SpecRef]) is not null);
}

public class RunSummarizerTests(TestDatabase db) : IClassFixture<TestDatabase>
{
    private const string ValidAnswer = """
        {"headline":"Consent validation returns the wrong error code","health":"degraded",
         "findings":[{"title":"FORMAT_ERROR instead of expected code","side":"bank","severity":"Major","scenarios":["NEG-CON-003"],
                      "explanation":"x","evidence":"Expected status 400 but got 201","specRef":"","nextStep":"Bank fixes validation"}],
         "attention":[]}
        """;

    private ChatClientFactory Factory(params string[] answers)
    {
        var queue = new Queue<string>(answers);
        return new ChatClientFactory(new AiOptions(), new StoreAuditSink(db.Store), db.Store.Masker)
        {
            ProviderOverride = _ => new ScriptedChatClient(() => queue.Dequeue()),
        };
    }

    private async Task<string> SeedRunAsync(string id)
    {
        await db.Store.SaveRunAsync(new RunInfo { Id = id, Source = "cli", Status = "Completed", BankBic = "TESTGE22" }, TestRuns.Create());
        return id;
    }

    [DatabaseFact]
    public async Task Stores_a_validated_summary_linked_to_its_audit_record()
    {
        var runId = await SeedRunAsync("SUM-OK");

        var result = await new RunSummarizer(db.Store, Factory(ValidAnswer)).SummarizeAsync(runId, "tester");

        Assert.Equal("ok", result.Entity.Status);
        Assert.Equal("degraded", result.Summary!.Health);
        var stored = await db.Store.GetLatestRunSummaryAsync(runId);
        Assert.Equal(result.Entity.Id, stored!.Id);

        await using var ctx = db.Store.CreateContext();
        var call = ctx.AiCalls.Single(c => c.Id == stored.AiCallId);
        Assert.Equal((AiFeatures.Summary, runId, "tester"), (call.Feature, call.RunId, call.TriggeredBy));
        Assert.DoesNotContain("SECRET", call.Request); // the digest is built from masked store data
    }

    [DatabaseFact]
    public async Task Retries_once_when_the_answer_is_not_json_then_succeeds()
    {
        var runId = await SeedRunAsync("SUM-RETRY");

        var result = await new RunSummarizer(db.Store, Factory("Here is my analysis: it failed.", ValidAnswer)).SummarizeAsync(runId);

        Assert.Equal("ok", result.Entity.Status);
        await using var ctx = db.Store.CreateContext();
        Assert.Equal(2, ctx.AiCalls.Count(c => c.RunId == runId));
    }

    [DatabaseFact]
    public async Task Keeps_the_raw_text_when_both_answers_are_unreadable_and_records_provider_errors()
    {
        var unreadable = await new RunSummarizer(db.Store, Factory("no json", "still no json")).SummarizeAsync(await SeedRunAsync("SUM-BAD"));
        Assert.Equal(("unstructured", "still no json"), (unreadable.Entity.Status, unreadable.Entity.Text));

        var failing = new ChatClientFactory(new AiOptions(), new StoreAuditSink(db.Store))
        {
            ProviderOverride = _ => new ScriptedChatClient(() => throw new HttpRequestException("connection refused")),
        };
        var failed = await new RunSummarizer(db.Store, failing).SummarizeAsync(await SeedRunAsync("SUM-FAIL"));
        Assert.Equal("failed", failed.Entity.Status);
        Assert.Contains("connection refused", failed.Entity.Text);
    }

    [DatabaseFact]
    public async Task Runs_without_results_get_a_summary_without_a_model_call()
    {
        await db.Store.SaveRunAsync(new RunInfo { Id = "SUM-CANCELLED", Source = "portal", Status = "Cancelled", BankBic = "TESTGE22", Error = "Cancelled by user" }, null);

        var result = await new RunSummarizer(db.Store, Factory()).SummarizeAsync("SUM-CANCELLED");

        Assert.Equal(("ok", "none", "inconclusive"), (result.Entity.Status, result.Entity.Provider, result.Summary!.Health));
        Assert.Contains("Cancelled by user", result.Summary.Headline);
    }

    private sealed class ScriptedChatClient(Func<string> answer) : IChatClient
    {
        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, answer())) { ModelId = "scripted" });

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }
}
