-- inbox.notes ledger: what the inbox step did with each pocket recording or span item.
-- pocket owns items.stage/status; this table is inbox's own progress.
CREATE TABLE inbox_items (
  item_id        TEXT PRIMARY KEY,        -- items.item_id
  status         TEXT NOT NULL,           -- pending | held | failed | done | dropped
  outcome        TEXT,                    -- note | tasks_only | skip_no_transcript | skip_short | skip_gate | tombstoned
  hold_reason    TEXT,                    -- quote_check_failed | failed | ollama_down | quota | ...
  flag_id        TEXT,                    -- open weekly flag while held for a person's choice
  articulate_id  TEXT,                    -- the note, when one was written
  kind           TEXT,                    -- idea | meeting | journal | content | other
  attempts       INTEGER NOT NULL DEFAULT 0,
  prompt_version TEXT,                    -- gate and note prompt hashes
  last_error     TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
