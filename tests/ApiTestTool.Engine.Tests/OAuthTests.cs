using System.Net;
using System.Net.Sockets;
using System.Web;
using ApiTestTool.Engine.OAuth;
using ApiTestTool.Engine.Profiles;

namespace ApiTestTool.Engine.Tests;

public class OAuthTests
{
    [Fact]
    public void Pkce_challenge_matches_the_RFC_7636_example()
    {
        // RFC 7636, Appendix B
        Assert.Equal("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", Pkce.CreateChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"));
    }

    [Fact]
    public void Pkce_verifier_is_43_base64url_characters_and_random()
    {
        var a = Pkce.CreateVerifier();
        var b = Pkce.CreateVerifier();
        Assert.Equal(43, a.Length);
        Assert.Matches("^[A-Za-z0-9_-]+$", a);
        Assert.NotEqual(a, b);
    }

    [Theory]
    [InlineData("https://auth.bank.ge/abc", "https://auth.bank.ge/abc/.well-known/oauth-authorization-server")]
    [InlineData("https://auth.bank.ge/abc/", "https://auth.bank.ge/abc/.well-known/oauth-authorization-server")]
    [InlineData("https://auth.bank.ge/.well-known/openid-configuration", "https://auth.bank.ge/.well-known/openid-configuration")]
    public void Discovery_url_gets_the_well_known_suffix_when_missing(string input, string expected)
    {
        Assert.Equal(expected, OAuthClient.NormalizeDiscoveryUrl(input));
    }

    [Fact]
    public void Authorize_url_has_pkce_and_tbc_quirks()
    {
        var tbc = BankProfileLoader.Load("TBCBGE22", RepoPaths.Banks);
        var quirks = OAuthQuirks.From(tbc.Quirks);

        var url = OAuthClient.BuildAuthorizeUrl("https://auth.test/authorize", "PSDGE-NBG-X", "http://localhost:5199/callback", "AIS:c1", "AISstate", "chal", quirks);
        var query = HttpUtility.ParseQueryString(new Uri(url).Query);

        Assert.Equal("code", query["response_type"]);
        Assert.Equal("PSDGE-NBG-X", query["client_id"]);
        Assert.Equal("http://localhost:5199/callback", query["redirect_uri"]);
        Assert.Equal("chal", query["code_challenge"]);
        Assert.Equal("S256", query["code_challenge_method"]);
        Assert.Equal("AISstate", query["state"]);
        Assert.Equal("AIS:c1 openid", query["scope"]);
        Assert.Equal("1234", query["nonce"]);
        Assert.Equal("ka-ge", query["ui_locales"]);
    }

    [Fact]
    public void Profiles_without_quirks_add_nothing()
    {
        var url = OAuthClient.BuildAuthorizeUrl("https://auth.test/authorize?tenant=1", "c", "http://localhost/cb", "AIS:c1", "s", "x", OAuthQuirks.None);
        var query = HttpUtility.ParseQueryString(new Uri(url).Query);

        Assert.Equal("1", query["tenant"]);
        Assert.Equal("AIS:c1", query["scope"]);
        Assert.Null(query["nonce"]);
    }

    [Fact]
    public async Task Listener_returns_code_and_state_and_ignores_other_paths()
    {
        var redirect = $"http://127.0.0.1:{FreePort()}/callback";
        await using var listener = OAuthCallbackListener.Start(redirect);
        using var http = new HttpClient();

        var wait = listener.WaitAsync(TimeSpan.FromSeconds(10), CancellationToken.None);
        var favicon = await http.GetAsync(redirect.Replace("/callback", "/favicon.ico", StringComparison.Ordinal));
        var page = await http.GetAsync(redirect + "?code=abc&state=AIS123");
        var callback = await wait;

        Assert.Equal(HttpStatusCode.NotFound, favicon.StatusCode);
        Assert.Equal(HttpStatusCode.OK, page.StatusCode);
        Assert.Equal("abc", callback.Code);
        Assert.Equal("AIS123", callback.State);
        Assert.Null(callback.Error);
    }

    [Fact]
    public async Task Listener_reports_errors_from_the_auth_server()
    {
        var redirect = $"http://127.0.0.1:{FreePort()}/cb";
        await using var listener = OAuthCallbackListener.Start(redirect);
        using var http = new HttpClient();

        var wait = listener.WaitAsync(TimeSpan.FromSeconds(10), CancellationToken.None);
        await http.GetAsync(redirect + "?error=access_denied&error_description=PSU%20cancelled&state=s");
        var callback = await wait;

        Assert.Equal("access_denied", callback.Error);
        Assert.Equal("PSU cancelled", callback.ErrorDescription);
    }

    [Fact]
    public async Task Listener_times_out_when_nobody_calls_back()
    {
        await using var listener = OAuthCallbackListener.Start($"http://127.0.0.1:{FreePort()}/callback");
        await Assert.ThrowsAsync<TimeoutException>(() => listener.WaitAsync(TimeSpan.FromMilliseconds(300), CancellationToken.None));
    }

    [Fact]
    public void Listener_rejects_busy_ports_and_non_local_redirects()
    {
        var blocker = new TcpListener(IPAddress.Loopback, 0);
        blocker.Start();
        try
        {
            var port = ((IPEndPoint)blocker.LocalEndpoint).Port;
            Assert.Throws<ConfigurationException>(() => OAuthCallbackListener.Start($"http://127.0.0.1:{port}/callback"));
        }
        finally
        {
            blocker.Stop();
        }

        Assert.Throws<ConfigurationException>(() => OAuthCallbackListener.Start("https://tpp.example.com/callback"));
        Assert.Throws<ConfigurationException>(() => OAuthCallbackListener.Start("http://tpp.example.com/callback"));
    }

    internal static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        var port = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return port;
    }
}
