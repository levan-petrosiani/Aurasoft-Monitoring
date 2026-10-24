using ApiTestTool.Portal.Components;
using ApiTestTool.Portal.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<PortalOptions>(builder.Configuration.GetSection(PortalOptions.Section));
builder.Services.AddSingleton<CatalogService>();
builder.Services.AddSingleton<MockBankService>();
builder.Services.AddSingleton<RunService>();
builder.Services.AddHostedService(sp => sp.GetRequiredService<RunService>());

builder.Services.AddRazorComponents().AddInteractiveServerComponents();

var app = builder.Build();

if (!app.Environment.IsDevelopment())
    app.UseExceptionHandler("/error", createScopeForErrors: true);

app.UseAntiforgery();
app.MapStaticAssets();

// Report files of a run (report.html, summary.json, junit.xml).
app.MapGet("/runs/{id}/files/{file}", (string id, string file, RunService runs) =>
{
    var allowed = new Dictionary<string, string>
    {
        ["report.html"] = "text/html",
        ["summary.json"] = "application/json",
        ["junit.xml"] = "application/xml",
        ["run.json"] = "application/json",
    };

    if (!allowed.TryGetValue(file, out var contentType) || runs.Get(id) is null)
        return Results.NotFound();

    var path = Path.Combine(runs.RunDirectory(id), file);
    return File.Exists(path) ? Results.File(path, contentType) : Results.NotFound();
});

app.MapRazorComponents<App>().AddInteractiveServerRenderMode();

app.Run();
