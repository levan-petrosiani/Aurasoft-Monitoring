using System.Text.Json;
using System.Text.Json.Nodes;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Profiles;

public static class BankProfileLoader
{
    /// <summary>Loads a profile by file path, or by BIC from <paramref name="banksDirectory"/> (<c>&lt;BIC&gt;.yaml</c>).</summary>
    public static BankProfile Load(string bicOrPath, string banksDirectory)
    {
        var path = File.Exists(bicOrPath) ? bicOrPath : Path.Combine(banksDirectory, $"{bicOrPath}.yaml");
        if (!File.Exists(path))
            throw new ConfigurationException($"Bank profile '{bicOrPath}' not found (looked for {Path.GetFullPath(path)})");

        return LoadFile(path);
    }

    public static BankProfile LoadFile(string path)
    {
        var raw = LoadWithBase(path, depth: 0);

        BankProfile profile;
        try
        {
            profile = raw.Deserialize<BankProfile>(YamlJson.s_modelOptions) ?? throw new ConfigurationException($"{path}: file is empty");
        }
        catch (JsonException ex)
        {
            throw new ConfigurationException($"{path}: {ex.Message}", ex);
        }

        Validate(profile, path);
        profile.SourcePath = Path.GetFullPath(path);
        profile.Raw = raw;
        return profile;
    }

    /// <summary>All profiles in a directory. Files starting with '_' are templates and are skipped.</summary>
    public static IEnumerable<string> List(string banksDirectory)
    {
        if (!Directory.Exists(banksDirectory))
            return [];

        return Directory.EnumerateFiles(banksDirectory, "*.yaml")
            .Where(f => !Path.GetFileName(f).StartsWith('_'))
            .Order(StringComparer.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Reads a profile and, when it has <c>extends: other.yaml</c>, deep-merges it over that base file
    /// (relative to the profile). Shared settings such as TPP certificates live in one base file.
    /// </summary>
    private static JsonObject LoadWithBase(string path, int depth)
    {
        if (depth > 5)
            throw new ConfigurationException($"{path}: 'extends' chain is too deep");

        if (YamlJson.ParseFile(path) is not JsonObject raw)
            throw new ConfigurationException($"{path}: profile must be a YAML mapping");

        if (raw["extends"] is not JsonValue extendsValue)
            return raw;

        raw.Remove("extends");
        var basePath = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path))!, extendsValue.GetValue<string>()));
        if (!File.Exists(basePath))
            throw new ConfigurationException($"{path}: base profile '{basePath}' not found");

        return Merge(LoadWithBase(basePath, depth + 1), raw);

        static JsonObject Merge(JsonObject target, JsonObject patch)
        {
            foreach (var (key, value) in patch.ToList())
            {
                if (value is JsonObject p && target[key] is JsonObject t)
                    Merge(t, p);
                else
                    target[key] = value?.DeepClone();
            }

            return target;
        }
    }

    private static void Validate(BankProfile profile, string path)
    {
        if (!Uri.TryCreate(profile.Endpoints.Xs2a, UriKind.Absolute, out var xs2a) || xs2a.Scheme is not ("https" or "http"))
            throw new ConfigurationException($"{path}: endpoints.xs2a must be an absolute http(s) URL");

        if (profile.Endpoints.Discovery is not null && !Uri.TryCreate(profile.Endpoints.Discovery, UriKind.Absolute, out _))
            throw new ConfigurationException($"{path}: endpoints.discovery must be an absolute URL");

        if (profile.ResponseSignature is not ("required" or "optional" or "ignore"))
            throw new ConfigurationException($"{path}: responseSignature must be required, optional or ignore");
    }
}
