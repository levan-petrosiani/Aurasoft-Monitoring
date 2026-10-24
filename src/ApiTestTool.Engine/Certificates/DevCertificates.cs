using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace ApiTestTool.Engine.Certificates;

/// <summary>
/// Throw-away self-signed certificates for local testing (ApiTestTool.MockBank and the MOCK profile).
/// Never use them against a real bank.
/// </summary>
public static class DevCertificates
{
    private static readonly Oid s_serverAuth = new("1.3.6.1.5.5.7.3.1");
    private static readonly Oid s_clientAuth = new("1.3.6.1.5.5.7.3.2");

    /// <summary>Certificate that looks like a PSD2 QWAC/QSealC: RSA 2048 with an organizationIdentifier.</summary>
    public static X509Certificate2 CreateTppCertificate(string commonName, string organizationId)
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest(
            $"CN={commonName}, O=ApiTestTool, OID.{Psd2CertificateInfo.OrganizationIdentifierOid}={organizationId}, C=GE",
            rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);

        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.NonRepudiation | X509KeyUsageFlags.KeyEncipherment, critical: true));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension([s_clientAuth], critical: false));

        return Persist(request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(1)));
    }

    /// <summary>TLS server certificate for localhost / 127.0.0.1 / ::1.</summary>
    public static X509Certificate2 CreateLocalhostServerCertificate(string organizationId)
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest(
            $"CN=localhost, O=ApiTestTool MockBank, OID.{Psd2CertificateInfo.OrganizationIdentifierOid}={organizationId}, C=GE",
            rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);

        var san = new SubjectAlternativeNameBuilder();
        san.AddDnsName("localhost");
        san.AddIpAddress(System.Net.IPAddress.Loopback);
        san.AddIpAddress(System.Net.IPAddress.IPv6Loopback);
        request.CertificateExtensions.Add(san.Build());
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.KeyEncipherment, critical: true));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension([s_serverAuth], critical: false));

        return Persist(request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(1)));
    }

    /// <summary>
    /// CreateSelfSigned returns an ephemeral key, which SChannel (Windows TLS) cannot use.
    /// A PFX round trip gives a key that TLS can use; it is removed when the certificate is disposed.
    /// </summary>
    private static X509Certificate2 Persist(X509Certificate2 ephemeral)
    {
        using (ephemeral)
        {
            return X509CertificateLoader.LoadPkcs12(ephemeral.Export(X509ContentType.Pfx), password: null, X509KeyStorageFlags.Exportable);
        }
    }
}
