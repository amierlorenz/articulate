CREATE TABLE IF NOT EXISTS schema_migrations (
  package    TEXT NOT NULL,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  PRIMARY KEY (package, name)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id       TEXT PRIMARY KEY,
  job          TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  status       TEXT NOT NULL,
  error        TEXT,
  summary_json TEXT
);

CREATE TABLE IF NOT EXISTS items (
  item_id      TEXT PRIMARY KEY,
  source       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  native_id    TEXT NOT NULL,
  version      TEXT NOT NULL DEFAULT '',
  course_key   TEXT,
  privacy      TEXT NOT NULL,
  exam_key     TEXT,
  priority     REAL NOT NULL DEFAULT 0,
  stage        TEXT NOT NULL,
  status       TEXT NOT NULL,
  hold_reason  TEXT,
  hold_since   TEXT,
  attempts     INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE (source, native_id, version)
);

CREATE TABLE IF NOT EXISTS item_events (
  id INTEGER PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items,
  run_id TEXT,
  at TEXT NOT NULL,
  stage TEXT,
  event TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY,
  run_id TEXT,
  item_id TEXT,
  step TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  pacific_date TEXT NOT NULL,
  local_night TEXT,
  started_at TEXT NOT NULL,
  ms INTEGER,
  outcome TEXT NOT NULL,
  counts_quota INTEGER NOT NULL,
  tokens_in INTEGER,
  tokens_out INTEGER,
  tokens_thinking INTEGER,
  finish_reason TEXT
);
CREATE INDEX IF NOT EXISTS model_calls_quota ON model_calls (provider, model, pacific_date);

CREATE TABLE IF NOT EXISTS model_state (
  model TEXT PRIMARY KEY,
  exhausted_until TEXT,
  missing_since TEXT
);

CREATE TABLE IF NOT EXISTS claude_usage (
  checked_at TEXT PRIMARY KEY,
  ok INTEGER NOT NULL,
  session_pct REAL,
  weekly_pct REAL,
  session_resets_at TEXT,
  weekly_resets_at TEXT,
  credential_expires_at TEXT,
  cli_version TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  external_id   TEXT PRIMARY KEY,
  source        TEXT NOT NULL DEFAULT 'articulate',
  title         TEXT NOT NULL,
  due_at        TEXT,
  has_due_time INTEGER NOT NULL DEFAULT 0,
  notes         TEXT,
  link          TEXT,
  origin_item_id TEXT,
  band          TEXT NOT NULL,
  prompt_version TEXT,
  state         TEXT NOT NULL,
  master_todo_id TEXT,
  send_failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  last_polled_at TEXT,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS vault_notes (
  articulate_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  written_hash TEXT NOT NULL,
  touched INTEGER NOT NULL DEFAULT 0,
  private INTEGER NOT NULL DEFAULT 0,
  source_item_id TEXT,
  command_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tombstones (
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS flags (
  flag_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  item_id TEXT,
  subject TEXT NOT NULL,
  choices_json TEXT NOT NULL,
  status TEXT NOT NULL,
  chosen TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS health (
  kind TEXT NOT NULL,
  key TEXT NOT NULL DEFAULT '',
  detail TEXT,
  interrupt INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  recovered_at TEXT,
  task_external_id TEXT,
  PRIMARY KEY (kind, key, first_seen)
);

CREATE TABLE IF NOT EXISTS handoff_files (
  file_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  written_at TEXT NOT NULL,
  acked_at TEXT,
  failed_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS encore_snapshots (
  schema TEXT PRIMARY KEY,
  exported_at TEXT NOT NULL,
  read_at TEXT NOT NULL,
  body_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
  course_key TEXT PRIMARY KEY,
  name TEXT,
  canvas_id INTEGER,
  coding INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS units (
  unit_id TEXT PRIMARY KEY,
  course_key TEXT,
  name TEXT,
  position INTEGER
);

CREATE TABLE IF NOT EXISTS exams (
  exam_id TEXT PRIMARY KEY,
  course_key TEXT,
  name TEXT,
  kind TEXT,
  date TEXT,
  unit_ids_json TEXT
);

CREATE TABLE IF NOT EXISTS source_files (
  file_id TEXT PRIMARY KEY,
  course_key TEXT,
  origin TEXT,
  slot TEXT NOT NULL,
  canvas_file_id INTEGER,
  canvas_updated_at TEXT,
  display_name TEXT,
  folder_hint TEXT,
  mime TEXT,
  role TEXT,
  page_count INTEGER,
  archive_path TEXT,
  superseded_by TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS pages (
  file_id TEXT,
  page INTEGER,
  page_hash TEXT,
  layer_text TEXT,
  layer_untrusted INTEGER DEFAULT 0,
  ocr_text TEXT,
  vl_text TEXT,
  title TEXT,
  notes TEXT,
  hidden INTEGER DEFAULT 0,
  PRIMARY KEY (file_id, page)
);

CREATE TABLE IF NOT EXISTS concepts (
  concept_id TEXT PRIMARY KEY,
  course_key TEXT,
  unit_id TEXT,
  kind TEXT,
  title TEXT,
  aliases_json TEXT,
  details_json TEXT,
  confidence TEXT,
  confidence_note TEXT,
  tier TEXT,
  tier_reasons_json TEXT,
  on_guide INTEGER DEFAULT 0,
  status TEXT,
  hold_reason TEXT,
  created_at TEXT,
  updated_at TEXT,
  shipped_in TEXT
);

CREATE TABLE IF NOT EXISTS contrast_groups (
  group_id TEXT PRIMARY KEY,
  course_key TEXT,
  name TEXT,
  question TEXT,
  member_ids_json TEXT,
  contrasts_json TEXT,
  status TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
  prompt_id TEXT PRIMARY KEY,
  concept_id TEXT,
  kind TEXT,
  content_json TEXT,
  status TEXT,
  generated_by TEXT,
  prompt_version TEXT,
  created_at TEXT,
  shipped_in TEXT
);

CREATE TABLE IF NOT EXISTS guide_items (
  file_id TEXT,
  position INTEGER,
  number TEXT,
  kind TEXT,
  page INTEGER,
  quote TEXT,
  answer_page INTEGER,
  answer_quote TEXT,
  verified INTEGER,
  verified_by TEXT,
  answer_verified INTEGER,
  matched_concept_ids_json TEXT,
  PRIMARY KEY (file_id, position)
);
