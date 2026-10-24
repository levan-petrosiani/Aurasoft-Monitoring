using ApiTestTool.AI.Rules;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;
using Microsoft.Extensions.AI;

namespace ApiTestTool.AI.Tests;

public class RuleCheckTests
{
    [Theory]
    [InlineData("MUST", "The ASPSP shall reject the request.", null)]
    [InlineData("MUST", "აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID.", null)]
    [InlineData("MAY", "If this function is supported, the ASPSP may offer it.", null)]
    [InlineData("SHOULD", "2022 წლის 31 მაისამდე მრავლობითი გადახდების მხარდაჭერა უმჯობესია.", null)]
    [InlineData("MUST", "The call contains the allPsd2 sub attribute.", "no requirement keyword")]
    [InlineData("MUST", "The ASPSP may support global consents.", "suggests MAY")]
    public void Questions_a_level_the_quote_does_not_support(string level, string quote, string? expected)
    {
        var note = NormativeLevel.Question(level, quote);
        if (expected is null)
            Assert.Null(note);
        else
            Assert.Contains(expected, note);
    }

    [Fact]
    public void Requirements_naming_different_fields_are_not_duplicates()
    {
        Assert.False(RuleDrafter.SameIdentifiers(
            "The ASPSP must accept a consent request containing 'availableAccounts' with value 'allAccounts'.",
            "The ASPSP must accept a consent request containing 'allPsd2' with value 'allAccounts'."));
        Assert.True(RuleDrafter.SameIdentifiers(
            "The ASPSP rejects validUntil in the past with 400 FORMAT_ERROR.",
            "A past validUntil is rejected by the bank: 400 and FORMAT_ERROR."));
    }

    [Theory]
    [InlineData("BG-IG", "BG")]
    [InlineData("GE-XS2A", "GE")]
    [InlineData("NBG-80-04", "NBG")]
    public void Rule_ids_use_the_standard_family(string standard, string prefix) => Assert.Equal(prefix, RuleDrafter.IdPrefix(standard));
}

public class RuleWorkflowTests(TestDatabase db) : IClassFixture<TestDatabase>
{
    private const string ClauseText = """
        The TPP sends the validUntil date. If the value exceeds 90 days from the request, the ASPSP shall return the error PERIOD_INVALID.
        A value of 9999-12-31 shall be replaced by the maximum allowed date.
        """;

    private static string Answer(params (string Level, string Requirement, string Quote)[] rules) => System.Text.Json.JsonSerializer.Serialize(new
    {
        rules = rules.Select(r => new
        {
            title = "t",
            level = r.Level,
            service = "AIS",
            area = "CON",
            requirement = r.Requirement,
            quote = r.Quote,
            quoteEnglish = "",
            operation = "POST /v2/consents/account-access",
            check = new { kind = "negative", description = "d", expectStatus = new[] { 400 }, expectErrorCodes = new[] { "PERIOD_INVALID" } },
            notes = "",
        }),
        skipped = "examples",
    });

    /// <summary>Each test uses its own standard (and so its own rule id prefix): the tests share one database.</summary>
    private async Task<string> SeedClauseAsync(string standard)
    {
        var key = $"{standard}-IG:1.0:9.1";
        await db.Store.ReplaceStandardAsync(
            new StandardDocumentEntity { Id = $"{standard}-IG", Version = "1.0", Title = "Test", Language = "en", Url = "u", Sha256 = "x", ClauseCount = 1 },
            [new ClauseEntity { Standard = $"{standard}-IG", Version = "1.0", Number = "9.1", Title = "Consent term", Text = ClauseText, Level = 1, Ord = 0, Language = "en", Key = key }]);
        return key;
    }

    private RuleDrafter Drafter(params string[] answers)
    {
        var queue = new Queue<string>(answers);
        return new RuleDrafter(db.Store, new ChatClientFactory(new AiOptions(), new StoreAuditSink(db.Store), db.Store.Masker)
        {
            ProviderOverride = _ => new ScriptedClient(() => queue.Dequeue()),
            EmbeddingOverride = new BagOfWordsEmbeddings(),
        });
    }

