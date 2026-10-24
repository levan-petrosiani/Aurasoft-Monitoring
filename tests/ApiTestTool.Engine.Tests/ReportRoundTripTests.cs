using System.Text.Json;
using ApiTestTool.Engine.Reporting;
using ApiTestTool.Engine.Running;

namespace ApiTestTool.Engine.Tests;

/// <summary>The portal reloads run history from summary.json, so it must deserialize back into a RunResult.</summary>
public class ReportRoundTripTests
{
    [Fact]
    public void Summary_json_round_trips_scenarios_steps_and_exchanges()
    {
        var run = new RunResult { ToolVersion = "1.0", BankBic = "TESTGE22", BankName = "Test", Xs2aUrl = "https://x/", StartedAtUtc = DateTime.UtcNow };
        var scenario = new ScenarioResult { Id = "S-1", Title = "t", Suite = "negative", Severity = "Major", Outcome = Outcome.Failed, Reason = "boom" };
        var step = new StepResult { Name = "POST", Kind = "request", Outcome = Outcome.Failed };
        step.Failures.Add("Status 201, expected 400");
        step.Warnings.Add("slow");
        step.Exchange = new HttpExchange { Method = "POST", Url = "https://x/v2", RequestHeaders = [new("X-Request-ID", "1")], StatusCode = 201 };
        step.Exchanges.Add(new HttpExchange { Method = "GET", Url = "https://x/.well-known", RequestHeaders = [] });
        scenario.Steps.Add(step);
        run.Scenarios.Add(scenario);

        var dir = Directory.CreateTempSubdirectory("aqt-report-");
        try
        {
            ReportWriter.Write(run, dir.FullName, ["json"]);
            var json = File.ReadAllText(Path.Combine(dir.FullName, "summary.json"));

            var back = JsonSerializer.Deserialize<RunResult>(json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true })!;

            var s = Assert.Single(back.Scenarios);
            Assert.Equal(Outcome.Failed, s.Outcome);
            Assert.Equal("boom", s.Reason);
            var st = Assert.Single(s.Steps);
            Assert.Equal(["Status 201, expected 400"], st.Failures);
            Assert.Equal(["slow"], st.Warnings);
            Assert.Equal(201, st.Exchange!.StatusCode);
            Assert.Single(st.Exchanges);
            Assert.Equal(0, back.PassRate);
        }
        finally
        {
            dir.Delete(recursive: true);
        }
    }
}
