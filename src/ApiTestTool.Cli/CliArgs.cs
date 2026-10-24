using ApiTestTool.Engine;

namespace ApiTestTool.Cli;

/// <summary>Minimal argument parser: <c>command [positional...] [--name value | --name=value | --flag]</c>.</summary>
internal sealed class CliArgs
{
    private static readonly HashSet<string> s_flags = new(StringComparer.OrdinalIgnoreCase) { "verbose", "no-preflight-gate", "sca-auto", "with-mock", "replace", "show-input" };

    private readonly Dictionary<string, string?> _options = new(StringComparer.OrdinalIgnoreCase);

    public string Command { get; private set; } = string.Empty;

    public List<string> Positional { get; } = [];

    public static CliArgs Parse(string[] args)
    {
        var result = new CliArgs();
        for (var i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            var isCommandAlias = result.Command.Length == 0 && arg is "--help" or "--version";
            if (arg.StartsWith("--", StringComparison.Ordinal) && arg.Length > 2 && !isCommandAlias)
            {
                var name = arg[2..];
                string? value = null;
                var eq = name.IndexOf('=');
                if (eq >= 0)
                {
                    value = name[(eq + 1)..];
                    name = name[..eq];
                }
                else if (!s_flags.Contains(name))
                {
                    if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                        throw new ConfigurationException($"Option --{name} needs a value");
                    value = args[++i];
                }

                result._options[name] = value;
            }
            else if (result.Command.Length == 0)
            {
                result.Command = arg.ToLowerInvariant();
            }
            else
            {
                result.Positional.Add(arg);
            }
        }

        return result;
    }

    public bool Has(string name) => _options.ContainsKey(name);

    public string? Get(string name) => _options.TryGetValue(name, out var v) ? v : null;

    /// <summary>Comma-separated option value as a list; empty when the option is absent.</summary>
    public List<string> GetList(string name) =>
        Get(name)?.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList() ?? [];
}
