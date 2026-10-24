using System.Globalization;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApiTestTool.Engine.Signing;

/// <summary>How a request is signed. Anything other than <see cref="Normal"/> deliberately breaks the signature for negative tests.</summary>
public enum SigningMode
{
    Normal,
    None,
    CorruptSignature,
    WrongDigest,
}

public sealed record SignatureResult(string JwsSignature, string Digest, IReadOnlyList<string> SignedHeaders, string Algorithm);

/// <summary>
/// Creates the <c>X-Jws-Signature</c> and <c>Digest</c> request headers: a detached JWS (JAdES, b64=false) over
/// selected HTTP headers, following the Open Banking Europe JWS profile used by the Georgian XS2A standard.
/// Ported from AltaSoft.PSD2.Crypto <c>JwsEncoder</c> (request side) without its dependencies.
/// </summary>
public sealed class JwsSigner
{
    public const string JwsSignatureHeader = "X-Jws-Signature";
    public const string DigestHeader = "Digest";
    public const string SigDMechanism = "http://uri.etsi.org/19182/HttpHeaders";

    private static readonly JsonSerializerOptions s_jsonOptions = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        DictionaryKeyPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly X509Certificate2 _certificate;
    private readonly List<string> _x5c;

    public JwsSigner(X509Certificate2 qsealc)
    {
        if (!qsealc.HasPrivateKey)
            throw new ConfigurationException("QSealC certificate has no private key");

        _certificate = qsealc;
        _x5c = GetCertificateChainAsX5C(qsealc);
    }

    /// <summary>
    /// Headers that go into <c>sigD.pars</c>: <c>x-request-id</c> plus PSU/client headers, as required by the
    /// Berlin Group signature profile. <c>digest</c> is always appended last.
    /// </summary>
    public static bool IsSigningHeader(string lowerCaseName) => lowerCaseName is
        "x-request-id" or "psu-id" or "psu-corporate-id" or "client-redirect-uri" or "api-contract-id";

    /// <param name="headers">Request headers in the order they are sent.</param>
    /// <param name="body">Request body bytes, or empty when there is no body.</param>
    /// <param name="nowUtc">Signing time (sigT).</param>
    /// <param name="mode">Signing mode; non-normal modes produce an invalid signature on purpose.</param>
    public SignatureResult Sign(IReadOnlyList<KeyValuePair<string, string>> headers, ReadOnlySpan<byte> body, DateTime nowUtc, SigningMode mode = SigningMode.Normal)
    {
        var signingHeaders = new List<(string key, string value)>();
        foreach (var (name, value) in headers)
        {
            var lower = name.ToLowerInvariant();
            if (IsSigningHeader(lower))
                signingHeaders.Add((lower, value));
        }

        var pars = signingHeaders.ConvertAll(x => x.key);
        pars.Add("digest");

        var alg = GetAlgorithm(_certificate);

        // Key order matches the original encoder so signatures are comparable when debugging.
        var protectedHeader = new Dictionary<string, object>
        {
            ["typ"] = "JOSE",
            ["b64"] = false,
            ["x5c"] = _x5c,
            ["crit"] = new[] { "b64", "sigT", "sigD" },
            ["sigT"] = nowUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
            ["sigD"] = new Dictionary<string, object>
            {
                ["mId"] = SigDMechanism,
                ["pars"] = pars,
            },
            ["alg"] = alg,
        };

        var protectedHeaderBase64 = Base64Url.Encode(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(protectedHeader, s_jsonOptions)));

        var digest = Digest.ComputeSha256(body);
        if (mode == SigningMode.WrongDigest)
            digest = Digest.ComputeSha256("this is not the request body"u8);

        signingHeaders.Add(("digest", digest));

        var headersToSign = string.Join('\n', signingHeaders.Select(x => $"{x.key}: {x.value}"));
        var signature = SignData($"{protectedHeaderBase64}.{headersToSign}", alg);

        if (mode == SigningMode.CorruptSignature)
            signature[^1] ^= 0xFF;

        return new SignatureResult($"{protectedHeaderBase64}..{Base64Url.Encode(signature)}", digest, pars, alg);
    }

    private byte[] SignData(string data, string alg)
    {
        var bytes = Encoding.UTF8.GetBytes(data);

        using var rsa = _certificate.GetRSAPrivateKey();
        if (rsa is not null)
            return rsa.SignData(bytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);

        using var ecdsa = _certificate.GetECDsaPrivateKey();
        if (ecdsa is not null)
            return ecdsa.SignData(bytes, HashAlgorithmName.SHA256); // IEEE P1363 format, as JWS requires

        throw new ConfigurationException($"QSealC key algorithm is not supported for {alg}");
    }

    private static string GetAlgorithm(X509Certificate2 cert)
    {
        using var rsa = cert.GetRSAPublicKey();
        if (rsa is not null)
            return "PS256";

        using var ecdsa = cert.GetECDsaPublicKey();
        if (ecdsa is not null)
            return "ES256";

        throw new ConfigurationException("QSealC must have an RSA or ECDSA key");
    }

    /// <summary>The signing certificate and whatever part of its chain can be built locally, without downloads.</summary>
    internal static List<string> GetCertificateChainAsX5C(X509Certificate2 cert)
    {
        using var chain = new X509Chain
        {
            ChainPolicy =
            {
                RevocationMode = X509RevocationMode.NoCheck,
                VerificationFlags = X509VerificationFlags.AllFlags,
                DisableCertificateDownloads = true,
                TrustMode = X509ChainTrustMode.CustomRootTrust,
            },
        };

        _ = chain.Build(cert);

        var result = new List<string>(chain.ChainElements.Count);
        foreach (var element in chain.ChainElements)
            result.Add(Convert.ToBase64String(element.Certificate.Export(X509ContentType.Cert)));

        if (result.Count == 0)
            result.Add(Convert.ToBase64String(cert.Export(X509ContentType.Cert)));

        return result;
    }
}
