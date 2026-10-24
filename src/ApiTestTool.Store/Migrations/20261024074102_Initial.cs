using System;
using System.Collections.Generic;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace ApiTestTool.Store.Migrations
{
    /// <inheritdoc />
    public partial class Initial : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "ai_calls",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    time_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    feature = table.Column<string>(type: "text", nullable: false),
                    provider = table.Column<string>(type: "text", nullable: false),
                    model = table.Column<string>(type: "text", nullable: false),
                    run_id = table.Column<string>(type: "text", nullable: true),
                    triggered_by = table.Column<string>(type: "text", nullable: true),
                    prompt_sha256 = table.Column<string>(type: "text", nullable: false),
                    request = table.Column<string>(type: "jsonb", nullable: false),
                    response = table.Column<string>(type: "text", nullable: true),
                    input_tokens = table.Column<long>(type: "bigint", nullable: true),
                    output_tokens = table.Column<long>(type: "bigint", nullable: true),
                    duration_ms = table.Column<long>(type: "bigint", nullable: false),
                    succeeded = table.Column<bool>(type: "boolean", nullable: false),
                    error = table.Column<string>(type: "text", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_ai_calls", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "events",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    time_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    run_id = table.Column<string>(type: "text", nullable: true),
                    source = table.Column<string>(type: "text", nullable: false),
                    type = table.Column<string>(type: "text", nullable: false),
                    level = table.Column<string>(type: "text", nullable: false),
                    message = table.Column<string>(type: "text", nullable: false),
                    data = table.Column<string>(type: "jsonb", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_events", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "runs",
                columns: table => new
                {
                    id = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: false),
                    source = table.Column<string>(type: "text", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false),
                    bank_bic = table.Column<string>(type: "text", nullable: false),
                    bank_name = table.Column<string>(type: "text", nullable: true),
                    xs2a_url = table.Column<string>(type: "text", nullable: true),
                    tpp_id = table.Column<string>(type: "text", nullable: true),
                    tool_version = table.Column<string>(type: "text", nullable: true),
                    created_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    started_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    finished_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    passed = table.Column<int>(type: "integer", nullable: false),
                    failed = table.Column<int>(type: "integer", nullable: false),
                    errors = table.Column<int>(type: "integer", nullable: false),
                    blocked = table.Column<int>(type: "integer", nullable: false),
                    skipped = table.Column<int>(type: "integer", nullable: false),
                    pass_rate = table.Column<double>(type: "double precision", nullable: false),
                    suites = table.Column<List<string>>(type: "text[]", nullable: false),
                    error = table.Column<string>(type: "text", nullable: true),
                    report_directory = table.Column<string>(type: "text", nullable: true),
                    options_json = table.Column<string>(type: "jsonb", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_runs", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "scenario_results",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    run_id = table.Column<string>(type: "character varying(100)", nullable: false),
                    ordinal = table.Column<int>(type: "integer", nullable: false),
                    scenario_id = table.Column<string>(type: "text", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    suite = table.Column<string>(type: "text", nullable: false),
                    severity = table.Column<string>(type: "text", nullable: false),
                    spec_ref = table.Column<string>(type: "text", nullable: true),
                    origin = table.Column<string>(type: "text", nullable: true),
                    outcome = table.Column<string>(type: "text", nullable: false),
                    reason = table.Column<string>(type: "text", nullable: true),
                    duration_ms = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_scenario_results", x => x.id);
                    table.ForeignKey(
                        name: "fk_scenario_results_runs_run_id",
                        column: x => x.run_id,
                        principalTable: "runs",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "step_results",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    scenario_result_id = table.Column<long>(type: "bigint", nullable: false),
                    run_id = table.Column<string>(type: "text", nullable: false),
                    ordinal = table.Column<int>(type: "integer", nullable: false),
                    is_cleanup = table.Column<bool>(type: "boolean", nullable: false),
                    name = table.Column<string>(type: "text", nullable: false),
                    kind = table.Column<string>(type: "text", nullable: false),
                    outcome = table.Column<string>(type: "text", nullable: false),
                    failures = table.Column<List<string>>(type: "text[]", nullable: false),
                    warnings = table.Column<List<string>>(type: "text[]", nullable: false),
                    info = table.Column<List<string>>(type: "text[]", nullable: false),
                    duration_ms = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_step_results", x => x.id);
                    table.ForeignKey(
                        name: "fk_step_results_scenario_results_scenario_result_id",
                        column: x => x.scenario_result_id,
                        principalTable: "scenario_results",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "exchanges",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityByDefaultColumn),
                    step_result_id = table.Column<long>(type: "bigint", nullable: false),
                    run_id = table.Column<string>(type: "text", nullable: false),
                    ordinal = table.Column<int>(type: "integer", nullable: false),
                    method = table.Column<string>(type: "text", nullable: false),
                    url = table.Column<string>(type: "text", nullable: false),
                    status_code = table.Column<int>(type: "integer", nullable: true),
                    duration_ms = table.Column<long>(type: "bigint", nullable: false),
                    error = table.Column<string>(type: "text", nullable: true),
                    started_utc = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    request_headers = table.Column<string>(type: "jsonb", nullable: false),
                    request_body = table.Column<string>(type: "text", nullable: true),
                    response_headers = table.Column<string>(type: "jsonb", nullable: false),
                    response_body = table.Column<string>(type: "text", nullable: true),
                    truncated = table.Column<bool>(type: "boolean", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("pk_exchanges", x => x.id);
                    table.ForeignKey(
                        name: "fk_exchanges_step_results_step_result_id",
                        column: x => x.step_result_id,
                        principalTable: "step_results",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "ix_ai_calls_feature",
                table: "ai_calls",
                column: "feature");

            migrationBuilder.CreateIndex(
                name: "ix_ai_calls_run_id",
                table: "ai_calls",
                column: "run_id");

            migrationBuilder.CreateIndex(
                name: "ix_ai_calls_time_utc",
                table: "ai_calls",
                column: "time_utc");

            migrationBuilder.CreateIndex(
                name: "ix_events_run_id",
                table: "events",
                column: "run_id");

            migrationBuilder.CreateIndex(
                name: "ix_events_time_utc",
                table: "events",
                column: "time_utc");

            migrationBuilder.CreateIndex(
                name: "ix_events_type",
                table: "events",
                column: "type");

            migrationBuilder.CreateIndex(
                name: "ix_exchanges_run_id",
                table: "exchanges",
                column: "run_id");

            migrationBuilder.CreateIndex(
                name: "ix_exchanges_started_utc",
                table: "exchanges",
                column: "started_utc");

            migrationBuilder.CreateIndex(
                name: "ix_exchanges_status_code",
                table: "exchanges",
                column: "status_code");

            migrationBuilder.CreateIndex(
                name: "ix_exchanges_step_result_id",
                table: "exchanges",
                column: "step_result_id");

            migrationBuilder.CreateIndex(
                name: "ix_runs_bank_bic_started_utc",
                table: "runs",
                columns: new[] { "bank_bic", "started_utc" });

            migrationBuilder.CreateIndex(
                name: "ix_runs_created_utc",
                table: "runs",
                column: "created_utc");

            migrationBuilder.CreateIndex(
                name: "ix_scenario_results_run_id",
                table: "scenario_results",
                column: "run_id");

            migrationBuilder.CreateIndex(
                name: "ix_scenario_results_scenario_id_outcome",
                table: "scenario_results",
                columns: new[] { "scenario_id", "outcome" });

            migrationBuilder.CreateIndex(
                name: "ix_step_results_run_id",
                table: "step_results",
                column: "run_id");

            migrationBuilder.CreateIndex(
                name: "ix_step_results_scenario_result_id",
                table: "step_results",
                column: "scenario_result_id");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "ai_calls");

            migrationBuilder.DropTable(
                name: "events");

            migrationBuilder.DropTable(
                name: "exchanges");

            migrationBuilder.DropTable(
                name: "step_results");

            migrationBuilder.DropTable(
                name: "scenario_results");

            migrationBuilder.DropTable(
                name: "runs");
        }
    }
}
