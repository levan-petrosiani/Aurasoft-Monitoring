using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Web;

namespace ApiTestTool.Engine.OAuth;

/// <summary>What the bank's auth server sent back to the redirect URI.</summary>
public sealed record OAuthCallback(string? Code, string? State, string? Error, string? ErrorDescription);

/// <summary>
/// Minimal HTTP listener for the OAuth redirect (e.g. http://localhost:5199/callback). Uses raw sockets on the
/// loopback interfaces, so it needs no admin rights or URL ACLs. Requests to other paths (favicon, …) get 404
/// and the listener keeps waiting.
/// </summary>
public sealed class OAuthCallbackListener : IAsyncDisposable
{
    private readonly List<TcpListener> _listeners = [];
    private readonly string _path;

    private OAuthCallbackListener(Uri redirectUri)
    {
        _path = redirectUri.AbsolutePath;
        RedirectUri = redirectUri;
    }

    public Uri RedirectUri { get; }

    /// <summary>Starts listening. Throws <see cref="ConfigurationException"/> if the URI is not a free local http port.</summary>
    public static OAuthCallbackListener Start(string redirectUri)
    {
        if (!Uri.TryCreate(redirectUri, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttp || !uri.IsLoopback)
            throw new ConfigurationException($"Manual SCA needs a local http redirect URI (e.g. http://localhost:5199/callback); got '{redirectUri}'");

        var listener = new OAuthCallbackListener(uri);
        IPAddress[] addresses = uri.Host is "127.0.0.1" ? [IPAddress.Loopback] : uri.Host is "[::1]" ? [IPAddress.IPv6Loopback] : new[] { IPAddress.Loopback, IPAddress.IPv6Loopback };

        foreach (var address in addresses)
        {
            var tcp = new TcpListener(address, uri.Port);
            try
            {
                tcp.Start();
                listener._listeners.Add(tcp);
            }
            catch (SocketException ex) when (address.AddressFamily == AddressFamily.InterNetworkV6 && listener._listeners.Count > 0 && ex.SocketErrorCode != SocketError.AddressAlreadyInUse)
            {
                // No IPv6 on this machine; IPv4 is enough.
            }
            catch (SocketException ex)
            {
                listener.StopAll();
                throw new ConfigurationException($"Cannot listen on {uri.GetLeftPart(UriPartial.Authority)} for the OAuth callback: {ex.Message}", ex);
            }
        }

        return listener;
    }

    /// <summary>Waits for a request on the redirect path and returns its query parameters.</summary>
    /// <exception cref="TimeoutException">No callback arrived in time.</exception>
    public async Task<OAuthCallback> WaitAsync(TimeSpan timeout, CancellationToken cancellationToken)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        cts.CancelAfter(timeout);

        var accepts = _listeners.ToDictionary(l => l, l => l.AcceptTcpClientAsync(cts.Token).AsTask());
        try
        {
            while (true)
            {
                var done = await Task.WhenAny(accepts.Values).ConfigureAwait(false);
                var listener = accepts.First(p => p.Value == done).Key;

                TcpClient client;
                try
                {
                    client = await done.ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
                {
                    throw new TimeoutException($"No OAuth callback on {RedirectUri} within {timeout.TotalSeconds:0} s");
                }

                accepts[listener] = listener.AcceptTcpClientAsync(cts.Token).AsTask();

                using (client)
                {
                    var callback = await HandleAsync(client, cts.Token).ConfigureAwait(false);
                    if (callback is not null)
                        return callback;
                }
            }
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new TimeoutException($"No OAuth callback on {RedirectUri} within {timeout.TotalSeconds:0} s");
        }
    }

    private async Task<OAuthCallback?> HandleAsync(TcpClient client, CancellationToken cancellationToken)
    {
        var stream = client.GetStream();
        var requestLine = await ReadRequestLineAsync(stream, cancellationToken).ConfigureAwait(false);
        var parts = requestLine?.Split(' ');
        if (parts is not { Length: >= 2 })
            return null;

        var target = new Uri(RedirectUri, parts[1]);
        if (!string.Equals(target.AbsolutePath, _path, StringComparison.Ordinal))
        {
            await RespondAsync(stream, 404, "Not found", cancellationToken).ConfigureAwait(false);
            return null;
        }

        var query = HttpUtility.ParseQueryString(target.Query);
        var callback = new OAuthCallback(query["code"], query["state"], query["error"], query["error_description"]);

        var message = callback.Error is null
            ? "Authorization received. You can close this window and return to ApiTestTool."
            : $"The bank returned an error: {WebUtility.HtmlEncode(callback.Error)} {WebUtility.HtmlEncode(callback.ErrorDescription ?? string.Empty)}";
        await RespondAsync(stream, 200, message, cancellationToken).ConfigureAwait(false);
        return callback;
    }

    private static async Task<string?> ReadRequestLineAsync(NetworkStream stream, CancellationToken cancellationToken)
    {
        // Read the request head (we only need the first line); requests here have no body.
        var buffer = new byte[8192];
        var total = 0;
        while (total < buffer.Length)
        {
            var read = await stream.ReadAsync(buffer.AsMemory(total), cancellationToken).ConfigureAwait(false);
            if (read == 0)
                break;
            total += read;
            if (Encoding.ASCII.GetString(buffer, 0, total).Contains("\r\n\r\n", StringComparison.Ordinal))
                break;
        }

        var head = Encoding.ASCII.GetString(buffer, 0, total);
        var end = head.IndexOf("\r\n", StringComparison.Ordinal);
        return end < 0 ? (head.Length > 0 ? head : null) : head[..end];
    }

    private static async Task RespondAsync(NetworkStream stream, int status, string message, CancellationToken cancellationToken)
    {
        var html = $"<!doctype html><html><head><meta charset=\"utf-8\"><title>ApiTestTool</title></head><body style=\"font-family:system-ui;padding:2rem\"><p>{message}</p></body></html>";
        var body = Encoding.UTF8.GetBytes(html);
        var head = Encoding.ASCII.GetBytes($"HTTP/1.1 {status} {(status == 200 ? "OK" : "Not Found")}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n");
        await stream.WriteAsync(head, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(body, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private void StopAll()
    {
        foreach (var l in _listeners)
            l.Stop();
        _listeners.Clear();
    }

    public ValueTask DisposeAsync()
    {
        StopAll();
        return ValueTask.CompletedTask;
    }
}
