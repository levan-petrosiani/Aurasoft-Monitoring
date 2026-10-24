using System.Net;
using System.Net.Security;
using System.Text.RegularExpressions;

namespace ApiTestTool.Engine.OAuth;

/// <summary>
/// Stands in for the browser during SCA on test servers: requests the authorize URL, follows redirects, and on a
/// simple consent page (a form with an "Approve"-like button and no password field) submits the approval, until
/// the auth server redirects to our callback. Works with ApiTestTool.MockBank and sandboxes such as the TBC mock.
/// Never use it against a real bank: it does not log in, it only clicks "approve".
/// Used by <c>aqt --sca-auto</c> and the portal's "complete SCA automatically" option.
/// </summary>
public static partial class HeadlessAuthorizer
{
    /// <summary>Recognised by MockBank (AutoApprove = headless) to approve without showing the login page.</summary>
    public const string UserAgent = "ApiTestTool-HeadlessSca/1.0";

    private const int MaxHops = 10;

    /// <summary>A <see cref="Running.RunContext.OpenBrowser"/> replacement.</summary>
    /// <param name="onError">Receives a message when the flow could not be completed.</param>
    public static Func<string, CancellationToken, Task> Create(Action<string> onError) => (url, cancellationToken) =>
    {
        // Fire and forget: the SCA step starts waiting for the callback only after OpenBrowser returns.
        _ = Task.Run(() => FollowAsync(url, onError, cancellationToken), cancellationToken);
        return Task.CompletedTask;
    };

