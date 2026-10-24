using ApiTestTool.Store.Masking;

namespace ApiTestTool.Store.Tests;

public class MaskingTests
{
    private readonly SensitiveDataMasker _masker = new();

    [Theory]
    [InlineData("Authorization", "Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig", "Bearer ***(masked)")]
    [InlineData("authorization", "Bearer 2c9e20322e7a…(masked)", "Bearer ***(masked)")] // engine's partial mask is masked fully
    [InlineData("PSU-ID", "user-12345", "***(masked)")]
    [InlineData("X-Request-ID", "6f1c0c1e-0000-4000-8000-000000000000", "6f1c0c1e-0000-4000-8000-000000000000")]
    public void Masks_secret_headers_only(string name, string value, string expected) =>
        Assert.Equal(expected, _masker.MaskHeader(name, value));

    [Fact]
    public void Masks_tokens_in_json_at_any_depth_and_keeps_other_fields()
    {
        var masked = _masker.MaskBody("""{"access_token":"abc.def.ghi","token_type":"Bearer","nested":{"refresh_token":"r-123"},"expires_in":3600}""");

        Assert.Equal("""{"access_token":"***(masked)","token_type":"Bearer","nested":{"refresh_token":"***(masked)"},"expires_in":3600}""", masked);
    }

    [Fact]
    public void Keeps_xs2a_error_codes_readable()
    {
        var masked = _masker.MaskBody("""{"code":"FORMAT_ERROR","tppMessages":[{"category":"ERROR","code":"CONSENT_UNKNOWN"}]}""");

        Assert.Contains("FORMAT_ERROR", masked);
        Assert.Contains("CONSENT_UNKNOWN", masked);
    }

    [Fact]
    public void Masks_form_bodies_and_query_strings()
    {
        var form = _masker.MaskBody("grant_type=authorization_code&code=SplxlOBeZQQYbYS6WxSbIA&code_verifier=dBjftJeZ4CVP&client_id=PSDGE-NBG-X");
        Assert.DoesNotContain("SplxlOBeZQQYbYS6WxSbIA", form);
        Assert.DoesNotContain("dBjftJeZ4CVP", form);
        Assert.Contains("grant_type=authorization_code", form);
        Assert.Contains("client_id=PSDGE-NBG-X", form);

        var url = _masker.MaskUrl("http://localhost:5199/callback?code=SplxlOBeZQQYbYS6WxSbIA&state=xyz");
        Assert.DoesNotContain("SplxlOBeZQQYbYS6WxSbIA", url);
        Assert.EndsWith("&state=xyz", url);
    }

    [Fact]
    public void Masks_ibans_but_keeps_country_check_digits_and_last_four()
    {
        var masked = _masker.MaskBody("""{"account":{"iban":"GE29NB0000000101904917"},"bic":"TBCBGE22"}""");

        Assert.Contains("GE29**************4917", masked);
        Assert.Contains("TBCBGE22", masked);
    }

    [Fact]
    public void Iban_masking_can_be_turned_off()
    {
        var masker = new SensitiveDataMasker(new MaskingOptions { MaskIbans = false });
        Assert.Contains("GE29NB0000000101904917", masker.MaskBody("""{"iban":"GE29NB0000000101904917"}"""));
    }

    [Fact]
    public void Plain_text_and_invalid_json_are_handled()
    {
        Assert.Equal("upstream timeout", _masker.MaskBody("upstream timeout"));
        Assert.Equal("{not json GE29**************4917", _masker.MaskBody("{not json GE29NB0000000101904917"));
        Assert.Null(_masker.MaskBody(null));
    }

    [Fact]
    public void Cap_cuts_long_text()
    {
        var (text, truncated) = SensitiveDataMasker.Cap(new string('x', 20), 10);
        Assert.True(truncated);
        Assert.StartsWith("xxxxxxxxxx…", text);
        Assert.Equal(("short", false), SensitiveDataMasker.Cap("short", 10));
    }
}
