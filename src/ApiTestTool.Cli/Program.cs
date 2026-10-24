using System.Globalization;
using ApiTestTool.Cli;
using ApiTestTool.Engine;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Reporting;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Scenarios;

CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.InvariantCulture;

using var cts = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) =>
{
    e.Cancel = true;
    cts.Cancel();
};

try
{
    var cli = CliArgs.Parse(args);
    return cli.Command switch
    {
        "run" => await Commands.RunAsync(cli, cts.Token),
        "mock" => await Commands.MockAsync(cts.Token),
        "db" => await StoreCommands.DbAsync(cli, cts.Token),
        "ai" => await AiCommands.AiAsync(cli, cts.Token),
        "standards" => await RuleCommands.StandardsAsync(cli, cts.Token),
        "rules" => await RuleCommands.RulesAsync(cli, cts.Token),
        "list" => Commands.List(cli),
        "validate" => Commands.Validate(cli),
        "version" or "--version" => Commands.Version(),
        "help" or "--help" or "-h" or "" => Commands.Help(),
        _ => Commands.Unknown(cli.Command),
    };
}
catch (ConfigurationException ex)
{
    Out.Error(ex.Message);
    return ExitCodes.ConfigurationError;
}
catch (OperationCanceledException)
{
    Out.Error("Cancelled");
    return ExitCodes.ConfigurationError;
}

namespace ApiTestTool.Cli
{
    internal static class ExitCodes
    {
        public const int Success = 0;
        public const int TestsFailed = 1;
        public const int ConfigurationError = 2;
    }

    internal static class Commands
    {
        public static async Task<int> RunAsync(CliArgs cli, CancellationToken cancellationToken)
        {
            var bank = cli.Get("bank") ?? throw new ConfigurationException("Missing --bank <BIC or profile path>. Use 'aqt list banks' to see profiles.");
            var paths = Paths.Resolve(cli);

            var profile = BankProfileLoader.Load(bank, paths.Banks);
            var templates = TemplateLibrary.Load(paths.Scenarios);
            var all = ScenarioLoader.LoadAll(paths.Scenarios);
            var scenarios = ScenarioLoader.Filter(all, cli.GetList("suite"), cli.GetList("scenario"));
            if (scenarios.Count == 0)
                throw new ConfigurationException("No scenarios match the given --suite/--scenario filter");

            // With the gate on, a filtered run still checks connectivity first, so a bank that is down fails once, clearly.
            var gate = !cli.Has("no-preflight-gate");
            if (gate && !scenarios.Any(s => s.Suite.Equals(ScenarioRunner.PreflightSuite, StringComparison.OrdinalIgnoreCase)))
                scenarios = [.. all.Where(s => s.Suite.Equals(ScenarioRunner.PreflightSuite, StringComparison.OrdinalIgnoreCase)), .. scenarios];

            var formats = cli.GetList("report");
            if (formats.Count == 0)
                formats = ["html", "json", "junit"];

            Out.Header($"ApiTestTool {ScenarioRunner.ToolVersion} - {profile.Bank.Name} ({profile.Bank.Bic})");
            Out.Muted($"XS2A: {profile.Endpoints.Xs2a}");
            Out.Muted($"Scenarios: {scenarios.Count} from {paths.Scenarios}");
            Console.WriteLine();

            await using var mock = cli.Has("with-mock") ? await EmbeddedMockBank.StartAsync(profile.Endpoints.Xs2a, cancellationToken) : null;

            if (cli.Get("wait") is { } wait)
                await BankWaiter.WaitAsync(profile.Endpoints.Xs2a, int.Parse(wait, CultureInfo.InvariantCulture), cancellationToken);

            using var ctx = RunContext.Create(profile, templates);
            ctx.Notify = Out.Notice;
            if (cli.Has("sca-auto"))
            {
                ctx.OpenBrowser = HeadlessBrowser.OpenAsync;
                ctx.Notify = message => Out.Muted("         (sca-auto) " + message); // nothing for the tester to do
            }
            if (ctx.TppId is not null)
                Out.Muted($"TPP id: {ctx.TppId}");

            var runner = new ScenarioRunner(ctx) { PreflightGate = gate };
            var verbose = cli.Has("verbose");
            runner.ScenarioCompleted += s => Out.Scenario(s, verbose);

            var run = await runner.RunAsync(scenarios, cancellationToken);

            var outDir = cli.Get("out") ?? Path.Combine(Directory.GetCurrentDirectory(), "reports");
            var runId = $"{profile.Bank.Bic}-{run.StartedAtUtc:yyyyMMdd-HHmmss}";
            var runDir = Path.Combine(outDir, runId);
            var files = ReportWriter.Write(run, runDir, formats);

            Console.WriteLine();
            Out.Summary(run);
            foreach (var file in files)
                Out.Muted($"  {file}");

            if (StoreCommands.RunConnection(cli) is { } db)
                await StoreCommands.SaveRunAsync(db, runId, runDir, run, cli.GetList("suite"), cancellationToken);

            return run.Count(Outcome.Failed) + run.Count(Outcome.Error) > 0 ? ExitCodes.TestsFailed : ExitCodes.Success;
        }