    [DatabaseFact]
    public async Task Drafts_are_checked_numbered_and_recorded()
    {
        var key = await SeedClauseAsync("TS");
        var result = await Drafter(Answer(
            ("MUST", "The ASPSP rejects validUntil beyond 90 days with PERIOD_INVALID.", "If the value exceeds 90 days from the request, the ASPSP shall return the error PERIOD_INVALID."),
            ("MUST", "The ASPSP replaces 9999-12-31 by the maximum date.", "A value of 9999-12-31 must be replaced by the maximum allowed date."), // "must" is not in the text
            ("MAY", "The ASPSP shortens the term.", "A value of 9999-12-31 shall be replaced by the maximum allowed date.")))  // level not backed
            .DraftAsync(key, "tester");

        Assert.Equal("ok", result.Status);
        Assert.Equal(["TS-CON-001", "TS-CON-002", "TS-CON-003"], result.Rules.Select(r => r.Id));
        Assert.Equal(["draft", "invalid", "draft"], result.Rules.Select(r => r.Status));
        Assert.Contains(result.Rules[1].CheckNotes, n => n.StartsWith("Quote check failed", StringComparison.Ordinal));
        Assert.Contains(result.Rules[2].CheckNotes, n => n.Contains("suggests MUST"));

        var history = await db.Store.GetRuleHistoryAsync("TS-CON-002");
        Assert.Equal(("drafted", "tester"), (history.Single().Action, history.Single().Reviewer));

        // A second draft of the same clause continues the numbering and flags the near-identical requirement.
        var again = await Drafter(Answer(("MUST", "The ASPSP rejects validUntil beyond 90 days with PERIOD_INVALID.", "the ASPSP shall return the error PERIOD_INVALID"))).DraftAsync(key);
        var dup = Assert.Single(again.Rules);
        Assert.Equal(("TS-CON-004", "TS-CON-001"), (dup.Id, dup.SimilarTo));
    }

    [DatabaseFact]
    public async Task Review_actions_record_the_reviewer_and_guard_the_workflow()
    {
        var key = await SeedClauseAsync("TR");
        var rules = (await Drafter(Answer(
            ("MUST", "Rule A about PERIOD_INVALID.", "the ASPSP shall return the error PERIOD_INVALID"),
            ("MUST", "Rule B about the maximum date.", "A value of 9999-12-31 shall be replaced"))).DraftAsync(key)).Rules;
        var book = new RuleBook(db.Store);
        var (a, b) = (rules[0].Id, rules[1].Id);

        await Assert.ThrowsAsync<InvalidOperationException>(() => book.ApproveAsync(a, " "));                 // reviewer required
        await Assert.ThrowsAsync<InvalidOperationException>(() => book.RejectAsync(b, "Ana", ""));           // reason required
        var edit = await Assert.ThrowsAsync<InvalidOperationException>(() => book.EditAsync(a, "Ana", new RuleEdit(Quote: "the ASPSP may ignore it")));
        Assert.Contains("not in the clause", edit.Message);

        await book.EditAsync(a, "Ana", new RuleEdit(Level: "should", Requirement: "Rule A, clarified."));
        var approved = await book.ApproveAsync(a, "Ana", "matches the standard");
        Assert.Equal(("approved", "Ana", "SHOULD"), (approved.Status, approved.ReviewedBy, approved.Level));
        await Assert.ThrowsAsync<InvalidOperationException>(() => book.EditAsync(a, "Ana", new RuleEdit(Title: "x"))); // reopen first

        await book.RejectAsync(b, "Ana", "duplicates the BG rule");
        await book.ReopenAsync(b, "Levan");
        Assert.Equal("draft", (await db.Store.GetRuleAsync(b))!.Status);

        var history = await db.Store.GetRuleHistoryAsync(a);
        Assert.Equal(["drafted", "edited", "approved"], history.Select(h => h.Action));
        Assert.Equal("MUST", System.Text.Json.JsonDocument.Parse(history[1].Before!).RootElement.GetProperty("level").GetString());
        Assert.Equal("SHOULD", System.Text.Json.JsonDocument.Parse(history[1].After!).RootElement.GetProperty("level").GetString());
    }

