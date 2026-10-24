using System.Text.Json.Nodes;

namespace ApiTestTool.MockBank;

/// <summary>Error responses in the Georgian XS2A format: application/problem+json with a Berlin Group <c>code</c>.</summary>
public static class Problems
{
    public static IResult Xs2a(int status, string code, string detail) =>
        Results.Json(
            new JsonObject
            {
                ["type"] = $"https://berlin-group.org/problems/{code.ToLowerInvariant().Replace('_', '-')}",
                ["title"] = code,
                ["status"] = status,
                ["detail"] = detail,
                ["code"] = code,
            },
            contentType: "application/problem+json",
            statusCode: status);

    public static IResult FormatError(string detail) => Xs2a(400, "FORMAT_ERROR", detail);

    public static IResult ParameterNotSupported(string detail) => Xs2a(400, "PARAMETER_NOT_SUPPORTED", detail);

    public static IResult ConsentUnknown() => Xs2a(403, "CONSENT_UNKNOWN", "The consent does not exist or belongs to another TPP");

    public static IResult TokenUnknown() => Xs2a(401, "TOKEN_UNKNOWN", "A valid Bearer access token is required");

    /// <summary>OAuth2 (RFC 6749) error for the token endpoint.</summary>
    public static IResult OAuth(string error, string description, int status = 400) =>
        Results.Json(new JsonObject { ["error"] = error, ["error_description"] = description }, statusCode: status);
}
