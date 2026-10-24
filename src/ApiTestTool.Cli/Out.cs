using ApiTestTool.Engine.Running;

namespace ApiTestTool.Cli;

/// <summary>Coloured console output.</summary>
internal static class Out
{
    public static void Header(string text) => Write(text, ConsoleColor.White);

    public static void Muted(string text) => Write(text, ConsoleColor.DarkGray);

    public static void Ok(string text) => Write("  OK    " + text, ConsoleColor.Green);

    /// <summary>Something the tester has to act on now (e.g. log in for SCA).</summary>
    public static void Notice(string text) => Write("  >>    " + text, ConsoleColor.Cyan);

    public static void Error(string text) => Write("ERROR: " + text, ConsoleColor.Red, Console.Error);

    public static void Scenario(ScenarioResult s, bool verbose)
    {
        var (label, color) = s.Outcome switch
        {
            Outcome.Passed => ("PASS ", ConsoleColor.Green),
            Outcome.Failed => ("FAIL ", ConsoleColor.Red),
            Outcome.Error => ("ERROR", ConsoleColor.Magenta),
            Outcome.Skipped => ("SKIP ", ConsoleColor.DarkGray),
            Outcome.Blocked => ("BLOCK", ConsoleColor.DarkYellow),
            _ => (s.Outcome.ToString().ToUpperInvariant(), ConsoleColor.Gray),
        };

        var warnings = s.AllWarnings.Count();
        Write($"  {label}  {s.Id,-14} {s.Title}  ({s.DurationMs} ms{(warnings > 0 ? $", {warnings} warning(s)" : "")})", color);

        if (s.Outcome is not Outcome.Passed && s.Reason is not null)
            Write($"         {s.Reason}", ConsoleColor.DarkGray);

        if (!verbose)
            return;

        foreach (var step in s.Steps.Concat(s.Cleanup))
        {
            Write($"         - {step.Name}: {step.Outcome}", ConsoleColor.Gray);
            foreach (var f in step.Failures) Write($"             x {f}", ConsoleColor.Red);
            foreach (var w in step.Warnings) Write($"             ! {w}", ConsoleColor.Yellow);
            foreach (var i in step.Info) Write($"             - {i}", ConsoleColor.DarkGray);
        }
    }

    public static void Summary(RunResult run)
    {
        var color = run.Count(Outcome.Failed) + run.Count(Outcome.Error) == 0 ? ConsoleColor.Green : ConsoleColor.Red;
        Write($"Passed {run.Count(Outcome.Passed)}, failed {run.Count(Outcome.Failed)}, errors {run.Count(Outcome.Error)}, blocked {run.Count(Outcome.Blocked)}, skipped {run.Count(Outcome.Skipped)} - pass rate {run.PassRate}%", color);
    }

    private static void Write(string text, ConsoleColor color, TextWriter? writer = null)
    {
        writer ??= Console.Out;
        var previous = Console.ForegroundColor;
        Console.ForegroundColor = color;
        writer.WriteLine(text);
        Console.ForegroundColor = previous;
    }
}
