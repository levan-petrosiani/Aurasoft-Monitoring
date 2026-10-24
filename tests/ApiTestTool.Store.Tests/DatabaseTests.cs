using ApiTestTool.Engine.Reporting;
using ApiTestTool.Store.Entities;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace ApiTestTool.Store.Tests;

public class DatabaseTests(TestDatabase db) : IClassFixture<TestDatabase>
{
    [DatabaseFact]
    public async Task Saves_a_run_and_replaces_it_on_save_again()
    {
        var info = new RunInfo { Id = "TESTGE22-20261024-080000", Source = "cli", Status = "Completed", BankBic = "TESTGE22", Suites = ["ais"] };

        await db.Store.SaveRunAsync(info, TestRuns.Create());
        await db.Store.SaveRunAsync(info with { Status = "Completed" }, TestRuns.Create()); // idempotent

        await using var ctx = db.Store.CreateContext();
        var run = await ctx.Runs.Include(r => r.Scenarios).ThenInclude(s => s.Steps).ThenInclude(st => st.Exchanges).SingleAsync(r => r.Id == info.Id);
        Assert.Equal(2, run.Scenarios.Count);
        Assert.Equal(3, run.Scenarios.SelectMany(s => s.Steps).SelectMany(s => s.Exchanges).Count()); // 2 SCA + 1 request; cleanup has none
        Assert.Equal(["ais"], run.Suites);

        // Masked data is what reaches the database.
        var tokenRows = await ctx.Exchanges.CountAsync(e => e.RunId == info.Id && (e.ResponseBody!.Contains("SECRET") || e.RequestBody!.Contains("SECRET")));
        Assert.Equal(0, tokenRows);

        // jsonb headers are queryable.
        var authHeaders = await ctx.Database.SqlQueryRaw<string>(
            "select h->>1 as \"Value\" from exchanges, jsonb_array_elements(request_headers) h where run_id = {0} and h->>0 = 'Authorization'", info.Id).ToListAsync();
        Assert.All(authHeaders, v => Assert.Equal("Bearer ***(masked)", v));
    }

    [DatabaseFact]
    public async Task Delete_cascades_and_events_and_ai_calls_are_stored()
    {
        var info = new RunInfo { Id = "DEL-1", Source = "portal", Status = "Completed", BankBic = "TESTGE22" };
        await db.Store.SaveRunAsync(info, TestRuns.Create());
        await db.Store.AddEventAsync("portal", "run.finished", "done", info.Id, data: new { passed = 1 });
        await db.Store.AddAiCallAsync(new AiCallEntity
        {
            Id = Guid.NewGuid(), TimeUtc = DateTime.UtcNow, Feature = "check", Provider = "ollama", Model = "qwen3:8b",
            PromptSha256 = "x", Request = """[{"role":"user","text":"hi"}]""", Succeeded = true, RunId = info.Id,
        });

        Assert.True(await db.Store.DeleteRunAsync(info.Id));

        await using var ctx = db.Store.CreateContext();
        Assert.Equal(0, await ctx.ScenarioResults.CountAsync(s => s.RunId == info.Id));
        Assert.Equal(0, await ctx.Exchanges.CountAsync(e => e.RunId == info.Id));
        // History is kept: events and AI calls are not owned by the run row.
        Assert.Equal(1, await ctx.Events.CountAsync(e => e.RunId == info.Id));
        Assert.Equal(1, await ctx.AiCalls.CountAsync(c => c.RunId == info.Id));
    }

    [DatabaseFact]
    public async Task Import_skips_existing_runs_unless_replace_is_set()
    {
        var root = Directory.CreateTempSubdirectory("aqt-import-db-").FullName;
        try
        {
            ReportWriter.Write(TestRuns.Create(), Path.Combine(root, "IMPGE22-20261024-080000"), ["json"]);

            var first = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: false);
            var second = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: false);
            var third = await RunImporter.ImportAsync(db.Store, [root], replaceExisting: true);

            Assert.Equal((1, 0), (first.Imported, first.Skipped));
            Assert.Equal((0, 1), (second.Imported, second.Skipped));
            Assert.Equal((1, 0), (third.Imported, third.Skipped));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [DatabaseFact]
    public async Task Clause_search_matches_any_word_with_english_stemming_and_by_meaning()
    {
        static ClauseEntity Clause(string number, string text, string language, float[] vector) => new()
        {
            Standard = "SR-IG", Version = "1", Number = number, Title = "t" + number, Text = text, Level = 1, Ord = int.Parse(number), Language = language,
            Key = $"SR-IG:1:{number}", Embedding = new Pgvector.Vector(vector),
        };

        float[] Axis(int i) { var v = new float[AqtDbContext.EmbeddingDimensions]; v[i] = 1; return v; }

        await db.Store.ReplaceStandardAsync(
            new StandardDocumentEntity { Id = "SR-IG", Version = "1", Title = "Search", Language = "en", Url = "u", Sha256 = "x" },
            [
                Clause("1", "The ASPSP rejects consents whose validUntil is in the past.", "en", Axis(0)),
                Clause("2", "Payment initiation with JSON encoding.", "en", Axis(1)),
                Clause("3", "თანხმობის ვადა არ აღემატება 90 დღეს.", "ka", Axis(2)),
            ]);

        // "consent" finds "consents" (English stemming); the other words do not have to be present.
        var byWords = await db.Store.SearchClausesAsync("consent rejected yesterday", null, "SR-IG");
        Assert.Equal("1", byWords.First().Clause.Number);
        Assert.True(byWords.First().ByWords);

        // Georgian words match as plain words.
        Assert.Equal("3", (await db.Store.SearchClausesAsync("თანხმობის", null, "SR-IG")).Single().Clause.Number);

        // A query vector close to clause 3 finds it with no shared words.
        var byMeaning = await db.Store.SearchClausesAsync("how long is a consent valid", Axis(2), "SR-IG");
        Assert.Contains(byMeaning, h => h.Clause.Number == "3" && h.ByMeaning);

        Assert.Equal(["1", "2", "3"], (await db.Store.GetOutlineAsync("SR-IG", "1")).Select(c => c.Number));
    }
}
