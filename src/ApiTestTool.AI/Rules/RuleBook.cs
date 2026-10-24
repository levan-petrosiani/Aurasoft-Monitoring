using System.Text;
using System.Text.Json;
using ApiTestTool.Standards;
using ApiTestTool.Store;
using ApiTestTool.Store.Entities;

namespace ApiTestTool.AI.Rules;

/// <summary>Fields a reviewer may change before approving.</summary>
public sealed record RuleEdit(string? Title = null, string? Level = null, string? Service = null, string? Requirement = null, string? Quote = null, string? QuoteEnglish = null, string? Operation = null, string? CheckJson = null);

/// <summary>
/// The human side of rules: approve, reject, edit, reopen, retire. Only these actions change a rule's status; each one
/// needs a reviewer name and is written to <c>rule_reviews</c> with the fields before and after. An edited quote is
/// checked against the clause again, so approval can never rest on words the standard does not contain.
/// </summary>
public sealed class RuleBook(AqtStore store)
{
    public Task<RuleEntity> ApproveAsync(string id, string reviewer, string? note = null, CancellationToken cancellationToken = default) =>
        ChangeAsync(id, reviewer, "approved", note, r =>
        {
            Require(r.Status == "draft", $"Only drafts can be approved; {r.Id} is {r.Status}.");
            r.Status = "approved";
        }, cancellationToken);

    public Task<RuleEntity> RejectAsync(string id, string reviewer, string note, CancellationToken cancellationToken = default) =>
        ChangeAsync(id, reviewer, "rejected", note, r =>
        {
            Require(!string.IsNullOrWhiteSpace(note), "Say why the rule is rejected (it helps the next draft).");
            Require(r.Status is "draft" or "approved", $"{r.Id} is {r.Status}; only drafts and approved rules can be rejected.");
            r.Status = "rejected";
        }, cancellationToken);

    public Task<RuleEntity> ReopenAsync(string id, string reviewer, string? note = null, CancellationToken cancellationToken = default) =>
        ChangeAsync(id, reviewer, "reopened", note, r =>
        {
            Require(r.Status is "approved" or "rejected", $"{r.Id} is {r.Status}; only approved or rejected rules can be reopened.");
            r.Status = "draft";
        }, cancellationToken);

    public Task<RuleEntity> RetireAsync(string id, string reviewer, string note, CancellationToken cancellationToken = default) =>
        ChangeAsync(id, reviewer, "retired", note, r =>
        {
            Require(r.Status == "approved", $"Only approved rules can be retired; {r.Id} is {r.Status}.");
            r.Status = "retired";
        }, cancellationToken);

    /// <summary>Edits a draft. Approved rules must be reopened first, so a change is always reviewed again.</summary>
    public async Task<RuleEntity> EditAsync(string id, string reviewer, RuleEdit edit, string? note = null, CancellationToken cancellationToken = default)
    {
        ClauseEntity? clause = null;
        if (edit.Quote is not null)
        {
            var rule = await store.GetRuleAsync(id, cancellationToken) ?? throw new InvalidOperationException($"No rule {id}");
            clause = await store.GetClauseAsync(rule.ClauseKey, cancellationToken) ?? throw new InvalidOperationException($"Clause {rule.ClauseKey} is not in the store");
        }

        return await ChangeAsync(id, reviewer, "edited", note, r =>
        {
            Require(r.Status is "draft" or "invalid", $"{r.Id} is {r.Status}; reopen it before editing.");
            if (edit.Quote is not null && QuoteVerifier.Check(edit.Quote, clause!.Text) is { } problem)
                throw new InvalidOperationException("The edited quote is not in the clause: " + problem);

            r.Title = edit.Title?.Trim() ?? r.Title;
            r.Level = edit.Level is null ? r.Level : Normalize(edit.Level, ["MUST", "SHOULD", "MAY"], "level");
            r.Service = edit.Service is null ? r.Service : Normalize(edit.Service, ["AIS", "PIS", "COMMON"], "service");
            r.Requirement = edit.Requirement?.Trim() ?? r.Requirement;
            r.Quote = edit.Quote?.Trim() ?? r.Quote;
            r.QuoteEnglish = edit.QuoteEnglish ?? r.QuoteEnglish;
            r.Operation = edit.Operation ?? r.Operation;
            r.CheckJson = edit.CheckJson ?? r.CheckJson;
            r.Embedding = edit.Requirement is null ? r.Embedding : null; // stale after a requirement change

            if (r.Status == "invalid" && edit.Quote is not null)
            {
                // A fixed quote makes an invalid draft reviewable.
                r.Status = "draft";
                r.CheckNotes = [.. r.CheckNotes.Where(n => !n.StartsWith("Quote check failed", StringComparison.Ordinal)), "Quote fixed by the reviewer and found in the clause."];
            }
        }, cancellationToken);
    }

