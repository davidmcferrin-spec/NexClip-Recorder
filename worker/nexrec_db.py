#!/usr/bin/env python3
"""PostgreSQL helpers for NexCLIP Recorder workers.

Connection settings come from the process environment (bootstrap):
NEXREC_PGHOST, NEXREC_PGPORT, NEXREC_PGDATABASE, NEXREC_PGUSER,
NEXREC_PGPASSWORD. A filesystem path is not the database. Tests may still
pass a temp path or set NEXREC_DB to one; that string only selects a private
schema inside the local Postgres database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable

import psycopg2
import psycopg2.extras

SCHEMA_REL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_NAMED = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")

# Additive columns for existing DBs (CREATE TABLE IF NOT EXISTS will not alter).
INPUT_FEATURE_COLUMNS: list[tuple[str, str]] = [
    ("feat_scte", "INTEGER NOT NULL DEFAULT 0"),
    ("feat_av_anomaly", "INTEGER NOT NULL DEFAULT 0"),
    ("feat_captions", "INTEGER NOT NULL DEFAULT 0"),
    ("feat_transcribe", "INTEGER NOT NULL DEFAULT 0"),
    ("feat_nielsen", "INTEGER NOT NULL DEFAULT 0"),
    ("feat_monitors", "INTEGER NOT NULL DEFAULT 0"),
    ("thresh_freeze_s", "REAL NOT NULL DEFAULT 2.0"),
    ("thresh_black_s", "REAL NOT NULL DEFAULT 2.0"),
    ("thresh_bars_s", "REAL NOT NULL DEFAULT 5.0"),
    ("transcribe_engine", "TEXT"),
    ("nexclip_slot", "INTEGER"),
    ("keep_interlace", "INTEGER"),
]

INPUT_FEATURE_DEFAULTS: dict[str, Any] = {
    "feat_scte": 0,
    "feat_av_anomaly": 0,
    "feat_captions": 0,
    "feat_transcribe": 0,
    "feat_nielsen": 0,
    "feat_monitors": 0,
    "thresh_freeze_s": 2.0,
    "thresh_black_s": 2.0,
    "thresh_bars_s": 5.0,
    "transcribe_engine": "",
    "nexclip_slot": None,
    "keep_interlace": None,
}

def _safe_schema(name: str) -> str:
    if not _SCHEMA_RE.match(name):
        raise RuntimeError(f"invalid postgres schema name: {name}")
    return name


def schema_for_env(env: dict[str, str]) -> str:
    explicit = str(env.get("NEXREC_PGSCHEMA") or "").strip()
    if explicit:
        return _safe_schema(explicit)
    legacy = str(env.get("NEXREC_DB") or "").strip()
    database = str(env.get("NEXREC_PGDATABASE") or "").strip()
    # A filesystem path is a test isolation key. Station databases stay on public
    # so a retired NEXREC_DB=.../nexrec.db line cannot move production data.
    if ("/" in legacy or legacy.endswith(".db")) and database in ("", "nexrec_test"):
        return "t_" + hashlib.sha1(legacy.encode()).hexdigest()[:16]
    return "public"


def _pg_params(env: dict[str, str]) -> dict[str, Any]:
    host = str(env.get("NEXREC_PGHOST") or "127.0.0.1")
    port = str(env.get("NEXREC_PGPORT") or "5432")
    database = str(env.get("NEXREC_PGDATABASE") or "").strip()
    user = str(env.get("NEXREC_PGUSER") or "").strip()
    password = env.get("NEXREC_PGPASSWORD")
    password = "" if password is None else str(password)
    if database == "":
        database = "nexrec_test"
        user = user or "nexrec_test"
        password = password or "nexrec_test"
    elif user == "" or password == "":
        raise RuntimeError(
            "PostgreSQL requires NEXREC_PGDATABASE, NEXREC_PGUSER, and NEXREC_PGPASSWORD"
        )
    return {
        "host": host,
        "port": port,
        "dbname": database,
        "user": user,
        "password": password,
        "connect_timeout": 8,
    }


def adapt_sql(sql: str) -> str:
    sql = re.sub(r"datetime\(\s*'now'\s*\)", "to_char(timezone('utc', clock_timestamp()), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')", sql)
    sql = sql.replace("IFNULL(", "COALESCE(")
    sql = re.sub(
        r"captions_fts\s+MATCH\s+(\?|:[A-Za-z_][A-Za-z0-9_]*)",
        lambda m: "tsv @@ plainto_tsquery('simple', " + m.group(1) + ")",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _split_sql(sql: str) -> list[str]:
    sql = re.sub(r"--.*?$", "", sql, flags=re.M)
    parts: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < len(sql) and sql[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


class PgRow(dict):
    """Dict row that also supports sqlite-style row[0] for a single-column read."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PgCursor(psycopg2.extras.RealDictCursor):
    def fetchone(self) -> PgRow | None:
        row = super().fetchone()
        if row is None:
            return None
        return PgRow(row)

    def fetchall(self) -> list[PgRow]:
        return [PgRow(r) for r in super().fetchall()]


