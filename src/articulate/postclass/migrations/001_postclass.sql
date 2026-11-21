-- Post-class quiz (§7.1, #71). One row per lecture; class_end is additive (when the 02:00 takeover is due).
CREATE TABLE IF NOT EXISTS postclass_quizzes (
  lecture_id TEXT PRIMARY KEY,
  course_key TEXT,
  event_id TEXT,
  recording_id TEXT,
  status TEXT,                  -- waiting | built | taken_over | failed
  batch_id TEXT,
  plan_id TEXT,
  created_at TEXT,
  built_at TEXT,
  class_end TEXT
);
