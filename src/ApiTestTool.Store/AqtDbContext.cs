using ApiTestTool.Store.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace ApiTestTool.Store;

/// <summary>PostgreSQL schema of the store. Tables and columns are snake_case so they are easy to query by hand (and by the ask-agent).</summary>
public sealed class AqtDbContext(DbContextOptions<AqtDbContext> options) : DbContext(options)
{
    public DbSet<RunEntity> Runs => Set<RunEntity>();

    public DbSet<ScenarioResultEntity> ScenarioResults => Set<ScenarioResultEntity>();

    public DbSet<StepResultEntity> StepResults => Set<StepResultEntity>();

    public DbSet<ExchangeEntity> Exchanges => Set<ExchangeEntity>();

    public DbSet<EventEntity> Events => Set<EventEntity>();

    public DbSet<AiCallEntity> AiCalls => Set<AiCallEntity>();

    /// <summary>Options for a connection string (snake_case naming, retries on transient errors).</summary>
    public static DbContextOptions<AqtDbContext> CreateOptions(string connectionString) =>
        Configure(new DbContextOptionsBuilder<AqtDbContext>(), connectionString).Options;

    internal static DbContextOptionsBuilder<AqtDbContext> Configure(DbContextOptionsBuilder<AqtDbContext> builder, string connectionString) =>
        builder
            .UseNpgsql(connectionString, npgsql => npgsql.EnableRetryOnFailure(3))
            .UseSnakeCaseNamingConvention();

    protected override void OnModelCreating(ModelBuilder model)
    {
        model.Entity<RunEntity>(run =>
        {
            run.ToTable("runs");
            run.HasKey(r => r.Id);
            run.Property(r => r.Id).HasMaxLength(100);
            run.Property(r => r.OptionsJson).HasColumnType("jsonb");
            run.HasIndex(r => new { r.BankBic, r.StartedUtc });
            run.HasIndex(r => r.CreatedUtc);
            run.HasMany(r => r.Scenarios).WithOne(s => s.Run).HasForeignKey(s => s.RunId).OnDelete(DeleteBehavior.Cascade);
        });

        model.Entity<ScenarioResultEntity>(s =>
        {
            s.ToTable("scenario_results");
            s.HasIndex(x => x.RunId);
            s.HasIndex(x => new { x.ScenarioId, x.Outcome });
            s.HasMany(x => x.Steps).WithOne(st => st.Scenario).HasForeignKey(st => st.ScenarioResultId).OnDelete(DeleteBehavior.Cascade);
        });

        model.Entity<StepResultEntity>(st =>
        {
            st.ToTable("step_results");
            st.HasIndex(x => x.RunId);
            st.HasMany(x => x.Exchanges).WithOne(e => e.Step).HasForeignKey(e => e.StepResultId).OnDelete(DeleteBehavior.Cascade);
        });

        model.Entity<ExchangeEntity>(e =>
        {
            e.ToTable("exchanges");
            e.Property(x => x.RequestHeaders).HasColumnType("jsonb");
            e.Property(x => x.ResponseHeaders).HasColumnType("jsonb");
            e.HasIndex(x => x.RunId);
            e.HasIndex(x => x.StatusCode);
            e.HasIndex(x => x.StartedUtc);
        });

        model.Entity<EventEntity>(ev =>
        {
            ev.ToTable("events");
            ev.Property(x => x.Data).HasColumnType("jsonb");
            ev.HasIndex(x => x.TimeUtc);
            ev.HasIndex(x => x.RunId);
            ev.HasIndex(x => x.Type);
        });

        model.Entity<AiCallEntity>(c =>
        {
            c.ToTable("ai_calls");
            c.Property(x => x.Request).HasColumnType("jsonb");
            c.HasIndex(x => x.TimeUtc);
            c.HasIndex(x => x.Feature);
            c.HasIndex(x => x.RunId);
        });
    }
}

/// <summary>Used by <c>dotnet ef</c> to create migrations: connection from <c>AQT_DB</c> or the docker-compose default.</summary>
internal sealed class AqtDbContextDesignTimeFactory : IDesignTimeDbContextFactory<AqtDbContext>
{
    public AqtDbContext CreateDbContext(string[] args) =>
        new(AqtDbContext.CreateOptions(StoreConnection.FromEnvironment() ?? StoreConnection.DockerComposeDefault));
}