class PgConn:
    def __init__(self, raw: Any, schema: str) -> None:
        self.raw = raw
        self.schema = schema

    def execute(self, sql: str, params: Any = ()) -> PgCursor:
        sql = adapt_sql(sql)
        if isinstance(params, dict):
            sql = _NAMED.sub(lambda m: "%(" + m.group(1) + ")s", sql)
            bound = params
        else:
            sql = sql.replace("?", "%s")
            bound = tuple(params or ())
        cur = self.raw.cursor(cursor_factory=PgCursor)
        try:
            cur.execute(sql, bound)
        except Exception:
            self.raw.rollback()
            raise
        return cur

    def executescript(self, sql: str) -> None:
        for stmt in _split_sql(sql):
            self.execute(stmt)
        self.commit()

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def connect(env: dict[str, str] | str | None = None) -> PgConn:
    """Open the local Postgres database.

    A str is a test isolation key (often a temp path). It is not a database file.
    """
    merged = {k: str(v) for k, v in os.environ.items()}
    if isinstance(env, str):
        merged["NEXREC_DB"] = env
    elif isinstance(env, dict):
        for key, value in env.items():
            if value is not None:
                merged[key] = str(value)
    schema = schema_for_env(merged)
    raw = psycopg2.connect(**_pg_params(merged))
    raw.autocommit = True
    with raw.cursor() as cur:
        if schema != "public":
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(f"SET search_path TO {schema}")
    raw.autocommit = False
    return PgConn(raw, schema)


def table_columns(conn: PgConn, table: str) -> set[str]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise RuntimeError("invalid table name")
    rows = conn.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ?
        """,
        (table,),
    ).fetchall()
    return {str(r["column_name"]) for r in rows}


def ensure_input_feature_columns(conn: PgConn) -> None:
    have = table_columns(conn, "inputs")
    for name, decl in INPUT_FEATURE_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE inputs ADD COLUMN {name} {decl}")
    exp_cols = table_columns(conn, "exports")
    if "nexclip_capture_id" not in exp_cols:
        conn.execute("ALTER TABLE exports ADD COLUMN nexclip_capture_id TEXT")


def ensure_fts(conn: PgConn) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'captions_fts'
        """
    ).fetchone()
    return row is not None


