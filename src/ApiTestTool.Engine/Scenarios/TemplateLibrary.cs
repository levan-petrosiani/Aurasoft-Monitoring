using System.Text.Json.Nodes;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Scenarios;

/// <summary>
/// Reusable header sets and JSON bodies from <c>scenarios/_templates/*.yaml</c>:
/// <code>
/// headers:
///   consent: { X-Request-ID: "{{uuid}}", ... }
/// bodies:
///   consent.accountList: { ... }
/// </code>
/// </summary>
public sealed class TemplateLibrary
{
    private readonly Dictionary<string, JsonObject> _headers = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, JsonNode> _bodies = new(StringComparer.OrdinalIgnoreCase);

    public static TemplateLibrary Empty { get; } = new();

    public IReadOnlyCollection<string> HeaderSetNames => _headers.Keys;

    public IReadOnlyCollection<string> BodyNames => _bodies.Keys;

    public static TemplateLibrary Load(string scenariosDirectory)
    {
        var library = new TemplateLibrary();
        var dir = Path.Combine(scenariosDirectory, ScenarioLoader.TemplatesFolder);
        if (!Directory.Exists(dir))
            return library;

        foreach (var file in Directory.EnumerateFiles(dir, "*.yaml").Order(StringComparer.OrdinalIgnoreCase))
        {
            if (YamlJson.ParseFile(file) is not JsonObject root)
                continue;

            foreach (var (section, value) in root)
            {
                if (value is not JsonObject entries)
                    throw new ConfigurationException($"{file}: '{section}' must be a mapping");

                foreach (var (name, entry) in entries)
                {
                    switch (section)
                    {
                        case "headers" when entry is JsonObject h:
                            library._headers[name] = h;
                            break;
                        case "bodies" when entry is not null:
                            library._bodies[name] = entry;
                            break;
                        default:
                            throw new ConfigurationException($"{file}: unknown template section '{section}' or invalid entry '{name}'");
                    }
                }
            }
        }

        return library;
    }

    public JsonObject GetHeaders(string name) =>
        _headers.TryGetValue(name, out var h) ? (JsonObject)h.DeepClone() : throw new ConfigurationException($"Header template '{name}' not found");

    public JsonNode GetBody(string name) =>
        _bodies.TryGetValue(name, out var b) ? b.DeepClone() : throw new ConfigurationException($"Body template '{name}' not found");
}
