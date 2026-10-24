using System.Text.Json.Nodes;
using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Running;
using ApiTestTool.Engine.Yaml;

namespace ApiTestTool.Engine.Tests;

public class YamlAndVariableTests
{
    [Fact]
    public void Plain_scalars_are_typed_and_quoted_scalars_stay_strings()
    {
        var node = YamlJson.Parse("""
            count: 4
            quoted: "4"
            flag: true
            nothing: null
            ip: 192.168.1.1
            ratio: 1.5
            list: [a, 2]
            """, "test")!;

        Assert.Equal(4L, node["count"]!.GetValue<long>());
        Assert.Equal("4", node["quoted"]!.GetValue<string>());
        Assert.True(node["flag"]!.GetValue<bool>());
        Assert.Null(node["nothing"]);
        Assert.True(((JsonObject)node).ContainsKey("nothing"));
        Assert.Equal("192.168.1.1", node["ip"]!.GetValue<string>());
        Assert.Equal(1.5m, node["ratio"]!.GetValue<decimal>());
        Assert.Equal(2L, node["list"]![1]!.GetValue<long>());
    }

    [Theory]
    [InlineData("{{date.today}}", "2026-10-20")]
    [InlineData("{{date.today+2d}}", "2026-10-22")]
    [InlineData("{{date.today-5d}}", "2026-10-15")]
    [InlineData("v2/{{bank.bic}}/x", "v2/TESTGE22/x")]
    [InlineData("{{vars.accounts.gel}}", "GE00TS0000000000000001")]
    [InlineData("{{tpp.redirectUri}}", "http://localhost:5199/callback")]
    public void Placeholders_are_resolved(string input, string expected)
    {
        Assert.Equal(expected, NewResolver().Resolve(input));
    }

    [Fact]
    public void Saved_values_are_available_to_later_steps()
    {
        var resolver = NewResolver();
        resolver.Save("consentId", "abc-123");
        Assert.Equal("v2/consents/account-access/abc-123/status", resolver.Resolve("v2/consents/account-access/{{consentId}}/status"));
    }

    [Fact]
    public void Unknown_variable_is_a_configuration_error()
    {
        Assert.Throws<ConfigurationException>(() => NewResolver().Resolve("{{nope}}"));
    }

    [Fact]
    public void Json_body_strings_are_resolved_but_numbers_are_kept()
    {
        var body = JsonNode.Parse("""{"validTo":"{{date.today+2d}}","frequencyPerDay":4}""");
        var resolved = NewResolver().Resolve(body)!;
        Assert.Equal("""{"validTo":"2026-10-22","frequencyPerDay":4}""", resolved.ToJsonString());
    }

    [Fact]
    public void Json_path_supports_wildcards_and_indexes()
    {
        var json = JsonNode.Parse("""{"additionalErrors":[{"code":"A"},{"code":"B"}],"_links":{"scaOAuth":{"href":"x"}}}""");
        Assert.Equal(["A", "B"], JsonPathLite.Select(json, "$.additionalErrors[*].code").Select(JsonPathLite.AsText));
        Assert.Equal("B", JsonPathLite.AsText(JsonPathLite.Select(json, "$.additionalErrors[1].code")[0]));
        Assert.Equal("x", JsonPathLite.AsText(JsonPathLite.Select(json, "$._links['scaOAuth'].href")[0]));
        Assert.Empty(JsonPathLite.Select(json, "$.missing.path"));
    }

    internal static BankProfile NewProfile() => new()
    {
        Bank = new BankInfo { Bic = "TESTGE22", Name = "Test Bank" },
        Endpoints = new EndpointSettings { Xs2a = "https://xs2a.test.local/" },
        Variables = new JsonObject { ["accounts"] = new JsonObject { ["gel"] = "GE00TS0000000000000001" } },
    };

    private static VariableResolver NewResolver() =>
        new(NewProfile(), "PSDGE-NBG-TEST01", () => new DateTime(2026, 10, 20, 23, 30, 0, DateTimeKind.Utc));
}
