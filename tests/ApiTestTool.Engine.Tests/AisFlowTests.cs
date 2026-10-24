using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using System.Web;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Signing;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Tests;

/// <summary>
/// Runs the shipped AIS scenarios end to end against an in-memory XS2A + OAuth server that behaves like the
/// AltaSoft stack (links, PKCE, scope AIS:&lt;consentId&gt;), with a fake browser that "logs in" and calls back.
/// </summary>
public class AisFlowTests
{
    [Theory]
    [InlineData("AIS-001")]
    [InlineData("AIS-002")]
    public async Task Shipped_ais_scenario_passes_against_a_conforming_bank(string id)
    {
        var aspsp = new FakeAspsp();
        var result = await RunShippedAsync(id, aspsp, browser: aspsp.LoginAndCallBack);

        Assert.True(result.Outcome == Outcome.Passed, Describe(result));
        Assert.True(aspsp.PkceVerified, "token request carried a code_verifier matching the S256 challenge");
        Assert.True(aspsp.AllXs2aRequestsSigned, "every XS2A request had a valid X-Jws-Signature");
        Assert.Equal("Client-Redirect-URI", aspsp.RedirectHeaderName);
        Assert.Equal("AIS:c-1", aspsp.RequestedScope);
        Assert.True(aspsp.ConsentDeleted);
        if (id == "AIS-001")
            Assert.True(aspsp.Refreshed, "refresh_token grant exercised");
    }

    [Fact]
    public async Task Wrong_state_in_callback_fails_the_sca_step_and_cleanup_still_deletes_the_consent()
    {
        var aspsp = new FakeAspsp();
        var result = await RunShippedAsync("AIS-001", aspsp, browser: (url, ct) => aspsp.LoginAndCallBack(url, ct, stateOverride: "forged"));

        var sca = result.Steps.Single(s => s.Kind == "sca");
        Assert.Equal(Outcome.Failed, sca.Outcome);
        Assert.Contains(sca.Failures, f => f.Contains("does not match the state", StringComparison.Ordinal));
        Assert.Equal(Outcome.Blocked, result.Steps.Last().Outcome);
        Assert.True(aspsp.ConsentDeleted, "cleanup runs without a token (authOptional)");
    }

