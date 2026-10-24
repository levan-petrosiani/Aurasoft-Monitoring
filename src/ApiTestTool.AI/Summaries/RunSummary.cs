using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace ApiTestTool.AI.Summaries;

/// <summary>The triage of one run, as the model returns it and after <see cref="Validate"/>.</summary>
public sealed partial class RunSummary
{
    public static readonly string[] HealthValues = ["healthy", "degraded", "broken", "inconclusive"];
    public static readonly string[] SideValues = ["bank", "our-side", "environment", "unclear"];
    public static readonly string[] SeverityValues = ["Critical", "Major", "Minor", "Info"];

    internal static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = false,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    /// <summary>One sentence a tester can read in the run list.</summary>
    public string Headline { get; set; } = "";

    /// <summary>healthy | degraded | broken | inconclusive.</summary>
    public string Health { get; set; } = "inconclusive";

    /// <summary>Problems grouped by root cause.</summary>
    public List<Finding> Findings { get; set; } = [];

    /// <summary>What changed since the bank's previous run. Set in code by <see cref="Validate"/>, not by the model.</summary>
    public string Comparison { get; set; } = "";

    /// <summary>Warnings in passed scenarios worth a look.</summary>
    public List<string> Attention { get; set; } = [];

    public sealed class Finding
    {
        public string Title { get; set; } = "";

        /// <summary>bank | our-side | environment | unclear.</summary>
        public string Side { get; set; } = "unclear";

        public string Severity { get; set; } = "Major";

        public List<string> Scenarios { get; set; } = [];

        public string Explanation { get; set; } = "";

        /// <summary>A short quote from the failures or response that shows the problem.</summary>
        public string Evidence { get; set; } = "";

        /// <summary>Clause reference, only if it appears in the run data; empty otherwise.</summary>
        public string SpecRef { get; set; } = "";

        public string NextStep { get; set; } = "";
    }

    /// <summary>JSON schema given to providers that support structured output, and quoted in the prompt.</summary>
    public const string JsonSchema = """
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["headline", "health", "findings", "attention"],
          "properties": {
            "headline": { "type": "string" },
            "health": { "type": "string", "enum": ["healthy", "degraded", "broken", "inconclusive"] },
            "findings": {
              "type": "array",
              "items": {
                "type": "object",
                "additionalProperties": false,
                "required": ["title", "side", "severity", "scenarios", "explanation", "evidence", "specRef", "nextStep"],
                "properties": {
                  "title": { "type": "string" },
                  "side": { "type": "string", "enum": ["bank", "our-side", "environment", "unclear"] },
                  "severity": { "type": "string", "enum": ["Critical", "Major", "Minor", "Info"] },
                  "scenarios": { "type": "array", "items": { "type": "string" } },
                  "explanation": { "type": "string" },
                  "evidence": { "type": "string" },
                  "specRef": { "type": "string" },
                  "nextStep": { "type": "string" }
                }
              }
            },
            "attention": { "type": "array", "items": { "type": "string" } }
          }
        }
        """;

    /// <summary>
    /// Reads the model's answer: tolerates code fences, a reasoning preamble or <c>&lt;think&gt;</c> blocks around the JSON.
    /// </summary>
    public static RunSummary Parse(string text)
    {
        var cleaned = ThinkBlock().Replace(text, "");
        var start = cleaned.IndexOf('{');
        var end = cleaned.LastIndexOf('}');
        if (start < 0 || end <= start)
            throw new JsonException("The answer contains no JSON object");

        return JsonSerializer.Deserialize<RunSummary>(cleaned[start..(end + 1)], Json) ?? throw new JsonException("The JSON object is empty");
    }

