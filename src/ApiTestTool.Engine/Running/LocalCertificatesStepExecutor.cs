using ApiTestTool.Engine.Certificates;
using ApiTestTool.Engine.Scenarios;

namespace ApiTestTool.Engine.Running;

/// <summary>Checks our own TPP certificates before anything is sent to the bank.</summary>
internal static class LocalCertificatesStepExecutor
{
    public static void Execute(Step step, RunContext ctx, StepResult result)
    {
        var spec = step.LocalCertificates!;
        var now = ctx.UtcNow();

        if (ctx.QwacInfo is null)
            result.Failures.Add("No QWAC configured (tpp.qwac) — mutual TLS is not possible");
        else
            Check("QWAC", ctx.QwacInfo);

        if (ctx.QsealcInfo is null)
        {
            if (spec.RequireQsealc)
                result.Failures.Add("No QSealC configured (tpp.qsealc) — requests cannot be signed");
        }
        else
        {
            Check("QSealC", ctx.QsealcInfo);
        }

        if (ctx.QwacInfo?.OrganizationId is { } qwacId && ctx.QsealcInfo?.OrganizationId is { } sealId && qwacId != sealId)
            result.Failures.Add($"QWAC and QSealC belong to different organisations: {qwacId} vs {sealId}");

        void Check(string name, Psd2CertificateInfo info)
        {
            result.Info.Add($"{name}: {info.Subject} ({info.KeyAlgorithm}, valid until {info.NotAfterUtc:yyyy-MM-dd})");

            if (!info.HasPrivateKey)
                result.Failures.Add($"{name} has no private key");

            if (!info.IsValidAt(now))
                result.Failures.Add($"{name} is not valid now (valid {info.NotBeforeUtc:yyyy-MM-dd} – {info.NotAfterUtc:yyyy-MM-dd})");
            else if (info.DaysToExpiry(now) < spec.WarnDaysBeforeExpiry)
                result.Warnings.Add($"{name} expires in {info.DaysToExpiry(now)} days");

            if (info.OrganizationId is null)
                result.Warnings.Add($"{name} has no organizationIdentifier (OID 2.5.4.97); banks use it as the TPP id");
            else
                result.Info.Add($"{name} TPP id: {info.OrganizationId}");

            if (info.KeyAlgorithm is not ("RSA" or "ECDSA"))
                result.Failures.Add($"{name} key algorithm {info.KeyAlgorithm} is not supported");
        }
    }
}
