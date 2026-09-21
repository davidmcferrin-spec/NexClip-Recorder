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
          enabled, live_transcode, copy_native, upconvert_1080i,
          video_bitrate, audio_bitrate, retention_days, preview_path,
          preview_enabled,
          feat_scte, feat_av_anomaly, feat_captions, feat_transcribe,
          feat_nielsen, feat_monitors, thresh_freeze_s, thresh_black_s,
          thresh_bars_s, transcribe_engine,
          created_at, updated_at
        ) VALUES (
          :id, :name, :source_type, :url, :decklink_device, :decklink_format,
          :enabled, :live_transcode, :copy_native, :upconvert_1080i,
          :video_bitrate, :audio_bitrate, :retention_days, :preview_path,
          :preview_enabled,
          :feat_scte, :feat_av_anomaly, :feat_captions, :feat_transcribe,
          :feat_nielsen, :feat_monitors, :thresh_freeze_s, :thresh_black_s,
          :thresh_bars_s, :transcribe_engine,
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
    conn.execute(
        """
        INSERT INTO exports (
          id, status, input_ids, t_in, t_out, quality, scope, path, size_bytes,
          protected, error, created_by, created_at, expires_at, nexclip_schedule_id
        ) VALUES (
          :id, :status, :input_ids, :t_in, :t_out, :quality, :scope, :path, :size_bytes,
          :protected, :error, :created_by, :created_at, :expires_at, :nexclip_schedule_id
        )
        """,
        rec,
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
    try:
        conn.execute("DELETE FROM captions_fts WHERE input_id=?", (input_id,))
    except sqlite3.OperationalError:
        pass
    conn.commit()
