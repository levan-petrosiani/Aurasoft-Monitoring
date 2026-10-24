using ApiTestTool.Engine.Running;

namespace ApiTestTool.Store.Tests;

/// <summary>A small run result: one passed scenario with an SCA token exchange, one failed with an IBAN in the response.</summary>
internal static class TestRuns
{
    public static RunResult Create()
    {
        var run = new RunResult
        {
            ToolVersion = "0.1.0",
            BankBic = "TESTGE22",
            BankName = "Test Bank",
            Xs2aUrl = "https://localhost:7443/",
            TppId = "PSDGE-NBG-TEST",
            StartedAtUtc = new DateTime(2026, 10, 24, 8, 0, 0, DateTimeKind.Utc),
            FinishedAtUtc = new DateTime(2026, 10, 24, 8, 0, 5, DateTimeKind.Utc),
        };

        var passed = new ScenarioResult { Id = "AIS-001", Title = "Account list consent", Suite = "ais", Severity = "Critical", SpecRef = "6.3.1", Outcome = Outcome.Passed, DurationMs = 1200 };
        var sca = new StepResult { Name = "SCA via OAuth", Kind = "sca", Outcome = Outcome.Passed };
        sca.Exchange = Exchange("POST", "https://localhost:7443/oauth/token", "grant_type=authorization_code&code=SECRETCODE123456789", 200, """{"access_token":"SECRET-ACCESS-TOKEN","refresh_token":"SECRET-REFRESH"}""");
        sca.Exchanges.Add(Exchange("POST", "https://localhost:7443/oauth/token", "grant_type=refresh_token&refresh_token=SECRET-REFRESH", 200, """{"access_token":"SECRET-ACCESS-2"}"""));
        passed.Steps.Add(sca);

        var failed = new ScenarioResult { Id = "NEG-CON-003", Title = "validUntil in the past", Suite = "negative", Severity = "Major", Outcome = Outcome.Failed, Reason = "Expected 400, got 201" };
        var request = new StepResult { Name = "Create consent", Kind = "request", Outcome = Outcome.Failed };
        request.Failures.Add("Expected status 400 but got 201");
        request.Exchange = Exchange("POST", "https://localhost:7443/v2/consents/account-access", """{"access":{"accounts":[{"iban":"GE29NB0000000101904917"}]}}""", 201, """{"consentId":"c-1"}""");
        failed.Steps.Add(request);
        var cleanup = new StepResult { Name = "Delete consent", Kind = "request", Outcome = Outcome.Passed };
        cleanup.Warnings.Add("cleanup returned 204 slowly");
        failed.Cleanup.Add(cleanup);

        run.Scenarios.Add(passed);
        run.Scenarios.Add(failed);
        return run;
    }

    private static HttpExchange Exchange(string method, string url, string requestBody, int status, string responseBody) => new()
    {
        Method = method,
        Url = url,
        RequestHeaders = [KeyValuePair.Create("Authorization", "Bearer SECRET-ACCESS-TOKEN"), KeyValuePair.Create("X-Request-ID", "r-1")],
        RequestBody = requestBody,
        StatusCode = status,
        ResponseBody = responseBody,
        DurationMs = 42,
        StartedAtUtc = new DateTime(2026, 10, 24, 8, 0, 1, DateTimeKind.Utc),
    };
}
