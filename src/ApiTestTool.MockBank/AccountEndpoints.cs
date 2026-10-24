using System.Globalization;
using System.Text.Json.Nodes;

namespace ApiTestTool.MockBank;

internal static class Tokens
{
    /// <summary>The valid, unexpired access token from the Authorization header, or null.</summary>
    public static AccessToken? FromHeader(HttpContext http, MockState state)
    {
        var header = http.Request.Headers.Authorization.ToString();
        if (!header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;

        return state.AccessTokens.TryGetValue(header[7..].Trim(), out var token) && token.ExpiresUtc > DateTime.UtcNow
            ? token
            : null;
    }
}

/// <summary>Account information endpoints (XSA2/Api/Controllers/AIS/AccountsController), with fixed sandbox data.</summary>
public static class AccountEndpoints
{
    public static void Map(WebApplication app)
    {
        var group = app.MapGroup("/v2/accounts");

        group.MapGet("", (HttpContext http, MockState state, bool? withBalance) =>
            Authorize(http, state, right: null, (consent, _) => Results.Json(new JsonObject
            {
                ["accounts"] = new JsonArray(state.Accounts.Select(a => (JsonNode)AccountJson(a, withBalance == true && consent.Rights.Contains("balances"))).ToArray()),
            })));

        group.MapGet("/{accountId}", (string accountId, HttpContext http, MockState state, bool? withBalance) =>
            Authorize(http, state, right: null, (consent, _) => Find(state, accountId) is { } a
                ? Results.Json(new JsonObject { ["account"] = AccountJson(a, withBalance == true && consent.Rights.Contains("balances")) })
                : Problems.Xs2a(403, "RESOURCE_UNKNOWN", $"Account '{accountId}' is not covered by the consent")));

        group.MapGet("/{accountId}/balances", (string accountId, HttpContext http, MockState state) =>
            Authorize(http, state, right: "balances", (_, _) => Find(state, accountId) is { } a
                ? Results.Json(new JsonObject
                {
                    ["account"] = new JsonObject { ["iban"] = a.Iban, ["currency"] = a.Currency },
                    ["balances"] = Balances(a),
                })
                : Problems.Xs2a(403, "RESOURCE_UNKNOWN", $"Account '{accountId}' is not covered by the consent")));

        group.MapGet("/{accountId}/transactions", (string accountId, HttpContext http, MockState state, string? bookingStatus, string? dateFrom, string? dateTo) =>
            Authorize(http, state, right: "transactions", (_, _) =>
            {
                if (bookingStatus is not ("booked" or "pending" or "both"))
                    return Problems.FormatError("Query parameter 'bookingStatus' must be booked, pending or both");
                if (Find(state, accountId) is not { } a)
                    return Problems.Xs2a(403, "RESOURCE_UNKNOWN", $"Account '{accountId}' is not covered by the consent");

                return Results.Json(new JsonObject
                {
                    ["account"] = new JsonObject { ["iban"] = a.Iban, ["currency"] = a.Currency },
                    ["transactions"] = Transactions(a, bookingStatus),
                });
            }));
    }

    /// <summary>Common AIS checks: Consent-ID header, token for that consent, consent valid, right granted.</summary>
    private static IResult Authorize(HttpContext http, MockState state, string? right, Func<Consent, AccessToken, IResult> handler)
    {
        var consentId = http.Request.Headers["Consent-ID"].ToString();
        if (consentId.Length == 0)
            return Problems.FormatError("Consent-ID header is required");

        var token = Tokens.FromHeader(http, state);
        if (token is null)
            return Problems.TokenUnknown();
        if (token.ConsentId != consentId)
            return Problems.Xs2a(401, "TOKEN_INVALID", "The access token was not issued for this consent");

        if (!ConsentEndpoints.TryGetOwnConsent(http, state, consentId, out var consent))
            return Problems.ConsentUnknown();
        if (consent.Status != "valid")
            return Problems.Xs2a(401, "CONSENT_INVALID", $"Consent is '{consent.Status}'");
        if (right is not null && consent.ConsentType != "aspspManaged" && !consent.Rights.Contains(right))
            return Problems.Xs2a(401, "CONSENT_INVALID", $"The consent does not grant '{right}'");

        consent.LastActionDate = DateOnly.FromDateTime(DateTime.UtcNow);
        return handler(consent, token);
    }

    private static MockAccount? Find(MockState state, string id) => state.Accounts.FirstOrDefault(a => a.ResourceId == id);

    private static JsonObject AccountJson(MockAccount a, bool withBalance)
    {
        var json = new JsonObject
        {
            ["resourceId"] = a.ResourceId,
            ["iban"] = a.Iban,
            ["currency"] = a.Currency,
            ["name"] = a.Name,
            ["product"] = a.Product,
            ["cashAccountType"] = "CACC",
            ["status"] = "enabled",
            ["_links"] = new JsonObject
            {
                ["balances"] = ConsentEndpoints.Link($"/v2/accounts/{a.ResourceId}/balances"),
                ["transactions"] = ConsentEndpoints.Link($"/v2/accounts/{a.ResourceId}/transactions"),
            },
        };

        if (withBalance)
            json["balances"] = Balances(a);

        return json;
    }

    private static JsonArray Balances(MockAccount a)
    {
        var today = DateTime.UtcNow.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        return
        [
            new JsonObject
            {
                ["balanceAmount"] = Amount(a.Currency, a.Balance),
                ["balanceType"] = "interimAvailable",
                ["referenceDate"] = today,
            },
            new JsonObject
            {
                ["balanceAmount"] = Amount(a.Currency, a.Balance + 100m),
                ["balanceType"] = "closingBooked",
                ["referenceDate"] = today,
            },
        ];
    }

    private static JsonObject Transactions(MockAccount a, string bookingStatus)
    {
        var result = new JsonObject();
        if (bookingStatus is "booked" or "both")
        {
            result["booked"] = new JsonArray(Enumerable.Range(1, 3).Select(i => (JsonNode)new JsonObject
            {
                ["transactionId"] = $"{a.ResourceId[..8]}-B{i}",
                ["bookingDate"] = DateTime.UtcNow.AddDays(-i * 3).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                ["valueDate"] = DateTime.UtcNow.AddDays(-i * 3).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                ["transactionAmount"] = Amount(a.Currency, -12.5m * i),
                ["creditorName"] = $"Shop {i}",
                ["remittanceInformationUnstructured"] = $"Card payment {i}",
            }).ToArray());
        }

        if (bookingStatus is "pending" or "both")
        {
            result["pending"] = new JsonArray(new JsonObject
            {
                ["transactionId"] = $"{a.ResourceId[..8]}-P1",
                ["transactionAmount"] = Amount(a.Currency, -5m),
                ["creditorName"] = "Coffee",
            });
        }

        result["_links"] = new JsonObject { ["account"] = ConsentEndpoints.Link($"/v2/accounts/{a.ResourceId}") };
        return result;
    }

    private static JsonObject Amount(string currency, decimal amount) => new()
    {
        ["currency"] = currency,
        ["amount"] = amount.ToString("0.00", CultureInfo.InvariantCulture),
    };
}
