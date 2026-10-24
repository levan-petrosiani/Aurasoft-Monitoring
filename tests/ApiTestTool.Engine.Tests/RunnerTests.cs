using System.Net;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Signing;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Tests;

public class RunnerTests
{
    private const string NegativeConsentScenario = """
        id: T-001
        title: missing PSU-IP-Address is rejected
        suite: negative
        steps:
          - request:
              method: POST
              path: v2/consents/account-access
              headersTemplate: consent
              removeHeaders: [PSU-IP-Address]
              bodyTemplate: consent.accountList
            expect:
              status: 400
              errorCode: FORMAT_ERROR
              echoRequestId: true
          - request:
              method: GET
              path: v2/never-reached
        """;

    [Fact]
    public async Task Passes_when_the_bank_answers_as_expected_and_our_request_is_signed()
    {
        var bank = new FakeBank((request, body) =>
        {
            Assert.False(request.Headers.Contains("PSU-IP-Address"));
            Assert.Contains("\"consentType\":\"accountList\"", body);
            return Problem(request, HttpStatusCode.BadRequest, "FORMAT_ERROR");
        });

        var result = await RunAsync(NegativeConsentScenario, bank, maxSteps: 1);

        Assert.Equal(Outcome.Passed, result.Outcome);
        Assert.True(bank.LastSignatureValid, bank.LastSignatureErrors);
    }

    [Fact]
    public async Task Fails_and_blocks_later_steps_when_the_bank_accepts_an_invalid_request()
    {
        var bank = new FakeBank((request, _) => Json(request, HttpStatusCode.Created, """{"consentId":"c1","consentStatus":"received"}"""));

        var result = await RunAsync(NegativeConsentScenario, bank);

        Assert.Equal(Outcome.Failed, result.Outcome);
        Assert.Contains("Status 201, expected 400", result.Steps[0].Failures);
        Assert.Contains(result.Steps[0].Failures, f => f.StartsWith("No error code", StringComparison.Ordinal));
        Assert.Equal(Outcome.Blocked, result.Steps[1].Outcome);
        Assert.Equal(1, bank.Calls);
    }

    [Fact]
    public async Task Wrong_error_code_and_missing_request_id_echo_are_failures()
    {
        var bank = new FakeBank((request, _) =>
        {
            var response = Problem(request, HttpStatusCode.BadRequest, "PARAMETER_NOT_SUPPORTED");
            response.Headers.Remove("X-Request-ID");
            return response;
        });

        var result = await RunAsync(NegativeConsentScenario, bank, maxSteps: 1);

        Assert.Equal(Outcome.Failed, result.Outcome);
        Assert.Contains("Error code 'PARAMETER_NOT_SUPPORTED', expected 'FORMAT_ERROR'", result.Steps[0].Failures);
        Assert.Contains("Response X-Request-ID is missing", result.Steps[0].Failures);
    }

    [Fact]
    public async Task Saved_values_flow_into_later_steps_and_cleanup_always_runs()
    {
        const string yaml = """
            id: T-002
            title: consent lifecycle
            suite: ais
            steps:
              - request: { method: POST, path: v2/consents/account-access, headersTemplate: consent, bodyTemplate: consent.accountList }
                expect: { status: 201, json: { $.consentStatus: received } }
                save: { consentId: $.consentId }
              - request: { method: GET, path: "v2/consents/account-access/{{consentId}}/status", headersTemplate: basic }
                expect: { status: 200, json: { $.consentStatus: valid } }
            cleanup:
              - request: { method: DELETE, path: "v2/consents/account-access/{{consentId}}", headersTemplate: basic }
                expect: { status: 204 }
            """;

        var bank = new FakeBank((request, _) => request.Method.Method switch
        {
            "POST" => Json(request, HttpStatusCode.Created, """{"consentId":"abc-1","consentStatus":"received"}"""),
            "GET" => Json(request, HttpStatusCode.OK, """{"consentStatus":"received"}"""),
            _ => Json(request, HttpStatusCode.NoContent, ""),
        });

        var result = await RunAsync(yaml, bank);

        Assert.Equal(Outcome.Failed, result.Outcome);
        Assert.Equal("https://xs2a.test.local/v2/consents/account-access/abc-1/status", bank.Urls[1]);
        Assert.Contains("$.consentStatus is 'received', expected 'valid'", result.Steps[1].Failures);
        Assert.Single(result.Cleanup);
        Assert.Equal("https://xs2a.test.local/v2/consents/account-access/abc-1", bank.Urls[2]);
    }

    [Fact]
    public async Task Scenario_is_skipped_when_profile_lacks_a_required_feature()
    {
        const string yaml = """
            id: T-003
            title: needs discovery
            suite: preflight
            requires: [endpoints.discovery]
            steps:
              - request: { method: GET, target: discovery, path: "" }
            """;

        var result = await RunAsync(yaml, new FakeBank((r, _) => Json(r, HttpStatusCode.OK, "{}")));

        Assert.Equal(Outcome.Skipped, result.Outcome);
    }

