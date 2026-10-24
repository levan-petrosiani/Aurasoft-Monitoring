using System.Net.Sockets;
using ApiTestTool.MockBank;
using Microsoft.AspNetCore.Builder;

namespace ApiTestTool.Cli;

/// <summary>Runs MockBank inside the aqt process for <c>--with-mock</c>, unless one is already listening.</summary>
internal sealed class EmbeddedMockBank : IAsyncDisposable
{
    private readonly WebApplication _app;

    private EmbeddedMockBank(WebApplication app)
    {
        _app = app;
    }

    /// <summary>Starts MockBank, or returns null when something already listens on the profile's XS2A port.</summary>
    public static async Task<EmbeddedMockBank?> StartAsync(string xs2aUrl, CancellationToken cancellationToken)
    {
        var uri = new Uri(xs2aUrl);
        if (!uri.IsLoopback)
            throw new Engine.ConfigurationException($"--with-mock only works with a localhost profile such as MOCK; this profile points to {uri.Host}");

        if (await IsListeningAsync(uri, cancellationToken))
        {
            Out.Muted($"MockBank already running on {uri.Host}:{uri.Port}; using it");
            return null;
        }

        var app = MockBankHost.Create([$"--MockBank:HttpsPort={uri.Port}"], quiet: true);
        await app.StartAsync(cancellationToken);
        Out.Muted($"Started MockBank in this process: XS2A {uri.GetLeftPart(UriPartial.Authority)}, dashboard http://localhost:7080/");
        return new EmbeddedMockBank(app);
    }

    public async ValueTask DisposeAsync()
    {
        await _app.StopAsync();
        await _app.DisposeAsync();
    }

    private static async Task<bool> IsListeningAsync(Uri uri, CancellationToken cancellationToken)
    {
        try
        {
            using var tcp = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(1));
            await tcp.ConnectAsync(uri.Host, uri.Port, timeout.Token);
            return true;
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException && !cancellationToken.IsCancellationRequested)
        {
            return false;
        }
    }
}
