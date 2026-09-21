#!/usr/bin/env python3
"""SQLite helpers for NexCLIP Recorder workers. Stdlib only."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable

SCHEMA_REL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection, schema_path: str | None = None) -> None:
    path = schema_path or SCHEMA_REL
    with open(path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn.executescript(sql)
    row = conn.execute("SELECT id FROM schema_migrations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (1, datetime('now'))"
        )
    conn.commit()


def fetchone(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(args)).fetchone()
    return dict(row) if row is not None else None


def fetchall(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def upsert_input(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO inputs (
          id, name, source_type, url, decklink_device, decklink_format,
          enabled, live_transcode, copy_native, upconvert_1080i,
          video_bitrate, audio_bitrate, retention_days, preview_path,
          preview_enabled, created_at, updated_at
        ) VALUES (
          :id, :name, :source_type, :url, :decklink_device, :decklink_format,
          :enabled, :live_transcode, :copy_native, :upconvert_1080i,
          :video_bitrate, :audio_bitrate, :retention_days, :preview_path,
          :preview_enabled, :created_at, :updated_at
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
