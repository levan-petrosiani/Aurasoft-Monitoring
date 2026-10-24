using System.Diagnostics;
using System.Reflection;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Scenarios;

namespace ApiTestTool.Engine.Running;

public sealed class ScenarioRunner
{
    private readonly RunContext _ctx;

    public ScenarioRunner(RunContext ctx)
    {
        _ctx = ctx;
    }

    /// <summary>Raised before each scenario runs, for progress output.</summary>
    public event Action<Scenario>? ScenarioStarted;

    /// <summary>Raised after each scenario finishes, for progress output.</summary>
    public event Action<ScenarioResult>? ScenarioCompleted;

    public static string ToolVersion => typeof(ScenarioRunner).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0.0.0";

    /// <summary>
    /// When true (default), a failed Critical preflight scenario blocks all non-preflight scenarios:
    /// if TLS or signing does not work, every other result would be the same failure repeated.
    /// </summary>
    public bool PreflightGate { get; init; } = true;

    public const string PreflightSuite = "preflight";

    public async Task<RunResult> RunAsync(IEnumerable<Scenario> scenarios, CancellationToken cancellationToken = default)
    {
        // Preflight first, then the rest in their given order.
        var ordered = scenarios
            .OrderBy(s => s.Suite.Equals(PreflightSuite, StringComparison.OrdinalIgnoreCase) ? 0 : 1)
            .ToList();
        string? gateReason = null;

        var run = new RunResult
        {
            ToolVersion = ToolVersion,
            BankBic = _ctx.Profile.Bank.Bic,
            BankName = _ctx.Profile.Bank.Name,
            Xs2aUrl = _ctx.Profile.Endpoints.Xs2a,
            TppId = _ctx.TppId,
            StartedAtUtc = _ctx.UtcNow(),
        };

        foreach (var scenario in ordered)
        {
            cancellationToken.ThrowIfCancellationRequested();

            var isPreflight = scenario.Suite.Equals(PreflightSuite, StringComparison.OrdinalIgnoreCase);
            ScenarioStarted?.Invoke(scenario);
            var result = gateReason is not null && !isPreflight
                ? Blocked(scenario, gateReason)
                : await RunScenarioAsync(scenario, cancellationToken).ConfigureAwait(false);

            if (PreflightGate && isPreflight && scenario.Severity == "Critical" && result.Outcome is Outcome.Failed or Outcome.Error)
                gateReason ??= $"Preflight {scenario.Id} failed";

            run.Scenarios.Add(result);
            ScenarioCompleted?.Invoke(result);
        }

        run.FinishedAtUtc = _ctx.UtcNow();
        return run;
    }

    public async Task<ScenarioResult> RunScenarioAsync(Scenario scenario, CancellationToken cancellationToken = default)
    {
        var result = new ScenarioResult
        {
            Id = scenario.Id,
            Title = scenario.Title,
            Suite = scenario.Suite,
            Severity = scenario.Severity,
            SpecRef = scenario.SpecRef,
            Origin = scenario.Origin,
            Source = scenario.SourcePath,
        };

        var missing = scenario.Requires?.FirstOrDefault(path => !IsSatisfied(path));
        if (missing is not null)
        {
            result.Outcome = Outcome.Skipped;
            result.Reason = $"Profile does not provide '{missing}'";
            return result;
        }

        var sw = Stopwatch.StartNew();
        var vars = new VariableResolver(_ctx.Profile, _ctx.TppId, _ctx.UtcNow);
        var stopped = false;

        for (var i = 0; i < scenario.Steps.Count; i++)
        {
            var step = scenario.Steps[i];
            if (stopped)
            {
                result.Steps.Add(new StepResult { Name = step.DisplayName(i), Kind = step.Kind, Outcome = Outcome.Blocked });
                continue;
            }

            var stepResult = await RunStepAsync(step, i, vars, cancellationToken).ConfigureAwait(false);
            result.Steps.Add(stepResult);

            if (stepResult.Outcome is Outcome.Failed or Outcome.Error)
            {
                stopped = true;
                result.Reason ??= $"{stepResult.Name}: {stepResult.Failures.FirstOrDefault()}";
            }
        }

        for (var i = 0; i < (scenario.Cleanup?.Count ?? 0); i++)
        {
            var stepResult = await RunStepAsync(scenario.Cleanup![i], i, vars, cancellationToken).ConfigureAwait(false);

            // Cleanup problems do not change the verdict, but stay visible.
            if (stepResult.Outcome is Outcome.Failed or Outcome.Error)
            {
                stepResult.Warnings.AddRange(stepResult.Failures.Select(f => "Cleanup: " + f));
                stepResult.Failures.Clear();
                stepResult.Outcome = Outcome.Passed;
            }

            result.Cleanup.Add(stepResult);
        }

        sw.Stop();
        result.DurationMs = sw.ElapsedMilliseconds;
        result.Outcome = result.Steps.Any(s => s.Outcome == Outcome.Error) ? Outcome.Error
            : result.Steps.Any(s => s.Outcome == Outcome.Failed) ? Outcome.Failed
            : Outcome.Passed;
        if (result.Outcome == Outcome.Passed)
            result.Reason = null;

        return result;
    }

