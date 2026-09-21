# Source: NexClip/api/app/recorders/schemas.py (local tree 2026-09-21).
# GitHub private; committed here as a pointer.

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Node-facing contract (bearer-token auth, mirrors app/workers/schemas.py)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    hostname: str
    recorder_type: Literal["decklink", "srt", "ndi"]
    # 4-8 per the user's spec -- registration creates that many blank
    # recorder_inputs rows (slot 1..num_slots), ready for admin assignment.
    num_slots: int = Field(ge=4, le=8)


class RegisterResponse(BaseModel):
    recorder_id: str
    token: str


class CheckinInputReport(BaseModel):
    slot: int
    reported_label: str | None = None
    is_present: bool = False
    # Mode 2 (continuous_24x7) only -- how far back this slot's local
    # buffer currently reaches. Omitted/None for Mode 1 nodes, which
    # never accumulate a buffer to report.
    buffer_earliest_at: datetime | None = None


class CheckinRequest(BaseModel):
    status: Literal["online", "error"] = "online"
    inputs: list[CheckinInputReport] = Field(default_factory=list)


class ScheduleResponse(BaseModel):
    plan_type: Literal["scheduled", "safety_net"]
    event_id: str | None
    planned_start: datetime
    planned_end: datetime
    library_id: str | None
    relative_dir: str
    filename: str


class StartCaptureRequest(BaseModel):
    # Echoes what the node just fetched from /schedule -- not re-derived
    # server-side, so what actually got recorded is exactly what the node
    # was told, even if the schedule changed a moment later.
    plan_type: Literal["scheduled", "safety_net"]
    event_id: str | None = None
    planned_start: datetime
    planned_end: datetime


class StartCaptureResponse(BaseModel):
    capture_id: str


class CompleteCaptureRequest(BaseModel):
    delivered_path: str


class FailCaptureRequest(BaseModel):
    error_detail: str


# ---------------------------------------------------------------------------
# Admin-facing views (session auth, admin.recorders.edit feature grant)
# ---------------------------------------------------------------------------


class RecorderInputOut(BaseModel):
    id: str
    slot: int
    reported_label: str | None
    is_present: bool
    last_seen_at: datetime | None
    buffer_earliest_at: datetime | None
    expected_source: str | None
    channel_id: str | None


class RecorderNodeOut(BaseModel):
    id: str
    hostname: str
    recorder_type: Literal["decklink", "srt", "ndi"]
    mode: Literal["scheduled_with_safety_net", "continuous_24x7"]
    status: Literal["online", "offline", "error"]
    last_heartbeat_at: datetime | None
    registered_at: datetime
    inputs: list[RecorderInputOut]


class UpdateRecorderInputRequest(BaseModel):
    expected_source: str | None = None
    channel_id: str | None = None
    # None alone is ambiguous between "leave unchanged" and "clear it" --
    # these two flags disambiguate explicitly rather than overloading
    # null, same reasoning as other nullable-field updates in this
    # codebase (e.g. UpdateShareRequest's if_version-gated fields).
    clear_expected_source: bool = False
    clear_channel: bool = False


# ---------------------------------------------------------------------------
# Mode 2: export requests
# ---------------------------------------------------------------------------


class ExportRequestOut(BaseModel):
    id: str
    recorder_input_id: str
    event_id: str | None
    requested_by: str | None
    title: str | None
    range_start: datetime
    range_end: datetime
    library_id: str | None
    status: Literal["pending", "exporting", "delivered", "failed"]
    capture_id: str | None
    created_at: datetime


class CreateManualExportRequestRequest(BaseModel):
    title: str | None = None
    range_start: datetime
    range_end: datetime
    library_id: str | None = None


class NextExportRequestResponse(BaseModel):
    export_request_id: str
    title: str | None
    range_start: datetime
    range_end: datetime
    library_id: str | None
    relative_dir: str
    filename: str


class StartExportResponse(BaseModel):
    capture_id: str
