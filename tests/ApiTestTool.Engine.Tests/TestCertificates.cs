using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace ApiTestTool.Engine.Tests;

internal static class TestCertificates
{
    public const string TppId = "PSDGE-NBG-TEST01";

    /// <summary>Self-signed RSA certificate with a PSD2 organizationIdentifier, like a test QSealC.</summary>
    public static X509Certificate2 CreateRsa(string organizationId = TppId)
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest($"CN=ApiTestTool Test TPP, OID.2.5.4.97={organizationId}, C=GE", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        return request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(1));
    }

    public static X509Certificate2 CreateEcdsa()
    {
        using var ecdsa = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var request = new CertificateRequest($"CN=ApiTestTool EC TPP, OID.2.5.4.97={TppId}", ecdsa, HashAlgorithmName.SHA256);
        return request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(1));
    }
}
