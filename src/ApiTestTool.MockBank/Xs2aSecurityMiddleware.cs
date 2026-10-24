using System.Security.Cryptography.X509Certificates;
using ApiTestTool.Engine.Certificates;
using ApiTestTool.Engine.Signing;
using Microsoft.Extensions.Options;

namespace ApiTestTool.MockBank;

/// <summary>
/// What a real ASPSP does around every XS2A call (paths under /v2):
/// requires the TPP's client certificate (mTLS) and a GUID X-Request-ID, verifies the X-Jws-Signature/Digest,
/// echoes X-Request-ID, and signs the response.
/// </summary>
public sealed class Xs2aSecurityMiddleware
{
    public const string TppIdItem = "TppId";

    private readonly RequestDelegate _next;
    private readonly MockBankOptions _options;
    private readonly JwsSigner _responseSigner;

    public Xs2aSecurityMiddleware(RequestDelegate next, IOptions<MockBankOptions> options, BankCertificates certificates)
    {
        _next = next;
        _options = options.Value;
        _responseSigner = new JwsSigner(certificates.QSealC);
    }

    public async Task InvokeAsync(HttpContext context)
    {
        if (!context.Request.Path.StartsWithSegments("/v2"))
        {
            await _next(context);
            return;
        }

        var requestId = context.Request.Headers["X-Request-ID"].ToString();
        if (requestId.Length > 0)
            context.Response.Headers["X-Request-ID"] = requestId;

        // Buffer the response so it can be signed after the endpoint has written it.
        var originalBody = context.Response.Body;
        using var buffer = new MemoryStream();
        context.Response.Body = buffer;

        try
        {
            var rejection = await CheckRequestAsync(context, requestId);
            if (rejection is not null)
                await rejection.ExecuteAsync(context);
            else
                await _next(context);

            var bytes = buffer.ToArray();
            if (_options.SignResponses)
            {
                var signature = _responseSigner.Sign([new("X-Request-ID", requestId)], bytes, DateTime.UtcNow);
                context.Response.Headers["Digest"] = signature.Digest;
                context.Response.Headers["X-Jws-Signature"] = signature.JwsSignature;
            }

            if (bytes.Length > 0)
                context.Response.ContentLength = bytes.Length;
            context.Response.Body = originalBody;
            if (bytes.Length > 0)
                await originalBody.WriteAsync(bytes, context.RequestAborted);
        }
        finally
        {
            context.Response.Body = originalBody;
        }
    }

    private async Task<IResult?> CheckRequestAsync(HttpContext context, string requestId)
    {
        var clientCert = context.Connection.ClientCertificate ?? await context.Connection.GetClientCertificateAsync(context.RequestAborted);
        if (clientCert is null)
            return Problems.Xs2a(401, "CERTIFICATE_MISSING", "No client certificate (QWAC) was presented");

        var tppId = Psd2CertificateInfo.GetOrganizationId(clientCert.SubjectName);
        if (tppId is null)
            return Problems.Xs2a(401, "CERTIFICATE_INVALID", "The client certificate has no organizationIdentifier (TPP id)");
        context.Items[TppIdItem] = tppId;

        if (!Guid.TryParse(requestId, out var guid) || guid == Guid.Empty)
            return Problems.FormatError("X-Request-ID header is missing or not a UUID");

        if (!_options.VerifySignatures)
            return null;

        context.Request.EnableBuffering();
        using var ms = new MemoryStream();
        await context.Request.Body.CopyToAsync(ms, context.RequestAborted);
        context.Request.Body.Position = 0;

        var headers = context.Request.Headers.ToDictionary(h => h.Key, h => h.Value.ToString(), StringComparer.OrdinalIgnoreCase);
        var verification = JwsVerifier.VerifyResponse(headers, ms.ToArray(), 0, DateTime.UtcNow);

        if (!verification.IsPresent)
            return Problems.Xs2a(401, "SIGNATURE_MISSING", "X-Jws-Signature header is missing");

        if (verification.Errors.Any(e => e.Contains("Digest", StringComparison.Ordinal)) && verification.Errors.All(e => !e.Contains("Signature value", StringComparison.Ordinal)))
            return Problems.FormatError("Digest: " + string.Join("; ", verification.Errors));

        if (!verification.IsValid)
            return Problems.Xs2a(401, "SIGNATURE_INVALID", string.Join("; ", verification.Errors));

        if (verification.SignerOrganizationId != tppId)
            return Problems.Xs2a(401, "CERTIFICATE_INVALID", $"QSealC belongs to '{verification.SignerOrganizationId}', QWAC to '{tppId}'");

        return null;
    }
}

/// <summary>The bank's own certificates, generated once at startup.</summary>
public sealed class BankCertificates(X509Certificate2 server, X509Certificate2 qsealc)
{
    public X509Certificate2 Server { get; } = server;

    public X509Certificate2 QSealC { get; } = qsealc;
}
