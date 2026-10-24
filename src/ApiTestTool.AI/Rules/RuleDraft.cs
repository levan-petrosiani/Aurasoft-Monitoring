using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace ApiTestTool.AI.Rules;

/// <summary>The model's answer when asked to draft rules from one clause.</summary>
public sealed partial class RuleDraftAnswer
{
    internal static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public List<RuleDraft> Rules { get; set; } = [];

    /// <summary>What in the clause was not turned into a rule, and why (informative text, TPP-only duties, ...).</summary>
    public string Skipped { get; set; } = "";

    public const string JsonSchema = """
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["rules", "skipped"],
          "properties": {
            "rules": {
              "type": "array",
              "items": {
                "type": "object",
                "additionalProperties": false,
                "required": ["title", "level", "service", "area", "requirement", "quote", "quoteEnglish", "operation", "check", "notes"],
                "properties": {
                  "title": { "type": "string" },
                  "level": { "type": "string", "enum": ["MUST", "SHOULD", "MAY"] },
                  "service": { "type": "string", "enum": ["AIS", "PIS", "COMMON"] },
                  "area": { "type": "string", "enum": ["CON", "ACC", "TRX", "PIS", "PST", "PCN", "SBK", "AUTH", "SIG", "TLS", "ERR", "HDR", "GEN"] },
                  "requirement": { "type": "string" },
                  "quote": { "type": "string" },
                  "quoteEnglish": { "type": "string" },
                  "operation": { "type": "string" },
                  "check": {
                    "type": "object",
                    "additionalProperties": false,
                    "required": ["kind", "description", "expectStatus", "expectErrorCodes"],
                    "properties": {
                      "kind": { "type": "string", "enum": ["negative", "positive", "header", "signature", "flow", "manual"] },
                      "description": { "type": "string" },
                      "expectStatus": { "type": "array", "items": { "type": "integer" } },
                      "expectErrorCodes": { "type": "array", "items": { "type": "string" } }
                    }
                  },
                  "notes": { "type": "string" }
                }
              }
            },
            "skipped": { "type": "string" }
          }
        }
        """;

    public static RuleDraftAnswer Parse(string text)
    {
        var cleaned = ThinkBlock().Replace(text, "");
        var start = cleaned.IndexOf('{');
        var end = cleaned.LastIndexOf('}');
        if (start < 0 || end <= start)
            throw new JsonException("The answer contains no JSON object");

        return JsonSerializer.Deserialize<RuleDraftAnswer>(cleaned[start..(end + 1)], Json) ?? throw new JsonException("The JSON object is empty");
    }

    [GeneratedRegex(@"<think>.*?</think>", RegexOptions.Singleline | RegexOptions.IgnoreCase)]
    private static partial Regex ThinkBlock();
}

public sealed class RuleDraft
{
    public string Title { get; set; } = "";

    public string Level { get; set; } = "MUST";

    public string Service { get; set; } = "COMMON";

    public string Area { get; set; } = "GEN";

    public string Requirement { get; set; } = "";

    public string Quote { get; set; } = "";

    public string QuoteEnglish { get; set; } = "";

    public string Operation { get; set; } = "";

    public RuleCheck Check { get; set; } = new();

    public string Notes { get; set; } = "";
}

/// <summary>How a rule could be verified by a test acting as a TPP.</summary>
public sealed class RuleCheck
{
    /// <summary>negative | positive | header | signature | flow | manual (not observable by an automated TPP test).</summary>
    public string Kind { get; set; } = "manual";

    public string Description { get; set; } = "";

    public List<int> ExpectStatus { get; set; } = [];

    public List<string> ExpectErrorCodes { get; set; } = [];
}