    private async Task<RuleEntity> ChangeAsync(string id, string reviewer, string action, string? note, Action<RuleEntity> change, CancellationToken cancellationToken)
    {
        Require(!string.IsNullOrWhiteSpace(reviewer), "A reviewer name is required.");
        var rule = await store.GetRuleAsync(id, cancellationToken) ?? throw new InvalidOperationException($"No rule {id}");
        var expected = rule.UpdatedUtc;
        var before = JsonSerializer.Serialize(RuleSnapshot.Of(rule), RuleDraftAnswer.Json);

        change(rule);

        var now = DateTime.UtcNow;
        rule.UpdatedUtc = now;
        if (action is "approved" or "rejected" or "retired")
        {
            rule.ReviewedBy = reviewer.Trim();
            rule.ReviewedUtc = now;
            rule.ReviewNote = note;
        }

        await store.UpdateRuleAsync(rule, expected, new RuleReviewEntity
        {
            RuleId = id,
            TimeUtc = now,
            Reviewer = reviewer.Trim(),
            Action = action,
            Note = note,
            Before = before,
            After = JsonSerializer.Serialize(RuleSnapshot.Of(rule), RuleDraftAnswer.Json),
        }, cancellationToken);
        return rule;
    }

    /// <summary>
    /// Writes approved rules to <c>rules/&lt;prefix&gt;/&lt;id&gt;.yaml</c> (and removes files of rules that are no longer
    /// approved), so the approved set is versioned in git next to the scenarios that will reference it.
    /// </summary>
    public async Task<(int Written, int Removed)> ExportAsync(string rulesDirectory, CancellationToken cancellationToken = default)
    {
        var approved = await store.ListRulesAsync("approved", cancellationToken: cancellationToken);
        var keep = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var rule in approved)
        {
            var dir = Path.Combine(rulesDirectory, RuleDrafter.IdPrefix(rule.Standard).ToLowerInvariant());
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, rule.Id + ".yaml");
            await File.WriteAllTextAsync(path, ToYaml(rule), new UTF8Encoding(false), cancellationToken);
            keep.Add(Path.GetFullPath(path));
        }

        var removed = 0;
        foreach (var file in Directory.Exists(rulesDirectory) ? Directory.EnumerateFiles(rulesDirectory, "*.yaml", SearchOption.AllDirectories) : [])
        {
            if (!Path.GetFileName(file).StartsWith('_') && !keep.Contains(Path.GetFullPath(file)))
            {
                File.Delete(file);
                removed++;
            }
        }

        return (approved.Count, removed);
    }

    internal static string ToYaml(RuleEntity r)
    {
        var check = r.CheckJson is null ? null : JsonSerializer.Deserialize<RuleCheck>(r.CheckJson, RuleDraftAnswer.Json);
        var sb = new StringBuilder();
        sb.AppendLine("# Exported from the rule store (aqt rules export). Edit rules in the portal, not here.");
        sb.AppendLine($"id: {r.Id}");
        sb.AppendLine($"title: {Q(r.Title)}");
        sb.AppendLine($"status: {r.Status}");
        sb.AppendLine($"level: {r.Level}");
        sb.AppendLine($"service: {r.Service}");
        sb.AppendLine($"area: {r.Area}");
        sb.AppendLine("source:");
        sb.AppendLine($"  standard: {r.Standard}");
        sb.AppendLine($"  version: {Q(r.Version)}");
        sb.AppendLine($"  clause: {Q(r.ClauseNumber)}");
        sb.AppendLine($"  quote: {Q(r.Quote)}");
        if (r.QuoteEnglish is not null)
            sb.AppendLine($"  quoteEnglish: {Q(r.QuoteEnglish)}  # AI translation");
        sb.AppendLine($"requirement: {Q(r.Requirement)}");
        if (r.Operation is not null)
            sb.AppendLine($"appliesTo:\n  operation: {Q(r.Operation)}");
        if (check is not null)
        {
            sb.AppendLine("check:");
            sb.AppendLine($"  kind: {check.Kind}");
            sb.AppendLine($"  description: {Q(check.Description)}");
            if (check.ExpectStatus.Count > 0)
                sb.AppendLine($"  expectStatus: [{string.Join(", ", check.ExpectStatus)}]");
            if (check.ExpectErrorCodes.Count > 0)
                sb.AppendLine($"  expectErrorCodes: [{string.Join(", ", check.ExpectErrorCodes)}]");
        }

        sb.AppendLine($"scenarios: [{string.Join(", ", r.Scenarios)}]");
        sb.AppendLine("drafted:");
        sb.AppendLine($"  by: {r.DraftedBy}");
        if (r.Model is not null)
            sb.AppendLine($"  model: {Q(r.Model)}");
        sb.AppendLine($"  at: {r.CreatedUtc:yyyy-MM-ddTHH:mm:ssZ}");
        sb.AppendLine("review:");
        sb.AppendLine($"  by: {Q(r.ReviewedBy ?? "")}");
        sb.AppendLine($"  at: {r.ReviewedUtc:yyyy-MM-ddTHH:mm:ssZ}");
        sb.AppendLine($"  note: {Q(r.ReviewNote ?? "")}");
        return sb.ToString();
    }

    /// <summary>YAML double-quoted scalar (JSON string syntax is valid YAML).</summary>
    private static string Q(string value) => JsonSerializer.Serialize(value, RuleDraftAnswer.Json);

    private static string Normalize(string value, string[] allowed, string what)
    {
        var v = value.Trim().ToUpperInvariant();
        return allowed.Contains(v) ? v : throw new InvalidOperationException($"Unknown {what} '{value}' (use {string.Join(", ", allowed)})");
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
