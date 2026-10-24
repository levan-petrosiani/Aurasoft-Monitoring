using System.Security.Cryptography;

namespace ApiTestTool.Engine.Signing;

/// <summary>
/// HTTP <c>Digest</c> header value (RFC 3230): <c>SHA-256=Base64(SHA-256(body))</c>.
/// An absent body is hashed as an empty byte array, as the Georgian XS2A signature profile requires.
/// </summary>
public static class Digest
{
    public const string Sha256Name = "SHA-256";

    public static string ComputeSha256(ReadOnlySpan<byte> body) => $"{Sha256Name}={Convert.ToBase64String(SHA256.HashData(body))}";

    /// <summary>Checks a received <c>Digest</c> header value against a body. Supports SHA-256/384/512.</summary>
    public static bool Verify(string headerValue, ReadOnlySpan<byte> body, out string? error)
    {
        var parts = headerValue.Split('=', 2);
        if (parts.Length != 2)
        {
            error = $"Invalid Digest header '{headerValue}'";
            return false;
        }

        byte[] expected;
        try
        {
            expected = Convert.FromBase64String(parts[1]);
        }
        catch (FormatException)
        {
            error = "Digest value is not valid Base64";
            return false;
        }

        byte[]? actual = parts[0].ToUpperInvariant() switch
        {
            "SHA-256" => SHA256.HashData(body),
            "SHA-384" => SHA384.HashData(body),
            "SHA-512" => SHA512.HashData(body),
            _ => null
        };

        if (actual is null)
        {
            error = $"Unsupported digest algorithm '{parts[0]}'";
            return false;
        }

        if (!CryptographicOperations.FixedTimeEquals(actual, expected))
        {
            error = "Digest does not match the body";
            return false;
        }

        error = null;
        return true;
    }
}
