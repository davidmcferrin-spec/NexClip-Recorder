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
  nexclip_schedule_id TEXT
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