    /// <summary>
    /// Checks the summary against the run in code and fixes what can be fixed: unknown scenario ids are dropped,
    /// clause references not in the run are cleared, enum values normalised. Returns what was changed or is missing.
    /// </summary>
    public List<string> Validate(RunDigest digest)
    {
        var notes = new List<string>();

        Health = Normalize(Health, HealthValues, "inconclusive", "health", notes);
        if (string.IsNullOrWhiteSpace(Headline))
            notes.Add("The summary has no headline.");

        foreach (var finding in Findings)
        {
            finding.Side = Normalize(finding.Side, SideValues, "unclear", $"side of \"{finding.Title}\"", notes);
            finding.Severity = Normalize(finding.Severity, SeverityValues, "Major", $"severity of \"{finding.Title}\"", notes);

            var unknown = finding.Scenarios.Where(id => !digest.ScenarioIds.Contains(id)).ToList();
            foreach (var id in unknown)
                notes.Add($"Removed scenario \"{id}\" from \"{finding.Title}\": it is not in this run.");
            finding.Scenarios = [.. finding.Scenarios.Except(unknown).Distinct(StringComparer.OrdinalIgnoreCase)];

            if (finding.SpecRef.Length > 0)
            {
                if (MatchSpecRef(finding.SpecRef, digest.SpecRefs) is { } canonical)
                {
                    finding.SpecRef = canonical; // models shorten references; store the run's exact wording
                }
                else
                {
                    notes.Add($"Cleared clause \"{finding.SpecRef}\" in \"{finding.Title}\": no scenario in this run references it.");
                    finding.SpecRef = "";
                }
            }
        }

        // Findings are for problems. One that only names passed/skipped scenarios is a warning: move it to Attention.
        foreach (var finding in Findings.Where(f => f.Scenarios.Count > 0 && f.Scenarios.All(id => digest.Outcomes.GetValueOrDefault(id) is "Passed" or "Skipped")).ToList())
        {
            Findings.Remove(finding);
            Attention.Add($"{string.Join(", ", finding.Scenarios)}: {finding.Title}");
            notes.Add($"Moved \"{finding.Title}\" to warnings: its scenarios passed.");
        }

        Comparison = digest.Comparison;

        var explained = Findings.SelectMany(f => f.Scenarios).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var unexplained = digest.ProblemScenarioIds.Where(id => !explained.Contains(id)).ToList();
        if (unexplained.Count > 0)
            notes.Add($"Not explained by any finding: {string.Join(", ", unexplained)}.");

        if (digest.ProblemScenarioIds.Count == 0 && Health == "broken")
            notes.Add("Health \"broken\" although no scenario failed or errored.");

        return notes;
    }

    public string ToJson() => JsonSerializer.Serialize(this, Json);

    /// <summary>Plain-text rendering for the CLI.</summary>
    public string ToText()
    {
        var sb = new StringBuilder();
        sb.AppendLine($"[{Health.ToUpperInvariant()}] {Headline}");
        foreach (var (f, i) in Findings.Select((f, i) => (f, i + 1)))
        {
            sb.AppendLine();
            sb.AppendLine($"{i}. {f.Title}  ({f.Severity}, {f.Side}){(f.SpecRef.Length > 0 ? " · " + f.SpecRef : "")}");
            if (f.Scenarios.Count > 0)
                sb.AppendLine($"   scenarios: {string.Join(", ", f.Scenarios)}");
            sb.AppendLine($"   {f.Explanation}");
            if (f.Evidence.Length > 0)
                sb.AppendLine($"   evidence: {f.Evidence}");
            if (f.NextStep.Length > 0)
                sb.AppendLine($"   next: {f.NextStep}");
        }

        if (Comparison.Length > 0)
        {
            sb.AppendLine();
            sb.AppendLine("Since the previous run: " + Comparison);
        }

        if (Attention.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("Worth a look:");
            foreach (var a in Attention)
                sb.AppendLine("  - " + a);
        }

        return sb.ToString();
    }

    /// <summary>
    /// The run's reference the cited one refers to: same text, or every clause number cited (e.g. 6.3.1.2, 14.11.1)
    /// appears in it. Null when the citation is not backed by the run.
    /// </summary>
    internal static string? MatchSpecRef(string cited, IEnumerable<string> runRefs)
    {
        var refs = runRefs.ToList();
        var exact = refs.FirstOrDefault(r => r.Contains(cited, StringComparison.OrdinalIgnoreCase) || cited.Contains(r, StringComparison.OrdinalIgnoreCase));
        if (exact is not null)
            return exact;

        var numbers = ClauseNumber().Matches(cited).Select(m => m.Value).ToList();
        if (numbers.Count == 0)
            return null;

        return refs.FirstOrDefault(r =>
        {
            var inRef = ClauseNumber().Matches(r).Select(m => m.Value).ToHashSet();
            return numbers.All(inRef.Contains);
        });
    }

    private static string Normalize(string value, string[] allowed, string fallback, string what, List<string> notes)
    {
        var match = allowed.FirstOrDefault(a => a.Equals(value?.Trim(), StringComparison.OrdinalIgnoreCase));
        if (match is not null)
            return match;

        notes.Add($"Unknown {what} \"{value}\" replaced by \"{fallback}\".");
        return fallback;
    }

    [GeneratedRegex(@"<think>.*?</think>", RegexOptions.Singleline | RegexOptions.IgnoreCase)]
    private static partial Regex ThinkBlock();

    /// <summary>Section numbers with at least one dot: 6.3.1.2, 14.11.1 (not page numbers or versions like v2).</summary>
    [GeneratedRegex(@"\b\d+(?:\.\d+)+\b")]
    private static partial Regex ClauseNumber();
}