def with_input_defaults(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(INPUT_FEATURE_DEFAULTS)
    out.update(rec)
    if out.get("transcribe_engine") is None:
        out["transcribe_engine"] = ""
    return out


def migrate(conn: PgConn, schema_path: str | None = None) -> None:
    path = schema_path or SCHEMA_REL
    with open(path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn.executescript(sql)
    ensure_input_feature_columns(conn)
    ensure_fts(conn)
    row = conn.execute("SELECT id FROM schema_migrations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (1, to_char(timezone('utc', clock_timestamp()), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))"
        )
    row2 = conn.execute("SELECT id FROM schema_migrations WHERE id=2").fetchone()
    if row2 is None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (2, to_char(timezone('utc', clock_timestamp()), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'))"
        )
    conn.commit()


def fetchone(conn: PgConn, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(args)).fetchone()
    return dict(row) if row is not None else None


def fetchall(conn: PgConn, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def upsert_input(conn: PgConn, rec: dict[str, Any]) -> None:
    rec = with_input_defaults(rec)
    conn.execute(
        """
        INSERT INTO inputs (
          id, name, source_type, url, decklink_device, decklink_format,
          enabled, live_transcode, copy_native, upconvert_1080i, keep_interlace,
          video_bitrate, audio_bitrate, retention_days, preview_path,
          preview_enabled,
          feat_scte, feat_av_anomaly, feat_captions, feat_transcribe,
          feat_nielsen, feat_monitors, thresh_freeze_s, thresh_black_s,
          thresh_bars_s, transcribe_engine, nexclip_slot,
          created_at, updated_at
        ) VALUES (
          :id, :name, :source_type, :url, :decklink_device, :decklink_format,
          :enabled, :live_transcode, :copy_native, :upconvert_1080i, :keep_interlace,
          :video_bitrate, :audio_bitrate, :retention_days, :preview_path,
          :preview_enabled,
          :feat_scte, :feat_av_anomaly, :feat_captions, :feat_transcribe,
          :feat_nielsen, :feat_monitors, :thresh_freeze_s, :thresh_black_s,
          :thresh_bars_s, :transcribe_engine, :nexclip_slot,
          :created_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          source_type=excluded.source_type,
          url=excluded.url,
          decklink_device=excluded.decklink_device,
          decklink_format=excluded.decklink_format,
          enabled=excluded.enabled,
          live_transcode=excluded.live_transcode,
          copy_native=excluded.copy_native,
          upconvert_1080i=excluded.upconvert_1080i,
          keep_interlace=excluded.keep_interlace,
          video_bitrate=excluded.video_bitrate,
          audio_bitrate=excluded.audio_bitrate,
          retention_days=excluded.retention_days,
          preview_path=excluded.preview_path,
          preview_enabled=excluded.preview_enabled,
          feat_scte=excluded.feat_scte,
          feat_av_anomaly=excluded.feat_av_anomaly,
          feat_captions=excluded.feat_captions,
          feat_transcribe=excluded.feat_transcribe,
          feat_nielsen=excluded.feat_nielsen,
          feat_monitors=excluded.feat_monitors,
          thresh_freeze_s=excluded.thresh_freeze_s,
          thresh_black_s=excluded.thresh_black_s,
          thresh_bars_s=excluded.thresh_bars_s,
          transcribe_engine=excluded.transcribe_engine,
          nexclip_slot=excluded.nexclip_slot,
          updated_at=excluded.updated_at
        """,
        rec,
    )
    conn.commit()


def insert_chunk(conn: PgConn, rec: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO chunks (
          id, input_id, path, kind, start_at, end_at, duration_s, size_bytes,
          width, height, fps, interlaced, codec, timecode_start, ready, orphan,
          created_at
        ) VALUES (
          :id, :input_id, :path, :kind, :start_at, :end_at, :duration_s, :size_bytes,
          :width, :height, :fps, :interlaced, :codec, :timecode_start, :ready, :orphan,
          :created_at
        )
        ON CONFLICT(path) DO UPDATE SET
          end_at=excluded.end_at,
          duration_s=excluded.duration_s,
          size_bytes=excluded.size_bytes,
          width=excluded.width,
          height=excluded.height,
          fps=excluded.fps,
          interlaced=excluded.interlaced,
          codec=excluded.codec,
          timecode_start=excluded.timecode_start,
          ready=excluded.ready,
          orphan=0
        """,
        rec,
    )
    conn.commit()


def chunks_overlapping(
    conn: PgConn,
    input_id: str,
    t_in: str,
    t_out: str,
    kind: str = "native",
) -> list[dict[str, Any]]:
    return fetchall(
        conn,
        """
        SELECT * FROM chunks
        WHERE input_id = ?
          AND kind = ?
          AND ready = 1
          AND orphan = 0
          AND start_at < ?
          AND IFNULL(end_at, start_at) > ?
        ORDER BY start_at ASC
        """,
        (input_id, kind, t_out, t_in),
    )


def enqueue_export(conn: PgConn, rec: dict[str, Any]) -> None:
    rec = dict(rec)
    rec.setdefault("nexclip_schedule_id", None)
    rec.setdefault("nexclip_capture_id", None)
    conn.execute(
        """
        INSERT INTO exports (
          id, status, input_ids, t_in, t_out, quality, scope, path, size_bytes,
          protected, error, created_by, created_at, expires_at, nexclip_schedule_id,
          nexclip_capture_id
        ) VALUES (
          :id, :status, :input_ids, :t_in, :t_out, :quality, :scope, :path, :size_bytes,
          :protected, :error, :created_by, :created_at, :expires_at, :nexclip_schedule_id,
          :nexclip_capture_id
        )
        """,
        rec,
    )
    conn.commit()


def get_setting(conn: PgConn, key: str, default: str = "") -> str:
    row = fetchone(conn, "SELECT value FROM settings WHERE key=?", (key,))
    return str(row["value"]) if row and row.get("value") is not None else default


def set_setting(conn: PgConn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def insert_event(conn: PgConn, rec: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO events (
          id, input_id, chunk_id, kind, subtype, t_start, t_end, pts, timecode,
          duration_s, payload_summary, payload_json, created_at
        ) VALUES (
          :id, :input_id, :chunk_id, :kind, :subtype, :t_start, :t_end, :pts, :timecode,
          :duration_s, :payload_summary, :payload_json, :created_at
        )
        """,
        rec,
    )
    conn.commit()


def insert_caption(conn: PgConn, rec: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO captions (
          id, input_id, chunk_id, kind, service, speaker, t_start, t_end, pts,
          timecode, text, created_at
        ) VALUES (
          :id, :input_id, :chunk_id, :kind, :service, :speaker, :t_start, :t_end, :pts,
          :timecode, :text, :created_at
        )
        """,
        rec,
    )
    try:
        conn.execute(
            """
            INSERT INTO captions_fts (id, input_id, kind, t_start, speaker, text)
            VALUES (:id, :input_id, :kind, :t_start, :speaker, :text)
            """,
            rec,
        )
    except psycopg2.Error:
        pass
    conn.commit()


def insert_loudness(conn: PgConn, rec: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO loudness_samples (
          id, input_id, export_id, t_at, lkfs, momentary, short_term, true_peak,
          lra, source, created_at
        ) VALUES (
          :id, :input_id, :export_id, :t_at, :lkfs, :momentary, :short_term, :true_peak,
          :lra, :source, :created_at
        )
        """,
        rec,
    )
    conn.commit()


def search_captions(
    conn: PgConn,
    query: str,
    input_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 200))
    try:
        sql = "SELECT id, input_id, kind, t_start, speaker, text FROM captions_fts WHERE tsv @@ plainto_tsquery('simple', ?)"
        args: list[Any] = [q]
        if input_id:
            sql += " AND input_id = ?"
            args.append(input_id)
        sql += " LIMIT CAST(? AS integer)"
        args.append(limit)
        return fetchall(conn, sql, args)
    except psycopg2.Error:
        sql = "SELECT id, input_id, kind, service, speaker, t_start, t_end, text FROM captions WHERE text LIKE ?"
        args = [f"%{q}%"]
        if input_id:
            sql += " AND input_id = ?"
            args.append(input_id)
        sql += " ORDER BY t_start DESC LIMIT CAST(? AS integer)"
        args.append(limit)
        return fetchall(conn, sql, args)


def delete_chunk_side_data(conn: PgConn, chunk_id: str) -> None:
    ids = [
        r["id"]
        for r in fetchall(conn, "SELECT id FROM captions WHERE chunk_id=?", (chunk_id,))
    ]
    conn.execute("DELETE FROM events WHERE chunk_id=?", (chunk_id,))
    conn.execute("DELETE FROM captions WHERE chunk_id=?", (chunk_id,))
    for cid in ids:
        try:
            conn.execute("DELETE FROM captions_fts WHERE id=?", (cid,))
        except psycopg2.Error:
            break
    conn.commit()


def delete_input_side_data(conn: PgConn, input_id: str) -> None:
    conn.execute("DELETE FROM events WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM captions WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM loudness_samples WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM analyze_jobs WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM input_heartbeats WHERE input_id=?", (input_id,))
    try:
        conn.execute("DELETE FROM captions_fts WHERE input_id=?", (input_id,))
    except psycopg2.Error:
        pass
    conn.commit()


# app_settings key -> process environment. Empty DB values do not clobber env.
# nexapp.mode is translated separately (wan -> nexapp-wan) for older readers.
APP_SETTING_ENV: dict[str, str] = {
    "station.display_name": "NEXREC_INSTANCE_NAME",
    "station.instance_id": "NEXREC_INSTANCE_ID",
    "station.public_url": "NEXREC_PUBLIC_URL",
    "station.timezone": "NEXREC_TIMEZONE",
    "storage.recordings": "NEXREC_STORAGE_DIR",
    "storage.exports": "NEXREC_EXPORTS_DIR",
    "storage.scratch": "NEXREC_SCRATCH_DIR",
    "storage.free_space_floor": "NEXREC_FREE_SPACE_FLOOR",
    "retention.raw_days": "NEXREC_NATIVE_RETENTION_DAYS",
    "retention.export_days": "NEXREC_EXPORT_RETENTION_DAYS",
    "ffmpeg.path": "NEXREC_FFMPEG",
    "ffmpeg.probe": "NEXREC_FFPROBE",
    "ffmpeg.video_bitrate": "NEXREC_BROADCAST_VIDEO_BITRATE",
    "ffmpeg.audio_bitrate": "NEXREC_BROADCAST_AUDIO_BITRATE",
    "ffmpeg.preset": "NEXREC_X264_PRESET",
    "ffmpeg.segment_seconds": "NEXREC_SEGMENT_SECONDS",
    "ffmpeg.gop_frames": "NEXREC_GOP_FRAMES",
    "ffmpeg.decklink_status_bin": "NEXREC_DECKLINK_STATUS_BIN",
    "preview.enabled": "NEXREC_PREVIEW_ENABLED",
    "preview.mediamtx_rtsp": "NEXREC_MEDIAMTX_RTSP",
    "preview.whep_port": "NEXREC_WHEP_PORT",
    "defaults.max_inputs": "NEXREC_MAX_INPUTS",
    "nexapp.enabled": "NEXREC_NEXAPP_ENABLED",
    "nexapp.issuer": "NEXAPP_ISSUER",
    "nexapp.base_url": "NEXAPP_BASE_URL",
    "nexapp.service_id": "NEXAPP_SERVICE_ID",
    "nexapp.public_key_path": "NEXAPP_PUBLIC_KEY_PATH",
    "nexapp.access_url": "NEXAPP_ACCESS_URL",
    "nexapp.redeem_url": "NEXAPP_REDEEM_URL",
    "nexapp.logout_url": "NEXAPP_LOGOUT_URL",
    "nexclip.enabled": "NEXREC_NEXCLIP_ENABLED",
    "nexclip.api_base": "NEXCLIP_BASE_URL",
    "nexclip.api_prefix": "NEXCLIP_API_PREFIX",
    "nexclip.recorder_id": "NEXCLIP_RECORDER_ID",
    "nexclip.poll_s": "NEXCLIP_POLL_S",
    "nexclip.num_slots": "NEXCLIP_NUM_SLOTS",
    "ldap.enabled": "NEXREC_LDAP_ENABLED",
    "ldap.url": "NEXREC_LDAP_URL",
    "ldap.bind_dn": "NEXREC_LDAP_BIND_DN",
    "ldap.base_dn": "NEXREC_LDAP_BASE_DN",
    "ldap.user_filter": "NEXREC_LDAP_USER_FILTER",
    "ldap.email_attr": "NEXREC_LDAP_EMAIL_ATTR",
    "ldap.display_attr": "NEXREC_LDAP_DISPLAY_ATTR",
    "intelligence.transcribe_engine": "NEXREC_TRANSCRIBE_ENGINE",
    "intelligence.transcribe_cmd": "NEXREC_TRANSCRIBE_CMD",
    "intelligence.transcribe_timeout_s": "NEXREC_TRANSCRIBE_TIMEOUT_S",
    "intelligence.loudness_timeout_s": "NEXREC_LOUDNESS_TIMEOUT_S",
    "intelligence.nielsen_cmd": "NEXREC_NIELSEN_PRESENCE_CMD",
}

_MODE_TO_ENV = {"wan": "nexapp-wan", "alias": "alias", "standalone": "standalone"}


def overlay_app_settings(conn: PgConn, env: dict[str, str]) -> dict[str, str]:
    """Prefer app_settings over the process environment unless break-glass is set."""
    out = dict(env)
    flag = str(env.get("NEXREC_ENV_OVERRIDES") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return out
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except psycopg2.Error:
        return out
    kv = {str(r["key"]): "" if r["value"] is None else str(r["value"]) for r in rows}
    for skey, ekey in APP_SETTING_ENV.items():
        if skey not in kv:
            continue
        # A stored row wins, including a cleared value. data_paths treats "" as unset.
        out[ekey] = kv[skey]
    mode = kv.get("nexapp.mode") or ""
    if mode:
        out["NEXREC_DEPLOY_MODE"] = _MODE_TO_ENV.get(mode, mode)
    if kv.get("nexapp.base_url") and not out.get("NEXAPP_ISSUER"):
        out["NEXAPP_ISSUER"] = kv["nexapp.base_url"]
    return out
