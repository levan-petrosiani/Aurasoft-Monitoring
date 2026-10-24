using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Options;

namespace ApiTestTool.MockBank;

/// <summary>Consent endpoints, shaped after XSA2/Api/Controllers/AIS/ConsentsController (Georgian XS2A v2).</summary>
public static class ConsentEndpoints
{
    private static readonly HashSet<string> s_supportedTypes = ["accountList", "aspspManaged", "detailed"];

    public static void Map(WebApplication app)
    {
        var group = app.MapGroup("/v2/consents");

        group.MapPost("/account-access", CreateAsync);
        group.MapGet("/account-access/{consentId}/status", GetStatus);
        group.MapGet("/account-access/{consentId}", GetInformation);
        group.MapDelete("/account-access/{consentId}", Delete);
        group.MapPost("/{consentId}/authorisations", StartAuthorisation);
        group.MapGet("/{consentId}/authorisations/{authorisationId}", GetScaStatus);
    }

    private static async Task<IResult> CreateAsync(HttpContext http, MockState state, IOptions<MockBankOptions> options)
    {
        var ip = http.Request.Headers["PSU-IP-Address"].ToString();
        if (ip.Length == 0)
            return Problems.FormatError("PSU-IP-Address header is required");
        if (!IPAddress.TryParse(ip, out _))
            return Problems.FormatError($"PSU-IP-Address '{ip}' is not an IP address");

        JsonObject body;
        try
        {
            body = await JsonNode.ParseAsync(http.Request.Body, cancellationToken: http.RequestAborted) as JsonObject
                   ?? throw new JsonException("not an object");
        }
        catch (JsonException ex)
        {
            return Problems.FormatError($"Request body is not a valid JSON object: {ex.Message}");
        }

        if (body["access"] is not JsonObject access)
            return Problems.FormatError("'access' is required");

        var consentType = Text(body["consentType"]);
        if (consentType == "global")
            return Problems.ParameterNotSupported("consentType 'global' (all PSD2 accounts) is not supported");
        if (consentType is null || !s_supportedTypes.Contains(consentType))
            return Problems.FormatError($"consentType '{consentType}' is not valid");

        var rights = access.Select(p => p.Value).OfType<JsonArray>()
            .SelectMany(a => a.OfType<JsonObject>())
            .SelectMany(e => e["rights"] as JsonArray ?? [])
            .Select(Text)
            .ToList();
        if (rights.Contains("trustedBeneficiaries"))
            return Problems.ParameterNotSupported("Access to trusted beneficiaries is not supported");
        if (rights.Count == 0)
            return Problems.FormatError("At least one access right is required");

        if (body["recurringIndicator"] is not JsonValue recurring || !recurring.TryGetValue<bool>(out var recurringIndicator))
            return Problems.FormatError("'recurringIndicator' must be true or false");

        if (!DateOnly.TryParseExact(Text(body["validTo"]), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var validTo))
            return Problems.FormatError("'validTo' must be a date (yyyy-MM-dd)");
        if (validTo < DateOnly.FromDateTime(DateTime.UtcNow))
            return Problems.FormatError("'validTo' is in the past");

        if (body["frequencyPerDay"] is not JsonValue freq || !freq.TryGetValue<int>(out var frequencyPerDay) || frequencyPerDay < 1)
            return Problems.FormatError("'frequencyPerDay' must be a positive number");
        if (frequencyPerDay > options.Value.MaxFrequencyPerDay)
            return Problems.FormatError($"'frequencyPerDay' {frequencyPerDay} exceeds the maximum {options.Value.MaxFrequencyPerDay}");

        var consent = new Consent
        {
            Id = Guid.NewGuid().ToString(),
            TppId = TppId(http),
            ConsentType = consentType,
            Access = (JsonObject)access.DeepClone(),
            RecurringIndicator = recurringIndicator,
            ValidTo = validTo,
            FrequencyPerDay = frequencyPerDay,
            RedirectUri = RedirectUri(http),
        };
        state.Consents[consent.Id] = consent;

        var self = $"/v2/consents/account-access/{consent.Id}";
        http.Response.Headers.Location = self;
        http.Response.Headers["ASPSP-SCA-Approach"] = "REDIRECT";

        return Results.Json(new JsonObject
        {
            ["consentStatus"] = consent.Status,
            ["consentId"] = consent.Id,
            ["_links"] = new JsonObject
            {
                ["self"] = Link(self),
                ["status"] = Link($"{self}/status"),
                ["startAuthorisation"] = Link($"/v2/consents/{consent.Id}/authorisations"),
            },
        }, statusCode: 201);
    }

