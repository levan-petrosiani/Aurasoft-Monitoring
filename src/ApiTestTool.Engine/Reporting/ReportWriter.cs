using System.Globalization;
using System.Net;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Xml.Linq;
using ApiTestTool.Engine.Running;

namespace ApiTestTool.Engine.Reporting;

public static class ReportWriter
{
    public static readonly string[] SupportedFormats = ["html", "json", "junit"];

    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    private static readonly JsonSerializerOptions s_json = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>Writes the requested report formats into <paramref name="directory"/> and returns the file paths.</summary>
    public static List<string> Write(RunResult run, string directory, IEnumerable<string> formats)
    {
        Directory.CreateDirectory(directory);
        var files = new List<string>();

        foreach (var format in formats.Select(f => f.Trim().ToLowerInvariant()).Distinct())
        {
            var (name, content) = format switch
            {
                "json" => ("summary.json", JsonSerializer.Serialize(run, s_json)),
                "html" => ("report.html", Html(run)),
                "junit" => ("junit.xml", JUnit(run)),
                _ => throw new ConfigurationException($"Unknown report format '{format}'. Supported: {string.Join(", ", SupportedFormats)}"),
            };

            var path = Path.Combine(directory, name);
            File.WriteAllText(path, content, new UTF8Encoding(false));
            files.Add(path);
        }

        return files;
    }

    internal static string JUnit(RunResult run)
    {
        var suites = new XElement("testsuites",
            new XAttribute("name", $"ApiTestTool {run.BankBic}"),
            new XAttribute("tests", run.Scenarios.Count),
            new XAttribute("failures", run.Count(Outcome.Failed)),
            new XAttribute("errors", run.Count(Outcome.Error)),
            new XAttribute("skipped", run.Count(Outcome.Skipped)));

        foreach (var group in run.Scenarios.GroupBy(s => s.Suite))
        {
            var suite = new XElement("testsuite",
                new XAttribute("name", $"{run.BankBic}.{group.Key}"),
                new XAttribute("tests", group.Count()),
                new XAttribute("failures", group.Count(s => s.Outcome == Outcome.Failed)),
                new XAttribute("errors", group.Count(s => s.Outcome == Outcome.Error)),
                new XAttribute("skipped", group.Count(s => s.Outcome is Outcome.Skipped)),
                new XAttribute("timestamp", run.StartedAtUtc.ToString("s", CultureInfo.InvariantCulture)));

            foreach (var s in group)
            {
                var testCase = new XElement("testcase",
                    new XAttribute("classname", $"{run.BankBic}.{s.Suite}"),
                    new XAttribute("name", $"{s.Id} {s.Title}"),
                    new XAttribute("time", (s.DurationMs / 1000.0).ToString("0.000", CultureInfo.InvariantCulture)));

                var details = string.Join(Environment.NewLine, s.Steps.SelectMany(st => st.Failures.Select(f => $"[{st.Name}] {f}")));
                switch (s.Outcome)
                {
                    case Outcome.Failed:
                        testCase.Add(new XElement("failure", new XAttribute("message", s.Reason ?? "failed"), details));
                        break;
                    case Outcome.Error:
                        testCase.Add(new XElement("error", new XAttribute("message", s.Reason ?? "error"), details));
                        break;
                    case Outcome.Skipped:
                        testCase.Add(new XElement("skipped", new XAttribute("message", s.Reason ?? "skipped")));
                        break;
                }

                var warnings = s.AllWarnings.ToList();
                if (warnings.Count > 0)
                    testCase.Add(new XElement("system-out", string.Join(Environment.NewLine, warnings.Select(w => "WARNING: " + w))));

                suite.Add(testCase);
            }

            suites.Add(suite);
        }

        return new XDocument(new XDeclaration("1.0", "utf-8", null), suites).ToString();
    }

