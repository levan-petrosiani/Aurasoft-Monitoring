using ApiTestTool.Portal.Components;
using ApiTestTool.Portal.Services;
using ApiTestTool.AI;
using ApiTestTool.Store;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<PortalOptions>(builder.Configuration.GetSection(PortalOptions.Section));
builder.Services.AddSingleton<CatalogService>();
builder.Services.AddSingleton<MockBankService>();
builder.Services.AddSingleton<RunService>();

// PostgreSQL store (optional): every run, scenario result, exchange and event is also written there.
if (builder.Configuration.GetConnectionString("Aqt") is { Length: > 0 } connection)
{
    builder.Services.AddSingleton(new AqtStore(connection, builder.Configuration.GetSection("ApiTestTool:Store").Get<StoreOptions>()));
    builder.Services.AddSingleton<StoreRecorder>();
    builder.Services.AddHostedService(sp => sp.GetRequiredService<StoreRecorder>());

    // AI run summaries need the store (they read runs from it and save summaries to it).
    builder.Services.Configure<AiOptions>(builder.Configuration.GetSection(AiOptions.Section));
    builder.Services.Configure<SummaryOptions>(builder.Configuration.GetSection(AiOptions.Section + ":Summary"));
    builder.Services.AddSingleton<SummaryService>();
}

builder.Services.AddHostedService(sp => sp.GetRequiredService<RunService>());

builder.Services.AddRazorComponents().AddInteractiveServerComponents();

var app = builder.Build();

if (app.Services.GetService<AqtStore>() is { } store && app.Configuration.GetValue<bool>("ApiTestTool:Store:MigrateOnStartup"))
{
    try
    {
        await store.MigrateAsync();
    }
    catch (Exception ex)
    {
        // The portal still works without the database (runs are kept as files); writes are retried per run.
        app.Logger.LogError(ex, "Could not migrate the database; is it running? (docker compose up -d)");
    }
}

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