    private static IResult GetStatus(string consentId, HttpContext http, MockState state)
    {
        if (!TryGetOwnConsent(http, state, consentId, out var consent))
            return Problems.ConsentUnknown();

        return Results.Json(new JsonObject { ["consentStatus"] = consent.Status });
    }

    private static IResult GetInformation(string consentId, HttpContext http, MockState state)
    {
        // Reading consent details needs the PSU's token; checked before existence, like the real API.
        var token = Tokens.FromHeader(http, state);
        if (token is null)
            return Problems.TokenUnknown();

        if (!TryGetOwnConsent(http, state, consentId, out var consent) || token.ConsentId != consentId)
            return Problems.ConsentUnknown();

        return Results.Json(new JsonObject
        {
            ["access"] = consent.Access.DeepClone(),
            ["consentType"] = consent.ConsentType,
            ["recurringIndicator"] = consent.RecurringIndicator,
            ["validTo"] = consent.ValidTo.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            ["frequencyPerDay"] = consent.FrequencyPerDay,
            ["lastActionDate"] = consent.LastActionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            ["consentStatus"] = consent.Status,
        });
    }

    private static IResult Delete(string consentId, HttpContext http, MockState state)
    {
        if (!TryGetOwnConsent(http, state, consentId, out var consent))
            return Problems.ConsentUnknown();

        consent.Status = "terminatedByTpp";
        state.RevokeTokensFor(consentId);
        return Results.NoContent();
    }

    private static IResult StartAuthorisation(string consentId, HttpContext http, MockState state, IOptions<MockBankOptions> options)
    {
        if (!TryGetOwnConsent(http, state, consentId, out var consent))
            return Problems.ConsentUnknown();

        if (consent.Status != "received")
            return Problems.Xs2a(409, "STATUS_INVALID", $"Consent is '{consent.Status}', authorisation needs 'received'");

        var redirect = RedirectUri(http);
        if (consent.RedirectUri is not null && redirect is not null && redirect != consent.RedirectUri)
            return Problems.FormatError("Client-Redirect-URI differs from the one sent when the consent was created");

        var authorisation = new Authorisation { Id = Guid.NewGuid().ToString(), ConsentId = consentId };
        state.Authorisations[authorisation.Id] = authorisation;

        var self = $"/v2/consents/{consentId}/authorisations/{authorisation.Id}";
        http.Response.Headers.Location = self;

        return Results.Json(new JsonObject
        {
            ["authorisationId"] = authorisation.Id,
            ["scaStatus"] = authorisation.ScaStatus,
            ["_links"] = new JsonObject
            {
                ["scaOAuth"] = Link($"{options.Value.HttpsBaseUrl}/.well-known/oauth-authorization-server"),
                ["scaStatus"] = Link(self),
            },
        }, statusCode: 201);
    }

    private static IResult GetScaStatus(string consentId, string authorisationId, HttpContext http, MockState state)
    {
        if (!TryGetOwnConsent(http, state, consentId, out _)
            || !state.Authorisations.TryGetValue(authorisationId, out var authorisation)
            || authorisation.ConsentId != consentId)
        {
            return Problems.Xs2a(403, "RESOURCE_UNKNOWN", "Authorisation not found");
        }

        return Results.Json(new JsonObject { ["scaStatus"] = authorisation.ScaStatus });
    }

    internal static bool TryGetOwnConsent(HttpContext http, MockState state, string consentId, out Consent consent)
    {
        if (state.Consents.TryGetValue(consentId, out consent!) && consent.TppId == TppId(http))
            return true;

        consent = null!;
        return false;
    }

    internal static string TppId(HttpContext http) => (string)http.Items[Xs2aSecurityMiddleware.TppIdItem]!;

    internal static JsonObject Link(string href) => new() { ["href"] = href };

    private static string? RedirectUri(HttpContext http)
    {
        var value = http.Request.Headers["Client-Redirect-URI"].ToString();
        if (value.Length == 0)
            value = http.Request.Headers["TPP-Redirect-URI"].ToString();
        return value.Length == 0 ? null : value;
    }

    private static string? Text(JsonNode? node) => node is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;
}
