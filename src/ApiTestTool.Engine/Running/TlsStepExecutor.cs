using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using ApiTestTool.Engine.Certificates;
using ApiTestTool.Engine.Scenarios;

namespace ApiTestTool.Engine.Running;

/// <summary>Opens a TLS connection to the bank (optionally with our QWAC) and reports protocol and server certificate facts.</summary>
internal static class TlsStepExecutor
{
    public static async Task ExecuteAsync(Step step, RunContext ctx, StepResult result, CancellationToken cancellationToken)
    {
        var spec = step.Tls!;
        var url = spec.Target switch
        {
            "discovery" => ctx.Profile.Endpoints.Discovery ?? throw new ConfigurationException("tls.target is 'discovery' but endpoints.discovery is not set"),
            _ => ctx.Profile.Endpoints.Xs2a,
        };

        var uri = new Uri(url);
        if (uri.Scheme != Uri.UriSchemeHttps)
        {
            result.Failures.Add($"{url} does not use HTTPS");
            return;
        }

        var minProtocol = spec.MinProtocol switch
        {
            "Tls12" => SslProtocols.Tls12,
            "Tls13" => SslProtocols.Tls13,
            _ => throw new ConfigurationException("tls.minProtocol must be Tls12 or Tls13"),
        };

        if (spec.SendClientCertificate && ctx.Qwac is null)
            result.Warnings.Add("No QWAC configured; connecting without a client certificate");

        X509Certificate2? serverCert = null;
        var policyErrors = SslPolicyErrors.None;

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(ctx.Profile.Limits.TimeoutSeconds));

        using var tcp = new TcpClient();
        try
        {
            await tcp.ConnectAsync(uri.Host, uri.Port, timeout.Token).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException && !cancellationToken.IsCancellationRequested)
        {
            result.Failures.Add($"Cannot connect to {uri.Host}:{uri.Port}: {(ex is OperationCanceledException ? "timeout" : ex.Message)}");
            return;
        }

        await using var ssl = new SslStream(tcp.GetStream(), leaveInnerStreamOpen: false);
        var options = new SslClientAuthenticationOptions
        {
            TargetHost = uri.Host,
            ClientCertificates = spec.SendClientCertificate && ctx.Qwac is not null ? new X509CertificateCollection { ctx.Qwac } : null,
            RemoteCertificateValidationCallback = (_, cert, _, errors) =>
            {
                serverCert = cert is null ? null : X509CertificateLoader.LoadCertificate(cert.GetRawCertData());
                policyErrors = errors;
                return true; // inspect below instead of aborting the handshake
            },
        };

        try
        {
            await ssl.AuthenticateAsClientAsync(options, timeout.Token).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is AuthenticationException or IOException or OperationCanceledException && !cancellationToken.IsCancellationRequested)
        {
            result.Failures.Add($"TLS handshake with {uri.Host} failed: {(ex is OperationCanceledException ? "timeout" : ex.Message)}");
            return;
        }

        result.Info.Add($"Protocol {ssl.SslProtocol}, cipher {ssl.NegotiatedCipherSuite}");
        if (ssl.SslProtocol < minProtocol)
            result.Failures.Add($"Negotiated {ssl.SslProtocol}, minimum is {minProtocol}");

        if (serverCert is null)
        {
            result.Failures.Add("Server did not present a certificate");
            return;
        }

        using (serverCert)
        {
            var info = Psd2CertificateInfo.From(serverCert);
            var now = ctx.UtcNow();
            result.Info.Add($"Server certificate: {info.Subject}");
            result.Info.Add($"Issuer: {info.Issuer}");
            result.Info.Add($"Valid until {info.NotAfterUtc:yyyy-MM-dd} ({info.DaysToExpiry(now)} days)");
            if (info.OrganizationId is not null)
                result.Info.Add($"Server organizationIdentifier: {info.OrganizationId}");

            if (policyErrors != SslPolicyErrors.None)
            {
                var message = $"Server certificate is not trusted by this machine: {policyErrors}";
                if (ctx.Profile.Tpp.ValidateServerCertificate)
                    result.Failures.Add(message);
                else
                    result.Warnings.Add(message + " (accepted because tpp.validateServerCertificate is false)");
            }

            if (!info.IsValidAt(now))
                result.Failures.Add($"Server certificate is not valid now (valid {info.NotBeforeUtc:yyyy-MM-dd} – {info.NotAfterUtc:yyyy-MM-dd})");
            else if (info.DaysToExpiry(now) < spec.WarnDaysBeforeExpiry)
                result.Warnings.Add($"Server certificate expires in {info.DaysToExpiry(now)} days");
        }
    }
}