        public static int List(CliArgs cli)
        {
            var paths = Paths.Resolve(cli);
            var what = cli.Positional.FirstOrDefault() ?? "scenarios";

            switch (what)
            {
                case "banks":
                    foreach (var file in BankProfileLoader.List(paths.Banks))
                    {
                        try
                        {
                            var p = BankProfileLoader.LoadFile(file);
                            Console.WriteLine($"{p.Bank.Bic,-12} {p.Bank.Name,-28} {p.Endpoints.Xs2a}");
                        }
                        catch (ConfigurationException ex)
                        {
                            Out.Error(ex.Message);
                        }
                    }
                    return ExitCodes.Success;

                case "scenarios":
                    var scenarios = ScenarioLoader.Filter(ScenarioLoader.LoadAll(paths.Scenarios), cli.GetList("suite"), []);
                    foreach (var s in scenarios.OrderBy(s => s.Suite).ThenBy(s => s.Id))
                        Console.WriteLine($"{s.Suite,-10} {s.Id,-14} {s.Severity,-9} {s.Title}");
                    Out.Muted($"{scenarios.Count} scenario(s)");
                    return ExitCodes.Success;

                default:
                    throw new ConfigurationException("Usage: aqt list banks|scenarios [--suite name]");
            }
        }

        public static int Validate(CliArgs cli)
        {
            var paths = Paths.Resolve(cli);
            var ok = true;

            foreach (var file in BankProfileLoader.List(paths.Banks))
            {
                try
                {
                    BankProfileLoader.LoadFile(file);
                    Out.Ok($"profile   {Path.GetFileName(file)}");
                }
                catch (ConfigurationException ex)
                {
                    ok = false;
                    Out.Error(ex.Message);
                }
            }

            try
            {
                var templates = TemplateLibrary.Load(paths.Scenarios);
                Out.Ok($"templates {templates.HeaderSetNames.Count} header set(s), {templates.BodyNames.Count} bod(ies)");

                var scenarios = ScenarioLoader.LoadAll(paths.Scenarios);
                foreach (var s in scenarios)
                {
                    foreach (var r in s.Steps.Concat(s.Cleanup ?? []).Select(st => st.Request).OfType<RequestSpec>())
                    {
                        if (r.HeadersTemplate is not null && !templates.HeaderSetNames.Contains(r.HeadersTemplate, StringComparer.OrdinalIgnoreCase))
                            throw new ConfigurationException($"{s.SourcePath}: unknown headersTemplate '{r.HeadersTemplate}'");
                        if (r.BodyTemplate is not null && !templates.BodyNames.Contains(r.BodyTemplate, StringComparer.OrdinalIgnoreCase))
                            throw new ConfigurationException($"{s.SourcePath}: unknown bodyTemplate '{r.BodyTemplate}'");
                    }
                }

                Out.Ok($"scenarios {scenarios.Count} valid");
            }
            catch (ConfigurationException ex)
            {
                ok = false;
                Out.Error(ex.Message);
            }

            return ok ? ExitCodes.Success : ExitCodes.ConfigurationError;
        }

