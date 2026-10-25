-- 0004: monitoring (Phase 6). Schedules are declared in monitoring.yaml; the store keeps when each last ran, and the
-- alerts computed in code after every saved run (regression, fixed, bank down, run failed).
CREATE TABLE monitor_state (
    name text PRIMARY KEY,            -- a schedule name, or __retention__ for the daily prune
    last_started_utc timestamptz NOT NULL,
    last_run_id text,
    last_status text,
    last_error text
);

CREATE TABLE alerts (
    id uuid PRIMARY KEY,
    created_utc timestamptz NOT NULL,
    run_id text NOT NULL,
    previous_run_id text,
    bank_bic text NOT NULL,
    -- regression (Passed -> Failed/Error) | fixed (Failed/Error -> Passed) | bank_down (critical preflight failed) | run_failed
    kind text NOT NULL,
    scenario_id text,
    before text,
    after text,
    message text NOT NULL,
    notified_utc timestamptz,
    notify_error text,
    acknowledged_by text,
    acknowledged_utc timestamptz,
    UNIQUE (run_id, kind, scenario_id)
);
CREATE INDEX ix_alerts_created_utc ON alerts (created_utc DESC);
CREATE INDEX ix_alerts_open ON alerts (created_utc DESC) WHERE acknowledged_utc IS NULL;

CREATE VIEW agent.alerts AS
    SELECT id, created_utc, run_id, previous_run_id, bank_bic, kind, scenario_id, before, after, message,
           acknowledged_by, acknowledged_utc
    FROM public.alerts;
GRANT SELECT ON agent.alerts TO aqt_agent;