    /// <summary>Transient failures that <see cref="RetryHandler"/> retried stay visible as warnings.</summary>
    private static void ReportRetries(StepResult step)
    {
        var exchanges = step.Exchange is null ? step.Exchanges : step.Exchanges.Prepend(step.Exchange);
        foreach (var x in exchanges)
        {
            var retried = x.ResponseHeaders.FirstOrDefault(h => h.Key.Equals(RetryHandler.RetriedHeader, StringComparison.OrdinalIgnoreCase)).Value;
            if (retried is not null)
                step.Warnings.Add($"{x.Method} {new Uri(x.Url).AbsolutePath}: transient failure ({retried}), succeeded after retry");
        }
    }

    private static ScenarioResult Blocked(Scenario scenario, string reason) => new()
    {
        Id = scenario.Id,
        Title = scenario.Title,
        Suite = scenario.Suite,
        Severity = scenario.Severity,
        SpecRef = scenario.SpecRef,
        Origin = scenario.Origin,
        Source = scenario.SourcePath,
        Outcome = Outcome.Blocked,
        Reason = reason,
    };

    private async Task<StepResult> RunStepAsync(Step step, int index, VariableResolver vars, CancellationToken cancellationToken)
    {
        var stepResult = new StepResult { Name = step.DisplayName(index), Kind = step.Kind };

        var missing = step.RunIf?.FirstOrDefault(name => !vars.IsSaved(name));
        if (missing is not null)
        {
            stepResult.Outcome = Outcome.Skipped;
            stepResult.Info.Add($"Skipped: '{missing}' was not saved by an earlier step");
            return stepResult;
        }

        var sw = Stopwatch.StartNew();
        try
        {
            if (step.Request is not null)
                await HttpStepExecutor.ExecuteAsync(step, _ctx, vars, stepResult, cancellationToken).ConfigureAwait(false);
            else if (step.Tls is not null)
                await TlsStepExecutor.ExecuteAsync(step, _ctx, stepResult, cancellationToken).ConfigureAwait(false);
            else if (step.Sca is not null)
                await ScaStepExecutor.ExecuteAsync(step, _ctx, vars, stepResult, cancellationToken).ConfigureAwait(false);
            else
                LocalCertificatesStepExecutor.Execute(step, _ctx, stepResult);

            ReportRetries(stepResult);
            stepResult.Outcome = stepResult.IsToolError ? Outcome.Error
                : stepResult.Failures.Count == 0 ? Outcome.Passed
                : Outcome.Failed;
        }
        catch (ConfigurationException ex)
        {
            stepResult.Failures.Add(ex.Message);
            stepResult.Outcome = Outcome.Error;
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            stepResult.Failures.Add($"Unexpected error: {ex.GetType().Name}: {ex.Message}");
            stepResult.Outcome = Outcome.Error;
        }

        sw.Stop();
        stepResult.DurationMs = sw.ElapsedMilliseconds;
        return stepResult;
    }

    /// <summary>
    /// A requires-path is satisfied when it resolves in the profile to something other than null, false or an empty
    /// string/array/object. <c>path=value</c> instead requires the value itself, or an array containing it
    /// (e.g. <c>features.consents=aspspManaged</c>).
    /// </summary>
    private bool IsSatisfied(string requirement)
    {
        var eq = requirement.IndexOf('=');
        if (eq > 0)
        {
            var expected = requirement[(eq + 1)..].Trim();
            var found = JsonPathLite.Select(_ctx.Profile.Raw, requirement[..eq].Trim());
            return found.Count > 0 && found[0] switch
            {
                JsonArray arr => arr.Any(item => string.Equals(JsonPathLite.AsText(item), expected, StringComparison.OrdinalIgnoreCase)),
                var node => string.Equals(JsonPathLite.AsText(node), expected, StringComparison.OrdinalIgnoreCase),
            };
        }

        var path = requirement;
        var matches = JsonPathLite.Select(_ctx.Profile.Raw, path);
        return matches.Count > 0 && matches[0] switch
        {
            null => false,
            JsonValue v when v.TryGetValue<bool>(out var b) => b,
            JsonValue v when v.TryGetValue<string>(out var s) => !string.IsNullOrWhiteSpace(s),
            JsonArray a => a.Count > 0,
            JsonObject o => o.Count > 0,
            _ => true,
        };
    }
}
