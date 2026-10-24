using ApiTestTool.Engine.Profiles;
using ApiTestTool.Engine.Scenarios;

namespace ApiTestTool.Engine.Tests;

/// <summary>Keeps the shipped YAML (profiles, templates, scenarios) loadable.</summary>
public class ContentFilesTests
{
    [Fact]
    public void All_scenarios_load_and_reference_existing_templates()
    {
        var templates = TemplateLibrary.Load(RepoPaths.Scenarios);
        var scenarios = ScenarioLoader.LoadAll(RepoPaths.Scenarios);

        Assert.NotEmpty(scenarios);
        foreach (var request in scenarios.SelectMany(s => s.Steps.Concat(s.Cleanup ?? [])).Select(s => s.Request).OfType<RequestSpec>())
        {
            if (request.HeadersTemplate is not null)
                Assert.Contains(request.HeadersTemplate, templates.HeaderSetNames);
            if (request.BodyTemplate is not null)
                Assert.Contains(request.BodyTemplate, templates.BodyNames);
        }
    }

    [Fact]
    public void All_bank_profiles_load_with_shared_settings()
    {
        var files = BankProfileLoader.List(RepoPaths.Banks).ToList();
        Assert.NotEmpty(files);

        // Self-contained profiles (MOCK, TBC-MOCK: no `extends`) use generated certificates and their own limits.
        foreach (var file in files.Where(f => File.ReadAllText(f).Contains("extends:", StringComparison.Ordinal)))
        {
            var profile = BankProfileLoader.LoadFile(file);
            Assert.NotNull(profile.Tpp.Qwac);           // from _shared.yaml
            Assert.Equal(200, profile.Limits.DelayBetweenRequestsMs);
        }
    }

    [Fact]
    public void Mock_profile_generates_tpp_certificates_with_an_organization_id()
    {
        var mock = BankProfileLoader.Load("MOCK", RepoPaths.Banks);
        using var qwac = Certificates.CertificateLoader.Load(mock.Tpp.Qwac!, RepoPaths.Banks, "tpp.qwac");

        Assert.True(qwac.HasPrivateKey);
        Assert.Equal("PSDGE-NBG-MOCKTPP", Certificates.Psd2CertificateInfo.From(qwac).OrganizationId);
    }

    [Fact]
    public void Bank_overrides_win_over_shared_settings()
    {
        var local = BankProfileLoader.Load("LOCAL", RepoPaths.Banks);
        Assert.False(local.Tpp.ValidateServerCertificate);
        Assert.Equal("../certs/qwac.pfx", local.Tpp.Qwac!.File);
    }
}
