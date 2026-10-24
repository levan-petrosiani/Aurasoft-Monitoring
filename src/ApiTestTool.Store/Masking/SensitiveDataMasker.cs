using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Web;

namespace ApiTestTool.Store.Masking;

/// <summary>
/// Removes secrets and personal data before anything is stored or sent to a model: auth headers, OAuth codes and
/// tokens (JSON, form and query string), PSU identifiers and, optionally, IBANs. The engine already masks the
/// Authorization header and token responses in reports; this is the second, stricter layer for the store and the AI.
/// </summary>
public sealed partial class SensitiveDataMasker(MaskingOptions? options = null)
{
    private const string Marker = "(masked)";

    private static readonly HashSet<string> s_headers = new(StringComparer.OrdinalIgnoreCase)
    {
        "Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie", "PSU-ID", "PSU-Corporate-ID",
    };

    private static readonly HashSet<string> s_keys = new(StringComparer.OrdinalIgnoreCase)
    {
        "access_token", "refresh_token", "id_token", "code", "code_verifier", "client_secret", "client_assertion", "password",
    };

    private readonly MaskingOptions _options = options ?? new MaskingOptions();

    public static SensitiveDataMasker Default { get; } = new();

    public string MaskHeader(string name, string value) =>
        s_headers.Contains(name) || _options.ExtraHeaders.Contains(name, StringComparer.OrdinalIgnoreCase) ? MaskValue(value) : value;

    public List<KeyValuePair<string, string>> MaskHeaders(IEnumerable<KeyValuePair<string, string>> headers) =>
        [.. headers.Select(h => KeyValuePair.Create(h.Key, MaskHeader(h.Key, h.Value)))];

    /// <summary>Masks secrets in a query string (e.g. the OAuth callback <c>?code=...</c>).</summary>
    public string MaskUrl(string url)
    {
        var q = url.IndexOf('?');
        return q < 0 ? MaskIbans(url) : MaskIbans(url[..q]) + "?" + MaskForm(url[(q + 1)..]);
    }

    /// <summary>Masks a request/response body: JSON, form-urlencoded or plain text.</summary>
    public string? MaskBody(string? body)
    {
        if (string.IsNullOrEmpty(body))
            return body;

        var trimmed = body.TrimStart();
        if (trimmed.StartsWith('{') || trimmed.StartsWith('['))
        {
            try
            {
                var node = JsonNode.Parse(body);
                if (node is not null)
                {
                    MaskNode(node);
                    return node.ToJsonString(s_json);
                }
            }
            catch (JsonException)
            {
                // not JSON after all: fall through to text masking
            }
        }

        return LooksLikeForm(body) ? MaskForm(body) : MaskIbans(body);
    }

    /// <summary>Cuts text to <paramref name="maxChars"/>; returns whether it was cut.</summary>
    public static (string? Text, bool Truncated) Cap(string? text, int maxChars) =>
        text is null || text.Length <= maxChars ? (text, false) : (text[..maxChars] + "…(truncated)", true);

    private static readonly JsonSerializerOptions s_json = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private void MaskNode(JsonNode node)
    {
        switch (node)
        {
            case JsonObject obj:
                foreach (var (key, value) in obj.ToList())
                {
                    if (value is JsonValue v && v.TryGetValue<string>(out var s) && IsSecretKey(key))
                        obj[key] = MaskValue(s);
                    else if (value is JsonValue sv && sv.TryGetValue<string>(out var text) && _options.MaskIbans)
                        obj[key] = MaskIbans(text);
                    else if (value is not null)
                        MaskNode(value);
                }
                break;

            case JsonArray array:
                for (var i = 0; i < array.Count; i++)
                {
                    if (array[i] is JsonValue v && v.TryGetValue<string>(out var s) && _options.MaskIbans)
                        array[i] = MaskIbans(s);
                    else if (array[i] is { } child)
                        MaskNode(child);
                }
                break;
        }
    }

    private string MaskForm(string form)
    {
        var parts = form.Split('&');
        for (var i = 0; i < parts.Length; i++)
        {
            var eq = parts[i].IndexOf('=');
            if (eq <= 0)
                continue;

            var key = HttpUtility.UrlDecode(parts[i][..eq]);
            if (IsSecretKey(key))
                parts[i] = parts[i][..(eq + 1)] + Uri.EscapeDataString(MaskValue(HttpUtility.UrlDecode(parts[i][(eq + 1)..])));
        }

        return MaskIbans(string.Join('&', parts));
    }

    private bool IsSecretKey(string key) => s_keys.Contains(key) || _options.ExtraKeys.Contains(key, StringComparer.OrdinalIgnoreCase);

    private string MaskIbans(string text) =>
        _options.MaskIbans ? IbanPattern().Replace(text, m => m.Value[..4] + new string('*', m.Value.Length - 8) + m.Value[^4..]) : text;

    /// <summary>
    /// Hides the whole value; only the auth scheme is kept. Values the engine already shortened in reports
    /// (prefix + "…(masked)") are masked fully as well.
    /// </summary>
    internal static string MaskValue(string value)
    {
        var space = value.IndexOf(' ');
        return space is > 0 and < 10 && value[..space] is "Bearer" or "Basic" or "bearer" or "basic"
            ? value[..space] + " ***" + Marker
            : "***" + Marker;
    }

    private static bool LooksLikeForm(string body) => !body.Contains(' ') && body.Contains('=') && !body.Contains('\n');

    /// <summary>IBAN: 2 letters, 2 check digits, 11-30 alphanumerics (Georgian IBANs are 22 characters).</summary>
    [GeneratedRegex(@"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")]
    private static partial Regex IbanPattern();
}

public sealed class MaskingOptions
{
    /// <summary>Replace the middle of IBANs with '*' (keeps country, check digits and the last 4 characters).</summary>
    public bool MaskIbans { get; set; } = true;

    /// <summary>Additional header names whose values are masked.</summary>
    public List<string> ExtraHeaders { get; set; } = [];

    /// <summary>Additional JSON/form keys whose values are masked (any depth).</summary>
    public List<string> ExtraKeys { get; set; } = [];
}