    [Fact]
    public async Task No_login_within_the_timeout_is_an_error_on_our_side()
    {
        const string yaml = """
            id: T-SCA
            title: sca timeout
            suite: ais
            steps:
              - sca: { mode: manual, discovery: "https://auth.test.local/a-1", scope: "AIS:c-1", timeoutSeconds: 1 }
            """;

        var aspsp = new FakeAspsp();
        var result = await RunAsync(LoadInline(yaml), aspsp, browser: (_, _) => Task.CompletedTask);

        Assert.Equal(Outcome.Error, result.Outcome);
        Assert.Contains(result.Steps[0].Failures, f => f.Contains("SCA was not completed", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Token_endpoint_rejection_is_a_bank_failure()
    {
        var aspsp = new FakeAspsp { RejectToken = true };
        var result = await RunShippedAsync("AIS-001", aspsp, browser: aspsp.LoginAndCallBack);

        var sca = result.Steps.Single(s => s.Kind == "sca");
        Assert.Equal(Outcome.Failed, sca.Outcome);
        Assert.Contains(sca.Failures, f => f.Contains("error 'invalid_grant'", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Ais_scenarios_are_skipped_for_banks_without_the_features()
    {
        var aspsp = new FakeAspsp();
        var result = await RunAsync(LoadShipped("AIS-002"), aspsp, aspsp.LoginAndCallBack, features: "features: { consents: [accountList], scaOAuth: true }");

        Assert.Equal(Outcome.Skipped, result.Outcome);
        Assert.Equal(0, aspsp.Calls);
    }

    private static Scenario LoadShipped(string id) =>
        ScenarioLoader.LoadAll(RepoPaths.Scenarios).Single(s => s.Id == id);

    private static Scenario LoadInline(string yaml)
    {
        var file = Path.Combine(Directory.CreateTempSubdirectory("aqt-sca-").FullName, "s.yaml");
        File.WriteAllText(file, yaml);
        return YamlJson.LoadModel<Scenario>(file);
    }

    private static Task<ScenarioResult> RunShippedAsync(string id, FakeAspsp aspsp, Func<string, CancellationToken, Task> browser) =>
        RunAsync(LoadShipped(id), aspsp, browser);

    private static async Task<ScenarioResult> RunAsync(Scenario scenario, FakeAspsp aspsp, Func<string, CancellationToken, Task> browser,
        string features = "features: { consents: [accountList, aspspManaged], scaOAuth: true }")
    {
        var profile = new BankProfile
        {
            Bank = new BankInfo { Bic = "TESTGE22", Name = "Test Bank" },
            Endpoints = new EndpointSettings { Xs2a = "https://xs2a.test.local/" },
            Tpp = new TppSettings { RedirectUri = $"http://127.0.0.1:{OAuthTests.FreePort()}/callback" },
            Raw = YamlJson.Parse(features, "profile"),
        };

        using var qwac = TestCertificates.CreateRsa();
        using var qsealc = TestCertificates.CreateRsa();
        using var ctx = new RunContext(profile, TemplateLibrary.Load(RepoPaths.Scenarios), qwac, qsealc, new HttpClient(aspsp))
        {
            OpenBrowser = browser,
        };

        return await new ScenarioRunner(ctx).RunScenarioAsync(scenario);
    }

    private static string Describe(ScenarioResult r) =>
        $"{r.Outcome}: {r.Reason}\n" + string.Join("\n", r.Steps.Concat(r.Cleanup).Select(s => $"  {s.Name}: {s.Outcome} {string.Join("; ", s.Failures)}"));

    /// <summary>In-memory XS2A + AuthServer following the AltaSoft implementation's shapes.</summary>
    private sealed class FakeAspsp : HttpMessageHandler
    {
        private const string ConsentId = "c-1";
        private const string AuthId = "a-1";
        private const string Code = "code-xyz";
        private string? _challenge;
        private string _consentStatus = "received";

        public bool RejectToken { get; init; }
        public int Calls { get; private set; }
        public bool PkceVerified { get; private set; }
        public bool Refreshed { get; private set; }
        public bool ConsentDeleted { get; private set; }
        public bool AllXs2aRequestsSigned { get; private set; } = true;
        public string? RedirectHeaderName { get; private set; }
        public string? RequestedScope { get; private set; }

        /// <summary>Fake browser: records the PKCE challenge, then calls the TPP redirect URI like the auth server would.</summary>
        public Task LoginAndCallBack(string authorizeUrl, CancellationToken ct) => LoginAndCallBack(authorizeUrl, ct, null);

        public Task LoginAndCallBack(string authorizeUrl, CancellationToken ct, string? stateOverride)
        {
            var q = HttpUtility.ParseQueryString(new Uri(authorizeUrl).Query);
            _challenge = q["code_challenge"];
            RequestedScope = q["scope"];
            var callback = $"{q["redirect_uri"]}?code={Code}&state={Uri.EscapeDataString(stateOverride ?? q["state"]!)}";

            // The listener answers only once the step starts waiting, so don't block here.
            _ = Task.Run(async () =>
            {
                using var http = new HttpClient();
                await http.GetAsync(callback, ct);
            }, ct);
            return Task.CompletedTask;
        }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Calls++;
            var uri = request.RequestUri!;
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(cancellationToken);

            if (uri.Host == "auth.test.local")
                return Auth(request, uri, body);

            CheckSignature(request, body);
            var bearer = request.Headers.Authorization?.Parameter;
            var path = uri.AbsolutePath;

            return (request.Method.Method, path) switch
            {
                ("POST", "/v2/consents/account-access") => Consent(request),
                ("POST", "/v2/consents/c-1/authorisations") => Json(request, HttpStatusCode.Created,
                    "{\"scaStatus\":\"received\",\"authorisationId\":\"" + AuthId + "\",\"_links\":{\"scaOAuth\":{\"href\":\"https://auth.test.local/" + AuthId + "\"}}}"),
                ("GET", "/v2/consents/account-access/c-1/status") => Json(request, HttpStatusCode.OK, $$"""{"consentStatus":"{{_consentStatus}}"}"""),
                ("GET", "/v2/accounts") when Authorized(request, bearer) => Json(request, HttpStatusCode.OK,
                    """{"accounts":[{"resourceId":"acc-1","iban":"GE29TS0000000000000001","currency":"GEL"}]}"""),
                ("GET", "/v2/accounts/acc-1/balances") when Authorized(request, bearer) => Json(request, HttpStatusCode.OK,
                    """{"balances":[{"balanceType":"interimAvailable","balanceAmount":{"currency":"GEL","amount":"100.00"}}]}"""),
                ("GET", "/v2/accounts/acc-1/transactions") when Authorized(request, bearer) && uri.Query.Contains("bookingStatus=booked", StringComparison.Ordinal) =>
                    Json(request, HttpStatusCode.OK, """{"transactions":{"booked":[]}}"""),
                ("DELETE", "/v2/consents/account-access/c-1") => Deleted(request),
                _ => Json(request, HttpStatusCode.Unauthorized, """{"code":"TOKEN_UNKNOWN"}"""),
            };
        }

        private HttpResponseMessage Consent(HttpRequestMessage request)
        {
            RedirectHeaderName = request.Headers.Contains("Client-Redirect-URI") ? "Client-Redirect-URI"
                : request.Headers.Contains("TPP-Redirect-URI") ? "TPP-Redirect-URI" : null;

            var response = Json(request, HttpStatusCode.Created,
                "{\"consentStatus\":\"received\",\"consentId\":\"" + ConsentId + "\",\"_links\":{\"startAuthorisation\":{\"href\":\"/v2/consents/" + ConsentId + "/authorisations\"}}}");
            response.Headers.Location = new Uri("/v2/consents/" + ConsentId, UriKind.Relative);
            return response;
        }

        private HttpResponseMessage Deleted(HttpRequestMessage request)
        {
            ConsentDeleted = true;
            return Json(request, HttpStatusCode.NoContent, "");
        }

        private static bool Authorized(HttpRequestMessage request, string? bearer) =>
            bearer is "access-1" or "access-2" && request.Headers.TryGetValues("Consent-ID", out var ids) && ids.Single() == ConsentId;

        private HttpResponseMessage Auth(HttpRequestMessage request, Uri uri, string body)
        {
            if (request.Method == HttpMethod.Get && uri.AbsolutePath == $"/{AuthId}/.well-known/oauth-authorization-server")
            {
                return Json(request, HttpStatusCode.OK, """
                    {"issuer":"https://auth.test.local","authorization_endpoint":"https://auth.test.local/authorize",
                     "token_endpoint":"https://auth.test.local/token","code_challenge_methods_supported":["S256"]}
                    """);
            }

            if (request.Method == HttpMethod.Post && uri.AbsolutePath == "/token")
            {
                var form = HttpUtility.ParseQueryString(body);
                if (RejectToken)
                    return Json(request, HttpStatusCode.BadRequest, """{"error":"invalid_grant","error_description":"code expired"}""");

                if (form["grant_type"] == "authorization_code" && form["code"] == Code && !string.IsNullOrEmpty(form["client_id"]))
                {
                    PkceVerified = _challenge is not null && Pkce.CreateChallenge(form["code_verifier"]!) == _challenge;
                    if (!PkceVerified)
                        return Json(request, HttpStatusCode.BadRequest, """{"error":"invalid_grant"}""");

                    _consentStatus = "valid";
                    return Json(request, HttpStatusCode.OK, """{"access_token":"access-1","token_type":"Bearer","expires_in":300,"refresh_token":"refresh-1","scope":"AIS:c-1"}""");
                }

                if (form["grant_type"] == "refresh_token" && form["refresh_token"] == "refresh-1")
                {
                    Refreshed = true;
                    return Json(request, HttpStatusCode.OK, """{"access_token":"access-2","token_type":"Bearer","expires_in":300,"refresh_token":"refresh-2","scope":"AIS:c-1"}""");
                }
            }

            return Json(request, HttpStatusCode.BadRequest, """{"error":"invalid_request"}""");
        }

        private void CheckSignature(HttpRequestMessage request, string body)
        {
            var headers = request.Headers.ToDictionary(h => h.Key, h => string.Join(", ", h.Value), StringComparer.OrdinalIgnoreCase);
            if (!JwsVerifier.VerifyResponse(headers, Encoding.UTF8.GetBytes(body), 0, DateTime.UtcNow).IsValid)
                AllXs2aRequestsSigned = false;
        }

        private static HttpResponseMessage Json(HttpRequestMessage request, HttpStatusCode status, string body)
        {
            var response = new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
            if (request.Headers.TryGetValues("X-Request-ID", out var ids))
                response.Headers.Add("X-Request-ID", ids);
            return response;
        }
    }
}
