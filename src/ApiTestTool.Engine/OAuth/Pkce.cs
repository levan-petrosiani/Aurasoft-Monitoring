using System.Security.Cryptography;
using System.Text;
using ApiTestTool.Engine.Signing;

namespace ApiTestTool.Engine.OAuth;

/// <summary>
/// PKCE (RFC 7636) with the S256 method, as the TestTool does in Psd2Session.GetOAuthUrlAsync:
/// verifier = Base64url(32 random bytes), challenge = Base64url(SHA-256(verifier)).
/// </summary>
public static class Pkce
{
    public const string Method = "S256";

    public static string CreateVerifier() => Base64Url.Encode(RandomNumberGenerator.GetBytes(32));

    public static string CreateChallenge(string verifier) => Base64Url.Encode(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));

    /// <summary>Random value for the OAuth <c>state</c> parameter.</summary>
    public static string CreateState(string prefix) => prefix + Guid.NewGuid().ToString("N");
}
