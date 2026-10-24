namespace ApiTestTool.MockBank;

public sealed class MockBankOptions
{
    public const string Section = "MockBank";

    /// <summary>HTTPS port for XS2A, discovery and the token endpoint (mutual TLS).</summary>
    public int HttpsPort { get; set; } = 7443;

    /// <summary>Plain HTTP port for the browser-facing pages: the OAuth authorize page and the dashboard.</summary>
    public int HttpPort { get; set; } = 7080;

    /// <summary>
    /// When the OAuth authorize request is approved without showing the login page:
    /// <c>always</c>, <c>headless</c> (only for ApiTestTool's --sca-auto, real browsers see the page) or <c>never</c>.
    /// </summary>
    public string AutoApprove { get; set; } = "headless";

    /// <summary>Reject XS2A requests without a valid X-Jws-Signature.</summary>
    public bool VerifySignatures { get; set; } = true;

    /// <summary>Sign XS2A responses (X-Jws-Signature + Digest) with the bank's generated QSealC.</summary>
    public bool SignResponses { get; set; } = true;

    /// <summary>The bank's own PSD2 id, used in its generated certificates.</summary>
    public string BankOrganizationId { get; set; } = "PSDGE-NBG-MOCKBANK";

    /// <summary>Highest accepted frequencyPerDay, like AspspSettings.MaxFrequencyPerDay.</summary>
    public int MaxFrequencyPerDay { get; set; } = 4;

    public int AccessTokenLifetimeSeconds { get; set; } = 900;

    public string HttpsBaseUrl => $"https://localhost:{HttpsPort}";

    public string HttpBaseUrl => $"http://localhost:{HttpPort}";
}
