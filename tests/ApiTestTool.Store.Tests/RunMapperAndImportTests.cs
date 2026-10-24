using System.Text.Json;
using ApiTestTool.Engine.Reporting;
using ApiTestTool.Store.Masking;

namespace ApiTestTool.Store.Tests;

public class RunMapperAndImportTests
{
    private static readonly RunInfo Info = new() { Id = "TESTGE22-20261024-080000", Source = "cli", Status = "Completed", BankBic = "TESTGE22" };

    [Fact]
    public void Maps_counts_scenarios_steps_cleanup_and_every_exchange()
    {
        var entity = RunMapper.ToEntity(Info, TestRuns.Create(), SensitiveDataMasker.Default, 64 * 1024);

        Assert.Equal((1, 1, 50.0), (entity.Passed, entity.Failed, entity.PassRate));
        Assert.Equal(["AIS-001", "NEG-CON-003"], entity.Scenarios.Select(s => s.ScenarioId));

        var sca = entity.Scenarios[0].Steps.Single();
        Assert.Equal(2, sca.Exchanges.Count); // Exchange + Exchanges[]

        var negative = entity.Scenarios[1];
        Assert.Equal([false, true], negative.Steps.Select(s => s.IsCleanup));
        Assert.Equal("Expected status 400 but got 201", negative.Steps[0].Failures.Single());
    }

    [Fact]
    public void Nothing_secret_survives_the_mapping()
    {
        var entity = RunMapper.ToEntity(Info, TestRuns.Create(), SensitiveDataMasker.Default, 64 * 1024);
        var all = JsonSerializer.Serialize(entity.Scenarios.SelectMany(s => s.Steps).SelectMany(s => s.Exchanges)
            .Select(e => new { e.Url, e.RequestHeaders, e.RequestBody, e.ResponseHeaders, e.ResponseBody }));

        Assert.DoesNotContain("SECRET", all);
        Assert.DoesNotContain("GE29NB0000000101904917", all);
    }

    [Fact]
    public void Long_bodies_are_capped_and_flagged()
    {
        var run = TestRuns.Create();
        run.Scenarios[1].Steps[0].Exchange!.ResponseBody = new string('x', 5000);

        var exchange = RunMapper.ToEntity(Info, run, SensitiveDataMasker.Default, 1000).Scenarios[1].Steps[0].Exchanges.Single();

        Assert.True(exchange.Truncated);
        Assert.True(exchange.ResponseBody!.Length < 1100);
    }

    [Fact]
    public void Reads_cli_and_portal_folders_including_cancelled_runs_without_results()
    {
        var root = Directory.CreateTempSubdirectory("aqt-import-").FullName;
        try
        {
            var cli = Path.Combine(root, "TESTGE22-20261024-080000");
            ReportWriter.Write(TestRuns.Create(), cli, ["json"]);

            var portal = Path.Combine(root, "portal", "20261024-080000-TESTGE22-ab12");
            ReportWriter.Write(TestRuns.Create(), portal, ["json"]);
            File.WriteAllText(Path.Combine(portal, "run.json"), """
                {"id":"20261024-080000-TESTGE22-ab12","request":{"bank":"TEST","suites":["ais"]},"status":"Completed",
                 "createdUtc":"2026-10-24T08:00:00Z","startedUtc":"2026-10-24T08:00:00Z","finishedUtc":"2026-10-24T08:00:05Z","bankName":"Test Bank"}
                """);

            var cancelled = Path.Combine(root, "portal", "20261024-090000-BAGAGE22-cd34");
            Directory.CreateDirectory(cancelled);
            File.WriteAllText(Path.Combine(cancelled, "run.json"), """
                {"id":"20261024-090000-BAGAGE22-cd34","request":{"bank":"BAGAGE22","suites":[]},"status":"Cancelled","error":"Cancelled before it started","createdUtc":"2026-10-24T09:00:00Z"}
                """);

            var (cliInfo, cliResult) = RunImporter.Read(cli);
            Assert.Equal(("TESTGE22-20261024-080000", "cli", 2), (cliInfo.Id, cliInfo.Source, cliResult!.Scenarios.Count));

            var (portalInfo, _) = RunImporter.Read(portal);
            Assert.Equal(("portal", "Completed", "ais"), (portalInfo.Source, portalInfo.Status, portalInfo.Suites.Single()));
            Assert.Contains("\"bank\":\"TEST\"", portalInfo.OptionsJson);

            var (cancelledInfo, cancelledResult) = RunImporter.Read(cancelled);
            Assert.Null(cancelledResult);
            Assert.Equal(("Cancelled", "BAGAGE22"), (cancelledInfo.Status, cancelledInfo.BankBic));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