        /// <summary><c>aqt mock</c>: runs MockBank in the foreground until Ctrl+C.</summary>
        public static async Task<int> MockAsync(CancellationToken cancellationToken)
        {
            await using var app = ApiTestTool.MockBank.MockBankHost.Create([]);
            await app.StartAsync(cancellationToken);
            Out.Muted("Press Ctrl+C to stop MockBank.");
            try
            {
                await Task.Delay(Timeout.Infinite, cancellationToken);
            }
            catch (OperationCanceledException)
            {
                // Ctrl+C
            }

            await app.StopAsync(CancellationToken.None);
            return ExitCodes.Success;
        }

        public static int Version()
        {
            Console.WriteLine(ScenarioRunner.ToolVersion);
            return ExitCodes.Success;
        }

        public static int Unknown(string command)
        {
            Out.Error($"Unknown command '{command}'");
            Help();
            return ExitCodes.ConfigurationError;
        }

        public static int Help()
        {
            Console.WriteLine($"""
                ApiTestTool {ScenarioRunner.ToolVersion} - tests a bank's XS2A sandbox as a TPP

                Usage:
                  aqt run --bank <BIC|profile.yaml> [--suite a,b] [--scenario ID,ID] [--report html,json,junit] [--out dir] [--verbose] [--no-preflight-gate]
                          [--sca-auto] [--wait <seconds>] [--with-mock]
                  aqt mock                     run MockBank (a fake bank) until Ctrl+C
                  aqt db migrate|status        create/upgrade the PostgreSQL store, show row counts
                  aqt db import [folder...] [--replace]   load existing report folders (default: ./reports) into the store
                  aqt ai check [--provider anthropic|ollama] [--model name]   one-line call to each AI provider
                  aqt ai summarize <run-id|latest> [--bank BIC] [--provider ..] [--model ..] [--show-input]   AI triage of a stored run
                  aqt standards fetch|ingest [--no-embed]|list|search "<text>" [--standard ID]|show <clause-key>
                  aqt rules draft <clause-key> [--provider ..] [--model ..] · list [--status s] [--standard ID] · show <id>
                  aqt rules approve|reject|reopen <id> --by <name> [--note ..] · export        rules from the standards
                  aqt list banks|scenarios [--suite name]
                  aqt validate
                  aqt version

                Common options:
                  --root <dir>        folder containing banks/ and scenarios/ (default: current folder, then the tool folder; env AQT_HOME)
                  --banks <dir>       override the bank profiles folder
                  --scenarios <dir>   override the scenarios folder
                  --db <connection>   PostgreSQL store (env AQT_DB); 'run' saves results there when set.
                                      'db' commands default to the docker-compose database (localhost:5435)

                Testing without a bank:
                  aqt run --bank MOCK --with-mock --sca-auto
                  --with-mock         start MockBank inside aqt for this run (or reuse one already running)
                  --sca-auto          follow the OAuth login redirect without a browser (auto-approving servers such as MockBank only)
                  --wait <seconds>    wait until the bank's XS2A port accepts connections before starting

                Exit codes: 0 all passed, 1 failures or errors in the run, 2 invalid input or configuration
                """);
            return ExitCodes.Success;
        }
    }

    internal sealed record Paths(string Banks, string Scenarios, string Root)
    {
        public string Standards => Path.Combine(Root, "standards");

        public string Rules => Path.Combine(Root, "rules");

        public static Paths Resolve(CliArgs cli)
        {
            var root = cli.Get("root") ?? Environment.GetEnvironmentVariable("AQT_HOME") ?? FindRoot();
            return new Paths(
                cli.Get("banks") ?? Path.Combine(root, "banks"),
                cli.Get("scenarios") ?? Path.Combine(root, "scenarios"),
                root);
        }

        private static string FindRoot()
        {
            var cwd = Directory.GetCurrentDirectory();
            return Directory.Exists(Path.Combine(cwd, "scenarios")) ? cwd : AppContext.BaseDirectory;
        }
    }
}
