-- Pocket slice schema additions (§3.4, #55)
CREATE TABLE IF NOT EXISTS pocket_recordings (
  recording_id   TEXT PRIMARY KEY,
  title          TEXT,
  folder_id      TEXT,
  state          TEXT NOT NULL,
  duration       REAL,
  recording_at   INTEGER,
  created_at     TEXT,
  updated_at     TEXT,
  first_seen_at  TEXT NOT NULL,
  last_polled_at TEXT NOT NULL,
  archived_at    TEXT,
  is_merged      INTEGER NOT NULL DEFAULT 0,
  is_private     INTEGER NOT NULL DEFAULT 0,
  abandoned      INTEGER NOT NULL DEFAULT 0
);
