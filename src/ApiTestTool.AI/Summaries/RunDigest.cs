using System.Globalization;
using System.Text;
using ApiTestTool.Store.Entities;

namespace ApiTestTool.AI.Summaries;

/// <summary>A change of one scenario's outcome compared with the bank's previous run (computed in code, not by the model).</summary>
public sealed record OutcomeChange(string ScenarioId, string? Before, string? After);

/// <summary>
/// The run as the model sees it: compact text with everything needed to explain the problems and nothing else.
/// Data comes from the store, so it is already masked. Facts the summary is later checked against are kept alongside.
/// </summary>
public sealed class RunDigest
{
    private const int MaxDetailedScenarios = 30;
    private const int MaxBodyChars = 1500;
    private const int MaxPassedWarnings = 15;

    public required string Text { get; init; }

    /// <summary>Scenario ids in the run.</summary>
    public required IReadOnlySet<string> ScenarioIds { get; init; }

    /// <summary>Scenarios that failed or errored: each should be explained by some finding.</summary>
    public required IReadOnlyList<string> ProblemScenarioIds { get; init; }

    /// <summary>Clause references present in the run (a summary may only cite these).</summary>
    public required IReadOnlySet<string> SpecRefs { get; init; }

    public required IReadOnlyList<OutcomeChange> Changes { get; init; }

    public string? PreviousRunId { get; init; }

    /// <summary>Outcome of every scenario in the run.</summary>
    public required IReadOnlyDictionary<string, string> Outcomes { get; init; }

    /// <summary>"Since the previous run" text, written in code from <see cref="Changes"/> so it is always correct.</summary>
    public required string Comparison { get; init; }

    public static RunDigest Build(RunEntity run, RunEntity? previous)
    {
        var inv = CultureInfo.InvariantCulture;
        var sb = new StringBuilder();
        var duration = run.StartedUtc is { } s && run.FinishedUtc is { } f ? $" · duration {(f - s).TotalSeconds.ToString("0", inv)} s" : "";

        sb.AppendLine($"RUN {run.Id} · bank {run.BankBic} ({run.BankName}) · XS2A {run.Xs2aUrl} · started from {run.Source} · status {run.Status}");
        sb.AppendLine($"Started {run.StartedUtc?.ToString("yyyy-MM-dd HH:mm:ss'Z'", inv)}{duration} · suites: {(run.Suites.Count == 0 ? "all" : string.Join(", ", run.Suites))}");
        sb.AppendLine($"Totals: passed {run.Passed}, failed {run.Failed}, errors {run.Errors}, blocked {run.Blocked}, skipped {run.Skipped} · pass rate {run.PassRate.ToString(inv)}%");
        if (run.Error is not null)
            sb.AppendLine($"Run-level error: {run.Error}");

        var changes = Compare(run, previous);
        sb.AppendLine();
        if (previous is null)
        {
            sb.AppendLine("CHANGES SINCE PREVIOUS RUN: there is no earlier run with results for this bank.");
        }
        else
        {
            sb.AppendLine($"CHANGES SINCE PREVIOUS RUN ({previous.Id}, {previous.CreatedUtc.ToString("yyyy-MM-dd HH:mm", inv)}Z, pass rate {previous.PassRate.ToString(inv)}%):");
            if (changes.Count == 0)
                sb.AppendLine("- none: every scenario run in both has the same outcome");
            foreach (var c in changes)
                sb.AppendLine($"- {c.ScenarioId}: {c.Before ?? "not run"} → {c.After ?? "not run"}");
        }

        var problems = run.Scenarios.Where(x => x.Outcome is "Failed" or "Error").ToList();
        var blocked = run.Scenarios.Where(x => x.Outcome == "Blocked").ToList();

        sb.AppendLine();
        sb.AppendLine(problems.Count == 0 ? "FAILED / ERROR SCENARIOS: none" : "FAILED / ERROR SCENARIOS:");
        foreach (var (scenario, index) in problems.Select((x, i) => (x, i)))
        {
            if (index >= MaxDetailedScenarios)
            {
                sb.AppendLine($"### {scenario.ScenarioId} · {scenario.Outcome} · {scenario.Reason} (details omitted: too many problems)");
                continue;
            }

            AppendScenario(sb, scenario);
        }

        if (blocked.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("BLOCKED (not executed because a critical preflight scenario failed):");
            foreach (var b in blocked)
                sb.AppendLine($"- {b.ScenarioId}: {b.Reason}");
        }

        var skipped = run.Scenarios.Where(x => x.Outcome == "Skipped").ToList();
        if (skipped.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("SKIPPED (the bank profile does not declare the feature; not a problem):");
            foreach (var group in skipped.GroupBy(x => x.Reason ?? ""))
                sb.AppendLine($"- {string.Join(", ", group.Select(x => x.ScenarioId))}: {group.Key}");
        }

        var passed = run.Scenarios.Where(x => x.Outcome == "Passed").ToList();
        sb.AppendLine();
        sb.AppendLine($"PASSED: {(passed.Count == 0 ? "none" : string.Join(", ", passed.Select(x => x.ScenarioId)))}");

        var warnings = passed
            .SelectMany(x => x.Steps.SelectMany(st => st.Warnings).Distinct().Select(w => $"{x.ScenarioId}: {w}"))
            .Distinct()
            .Take(MaxPassedWarnings)
            .ToList();
        if (warnings.Count > 0)
        {
            sb.AppendLine("WARNINGS IN PASSED SCENARIOS (deviations that did not fail the test):");
            foreach (var w in warnings)
                sb.AppendLine("- " + w);
        }

        return new RunDigest
        {
            Text = sb.ToString(),
            ScenarioIds = run.Scenarios.Select(x => x.ScenarioId).ToHashSet(StringComparer.OrdinalIgnoreCase),
            ProblemScenarioIds = [.. problems.Select(x => x.ScenarioId)],
            SpecRefs = run.Scenarios.Select(x => x.SpecRef).OfType<string>().Where(x => x.Length > 0).ToHashSet(StringComparer.OrdinalIgnoreCase),
            Changes = changes,
            PreviousRunId = previous?.Id,
            Outcomes = run.Scenarios.GroupBy(x => x.ScenarioId, StringComparer.OrdinalIgnoreCase).ToDictionary(g => g.Key, g => g.First().Outcome, StringComparer.OrdinalIgnoreCase),
            Comparison = DescribeChanges(run, previous, changes),
        };
    }

