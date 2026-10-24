using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace ApiTestTool.Engine.Yaml;

/// <summary>
/// Converts YAML documents into <see cref="JsonNode"/> trees.
/// Plain scalars are typed with the YAML core schema (null, bool, int, float);
/// quoted scalars always stay strings, so <c>"4"</c> and <c>4</c> are different values.
/// </summary>
public static partial class YamlJson
{
    internal static readonly JsonSerializerOptions s_modelOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    public static JsonNode? Parse(string yaml, string sourceName)
    {
        var stream = new YamlStream();
        try
        {
            using var reader = new StringReader(yaml);
            stream.Load(reader);
        }
        catch (YamlException ex)
        {
            throw new ConfigurationException($"{sourceName}: invalid YAML at line {ex.Start.Line}, column {ex.Start.Column}: {ex.Message}", ex);
        }

        return stream.Documents.Count == 0 ? null : ToJsonNode(stream.Documents[0].RootNode);
    }

    public static JsonNode? ParseFile(string path) => Parse(File.ReadAllText(path), path);

    /// <summary>Deserializes a YAML file into a typed model. Unknown properties are rejected to catch typos.</summary>
    public static T LoadModel<T>(string path) where T : class
    {
        var node = ParseFile(path) ?? throw new ConfigurationException($"{path}: file is empty");
        try
        {
            return node.Deserialize<T>(s_modelOptions) ?? throw new ConfigurationException($"{path}: file is empty");
        }
        catch (JsonException ex)
        {
            throw new ConfigurationException($"{path}: {ex.Message}", ex);
        }
    }

    public static JsonNode? ToJsonNode(YamlNode node)
    {
        switch (node)
        {
            case YamlMappingNode map:
                var obj = new JsonObject();
                foreach (var (key, value) in map.Children)
                {
                    var name = ((YamlScalarNode)key).Value ?? string.Empty;
                    obj[name] = ToJsonNode(value);
                }
                return obj;

            case YamlSequenceNode seq:
                var arr = new JsonArray();
                foreach (var item in seq.Children)
                    arr.Add(ToJsonNode(item));
                return arr;

            case YamlScalarNode scalar:
                return ScalarToJson(scalar);

            default:
                throw new ConfigurationException($"Unsupported YAML node at line {node.Start.Line}");
        }
    }

    private static JsonNode? ScalarToJson(YamlScalarNode scalar)
    {
        var value = scalar.Value ?? string.Empty;

        if (scalar.Style is ScalarStyle.SingleQuoted or ScalarStyle.DoubleQuoted or ScalarStyle.Literal or ScalarStyle.Folded)
            return JsonValue.Create(value);

        switch (value)
        {
            case "" or "~" or "null" or "Null" or "NULL":
                return null;
            case "true" or "True" or "TRUE":
                return JsonValue.Create(true);
            case "false" or "False" or "FALSE":
                return JsonValue.Create(false);
        }

        if (IntRegex().IsMatch(value) && long.TryParse(value, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var l))
            return JsonValue.Create(l);

        if (FloatRegex().IsMatch(value) && decimal.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
            return JsonValue.Create(d);

        return JsonValue.Create(value);
    }

    [GeneratedRegex(@"^[-+]?[0-9]+$")]
    private static partial Regex IntRegex();

    [GeneratedRegex(@"^[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$")]
    private static partial Regex FloatRegex();
}
