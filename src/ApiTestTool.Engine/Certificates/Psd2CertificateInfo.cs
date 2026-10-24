using System.Security.Cryptography.X509Certificates;

namespace ApiTestTool.Engine.Certificates;

/// <summary>
/// The PSD2-relevant facts about an eIDAS certificate (QWAC / QSealC).
/// </summary>
public sealed record Psd2CertificateInfo(
    string Subject,
    string Issuer,
    string Thumbprint,
    string? OrganizationId,
    DateTime NotBeforeUtc,
    DateTime NotAfterUtc,
    bool HasPrivateKey,
    string KeyAlgorithm)
{
    /// <summary>OID of the organizationIdentifier attribute that carries the PSD2 authorisation number (TppId).</summary>
    public const string OrganizationIdentifierOid = "2.5.4.97";

    public int DaysToExpiry(DateTime nowUtc) => (int)Math.Floor((NotAfterUtc - nowUtc).TotalDays);

    public bool IsValidAt(DateTime nowUtc) => nowUtc >= NotBeforeUtc && nowUtc <= NotAfterUtc;

    public static Psd2CertificateInfo From(X509Certificate2 cert)
    {
        return new Psd2CertificateInfo(
            cert.Subject,
            cert.Issuer,
            cert.Thumbprint,
            GetOrganizationId(cert.SubjectName),
            cert.NotBefore.ToUniversalTime(),
            cert.NotAfter.ToUniversalTime(),
            cert.HasPrivateKey,
            cert.GetKeyAlgorithm() switch
            {
                "1.2.840.113549.1.1.1" => "RSA",
                "1.2.840.10045.2.1" => "ECDSA",
                var other => other
            });
    }

    public static string? GetOrganizationId(X500DistinguishedName name)
    {
        foreach (var rdn in name.EnumerateRelativeDistinguishedNames())
        {
            if (!rdn.HasMultipleElements && rdn.GetSingleElementType().Value == OrganizationIdentifierOid)
                return rdn.GetSingleElementValue();
        }

        return null;
    }
}
