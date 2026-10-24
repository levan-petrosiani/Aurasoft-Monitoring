using System;
using System.Collections.Generic;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace ApiTestTool.Store.Migrations
{
    /// <inheritdoc />
    public partial class RunSummaries : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "run_summaries",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    run_id = table.Column<string>(type: "text", nullable: false),
                    created_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    provider = table.Column<string>(type: "text", nullable: false),
                    model = table.Column<string>(type: "text", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false),
                    summary_json = table.Column<string>(type: "jsonb", nullable: true),
                    text = table.Column<string>(type: "text", nullable: true),
                    validation_notes = table.Column<List<string>>(type: "text[]", nullable: false),
                    ai_call_id = table.Column<Guid>(type: "uuid", nullable: true),
                    triggered_by = table.Column<string>(type: "text", nullable: true),
                    duration_ms = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_run_summaries", x => x.id);
                });

            migrationBuilder.CreateIndex(
                name: "ix_run_summaries_run_id_created_utc",
                table: "run_summaries",
                columns: new[] { "run_id", "created_utc" });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "run_summaries");
        }
    }
}
