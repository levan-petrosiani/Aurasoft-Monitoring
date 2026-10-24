using System;
using System.Collections.Generic;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;
using Pgvector;

#nullable disable

namespace ApiTestTool.Store.Migrations
{
    /// <inheritdoc />
    public partial class StandardsAndRules : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AlterDatabase()
                .Annotation("Npgsql:PostgresExtension:vector", ",,");

            migrationBuilder.CreateTable(
                name: "clauses",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    standard = table.Column<string>(type: "text", nullable: false),
                    version = table.Column<string>(type: "text", nullable: false),
                    number = table.Column<string>(type: "text", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    text = table.Column<string>(type: "text", nullable: false),
                    level = table.Column<int>(type: "integer", nullable: false),
                    parent = table.Column<string>(type: "text", nullable: true),
                    ord = table.Column<int>(type: "integer", nullable: false),
                    page = table.Column<int>(type: "integer", nullable: true),
                    language = table.Column<string>(type: "text", nullable: false),
                    key = table.Column<string>(type: "text", nullable: false),
                    embedding = table.Column<Vector>(type: "vector(1024)", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_clauses", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "rule_reviews",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    rule_id = table.Column<string>(type: "text", nullable: false),
                    time_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    reviewer = table.Column<string>(type: "text", nullable: false),
                    action = table.Column<string>(type: "text", nullable: false),
                    note = table.Column<string>(type: "text", nullable: true),
                    before = table.Column<string>(type: "jsonb", nullable: true),
                    after = table.Column<string>(type: "jsonb", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_rule_reviews", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "rules",
                columns: table => new
                {
                    id = table.Column<string>(type: "text", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    level = table.Column<string>(type: "text", nullable: false),
                    service = table.Column<string>(type: "text", nullable: false),
                    area = table.Column<string>(type: "text", nullable: false),
                    requirement = table.Column<string>(type: "text", nullable: false),
                    standard = table.Column<string>(type: "text", nullable: false),
                    version = table.Column<string>(type: "text", nullable: false),
                    clause_number = table.Column<string>(type: "text", nullable: false),
                    clause_key = table.Column<string>(type: "text", nullable: false),
                    quote = table.Column<string>(type: "text", nullable: false),
                    quote_english = table.Column<string>(type: "text", nullable: true),
                    operation = table.Column<string>(type: "text", nullable: true),
                    check_json = table.Column<string>(type: "jsonb", nullable: true),
                    notes = table.Column<string>(type: "text", nullable: true),
                    check_notes = table.Column<List<string>>(type: "text[]", nullable: false),
                    similar_to = table.Column<string>(type: "text", nullable: true),
                    drafted_by = table.Column<string>(type: "text", nullable: false),
                    model = table.Column<string>(type: "text", nullable: true),
                    ai_call_id = table.Column<Guid>(type: "uuid", nullable: true),
                    created_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    reviewed_by = table.Column<string>(type: "text", nullable: true),
                    reviewed_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    review_note = table.Column<string>(type: "text", nullable: true),
                    scenarios = table.Column<List<string>>(type: "text[]", nullable: false),
                    embedding = table.Column<Vector>(type: "vector(1024)", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_rules", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "standard_documents",
                columns: table => new
                {
                    id = table.Column<string>(type: "text", nullable: false),
                    version = table.Column<string>(type: "text", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    publisher = table.Column<string>(type: "text", nullable: true),
                    language = table.Column<string>(type: "text", nullable: false),
                    url = table.Column<string>(type: "text", nullable: false),
                    sha256 = table.Column<string>(type: "text", nullable: false),
                    license = table.Column<string>(type: "text", nullable: true),
                    ingested_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    clause_count = table.Column<int>(type: "integer", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_standard_documents", x => new { x.id, x.version });
                });

            migrationBuilder.CreateIndex(
                name: "ix_clauses_key",
                table: "clauses",
                column: "key",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "ix_clauses_standard_version_ord",
                table: "clauses",
                columns: new[] { "standard", "version", "ord" });

            migrationBuilder.CreateIndex(
                name: "ix_rule_reviews_rule_id_time_utc",
                table: "rule_reviews",
                columns: new[] { "rule_id", "time_utc" });

            migrationBuilder.CreateIndex(
                name: "ix_rules_clause_key",
                table: "rules",
                column: "clause_key");

            migrationBuilder.CreateIndex(
                name: "ix_rules_status_standard",
                table: "rules",
                columns: new[] { "status", "standard" });

            // Full-text search over clauses: English stemming for English documents, plain words otherwise
            // (PostgreSQL has no Georgian stemmer). Generated, so it can never be out of date.
            migrationBuilder.Sql("""
                ALTER TABLE clauses ADD COLUMN search tsvector GENERATED ALWAYS AS (
                    CASE WHEN language = 'en'
                        THEN to_tsvector('english', coalesce(number, '') || ' ' || coalesce(title, '') || ' ' || coalesce(text, ''))
                        ELSE to_tsvector('simple', coalesce(number, '') || ' ' || coalesce(title, '') || ' ' || coalesce(text, ''))
                    END) STORED;
                CREATE INDEX ix_clauses_search ON clauses USING gin (search);
                CREATE INDEX ix_clauses_embedding ON clauses USING hnsw (embedding vector_cosine_ops);
                CREATE INDEX ix_rules_embedding ON rules USING hnsw (embedding vector_cosine_ops);
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "clauses");

            migrationBuilder.DropTable(
                name: "rule_reviews");

            migrationBuilder.DropTable(
                name: "rules");

            migrationBuilder.DropTable(
                name: "standard_documents");

            migrationBuilder.AlterDatabase()
                .OldAnnotation("Npgsql:PostgresExtension:vector", ",,");
        }
    }
}
