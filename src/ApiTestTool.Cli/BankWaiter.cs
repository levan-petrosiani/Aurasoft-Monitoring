using System.Net.Sockets;
using ApiTestTool.Engine;

namespace ApiTestTool.Cli;

/// <summary><c>--wait</c>: lets the CLI start together with MockBank (e.g. a multi-project launch in the IDE).</summary>
internal static class BankWaiter
{
    public static async Task WaitAsync(string xs2aUrl, int seconds, CancellationToken cancellationToken)
    {
        var uri = new Uri(xs2aUrl);
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        var announced = false;

        while (true)
        {
            try
            {
                using var tcp = new TcpClient();
                using var attempt = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                attempt.CancelAfter(TimeSpan.FromSeconds(2));
                await tcp.ConnectAsync(uri.Host, uri.Port, attempt.Token);
                return;
            }
            catch (Exception ex) when (ex is SocketException or OperationCanceledException && !cancellationToken.IsCancellationRequested)
            {
                if (DateTime.UtcNow > deadline)
                    throw new ConfigurationException($"{uri.Host}:{uri.Port} did not accept connections within {seconds} s. Is the bank (or MockBank) running?");

                if (!announced)
                {
                    Out.Muted($"Waiting up to {seconds} s for {uri.Host}:{uri.Port} ...");
                    announced = true;
                }

                await Task.Delay(500, cancellationToken);
            }
        }
    }
}