    [DatabaseFact]
    public async Task A_stale_review_is_refused()
    {
        var key = await SeedClauseAsync("TQ");
        var rule = (await Drafter(Answer(("MUST", "Rule C.", "the ASPSP shall return the error PERIOD_INVALID"))).DraftAsync(key)).Rules.Single();
        var stale = await db.Store.GetRuleAsync(rule.Id);

        await new RuleBook(db.Store).ApproveAsync(rule.Id, "Ana");

        stale!.Status = "rejected";
        var ex = await Assert.ThrowsAsync<InvalidOperationException>(() => db.Store.UpdateRuleAsync(stale, stale.UpdatedUtc, new RuleReviewEntity { RuleId = rule.Id, Reviewer = "Levan", Action = "rejected" }));
        Assert.Contains("changed by someone else", ex.Message);
    }

    [DatabaseFact]
    public async Task Exports_only_approved_rules_as_yaml_and_removes_stale_files()
    {
        var key = await SeedClauseAsync("TP");
        var rule = (await Drafter(Answer(("MUST", "Rule D with \"quotes\".", "the ASPSP shall return the error PERIOD_INVALID"))).DraftAsync(key)).Rules.Single();
        var book = new RuleBook(db.Store);
        await book.ApproveAsync(rule.Id, "Ana");
        var dir = Directory.CreateTempSubdirectory("aqt-rules-").FullName;
        try
        {
            File.WriteAllText(Path.Combine(dir, "_example.yaml"), "kept");
            Directory.CreateDirectory(Path.Combine(dir, "tp"));
            File.WriteAllText(Path.Combine(dir, "tp", "TP-OLD-001.yaml"), "stale");

            var (written, removed) = await book.ExportAsync(dir);

            var yaml = File.ReadAllText(Path.Combine(dir, "tp", rule.Id + ".yaml"));
            Assert.True(written >= 1);
            Assert.Equal(1, removed);
            Assert.Contains($"id: {rule.Id}", yaml);
            Assert.Contains("requirement: \"Rule D with \\\"quotes\\\".\"", yaml);
            Assert.Contains("by: \"Ana\"", yaml);
            Assert.True(File.Exists(Path.Combine(dir, "_example.yaml")));
            var parsed = new YamlDotNet.Serialization.Deserializer().Deserialize<Dictionary<string, object>>(yaml); // valid YAML
            Assert.Equal("approved", parsed["status"]);
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    private sealed class ScriptedClient(Func<string> answer) : IChatClient
    {
        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, answer())));

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    /// <summary>Deterministic stand-in for bge-m3: hashed bag of words, so equal texts are identical and different ones differ.</summary>
    private sealed class BagOfWordsEmbeddings : IEmbeddingGenerator<string, Embedding<float>>
    {
        public Task<GeneratedEmbeddings<Embedding<float>>> GenerateAsync(IEnumerable<string> values, EmbeddingGenerationOptions? options = null, CancellationToken cancellationToken = default)
        {
            var result = new GeneratedEmbeddings<Embedding<float>>();
            foreach (var text in values)
            {
                var v = new float[ApiTestTool.Store.AqtDbContext.EmbeddingDimensions];
                foreach (var word in text.ToLowerInvariant().Split(' ', '.', ',', ':'))
                    if (word.Length > 0)
                        v[(word.GetHashCode(StringComparison.Ordinal) & 0x7fffffff) % v.Length] += 1;
                result.Add(new Embedding<float>(v));
            }

            return Task.FromResult(result);
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }
}
