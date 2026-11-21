-- tasks slice ledger additions (§3.4 Tasks; the tasks table itself is core's).

-- One row per item the extractor has read: each recording is extracted once (1.21).
CREATE TABLE IF NOT EXISTS task_extractions (
  item_id        TEXT PRIMARY KEY,
  recording_id   TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  extracted_at   TEXT NOT NULL,
  candidates     INTEGER NOT NULL DEFAULT 0
);

-- Candidates that are never tasks (Amier asking others, musings, quotes not in the transcript).
-- Kept for `articulate eval tasks` (Phase 5); never sent.
CREATE TABLE IF NOT EXISTS task_drops (
  id             INTEGER PRIMARY KEY,
  item_id        TEXT NOT NULL,
  title          TEXT NOT NULL,
  quote          TEXT,
  kind           TEXT,
  confidence     TEXT,
  reason         TEXT NOT NULL,          -- delegated | musing | quote_not_found | invalid
  prompt_version TEXT NOT NULL,
  created_at     TEXT NOT NULL
);

-- Title embeddings (embed_personal) for the 14-day near-duplicate check.
CREATE TABLE IF NOT EXISTS task_embeddings (
  external_id TEXT PRIMARY KEY,
  vector_json TEXT NOT NULL
);

-- Tasks that went to Inbox/Articulate tasks.md before master-todo's intents worked (Q10).
CREATE TABLE IF NOT EXISTS task_fallback (
  external_id TEXT PRIMARY KEY,
  added_at    TEXT NOT NULL
);

-- Small counters, e.g. consecutive runs whose sends failed.
CREATE TABLE IF NOT EXISTS task_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
