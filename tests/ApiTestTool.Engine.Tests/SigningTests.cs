using System.Text;
using System.Text.Json;
using ApiTestTool.Engine.Certificates;
using ApiTestTool.Engine.Signing;

namespace ApiTestTool.Engine.Tests;

public class SigningTests
{
    private static readonly DateTime s_now = new(2026, 10, 20, 10, 0, 0, DateTimeKind.Utc);

    [Fact]
    public void Digest_of_empty_body_is_sha256_of_nothing()
    {
        Assert.Equal("SHA-256=47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=", Digest.ComputeSha256([]));
    }

    [Fact]
    public void Digest_of_body_matches_known_value()
    {
        Assert.Equal("SHA-256=ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=", Digest.ComputeSha256("abc"u8));
        Assert.True(Digest.Verify("SHA-256=ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=", "abc"u8, out _));
        Assert.False(Digest.Verify("SHA-256=ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=", "abd"u8, out _));
    }

    [Fact]
    public void Signs_x_request_id_and_psu_headers_then_digest()
    {
        using var cert = TestCertificates.CreateRsa();
        var signer = new JwsSigner(cert);

        var result = signer.Sign(
        [
            new("X-Request-ID", "7c7cf89d-a25b-4d75-b9c0-01b5906831ca"),
            new("PSU-IP-Address", "192.168.1.1"),   // not a signing header in this profile
            new("PSU-ID", "user1"),
        ], "{}"u8, s_now);

        Assert.Equal(["x-request-id", "psu-id", "digest"], result.SignedHeaders);
        Assert.Equal("PS256", result.Algorithm);

        var protectedHeader = JsonDocument.Parse(Base64Url.Decode(result.JwsSignature.Split("..")[0])).RootElement;
        Assert.Equal("JOSE", protectedHeader.GetProperty("typ").GetString());
        Assert.False(protectedHeader.GetProperty("b64").GetBoolean());
        Assert.Equal("2026-10-20T10:00:00Z", protectedHeader.GetProperty("sigT").GetString());
        Assert.Equal(["b64", "sigT", "sigD"], protectedHeader.GetProperty("crit").EnumerateArray().Select(e => e.GetString()));
        Assert.Equal(JwsSigner.SigDMechanism, protectedHeader.GetProperty("sigD").GetProperty("mId").GetString());
        Assert.NotEmpty(protectedHeader.GetProperty("x5c").EnumerateArray());
    }

    [Fact]
    public void Signature_verifies_with_the_signers_certificate()
    {
        using var cert = TestCertificates.CreateRsa();
        var body = Encoding.UTF8.GetBytes("""{"consentType":"accountList"}""");
        var headers = Signed(cert, body, SigningMode.Normal);

        var verification = JwsVerifier.VerifyResponse(headers, body, 201, s_now);

        Assert.True(verification.IsValid, string.Join("; ", verification.Errors));
        Assert.Equal(TestCertificates.TppId, verification.SignerOrganizationId);
    }

    [Fact]
    public void Ecdsa_certificate_signs_with_ES256()
    {
        using var cert = TestCertificates.CreateEcdsa();
        var headers = Signed(cert, [], SigningMode.Normal);

        var verification = JwsVerifier.VerifyResponse(headers, [], 200, s_now);

        Assert.True(verification.IsValid, string.Join("; ", verification.Errors));
    }

    [Theory]
    [InlineData(SigningMode.CorruptSignature, "Signature value is invalid")]
    [InlineData(SigningMode.WrongDigest, "Digest does not match the body")]
    public void Broken_signing_modes_are_detected(SigningMode mode, string expectedError)
    {
        using var cert = TestCertificates.CreateRsa();
        var body = "{}"u8.ToArray();
        var headers = Signed(cert, body, mode);

        var verification = JwsVerifier.VerifyResponse(headers, body, 201, s_now);

        Assert.Contains(expectedError, verification.Errors);
    }

    [Fact]
    public void Missing_signature_is_reported_as_not_present()
    {
        var verification = JwsVerifier.VerifyResponse(new Dictionary<string, string>(), [], 200, s_now);
        Assert.False(verification.IsPresent);
    }

    [Fact]
    public void Organization_id_is_read_from_the_subject()
    {
        using var cert = TestCertificates.CreateRsa("PSDGE-NBG-123456");
        Assert.Equal("PSDGE-NBG-123456", Psd2CertificateInfo.From(cert).OrganizationId);
    }

    private static Dictionary<string, string> Signed(System.Security.Cryptography.X509Certificates.X509Certificate2 cert, byte[] body, SigningMode mode)
    {
        var headers = new List<KeyValuePair<string, string>> { new("X-Request-ID", Guid.NewGuid().ToString()) };
        var result = new JwsSigner(cert).Sign(headers, body, s_now, mode);

        var all = headers.ToDictionary(h => h.Key, h => h.Value, StringComparer.OrdinalIgnoreCase);
        all[JwsSigner.DigestHeader] = result.Digest;
        all[JwsSigner.JwsSignatureHeader] = result.JwsSignature;
        return all;
    }
}
