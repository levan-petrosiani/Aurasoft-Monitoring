-- 0003: the ask-agent (Phase 4). Its SQL tool LOGS IN as role aqt_agent, which can read only the views in schema
-- `agent` (no request/response headers or bodies, no AI audit, no embeddings), one statement per call, in a read-only
-- transaction with a timeout. The database enforces this; the tool does not try to filter SQL text. (SET ROLE from the
-- main user is not enough: the agent's SQL could switch back with set_config('role', ...).)
-- The password is set by `aqt db migrate` from AQT_AGENT_DB_PASSWORD (dev default aqt_agent_dev).
CREATE SCHEMA IF NOT EXISTS agent;

CREATE VIEW agent.runs AS
    SELECT id, source, status, bank_bic, bank_name, created_utc, started_utc, finished_utc,
           passed, failed, errors, blocked, skipped, pass_rate, suites, error
    FROM public.runs;
CREATE VIEW agent.scenario_results AS
    SELECT s.id, s.run_id, r.bank_bic, coalesce(r.started_utc, r.created_utc) AS run_started_utc, s.ordinal, s.scenario_id,
           s.title, s.suite, s.severity, s.spec_ref, s.outcome, s.reason, s.duration_ms
    FROM public.scenario_results s JOIN public.runs r ON r.id = s.run_id;
CREATE VIEW agent.step_results AS
    SELECT id, scenario_result_id, run_id, ordinal, is_cleanup, name, kind, outcome, failures, warnings, duration_ms
    FROM public.step_results;
CREATE VIEW agent.exchanges AS
    SELECT id, step_result_id, run_id, ordinal, method, url, status_code, duration_ms, error, started_utc
    FROM public.exchanges;
CREATE VIEW agent.events AS
    SELECT id, time_utc, run_id, source, type, level, message FROM public.events;
CREATE VIEW agent.run_summaries AS
    SELECT id, run_id, created_utc, provider, model, status, summary_json->>'headline' AS headline, summary_json->>'health' AS health
    FROM public.run_summaries;
CREATE VIEW agent.rules AS
    SELECT id, status, title, level, service, area, requirement, standard, version, clause_number, clause_key, operation,
           scenarios, reviewed_by, reviewed_utc
    FROM public.rules;
CREATE VIEW agent.clauses AS
    SELECT key, standard, version, number, title, level, language, page FROM public.clauses;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aqt_agent') THEN
        CREATE ROLE aqt_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    ELSE
        ALTER ROLE aqt_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;
GRANT USAGE ON SCHEMA agent TO aqt_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA agent TO aqt_agent;

-- One row per question: the answer, the tools the agent called (arguments and a preview of each result), and the
-- citation check (ids in the answer that never appeared in a tool result).
CREATE TABLE agent_questions (
    id uuid PRIMARY KEY,
    asked_utc timestamptz NOT NULL,
    asked_by text,
    question text NOT NULL,
    answer text,
    -- ok | unverified (an id in the answer was not in any tool result) | failed
    status text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    tool_calls jsonb NOT NULL DEFAULT '[]',
    citation_notes text[] NOT NULL DEFAULT '{}',
    ai_call_ids uuid[] NOT NULL DEFAULT '{}',
    error text,
    duration_ms bigint NOT NULL DEFAULT 0
);
CREATE INDEX ix_agent_questions_asked_utc ON agent_questions (asked_utc DESC);
