using System.Globalization;
using System.Text.Json.Nodes;

namespace ApiTestTool.Engine.Running;

/// <summary>
/// The small JSONPath subset scenarios need: <c>$</c>, <c>.name</c>, <c>['name']</c>, <c>[0]</c> and <c>[*]</c>.
/// </summary>
public static class JsonPathLite
{
    /// <summary>All nodes matched by the path. A present JSON null is returned as a null element.</summary>
    public static List<JsonNode?> Select(JsonNode? root, string path)
    {
        var current = new List<JsonNode?> { root };
        foreach (var segment in Parse(path))
        {
            var next = new List<JsonNode?>();
            foreach (var node in current)
            {
                switch (segment)
                {
                    case { Wildcard: true }:
                        if (node is JsonArray wa) next.AddRange(wa);
                        else if (node is JsonObject wo) next.AddRange(wo.Select(p => p.Value));
                        break;
                    case { Index: { } i }:
                        if (node is JsonArray arr && i >= 0 && i < arr.Count) next.Add(arr[i]);
                        break;
                    case { Name: { } name }:
                        if (node is JsonObject obj && obj.TryGetPropertyValue(name, out var child)) next.Add(child);
                        break;
                }
            }

            current = next;
        }

        return current;
    }

    public static bool Exists(JsonNode? root, string path) => Select(root, path).Count > 0;

    /// <summary>Text form of a matched value: strings unquoted, null as "null", other JSON compact.</summary>
    public static string? AsText(JsonNode? node) => node switch
    {
        null => "null",
        JsonValue v when v.TryGetValue<string>(out var s) => s,
        JsonValue v when v.TryGetValue<bool>(out var b) => b ? "true" : "false",
        JsonValue v when v.TryGetValue<decimal>(out var d) => d.ToString(CultureInfo.InvariantCulture),
        _ => node.ToJsonString(),
    };

    private sealed record Segment(string? Name, int? Index, bool Wildcard);

    private static List<Segment> Parse(string path)
    {
        var p = path.Trim();
        if (p.StartsWith('$'))
            p = p[1..];

        var result = new List<Segment>();
        var i = 0;
        while (i < p.Length)
        {
            if (p[i] == '.')
            {
                i++;
                var start = i;
                while (i < p.Length && p[i] != '.' && p[i] != '[') i++;
                var name = p[start..i];
                result.Add(name == "*" ? new Segment(null, null, true) : new Segment(name, null, false));
            }
            else if (p[i] == '[')
            {
                var end = p.IndexOf(']', i);
                if (end < 0)
                    throw new ConfigurationException($"Invalid JSON path '{path}': missing ']'");

                var inner = p[(i + 1)..end].Trim();
                i = end + 1;

                if (inner == "*")
                    result.Add(new Segment(null, null, true));
                else if (inner.Length >= 2 && inner[0] is '\'' or '"')
                    result.Add(new Segment(inner[1..^1], null, false));
                else if (int.TryParse(inner, NumberStyles.None, CultureInfo.InvariantCulture, out var idx))
                    result.Add(new Segment(null, idx, false));
                else
                    throw new ConfigurationException($"Invalid JSON path '{path}': unsupported selector [{inner}]");
            }
            else
            {
                // Allow paths without a leading '$.' such as "consentId".
                var start = i;
                while (i < p.Length && p[i] != '.' && p[i] != '[') i++;
                result.Add(new Segment(p[start..i], null, false));
            }
        }

        return result;
    }
}
