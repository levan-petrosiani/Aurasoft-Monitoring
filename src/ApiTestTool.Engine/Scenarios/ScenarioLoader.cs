using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Scenarios;

public static class ScenarioLoader
{
    public const string TemplatesFolder = "_templates";

    /// <summary>
    /// Loads every scenario under <paramref name="directory"/> (recursively). The <c>_templates</c> folder and files
    /// starting with '_' are skipped. Throws <see cref="ConfigurationException"/> listing every invalid file.
    /// </summary>
    public static List<Scenario> LoadAll(string directory)
    {
        if (!Directory.Exists(directory))
            throw new ConfigurationException($"Scenarios directory not found: {Path.GetFullPath(directory)}");

        var scenarios = new List<Scenario>();
        var errors = new List<string>();

        foreach (var file in Directory.EnumerateFiles(directory, "*.yaml", SearchOption.AllDirectories).Order(StringComparer.OrdinalIgnoreCase))
        {
            var relative = Path.GetRelativePath(directory, file);
            if (relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Any(p => p.StartsWith('_')))
                continue;

            try
            {
                var scenario = YamlJson.LoadModel<Scenario>(file);
                Validate(scenario, file);
                scenario.SourcePath = Path.GetFullPath(file);
                scenarios.Add(scenario);
            }
            catch (ConfigurationException ex)
            {
                errors.Add(ex.Message);
            }
        }

        foreach (var dup in scenarios.GroupBy(s => s.Id, StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1))
            errors.Add($"Duplicate scenario id '{dup.Key}': {string.Join(", ", dup.Select(s => s.SourcePath))}");

        if (errors.Count > 0)
            throw new ConfigurationException("Invalid scenarios:" + Environment.NewLine + string.Join(Environment.NewLine, errors.Select(e => "  - " + e)));

        return scenarios;
    }

    /// <summary>Filters by suite names and/or scenario ids (case-insensitive). Empty filters match everything.</summary>
    public static List<Scenario> Filter(IEnumerable<Scenario> scenarios, IReadOnlyCollection<string> suites, IReadOnlyCollection<string> ids)
    {
        return scenarios
            .Where(s => suites.Count == 0 || suites.Contains(s.Suite, StringComparer.OrdinalIgnoreCase))
            .Where(s => ids.Count == 0 || ids.Contains(s.Id, StringComparer.OrdinalIgnoreCase))
            .ToList();
    }

    private static void Validate(Scenario scenario, string file)
    {
        if (scenario.Steps.Count == 0)
            throw new ConfigurationException($"{file}: scenario has no steps");

        foreach (var (step, index) in scenario.Steps.Concat(scenario.Cleanup ?? []).Select((s, i) => (s, i)))
        {
            if (step.KindCount != 1)
                throw new ConfigurationException($"{file}: step {index + 1} must have exactly one of 'request', 'tls', 'localCertificates', 'sca'");

            if (step.Sca is { } sca)
            {
                if (sca.Mode != "manual")
                    throw new ConfigurationException($"{file}: step {index + 1}: sca.mode '{sca.Mode}' is not supported yet (manual)");
                if (sca.TimeoutSeconds is < 5 or > 3600)
                    throw new ConfigurationException($"{file}: step {index + 1}: sca.timeoutSeconds must be between 5 and 3600");
            }

            if (step.Request is { } r)
            {
                if (r.Signing is not ("normal" or "none" or "corruptSignature" or "wrongDigest"))
                    throw new ConfigurationException($"{file}: step {index + 1}: signing must be normal, none, corruptSignature or wrongDigest");

                if (r.Target is not ("xs2a" or "discovery" or "absolute"))
                    throw new ConfigurationException($"{file}: step {index + 1}: target must be xs2a, discovery or absolute");
            }

            if (step.Expect?.ResponseSignature is { } rs && rs is not ("required" or "optional" or "ignore"))
                throw new ConfigurationException($"{file}: step {index + 1}: responseSignature must be required, optional or ignore");
        }

        if (scenario.Severity is not ("Critical" or "Major" or "Minor" or "Info"))
            throw new ConfigurationException($"{file}: severity must be Critical, Major, Minor or Info");
    }
}