    private static void AppendScenario(StringBuilder sb, ScenarioResultEntity scenario)
    {
        sb.AppendLine($"### {scenario.ScenarioId} · suite {scenario.Suite} · severity {scenario.Severity} · {scenario.Outcome}");
        sb.AppendLine($"Title: {scenario.Title}");
        if (scenario.SpecRef is not null)
            sb.AppendLine($"Spec: {scenario.SpecRef}");
        if (scenario.Reason is not null)
            sb.AppendLine($"Reason: {scenario.Reason}");

        foreach (var step in scenario.Steps.Where(st => st.Outcome is not "Passed" || st.Warnings.Count > 0))
        {
            sb.AppendLine($"Step \"{step.Name}\" ({step.Kind}{(step.IsCleanup ? ", cleanup" : "")}) {step.Outcome}");
            foreach (var failure in step.Failures)
                sb.AppendLine("  failure: " + failure);
            foreach (var warning in step.Warnings)
                sb.AppendLine("  warning: " + warning);

            foreach (var x in step.Exchanges)
            {
                sb.AppendLine($"  {x.Method} {x.Url} → {(x.StatusCode?.ToString(CultureInfo.InvariantCulture) ?? "no response")} ({x.DurationMs} ms){(x.Error is null ? "" : " error: " + x.Error)}");
                if (step.Outcome is not "Passed" && !string.IsNullOrWhiteSpace(x.ResponseBody))
                    sb.AppendLine("  response body: " + Excerpt(x.ResponseBody));
            }
        }
    }

    internal static List<OutcomeChange> Compare(RunEntity run, RunEntity? previous)
    {
        if (previous is null)
            return [];

        var before = previous.Scenarios.GroupBy(x => x.ScenarioId).ToDictionary(g => g.Key, g => g.First().Outcome, StringComparer.OrdinalIgnoreCase);
        var after = run.Scenarios.GroupBy(x => x.ScenarioId).ToDictionary(g => g.Key, g => g.First().Outcome, StringComparer.OrdinalIgnoreCase);

        return [.. before.Keys.Union(after.Keys, StringComparer.OrdinalIgnoreCase)
            .Select(id => new OutcomeChange(id, before.GetValueOrDefault(id), after.GetValueOrDefault(id)))
            // Only scenarios run both times count as changes; a different selection is not a regression.
            .Where(c => c.Before is not null && c.After is not null && !string.Equals(c.Before, c.After, StringComparison.Ordinal))
            .OrderBy(c => c.ScenarioId, StringComparer.Ordinal)];
    }

    internal static string DescribeChanges(RunEntity run, RunEntity? previous, IReadOnlyList<OutcomeChange> changes)
    {
        if (previous is null)
            return "No earlier run with results for this bank.";

        var inv = CultureInfo.InvariantCulture;
        var rate = $"Pass rate {previous.PassRate.ToString(inv)}% → {run.PassRate.ToString(inv)}% (previous run {previous.Id}).";
        if (changes.Count == 0)
            return rate + " Every scenario run both times has the same outcome.";

        var moves = changes
            .GroupBy(c => $"{c.Before} → {c.After}")
            .OrderBy(g => g.Key, StringComparer.Ordinal)
            .Select(g => $"{g.Key}: {string.Join(", ", g.Select(c => c.ScenarioId))}");
        return rate + " " + string.Join(". ", moves) + ".";
    }

    private static string Excerpt(string body)
    {
        var oneLine = body.ReplaceLineEndings(" ");
        return oneLine.Length <= MaxBodyChars ? oneLine : oneLine[..MaxBodyChars] + "…";
    }
}