    [Fact]
    public async Task Connection_failure_is_a_bank_failure_not_a_tool_error()
    {
        var bank = new FakeBank((_, _) => throw new HttpRequestException("Connection refused"));

        var result = await RunAsync(NegativeConsentScenario, bank, maxSteps: 1);

        Assert.Equal(Outcome.Failed, result.Outcome);
        Assert.Contains(result.Steps[0].Failures, f => f.Contains("Connection refused", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Failed_critical_preflight_runs_first_and_blocks_the_rest()
    {
        const string preflight = """
            id: P-1
            title: alive
            suite: preflight
            severity: Critical
            steps:
              - request: { method: GET, path: v2/alive }
                expect: { status: 200 }
            """;

        var bank = new FakeBank((_, _) => throw new HttpRequestException("Connection refused"));
        var dir = Directory.CreateTempSubdirectory("aqt-test-");
        try
        {
            await File.WriteAllTextAsync(Path.Combine(dir.FullName, "a-negative.yaml"), NegativeConsentScenario);
            await File.WriteAllTextAsync(Path.Combine(dir.FullName, "b-preflight.yaml"), preflight);
            var scenarios = ScenarioLoader.LoadAll(dir.FullName);

            var profile = YamlAndVariableTests.NewProfile();
            using var ctx = new RunContext(profile, TemplateLibrary.Load(RepoPaths.Scenarios), null, null, new HttpClient(bank));
            var run = await new ScenarioRunner(ctx).RunAsync(scenarios);

            Assert.Equal(["P-1", "T-001"], run.Scenarios.Select(s => s.Id));
            Assert.Equal(Outcome.Failed, run.Scenarios[0].Outcome);
            Assert.Equal(Outcome.Blocked, run.Scenarios[1].Outcome);
            Assert.Equal(1, bank.Calls);
        }
        finally
        {
            dir.Delete(recursive: true);
        }
    }

    private static async Task<ScenarioResult> RunAsync(string scenarioYaml, FakeBank bank, int? maxSteps = null)
    {
        var dir = Directory.CreateTempSubdirectory("aqt-test-");
        try
        {
            var file = Path.Combine(dir.FullName, "scenario.yaml");
            await File.WriteAllTextAsync(file, scenarioYaml);
            var scenario = YamlJson.LoadModel<Scenario>(file);
            if (maxSteps is not null)
                scenario.Steps.RemoveRange(maxSteps.Value, scenario.Steps.Count - maxSteps.Value);

            var profile = YamlAndVariableTests.NewProfile();
            profile.Raw = YamlJson.Parse("bank: { bic: TESTGE22 }\nendpoints: { xs2a: https://xs2a.test.local/ }", "profile");

            using var qsealc = TestCertificates.CreateRsa();
            using var ctx = new RunContext(profile, TemplateLibrary.Load(RepoPaths.Scenarios), qwac: null, qsealc: qsealc, httpClient: new HttpClient(bank));
            return await new ScenarioRunner(ctx).RunScenarioAsync(scenario);
        }
        finally
        {
            dir.Delete(recursive: true);
        }
    }

    private static HttpResponseMessage Problem(HttpRequestMessage request, HttpStatusCode status, string code)
    {
        var response = Json(request, status, $$"""{"type":"about:blank","title":"Bad request","code":"{{code}}"}""");
        response.Content.Headers.ContentType = new("application/problem+json");
        return response;
    }

    private static HttpResponseMessage Json(HttpRequestMessage request, HttpStatusCode status, string body)
    {
        var response = new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        if (request.Headers.TryGetValues("X-Request-ID", out var ids))
            response.Headers.Add("X-Request-ID", ids);
        return response;
    }

    /// <summary>In-memory bank: records calls and verifies the request signature like a real ASPSP would.</summary>
    private sealed class FakeBank(Func<HttpRequestMessage, string, HttpResponseMessage> respond) : HttpMessageHandler
    {
        public int Calls { get; private set; }

        public List<string> Urls { get; } = [];

        public bool LastSignatureValid { get; private set; }

        public string LastSignatureErrors { get; private set; } = string.Empty;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Calls++;
            Urls.Add(request.RequestUri!.ToString());

            var body = request.Content is null ? [] : await request.Content.ReadAsByteArrayAsync(cancellationToken);
            var headers = request.Headers.ToDictionary(h => h.Key, h => string.Join(", ", h.Value), StringComparer.OrdinalIgnoreCase);
            var verification = JwsVerifier.VerifyResponse(headers, body, 0, DateTime.UtcNow);
            LastSignatureValid = verification.IsValid;
            LastSignatureErrors = string.Join("; ", verification.Errors);

            return respond(request, Encoding.UTF8.GetString(body));
        }
    }
}

internal static class RepoPaths
{
    private static readonly Lazy<string> s_root = new(() =>
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "ApiTestTool.sln")))
                return dir.FullName;
        }

        throw new InvalidOperationException("ApiTestTool.sln not found above the test output folder");
    });

    public static string Root => s_root.Value;

    public static string Scenarios => Path.Combine(Root, "scenarios");

    public static string Banks => Path.Combine(Root, "banks");
}
