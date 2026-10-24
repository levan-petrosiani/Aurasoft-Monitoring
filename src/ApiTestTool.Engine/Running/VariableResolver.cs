using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using ApiTestTool.Engine.Profiles;

namespace ApiTestTool.Engine.Running;

/// <summary>
/// Expands <c>{{...}}</c> placeholders:
/// <list type="bullet">
/// <item><c>uuid</c> — a new GUID every time</item>
/// <item><c>date.today</c>, <c>date.today+2d</c>, <c>date.today-5d</c> — yyyy-MM-dd (UTC)</item>
/// <item><c>datetime.now</c> — ISO 8601 UTC</item>
/// <item><c>bank.bic</c>, <c>bank.name</c>, <c>tpp.redirectUri</c>, <c>tpp.id</c></item>
/// <item><c>vars.path.to.value</c> — profile <c>variables</c></item>
/// <item><c>env.NAME</c> — environment variable</item>
/// <item>any other name — a value saved by an earlier step</item>
/// </list>
/// </summary>
public sealed partial class VariableResolver
{
    private readonly BankProfile _profile;
    private readonly Func<DateTime> _utcNow;
    private readonly string? _tppId;
    private readonly Dictionary<string, string> _saved = new(StringComparer.Ordinal);

    public VariableResolver(BankProfile profile, string? tppId, Func<DateTime>? utcNow = null)
    {
        _profile = profile;
        _tppId = tppId;
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    public IReadOnlyDictionary<string, string> Saved => _saved;

    public void Save(string name, string value) => _saved[name] = value;

    public string Resolve(string text) => PlaceholderRegex().Replace(text, m => Evaluate(m.Groups[1].Value.Trim()));

    /// <summary>Like <see cref="Resolve(string)"/>, but returns null instead of throwing when a variable is unknown.</summary>
    public string? TryResolve(string text)
    {
        try
        {
            return Resolve(text);
        }
        catch (ConfigurationException)
        {
            return null;
        }
    }

    public bool IsSaved(string name) => _saved.ContainsKey(name);

    /// <summary>Returns a deep copy of <paramref name="node"/> with every string value resolved.</summary>
    public JsonNode? Resolve(JsonNode? node) => node switch
    {
        null => null,
        JsonObject obj => new JsonObject(obj.Select(p => KeyValuePair.Create(p.Key, Resolve(p.Value)))),
        JsonArray arr => new JsonArray(arr.Select(Resolve).ToArray()),
        JsonValue v when v.TryGetValue<string>(out var s) => JsonValue.Create(Resolve(s)),
        _ => node.DeepClone(),
    };

    private string Evaluate(string expr)
    {
        if (expr == "uuid")
            return Guid.NewGuid().ToString();

        if (expr == "datetime.now")
            return _utcNow().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);

        var date = DateRegex().Match(expr);
        if (date.Success)
        {
            var day = DateOnly.FromDateTime(_utcNow());
            if (date.Groups[1].Success)
            {
                var n = int.Parse(date.Groups[2].Value, CultureInfo.InvariantCulture);
                day = day.AddDays(date.Groups[1].Value == "-" ? -n : n);
            }

            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        switch (expr)
        {
            case "bank.bic": return _profile.Bank.Bic;
            case "bank.name": return _profile.Bank.Name;
            case "tpp.redirectUri": return _profile.Tpp.RedirectUri;
            case "tpp.id": return _tppId ?? throw new ConfigurationException("{{tpp.id}} needs a QWAC with an organizationIdentifier");
        }

        if (expr.StartsWith("env.", StringComparison.Ordinal))
            return Environment.GetEnvironmentVariable(expr[4..]) ?? throw new ConfigurationException($"Environment variable '{expr[4..]}' is not set");

        if (expr.StartsWith("vars.", StringComparison.Ordinal))
        {
            var matches = JsonPathLite.Select(_profile.Variables, "$." + expr[5..]);
            if (matches.Count == 0)
                throw new ConfigurationException($"Profile variable '{expr[5..]}' is not defined in {_profile.SourcePath}");
            return JsonPathLite.AsText(matches[0]) ?? string.Empty;
        }

        if (_saved.TryGetValue(expr, out var saved))
            return saved;

        throw new ConfigurationException($"Unknown variable '{{{{{expr}}}}}'");
    }

    [GeneratedRegex(@"\{\{([^{}]+)\}\}")]
    private static partial Regex PlaceholderRegex();

    [GeneratedRegex(@"^date\.today(?:([+-])(\d+)d)?$")]
    private static partial Regex DateRegex();
}
