-- 0002: test scenarios drafted by AI from approved rules (Phase 3). A draft is checked in code (loads with the engine,
-- dry run on MockBank) and only becomes a file in scenarios/ when a person approves it.
CREATE TABLE scenario_drafts (
    id uuid PRIMARY KEY,
    rule_id text NOT NULL,
    scenario_id text NOT NULL,
    suite text NOT NULL,
    title text NOT NULL,
    yaml text NOT NULL,
    -- draft (passed the checks, waiting for review) | failed (no valid scenario after the attempts) | approved | rejected
    status text NOT NULL,
    attempts integer NOT NULL,
    check_notes text[] NOT NULL DEFAULT '{}',
    -- Outcome of the dry run on MockBank: Passed | Failed | Error | Skipped | Blocked; null if it never got that far.
    dry_run_outcome text,
    dry_run jsonb,
    -- The model's explanation when it believes MockBank does not implement the rule (the scenario is then right and
    -- MockBank needs work); a reviewer decides.
    mock_gap text,
    model text,
    ai_call_ids uuid[] NOT NULL DEFAULT '{}',
    drafted_by text NOT NULL,
    created_utc timestamptz NOT NULL,
    updated_utc timestamptz NOT NULL,
    reviewed_by text,
    reviewed_utc timestamptz,
    review_note text,
    file_path text
);

CREATE INDEX ix_scenario_drafts_rule_id ON scenario_drafts (rule_id);
CREATE INDEX ix_scenario_drafts_status ON scenario_drafts (status, created_utc);
