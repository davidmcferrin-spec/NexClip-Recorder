-- NexCLIP Recorder SQLite schema (WAL). Applied by worker/nexrec_db.py and
-- web/nexrec-auth-lib.php. Keep both in sync.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  id INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT,
  email TEXT,
  display_name TEXT,
  role TEXT NOT NULL DEFAULT 'viewer',
  source TEXT NOT NULL DEFAULT 'local',
  nexapp_sub TEXT,
  disabled_at TEXT,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inputs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  url TEXT,
  decklink_device TEXT,
  decklink_format TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  live_transcode INTEGER NOT NULL DEFAULT 0,
  copy_native INTEGER NOT NULL DEFAULT 1,
  upconvert_1080i INTEGER NOT NULL DEFAULT 0,
  video_bitrate TEXT,
  audio_bitrate TEXT,
  retention_days INTEGER NOT NULL DEFAULT 28,
  preview_path TEXT,
  preview_enabled INTEGER NOT NULL DEFAULT 1,
  -- Per-input monitoring / intelligence (all default off).
  feat_scte INTEGER NOT NULL DEFAULT 0,
  feat_av_anomaly INTEGER NOT NULL DEFAULT 0,
  feat_captions INTEGER NOT NULL DEFAULT 0,
  feat_transcribe INTEGER NOT NULL DEFAULT 0,
  feat_nielsen INTEGER NOT NULL DEFAULT 0,
  feat_monitors INTEGER NOT NULL DEFAULT 0,
  thresh_freeze_s REAL NOT NULL DEFAULT 2.0,
  thresh_black_s REAL NOT NULL DEFAULT 2.0,
  thresh_bars_s REAL NOT NULL DEFAULT 5.0,
  transcribe_engine TEXT,
  nexclip_slot INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  input_id TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'native',
  start_at TEXT NOT NULL,
  end_at TEXT,
  duration_s REAL,
  size_bytes INTEGER,
  width INTEGER,
  height INTEGER,
  fps REAL,
  interlaced INTEGER,
  codec TEXT,
  timecode_start TEXT,
  ready INTEGER NOT NULL DEFAULT 0,
  orphan INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (input_id) REFERENCES inputs(id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_input_start ON chunks(input_id, start_at);
CREATE INDEX IF NOT EXISTS idx_chunks_ready ON chunks(ready, orphan);

CREATE TABLE IF NOT EXISTS exports (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'queued',
  input_ids TEXT NOT NULL,
  t_in TEXT NOT NULL,
  t_out TEXT NOT NULL,
  quality TEXT NOT NULL DEFAULT 'full',
  scope TEXT NOT NULL DEFAULT 'one',
  path TEXT,
  size_bytes INTEGER,
  protected INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  nexclip_schedule_id TEXT,
  nexclip_capture_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_exports_status ON exports(status);
CREATE INDEX IF NOT EXISTS idx_exports_expires ON exports(expires_at, protected);

CREATE TABLE IF NOT EXISTS nexclip_events (
  id TEXT PRIMARY KEY,
  input_id TEXT,
  title TEXT,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  payload TEXT,
  export_id TEXT,
  status TEXT NOT NULL DEFAULT 'cached',
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Timeline-aligned intelligence events (SCTE, freeze/black/bars, Nielsen stubs).
-- t_start/t_end are NTP wall-clock ISO-8601 Z, aligned to the chunk timeline.
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  input_id TEXT NOT NULL,
  chunk_id TEXT,
  kind TEXT NOT NULL,
  subtype TEXT,
  t_start TEXT NOT NULL,
  t_end TEXT,
  pts REAL,
  timecode TEXT,
  duration_s REAL,
  payload_summary TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (input_id) REFERENCES inputs(id)
);

CREATE INDEX IF NOT EXISTS idx_events_input_start ON events(input_id, t_start);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, input_id);

-- Caption (608/708) and transcript cues. FTS5 virtual table is created in
-- migrate() when the SQLite build includes fts5 (Ubuntu 24.04 does).
CREATE TABLE IF NOT EXISTS captions (
  id TEXT PRIMARY KEY,
  input_id TEXT NOT NULL,
  chunk_id TEXT,
  kind TEXT NOT NULL,
  service TEXT,
  speaker TEXT,
  t_start TEXT NOT NULL,
  t_end TEXT,
  pts REAL,
  timecode TEXT,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (input_id) REFERENCES inputs(id)
);

CREATE INDEX IF NOT EXISTS idx_captions_input_start ON captions(input_id, t_start);

-- CALM-oriented loudness samples (ITU-R BS.1770 / ATSC A/85). Export editor.
CREATE TABLE IF NOT EXISTS loudness_samples (
  id TEXT PRIMARY KEY,
  input_id TEXT,
  export_id TEXT,
  t_at TEXT NOT NULL,
  lkfs REAL NOT NULL,
  momentary REAL,
  short_term REAL,
  true_peak REAL,
  lra REAL,
  source TEXT NOT NULL DEFAULT 'export_window',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_loudness_input_t ON loudness_samples(input_id, t_at);

-- Sidecar jobs (loudness measure, optional future analyzers). PHP never shells FFmpeg.
CREATE TABLE IF NOT EXISTS analyze_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'queued',
  kind TEXT NOT NULL,
  input_id TEXT,
  export_id TEXT,
  t_in TEXT,
  t_out TEXT,
  path TEXT,
  error TEXT,
  result_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyze_status ON analyze_jobs(status, kind);

-- Day-to-day admin (Setup). Secrets stay in nexrec.env; this table is seeded
-- once from the environment, then the row wins until an operator edits it.
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at TEXT,
  updated_by TEXT
);

-- Record-worker heartbeat + last DeckLink/IP signal observation.
CREATE TABLE IF NOT EXISTS input_heartbeats (
  input_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL DEFAULT '',
  transport TEXT NOT NULL DEFAULT '',
  signal TEXT NOT NULL DEFAULT 'unknown',
  sdi_lock INTEGER,
  format TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  last_chunk_at TEXT,
  seen_at TEXT NOT NULL
);
