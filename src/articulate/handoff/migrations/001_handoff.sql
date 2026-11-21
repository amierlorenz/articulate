-- Migration for handoff package (§3.7, §3.4)

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    answered_at TEXT NOT NULL,
    answer_text TEXT,
    grade INTEGER NOT NULL,
    ms_taken INTEGER,
    session_kind TEXT,
    calibrated INTEGER DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_prompt ON attempts (prompt_id);
CREATE INDEX IF NOT EXISTS idx_attempts_concept ON attempts (concept_id);

CREATE TABLE IF NOT EXISTS handoff_card_flags (
    flag_id TEXT PRIMARY KEY,
    prompt_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    note TEXT,
    suggested_fix TEXT,
    flagged_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued', -- queued | regenerated | listed
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_handoff_card_flags_state ON handoff_card_flags (state);
CREATE INDEX IF NOT EXISTS idx_handoff_card_flags_prompt ON handoff_card_flags (prompt_id);