    internal static string Html(RunResult run)
    {
        var sb = new StringBuilder();
        sb.Append(Inv, $$"""
            <!doctype html>
            <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <title>API test report — {{E(run.BankBic)}}</title>
            <style>
              :root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e5e5ea; --pass:#1a7f37; --fail:#cf222e; --warn:#9a6700; --skip:#6e6e73; --err:#8250df; --code:#f6f8fa; }
              @media (prefers-color-scheme: dark) { :root { --bg:#111; --fg:#e8e8ea; --muted:#9a9aa0; --line:#2c2c30; --pass:#3fb950; --fail:#f85149; --warn:#d29922; --skip:#8b949e; --err:#a371f7; --code:#1b1b1f; } }
              body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); margin: 0 auto; max-width: 1100px; padding: 24px 16px; }
              h1 { font-size: 22px; margin: 0 0 4px; } .muted { color: var(--muted); }
              .tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }
              .tile { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 110px; }
              .tile b { display: block; font-size: 22px; }
              table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
              .o-Passed { color: var(--pass); } .o-Failed { color: var(--fail); } .o-Blocked, .o-Skipped { color: var(--skip); } .o-Error { color: var(--err); }
              details { border: 1px solid var(--line); border-radius: 8px; margin: 10px 0; padding: 8px 12px; } summary { cursor: pointer; font-weight: 600; }
              pre { background: var(--code); padding: 8px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
              ul { margin: 4px 0; padding-left: 20px; } .warn { color: var(--warn); } .fail { color: var(--fail); }
            </style></head><body>
            <h1>{{E(run.BankName)}} ({{E(run.BankBic)}})</h1>
            <div class="muted">{{E(run.Xs2aUrl)}} · TPP {{E(run.TppId ?? "—")}} · {{run.StartedAtUtc:yyyy-MM-dd HH:mm:ss}} UTC · ApiTestTool {{E(run.ToolVersion)}}</div>
            <div class="tiles">
              <div class="tile"><b>{{run.PassRate.ToString("0.#", CultureInfo.InvariantCulture)}}%</b>pass rate</div>
              <div class="tile"><b class="o-Passed">{{run.Count(Outcome.Passed)}}</b>passed</div>
              <div class="tile"><b class="o-Failed">{{run.Count(Outcome.Failed)}}</b>failed</div>
              <div class="tile"><b class="o-Error">{{run.Count(Outcome.Error)}}</b>errors</div>
              <div class="tile"><b class="o-Skipped">{{run.Count(Outcome.Skipped)}}</b>skipped</div>
            </div>
            <table><thead><tr><th>ID</th><th>Scenario</th><th>Suite</th><th>Severity</th><th>Result</th><th>Reason</th></tr></thead><tbody>
            """);

        foreach (var s in run.Scenarios)
        {
            sb.Append(Inv, $"""<tr><td><a href="#{E(s.Id)}">{E(s.Id)}</a></td><td>{E(s.Title)}</td><td>{E(s.Suite)}</td><td>{E(s.Severity)}</td><td class="o-{s.Outcome}">{s.Outcome}</td><td>{E(s.Reason ?? "")}</td></tr>""");
        }

        sb.Append("</tbody></table><h2>Details</h2>");

        foreach (var s in run.Scenarios)
        {
            sb.Append(Inv, $"""<details id="{E(s.Id)}"{(s.Outcome is Outcome.Failed or Outcome.Error ? " open" : "")}><summary><span class="o-{s.Outcome}">{s.Outcome}</span> · {E(s.Id)} — {E(s.Title)}</summary>""");
            if (s.SpecRef is not null) sb.Append(Inv, $"<div class=\"muted\">Spec: {E(s.SpecRef)}</div>");
            if (s.Origin is not null) sb.Append(Inv, $"<div class=\"muted\">Origin: {E(s.Origin)}</div>");

            foreach (var step in s.Steps.Concat(s.Cleanup))
                AppendStep(sb, step, s.Cleanup.Contains(step));

            sb.Append("</details>");
        }

        sb.Append("</body></html>");
        return sb.ToString();
    }

    private static void AppendStep(StringBuilder sb, StepResult step, bool isCleanup)
    {
        sb.Append(Inv, $"""<h4>{(isCleanup ? "Cleanup: " : "")}{E(step.Name)} <span class="o-{step.Outcome}">{step.Outcome}</span> <span class="muted">{step.DurationMs} ms</span></h4>""");
        AppendList(sb, step.Failures, "fail");
        AppendList(sb, step.Warnings, "warn");
        AppendList(sb, step.Info, "muted");

        foreach (var x in step.Exchange is null ? step.Exchanges : step.Exchanges.Prepend(step.Exchange))
        {
            var request = new StringBuilder($"{x.Method} {x.Url}\n");
            foreach (var (k, v) in x.RequestHeaders) request.Append(Inv, $"{k}: {v}\n");
            if (x.RequestBody is not null) request.Append('\n').Append(x.RequestBody);

            var response = new StringBuilder(x.StatusCode is null ? $"(no response) {x.Error}\n" : $"{x.StatusCode} ({x.DurationMs} ms)\n");
            foreach (var (k, v) in x.ResponseHeaders) response.Append(Inv, $"{k}: {v}\n");
            if (!string.IsNullOrEmpty(x.ResponseBody)) response.Append('\n').Append(PrettyJson(x.ResponseBody));

            sb.Append(Inv, $"<div class=\"muted\">Request</div><pre>{E(request.ToString())}</pre><div class=\"muted\">Response</div><pre>{E(response.ToString())}</pre>");
        }
    }

    private static void AppendList(StringBuilder sb, List<string> items, string css)
    {
        if (items.Count == 0) return;
        sb.Append(Inv, $"<ul class=\"{css}\">");
        foreach (var item in items) sb.Append(Inv, $"<li>{E(item)}</li>");
        sb.Append("</ul>");
    }

    private static string PrettyJson(string text)
    {
        try
        {
            using var doc = JsonDocument.Parse(text);
            return JsonSerializer.Serialize(doc.RootElement, s_json);
        }
        catch (JsonException)
        {
            return text;
        }
    }

    private static string E(string value) => WebUtility.HtmlEncode(value);
}
