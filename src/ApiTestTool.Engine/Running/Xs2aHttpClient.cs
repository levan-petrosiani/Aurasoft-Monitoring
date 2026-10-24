using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using ApiTestTool.Engine.Profiles;

namespace ApiTestTool.Engine.Running;

internal static class Xs2aHttpClient
{
    /// <summary>HttpClient with the QWAC as TLS client certificate (mutual TLS), no redirects and no decompression.</summary>
    public static HttpClient Create(BankProfile profile, X509Certificate2? qwac)
    {
        var handler = new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            AutomaticDecompression = DecompressionMethods.None,
            PooledConnectionLifetime = TimeSpan.FromMinutes(2),
            SslOptions = new SslClientAuthenticationOptions
            {
                ClientCertificates = qwac is null ? null : new X509CertificateCollection { qwac },
                LocalCertificateSelectionCallback = qwac is null ? null : (_, _, _, _, _) => qwac,
                RemoteCertificateValidationCallback = (_, _, _, errors) => !profile.Tpp.ValidateServerCertificate || errors == SslPolicyErrors.None,
            },
        };

        var retry = profile.Limits.Retry;
        HttpMessageHandler pipeline = retry is { Attempts: > 1 } ? new RetryHandler(retry) { InnerHandler = handler } : handler;

        return new HttpClient(pipeline)
        {
            // The client timeout covers all attempts of one request.
            Timeout = TimeSpan.FromSeconds(profile.Limits.TimeoutSeconds * Math.Max(1, retry?.Attempts ?? 1)),
        };
    }
}
