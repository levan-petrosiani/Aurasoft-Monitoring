using System.Text.Json.Nodes;

namespace ApiTestTool.Engine.OAuth;

/// <summary>
/// Bank-specific OAuth deviations, from the profile's <c>quirks.oauth</c>:
/// <code>
/// quirks:
///   oauth:
///     scopeSuffix: " openid"     # appended to the scope (TBC)
///     nonce: "1234"              # adds nonce=...
///     uiLocales: ka-ge           # adds ui_locales=...
///     extraParams: { prompt: login }
/// </code>
/// </summary>
public sealed record OAuthQuirks(string? ScopeSuffix, string? Nonce, string? UiLocales, IReadOnlyDictionary<string, string> ExtraParams)
{
    public static OAuthQuirks None { get; } = new(null, null, null, new Dictionary<string, string>());

    public static OAuthQuirks From(JsonObject? quirks)
    {
        if (quirks?["oauth"] is not JsonObject oauth)
            return None;

        var extra = new Dictionary<string, string>(StringComparer.Ordinal);
        if (oauth["extraParams"] is JsonObject extraParams)
        {
            foreach (var (key, value) in extraParams)
            {
                if (value is not null)
                    extra[key] = value.ToString();
            }
        }

        return new OAuthQuirks(Text(oauth["scopeSuffix"]), Text(oauth["nonce"]), Text(oauth["uiLocales"]), extra);

        static string? Text(JsonNode? node) => node?.GetValueKind() == System.Text.Json.JsonValueKind.String ? node.GetValue<string>() : node?.ToString();
    }
}
