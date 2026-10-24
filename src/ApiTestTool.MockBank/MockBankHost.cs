using System.Globalization;
using System.Net;
using System.Text;
using ApiTestTool.Engine.Certificates;
using Microsoft.AspNetCore.Server.Kestrel.Https;
using Microsoft.Extensions.Options;

namespace ApiTestTool.MockBank;

/// <summary>
/// Builds the MockBank web application. Used by MockBank's own Program and by <c>aqt --with-mock</c> / <c>aqt mock</c>,
/// which host it inside the CLI process so a single IDE launch is enough.
/// </summary>
public static class MockBankHost
{
    /// <param name="args">Command-line style configuration overrides, e.g. <c>--MockBank:AutoApprove=never</c>.</param>
    /// <param name="quiet">Hide per-request logging and the startup banner (when running inside aqt).</param>
    public static WebApplication Create(string[] args, bool quiet = false)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            Args = args,
            ApplicationName = typeof(MockBankHost).Assembly.GetName().Name,
            ContentRootPath = AppContext.BaseDirectory,
        });

        builder.Configuration.AddJsonFile(Path.Combine(AppContext.BaseDirectory, "mockbank.settings.json"), optional: true);
        builder.Configuration.AddEnvironmentVariables();
        builder.Configuration.AddCommandLine(args);

        if (quiet)
            builder.Logging.SetMinimumLevel(LogLevel.Warning);

        builder.Services.Configure<MockBankOptions>(builder.Configuration.GetSection(MockBankOptions.Section));
        var options = builder.Configuration.GetSection(MockBankOptions.Section).Get<MockBankOptions>() ?? new MockBankOptions();

        // The bank's own certificates: TLS server certificate and the QSealC for response signatures.
        var certificates = new BankCertificates(
            DevCertificates.CreateLocalhostServerCertificate(options.BankOrganizationId),
            DevCertificates.CreateTppCertificate("ApiTestTool MockBank seal", options.BankOrganizationId));
        builder.Services.AddSingleton(certificates);
        builder.Services.AddSingleton<MockState>();

        builder.WebHost.ConfigureKestrel(kestrel =>
        {
            // XS2A, discovery and token endpoint: TLS with an optional client certificate (checked by the middleware,
            // so missing certificates get a proper XS2A error instead of a failed handshake).
            kestrel.ListenLocalhost(options.HttpsPort, listen => listen.UseHttps(new HttpsConnectionAdapterOptions
            {
                ServerCertificate = certificates.Server,
                ClientCertificateMode = ClientCertificateMode.AllowCertificate,
                ClientCertificateValidation = (_, _, _) => true, // any TPP certificate is accepted, as in a sandbox with test CAs
            }));

            // Browser-facing pages (OAuth login, dashboard): plain HTTP, so browsers show no certificate prompts.
            kestrel.ListenLocalhost(options.HttpPort);
        });

        var app = builder.Build();

        if (!quiet)
        {
            app.Use(async (context, next) =>
            {
                await next();
                var tpp = context.Items.TryGetValue(Xs2aSecurityMiddleware.TppIdItem, out var id) ? $" [{id}]" : string.Empty;
                app.Logger.LogInformation("{Method} {Path}{Query} -> {Status}{Tpp}", context.Request.Method, context.Request.Path, context.Request.QueryString, context.Response.StatusCode, tpp);
            });
        }
        app.UseMiddleware<Xs2aSecurityMiddleware>();

        ConsentEndpoints.Map(app);
        AccountEndpoints.Map(app);
        OAuthEndpoints.Map(app);

        app.MapGet("/", (MockState state, IOptions<MockBankOptions> o) => Results.Content(Dashboard(state, o.Value), "text/html"));

        if (!quiet)
            app.Lifetime.ApplicationStarted.Register(() => PrintBanner(options));

        return app;
    }

    private static void PrintBanner(MockBankOptions options)
    {
        Console.WriteLine();
        Console.WriteLine("  ApiTestTool MockBank is running (in-memory, certificates generated for this run)");
        Console.WriteLine($"    XS2A + token (mTLS) : {options.HttpsBaseUrl}/v2/...");
        Console.WriteLine($"    OAuth discovery     : {options.HttpsBaseUrl}/.well-known/oauth-authorization-server");
        Console.WriteLine($"    Login page          : {options.HttpBaseUrl}/oauth/authorize   (auto-approve: {options.AutoApprove})");
        Console.WriteLine($"    Dashboard           : {options.HttpBaseUrl}/");
        Console.WriteLine();
        Console.WriteLine("  Test it with:  aqt run --bank MOCK --sca-auto");
        Console.WriteLine();
    }

    private static string Dashboard(MockState state, MockBankOptions o)
    {
        static string E(string s) => WebUtility.HtmlEncode(s);

        var rows = new StringBuilder();
        foreach (var c in state.Consents.Values.OrderByDescending(c => c.CreatedUtc).Take(50))
            rows.Append(CultureInfo.InvariantCulture, $"<tr><td>{c.CreatedUtc:HH:mm:ss}</td><td><code>{E(c.Id)}</code></td><td>{E(c.TppId)}</td><td>{E(c.ConsentType)}</td><td>{E(c.Status)}</td></tr>");

        return $$"""
            <!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="5"><title>MockBank</title>
            <style>body{font:14px/1.5 system-ui,sans-serif;margin:24px auto;max-width:960px;padding:0 16px}
            table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e5e5ea;padding:6px;text-align:left}code{font-size:12px}</style></head><body>
            <h1>ApiTestTool MockBank</h1>
            <p>XS2A: <code>{{o.HttpsBaseUrl}}/v2</code> · Discovery: <code>{{o.HttpsBaseUrl}}/.well-known/oauth-authorization-server</code> · Auto-approve: <b>{{o.AutoApprove}}</b></p>
            <p>{{state.Consents.Count}} consent(s), {{state.AccessTokens.Count}} access token(s). Refreshes every 5 s.</p>
            <table><thead><tr><th>Created (UTC)</th><th>Consent</th><th>TPP</th><th>Type</th><th>Status</th></tr></thead><tbody>{{rows}}</tbody></table>
            </body></html>
            """;
    }
}
