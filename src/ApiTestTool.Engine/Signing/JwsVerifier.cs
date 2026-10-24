using System.Globalization;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using ApiTestTool.Engine.Certificates;

namespace ApiTestTool.Engine.Signing;

public sealed record JwsVerificationResult(bool IsPresent, IReadOnlyList<string> Errors, IReadOnlyList<string> Warnings, string? SignerSubject, string? SignerOrganizationId)
{
    public bool IsValid => IsPresent && Errors.Count == 0;
}

/// <summary>
/// Verifies the bank's <c>X-Jws-Signature</c> on a response: protected header rules, signature value over the
/// headers listed in <c>sigD.pars</c>, and the body <c>Digest</c>. It does not check the certificate's trust chain,
/// because sandbox certificates are often issued by test CAs; the signer is reported instead.
/// </summary>
public static class JwsVerifier
{
    private static readonly HashSet<string> s_validAlgorithms = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"];

    /// <param name="headers">Response headers (content headers included), keys in any case.</param>
    /// <param name="body">Response body bytes.</param>
    /// <param name="statusCode">HTTP status, used for the <c>(response-status)</c> pseudo-header.</param>
    /// <param name="nowUtc">Current time for the sigT check.</param>
    public static JwsVerificationResult VerifyResponse(IReadOnlyDictionary<string, string> headers, ReadOnlySpan<byte> body, int statusCode, DateTime nowUtc)
    {
        var errors = new List<string>();
        var warnings = new List<string>();
        var h = new Dictionary<string, string>(headers, StringComparer.OrdinalIgnoreCase);

        if (!h.TryGetValue(JwsSigner.JwsSignatureHeader, out var jws) || string.IsNullOrWhiteSpace(jws))
            return new JwsVerificationResult(false, ["X-Jws-Signature header is missing"], warnings, null, null);

        var parts = jws.Split("..");
        if (parts.Length != 2)
            return Fail("X-Jws-Signature is not a detached JWS ('<header>..<signature>')");

        JsonElement header;
        try
        {
            using var doc = JsonDocument.Parse(Base64Url.Decode(parts[0]));
            header = doc.RootElement.Clone();
        }
        catch (Exception ex) when (ex is FormatException or JsonException)
        {
            return Fail($"Protected header cannot be decoded: {ex.Message}");
        }

        // b64 must be false
        if (!header.TryGetProperty("b64", out var b64) || b64.ValueKind != JsonValueKind.False)
            errors.Add("Protected header: 'b64' must be false");

        // alg
        var alg = header.TryGetProperty("alg", out var algEl) ? algEl.GetString() : null;
        if (alg is null || !s_validAlgorithms.Contains(alg))
            errors.Add($"Protected header: invalid 'alg' ({alg ?? "missing"})");

        // crit
        var crit = ReadStringArray(header, "crit");
        foreach (var required in new[] { "b64", "sigT", "sigD" })
        {
            if (!crit.Contains(required))
                errors.Add($"Protected header: 'crit' must contain '{required}'");
        }

        // typ
        if (header.TryGetProperty("typ", out var typ) && typ.GetString() != "JOSE")
            warnings.Add($"Protected header: 'typ' should be 'JOSE' (got '{typ.GetString()}')");

        // sigT — clock skew between our machine and the bank is possible, so these are warnings.
        if (header.TryGetProperty("sigT", out var sigTEl)
            && DateTime.TryParse(sigTEl.GetString(), CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var sigT))
        {
            var diff = (sigT - nowUtc).TotalSeconds;
            if (diff > 5)
                warnings.Add($"sigT is {diff:0}s in the future (clock skew?)");
            else if (diff < -120)
                warnings.Add($"sigT is {-diff:0}s in the past");
        }
        else
        {
            errors.Add("Protected header: 'sigT' is missing or not a valid UTC time");
        }

        // sigD
        List<string> pars = [];
        if (header.TryGetProperty("sigD", out var sigD))
        {
            if (!sigD.TryGetProperty("mId", out var mid) || mid.GetString() != JwsSigner.SigDMechanism)
                errors.Add($"Protected header: 'sigD.mId' must be '{JwsSigner.SigDMechanism}'");
            pars = ReadStringArray(sigD, "pars");
        }
        else
        {
            errors.Add("Protected header: 'sigD' is missing");
        }

        foreach (var required in new[] { "x-request-id", "digest" })
        {
            if (!pars.Contains(required))
                errors.Add($"Protected header: 'sigD.pars' must contain '{required}'");
        }

        // x5c
        var x5c = ReadStringArray(header, "x5c");
        X509Certificate2? signer = null;
        if (x5c.Count == 0)
        {
            errors.Add("Protected header: 'x5c' (signing certificate) is missing");
        }
        else
        {
            try
            {
                signer = X509CertificateLoader.LoadCertificate(Convert.FromBase64String(x5c[0]));
            }
            catch (Exception ex) when (ex is FormatException or CryptographicException)
            {
                errors.Add($"Protected header: 'x5c[0]' is not a valid certificate: {ex.Message}");
            }
        }

        // Signature value
        if (signer is not null && alg is not null && s_validAlgorithms.Contains(alg) && pars.Count > 0)
        {
            var lines = new List<string>(pars.Count);
            var missing = false;
            foreach (var key in pars)
            {
                string? value = key switch
                {
                    "(response-status)" => statusCode.ToString(CultureInfo.InvariantCulture),
                    "content-type" => h.TryGetValue(key, out var ct) ? ct.ToLowerInvariant() : null,
                    _ => h.TryGetValue(key, out var v) ? v : null,
                };

                if (value is null)
                {
                    errors.Add($"Header '{key}' listed in sigD.pars is not present in the response");
                    missing = true;
                    continue;
                }

                lines.Add($"{key}: {value}");
            }

            if (!missing)
            {
                var data = Encoding.UTF8.GetBytes($"{parts[0]}.{string.Join('\n', lines)}");
                byte[] signature;
                try
                {
                    signature = Base64Url.Decode(parts[1]);
                    if (!VerifySignature(signer, alg, data, signature))
                        errors.Add("Signature value is invalid");
                }
                catch (FormatException)
                {
                    errors.Add("Signature value is not valid Base64url");
                }
            }
        }

        // Digest
        if (h.TryGetValue("Digest", out var digest))
        {
            if (!Digest.Verify(digest, body, out var digestError))
                errors.Add(digestError!);
        }
        else
        {
            errors.Add("Digest header is missing");
        }

        var info = signer is null ? null : Psd2CertificateInfo.From(signer);
        signer?.Dispose();
        return new JwsVerificationResult(true, errors, warnings, info?.Subject, info?.OrganizationId);

        JwsVerificationResult Fail(string error) => new(true, [error], warnings, null, null);
    }

    private static bool VerifySignature(X509Certificate2 cert, string alg, byte[] data, byte[] signature)
    {
        var hash = alg[2..] switch
        {
            "256" => HashAlgorithmName.SHA256,
            "384" => HashAlgorithmName.SHA384,
            _ => HashAlgorithmName.SHA512,
        };

        if (alg.StartsWith("ES", StringComparison.Ordinal))
        {
            using var ecdsa = cert.GetECDsaPublicKey();
            return ecdsa is not null && ecdsa.VerifyData(data, signature, hash);
        }

        using var rsa = cert.GetRSAPublicKey();
        if (rsa is null)
            return false;

        var padding = alg.StartsWith("PS", StringComparison.Ordinal) ? RSASignaturePadding.Pss : RSASignaturePadding.Pkcs1;
        return rsa.VerifyData(data, signature, hash, padding);
    }

    private static List<string> ReadStringArray(JsonElement element, string name)
    {
        var result = new List<string>();
        if (element.TryGetProperty(name, out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.String)
                    result.Add(item.GetString()!);
            }
        }

        return result;
    }
}