    private static async Task FollowAsync(string url, Action<string> onError, CancellationToken cancellationToken)
    {
        using var handler = new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseCookies = true,
            // Self-signed certificates are accepted only on this machine (MockBank); remote sandboxes must be valid.
            SslOptions = { RemoteCertificateValidationCallback = (sender, _, _, errors) => errors == SslPolicyErrors.None || IsLoopback(sender) },
        };
        using var http = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(30) };
        http.DefaultRequestHeaders.UserAgent.ParseAdd(UserAgent);

        var callback = RedirectUriOf(url);

        try
        {
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            for (var hop = 0; hop < MaxHops; hop++)
            {
                var target = request.RequestUri!;
                using var response = await SendWithRetryAsync(http, request, cancellationToken).ConfigureAwait(false);

                // Our own callback answered: the flow is complete.
                if (callback is not null && IsSameEndpoint(target, callback))
                    return;

                if ((int)response.StatusCode is >= 300 and < 400 && response.Headers.Location is { } location)
                {
                    request = new HttpRequestMessage(HttpMethod.Get, location.IsAbsoluteUri ? location : new Uri(target, location));
                    continue;
                }

                var html = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                if (response.StatusCode == HttpStatusCode.OK && TryBuildApproval(html, target) is { } approval)
                {
                    request = approval;
                    continue;
                }

                onError(response.StatusCode == HttpStatusCode.OK
                    ? "Automatic SCA: the auth server showed a page without a simple approve button (a real login?). Log in manually instead."
                    : $"Automatic SCA: the auth server answered {(int)response.StatusCode} at {target.GetLeftPart(UriPartial.Path)}.");
                return;
            }

            onError("Automatic SCA: too many redirects or pages before reaching the callback.");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            onError($"Automatic SCA: could not complete the authorization: {ex.Message}");
        }
    }

    /// <summary>Sandboxes may inject 5xx failures; retry those a few times before giving up.</summary>
    private static async Task<HttpResponseMessage> SendWithRetryAsync(HttpClient http, HttpRequestMessage request, CancellationToken cancellationToken)
    {
        if (request.Content is not null)
            await request.Content.LoadIntoBufferAsync(cancellationToken).ConfigureAwait(false);

        for (var attempt = 1; ; attempt++)
        {
            var copy = new HttpRequestMessage(request.Method, request.RequestUri) { Content = request.Content };
            var response = await http.SendAsync(copy, cancellationToken).ConfigureAwait(false);
            if (attempt >= 4 || (int)response.StatusCode is not (502 or 503 or 504))
                return response;

            response.Dispose();
            await Task.Delay(500 * attempt, cancellationToken).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Finds a form with an approve-like submit button and builds the POST it would send.
    /// Pages with a password field are a real login and are never submitted.
    /// </summary>
    internal static HttpRequestMessage? TryBuildApproval(string html, Uri pageUri)
    {
        if (PasswordInputRegex().IsMatch(html))
            return null;

        foreach (Match form in FormRegex().Matches(html))
        {
            var attributes = form.Groups["attrs"].Value;
            var body = form.Groups["body"].Value;

            var method = Attr(attributes, "method") ?? "get";
            if (!method.Equals("post", StringComparison.OrdinalIgnoreCase))
                continue;

            var approve = SubmitRegex().Matches(body)
                .Select(m => (name: Attr(m.Value, "name"), value: Attr(m.Value, "value"), text: m.Groups["text"].Value))
                .FirstOrDefault(b => IsApproval(b.value) || IsApproval(b.text));
            if (approve == default)
                continue;

            var fields = new List<KeyValuePair<string, string>>();
            foreach (Match input in HiddenInputRegex().Matches(body))
            {
                if (Attr(input.Value, "name") is { } name)
                    fields.Add(new(name, WebUtility.HtmlDecode(Attr(input.Value, "value") ?? string.Empty)));
            }

            if (approve.name is not null)
                fields.Add(new(approve.name, WebUtility.HtmlDecode(approve.value ?? string.Empty)));

            var action = Attr(attributes, "action");
            var actionUri = string.IsNullOrEmpty(action) ? pageUri : new Uri(pageUri, WebUtility.HtmlDecode(action));
            return new HttpRequestMessage(HttpMethod.Post, actionUri) { Content = new FormUrlEncodedContent(fields) };
        }

        return null;
    }

    private static Uri? RedirectUriOf(string authorizeUrl)
    {
        var query = new Uri(authorizeUrl).Query.TrimStart('?');
        foreach (var pair in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var kv = pair.Split('=', 2);
            if (kv[0] == "redirect_uri" && kv.Length == 2 && Uri.TryCreate(Uri.UnescapeDataString(kv[1]), UriKind.Absolute, out var uri))
                return uri;
        }

        return null;
    }

    private static bool IsSameEndpoint(Uri a, Uri b) =>
        a.Port == b.Port && string.Equals(a.AbsolutePath, b.AbsolutePath, StringComparison.OrdinalIgnoreCase)
        && (string.Equals(a.Host, b.Host, StringComparison.OrdinalIgnoreCase) || a.IsLoopback && b.IsLoopback);

    private static bool IsApproval(string? text) =>
        !string.IsNullOrWhiteSpace(text) && ApproveWordRegex().IsMatch(text) && !RejectWordRegex().IsMatch(text);

    private static string? Attr(string tag, string name)
    {
        var m = Regex.Match(tag, $@"\b{name}\s*=\s*(?:""(?<v>[^""]*)""|'(?<v>[^']*)'|(?<v>[^\s>]+))", RegexOptions.IgnoreCase);
        return m.Success ? m.Groups["v"].Value : null;
    }

    private static bool IsLoopback(object sender) =>
        sender is SslStream { TargetHostName: var host } && (host is "localhost" || IPAddress.TryParse(host, out var ip) && IPAddress.IsLoopback(ip));

    [GeneratedRegex(@"<form(?<attrs>[^>]*)>(?<body>.*?)</form>", RegexOptions.IgnoreCase | RegexOptions.Singleline)]
    private static partial Regex FormRegex();

    [GeneratedRegex(@"<button[^>]*>(?<text>.*?)</button>|<input[^>]*type\s*=\s*[""']?submit[^>]*>", RegexOptions.IgnoreCase | RegexOptions.Singleline)]
    private static partial Regex SubmitRegex();

    [GeneratedRegex(@"<input[^>]*type\s*=\s*[""']?hidden[^>]*>", RegexOptions.IgnoreCase)]
    private static partial Regex HiddenInputRegex();

    [GeneratedRegex(@"<input[^>]*type\s*=\s*[""']?password", RegexOptions.IgnoreCase)]
    private static partial Regex PasswordInputRegex();

    [GeneratedRegex(@"approve|allow|accept|confirm|authori[sz]e|grant", RegexOptions.IgnoreCase)]
    private static partial Regex ApproveWordRegex();

    [GeneratedRegex(@"deny|reject|decline|cancel", RegexOptions.IgnoreCase)]
    private static partial Regex RejectWordRegex();
}
