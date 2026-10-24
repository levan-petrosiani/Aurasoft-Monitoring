using System.Net;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Scenarios;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Tests;

/// <summary>Engine features added for public sandboxes such as the TBC mock.</summary>
public class SandboxSupportTests
{
    [Fact]
    public async Task Approve_form_is_submitted_with_its_button_and_hidden_fields()
    {
        const string html = """
            <form method="post"><input type="hidden" name="txn" value="a&amp;b">
              <button name="decision" value="approve">Approve</button>
              <button name="decision" value="deny">Deny</button>
            </form>
            """;

        var request = HeadlessAuthorizer.TryBuildApproval(html, new Uri("https://bank.test/mock/authorize/42"));

        Assert.NotNull(request);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("https://bank.test/mock/authorize/42", request.RequestUri!.ToString());
        Assert.Equal("txn=a%26b&decision=approve", await request.Content!.ReadAsStringAsync());
    }

    [Fact]
    public void Real_login_pages_are_never_submitted()
    {
        const string html = """<form method="post"><input type="password" name="pwd"><button>Approve</button></form>""";
        Assert.Null(HeadlessAuthorizer.TryBuildApproval(html, new Uri("https://bank.test/login")));
    }

    [Fact]
    public void Pages_without_an_approve_button_are_not_submitted()
    {
        const string html = """<form method="post"><button name="decision" value="deny">Deny</button></form>""";
        Assert.Null(HeadlessAuthorizer.TryBuildApproval(html, new Uri("https://bank.test/x")));
    }

    [Fact]
    public async Task Host_relative_links_keep_the_base_path_once_and_transient_failures_are_retried()
    {
        var calls = new List<string>();
        var attempts = 0;
        var bank = new StubHandler(request =>
        {
            calls.Add(request.RequestUri!.ToString());
            return ++attempts == 1
                ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("""{"consentStatus":"valid"}""", System.Text.Encoding.UTF8, "application/json") };
        });

        var profile = new BankProfile
        {
            Bank = new BankInfo { Bic = "TBCBGE22", Name = "TBC" },
            Endpoints = new EndpointSettings { Xs2a = "https://bank.test/0.8/" },
            ResponseSignature = "ignore",
        };
        var http = new HttpClient(new RetryHandlerAccessor(new RetrySettings { Attempts = 3, DelayMs = 1 }, bank));
        using var ctx = new RunContext(profile, TemplateLibrary.Empty, null, null, http);

        var dir = Directory.CreateTempSubdirectory("aqt-sbx-");
        try
        {
            var file = Path.Combine(dir.FullName, "s.yaml");
            await File.WriteAllTextAsync(file, """
                id: S-1
                title: link
                suite: tbc
                steps:
                  - request: { method: GET, path: "/0.8/v1/consents/abc/status", signing: none }
                    expect: { status: 200, json: { $.consentStatus: valid } }
                """);
            var result = await new ScenarioRunner(ctx).RunScenarioAsync(YamlJson.LoadModel<Scenario>(file));

            Assert.Equal(Outcome.Passed, result.Outcome);
            Assert.All(calls, url => Assert.Equal("https://bank.test/0.8/v1/consents/abc/status", url));
            Assert.Equal(2, calls.Count);
            Assert.Contains(result.Steps[0].Warnings, w => w.Contains("transient failure (503)", StringComparison.Ordinal));
        }
        finally
        {
            dir.Delete(recursive: true);
        }
    }

    private sealed class StubHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(respond(request));
    }

    /// <summary>RetryHandler is internal; the tests project sees it through InternalsVisibleTo.</summary>
    private sealed class RetryHandlerAccessor : DelegatingHandler
    {
        public RetryHandlerAccessor(RetrySettings settings, HttpMessageHandler inner)
        {
            InnerHandler = new RetryHandler(settings) { InnerHandler = inner };
        }
    }
}
