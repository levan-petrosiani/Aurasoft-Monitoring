using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text.Json.Nodes;

namespace ApiTestTool.MockBank;

public sealed class Consent
{
    public required string Id { get; init; }
    public required string TppId { get; init; }
    public required string ConsentType { get; init; }
    public required JsonObject Access { get; init; }
    public required bool RecurringIndicator { get; init; }
    public required DateOnly ValidTo { get; init; }
    public required int FrequencyPerDay { get; init; }
    public string? RedirectUri { get; init; }
    public string Status { get; set; } = "received";
    public DateTime CreatedUtc { get; init; } = DateTime.UtcNow;
    public DateOnly LastActionDate { get; set; } = DateOnly.FromDateTime(DateTime.UtcNow);

    /// <summary>All rights granted anywhere in the access object (payments, cards, savings, ...).</summary>
    public HashSet<string> Rights => Access
        .Select(p => p.Value).OfType<JsonArray>()
        .SelectMany(list => list.OfType<JsonObject>())
        .SelectMany(entry => entry["rights"] as JsonArray ?? [])
        .Select(r => r?.GetValue<string>() ?? string.Empty)
        .ToHashSet(StringComparer.Ordinal);
}

public sealed class Authorisation
{
    public required string Id { get; init; }
    public required string ConsentId { get; init; }
    public string ScaStatus { get; set; } = "received";
}

public sealed record AuthorizationCode(string Code, string ClientId, string RedirectUri, string Scope, string ConsentId, string CodeChallenge, DateTime ExpiresUtc)
{
    public bool Used { get; set; }
}

public sealed record AccessToken(string Token, string ClientId, string ConsentId, string Scope, DateTime ExpiresUtc);

public sealed record RefreshToken(string Token, string ClientId, string ConsentId, string Scope);

public sealed record MockAccount(string ResourceId, string Iban, string Currency, string Name, string Product, decimal Balance);

/// <summary>Everything the mock bank remembers, in memory. Restarting MockBank starts from scratch.</summary>
public sealed class MockState
{
    public ConcurrentDictionary<string, Consent> Consents { get; } = new();
    public ConcurrentDictionary<string, Authorisation> Authorisations { get; } = new();
    public ConcurrentDictionary<string, AuthorizationCode> Codes { get; } = new();
    public ConcurrentDictionary<string, AccessToken> AccessTokens { get; } = new();
    public ConcurrentDictionary<string, RefreshToken> RefreshTokens { get; } = new();

    /// <summary>The sandbox PSU's accounts (every consent sees the same test customer).</summary>
    public IReadOnlyList<MockAccount> Accounts { get; } =
    [
        new("3dc3d5b3-7023-4848-9853-f5400a64e80f", "GE29MK0000000000000001", "GEL", "Main account", "Current account", 1540.25m),
        new("3dc3d5b3-7023-4848-9853-f5400a64e81f", "GE02MK0000000000000002", "USD", "Savings in USD", "Current account", 820.00m),
    ];

    public static string NewToken() => Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();

    public void RevokeTokensFor(string consentId)
    {
        foreach (var t in AccessTokens.Values.Where(t => t.ConsentId == consentId))
            AccessTokens.TryRemove(t.Token, out _);
        foreach (var t in RefreshTokens.Values.Where(t => t.ConsentId == consentId))
            RefreshTokens.TryRemove(t.Token, out _);
    }
}
