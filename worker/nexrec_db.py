#!/usr/bin/env python3
"""SQLite helpers for NexCLIP Recorder workers. Stdlib only."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable

SCHEMA_REL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")

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

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS captions_fts USING fts5(
  id UNINDEXED,
  input_id UNINDEXED,
  kind UNINDEXED,
  t_start UNINDEXED,
  speaker UNINDEXED,
  text
)
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_input_feature_columns(conn: sqlite3.Connection) -> None:
    have = table_columns(conn, "inputs")
    for name, decl in INPUT_FEATURE_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE inputs ADD COLUMN {name} {decl}")
    exp_cols = table_columns(conn, "exports")
    if "nexclip_capture_id" not in exp_cols:
        conn.execute("ALTER TABLE exports ADD COLUMN nexclip_capture_id TEXT")


def ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(FTS_DDL)
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def with_input_defaults(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(INPUT_FEATURE_DEFAULTS)
    out.update(rec)
    if out.get("transcribe_engine") is None:
        out["transcribe_engine"] = ""
    return out


def migrate(conn: sqlite3.Connection, schema_path: str | None = None) -> None:
    path = schema_path or SCHEMA_REL
    with open(path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn.executescript(sql)
    ensure_input_feature_columns(conn)
    ensure_fts(conn)
    row = conn.execute("SELECT id FROM schema_migrations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (1, datetime('now'))"
        )
    row2 = conn.execute("SELECT id FROM schema_migrations WHERE id=2").fetchone()
    if row2 is None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (2, datetime('now'))"
        )
    conn.commit()


def fetchone(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(args)).fetchone()
    return dict(row) if row is not None else None


def fetchall(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def upsert_input(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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


def insert_chunk(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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
    conn: sqlite3.Connection,
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


def enqueue_export(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = fetchone(conn, "SELECT value FROM settings WHERE key=?", (key,))
    return str(row["value"]) if row and row.get("value") is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def insert_event(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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


def insert_caption(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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
    except sqlite3.OperationalError:
        pass
    conn.commit()


def insert_loudness(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
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
    conn: sqlite3.Connection,
    query: str,
    input_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 200))
    try:
        sql = "SELECT id, input_id, kind, t_start, speaker, text FROM captions_fts WHERE captions_fts MATCH ?"
        args: list[Any] = [q]
        if input_id:
            sql += " AND input_id = ?"
            args.append(input_id)
        sql += " LIMIT ?"
        args.append(limit)
        return fetchall(conn, sql, args)
    except sqlite3.OperationalError:
        sql = "SELECT id, input_id, kind, service, speaker, t_start, t_end, text FROM captions WHERE text LIKE ?"
        args = [f"%{q}%"]
        if input_id:
            sql += " AND input_id = ?"
            args.append(input_id)
        sql += " ORDER BY t_start DESC LIMIT ?"
        args.append(limit)
        return fetchall(conn, sql, args)


def delete_chunk_side_data(conn: sqlite3.Connection, chunk_id: str) -> None:
    ids = [
        r["id"]
        for r in fetchall(conn, "SELECT id FROM captions WHERE chunk_id=?", (chunk_id,))
    ]
    conn.execute("DELETE FROM events WHERE chunk_id=?", (chunk_id,))
    conn.execute("DELETE FROM captions WHERE chunk_id=?", (chunk_id,))
    for cid in ids:
        try:
            conn.execute("DELETE FROM captions_fts WHERE id=?", (cid,))
        except sqlite3.OperationalError:
            break
    conn.commit()


def delete_input_side_data(conn: sqlite3.Connection, input_id: str) -> None:
    conn.execute("DELETE FROM events WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM captions WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM loudness_samples WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM analyze_jobs WHERE input_id=?", (input_id,))
    conn.execute("DELETE FROM input_heartbeats WHERE input_id=?", (input_id,))
    try:
        conn.execute("DELETE FROM captions_fts WHERE input_id=?", (input_id,))
    except sqlite3.OperationalError:
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


def overlay_app_settings(conn: sqlite3.Connection, env: dict[str, str]) -> dict[str, str]:
    """Prefer app_settings over the process environment unless break-glass is set."""
    out = dict(env)
    flag = str(env.get("NEXREC_ENV_OVERRIDES") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return out
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except sqlite3.OperationalError:
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
