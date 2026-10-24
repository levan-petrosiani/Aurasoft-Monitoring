using System.Net.Sockets;
using ApiTestTool.MockBank;
using Microsoft.Extensions.Options;

namespace ApiTestTool.Portal.Services;

/// <summary>Hosts ApiTestTool.MockBank inside the portal process on demand.</summary>
public sealed class MockBankService : IAsyncDisposable
{
    private readonly SemaphoreSlim _lock = new(1, 1);
    private readonly Uri _xs2a;
    private WebApplication? _app;

    public MockBankService(IOptions<PortalOptions> options)
    {
        _xs2a = new Uri(options.Value.MockBankXs2aUrl);
    }

    public event Action? Changed;

    /// <summary>True when this portal hosts MockBank.</summary>
    public bool IsHostedHere => _app is not null;

    public string Xs2aUrl => _xs2a.ToString();

    public string DashboardUrl => "http://localhost:7080/";

    /// <summary>True when something (this portal, the MockBank project or <c>aqt mock</c>) listens on the MockBank port.</summary>
    public async Task<bool> IsReachableAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var tcp = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(1));
            await tcp.ConnectAsync(_xs2a.Host, _xs2a.Port, timeout.Token);
            return true;
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException)
        {
            return false;
        }
    }

    /// <summary>Starts MockBank here unless one is already reachable.</summary>
    public async Task EnsureRunningAsync(CancellationToken cancellationToken = default)
    {
        await _lock.WaitAsync(cancellationToken);
        try
        {
            if (_app is not null || await IsReachableAsync(cancellationToken))
                return;

            var app = MockBankHost.Create([$"--MockBank:HttpsPort={_xs2a.Port}"], quiet: true);
            await app.StartAsync(cancellationToken);
            _app = app;
        }
        finally
        {
            _lock.Release();
        }

        Changed?.Invoke();
    }

    public async Task StopAsync()
    {
        await _lock.WaitAsync();
        try
        {
            if (_app is null)
                return;

            await _app.StopAsync();
            await _app.DisposeAsync();
            _app = null;
        }
        finally
        {
            _lock.Release();
        }

        Changed?.Invoke();
    }

    public async ValueTask DisposeAsync() => await StopAsync();
}
