"""
Google Calendar Integration Tool — Prometheus Phase 2.

Capabilities:
- List events (hari ini, minggu ini, range tertentu)
- Create event / reminder dari natural language
- Update event
- Delete event
- Check free/busy slots untuk smart scheduling

Auth: OAuth2 dengan service account atau user credentials
Setup: lihat README section Google Calendar Setup
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.tools.base import (
    BaseTool,
    PermissionLevel,
    ToolParameter,
    ToolResult,
    ToolSchema,
    ToolStatus,
)

logger = logging.getLogger(__name__)


def _get_calendar_service():
    """
    Build Google Calendar API service.
    Pakai OAuth2 credentials dari file yang di-set di GOOGLE_CALENDAR_CREDENTIALS_FILE.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    import json

    creds_file = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "")
    token_file = os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", "calendar_token.json")

    if not creds_file or not os.path.exists(creds_file):
        raise ValueError(
            "GOOGLE_CALENDAR_CREDENTIALS_FILE tidak di-set atau file tidak ada. "
            "Download credentials dari Google Cloud Console."
        )

    creds = None

    # Load existing token kalau ada
    if os.path.exists(token_file):
        with open(token_file) as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data)

    # Refresh token kalau expired
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            import json
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise ValueError(
            "Google Calendar credentials tidak valid. "
            "Jalankan setup OAuth2 dulu: python scripts/setup_google_calendar.py"
        )

    return build("calendar", "v3", credentials=creds)


def _parse_datetime(dt_str: str) -> datetime:
    """Parse berbagai format datetime string."""
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {dt_str}")


def _format_event(event: dict) -> dict:
    """Format Google Calendar event ke format yang lebih clean."""
    start = event.get("start", {})
    end = event.get("end", {})

    return {
        "id": event.get("id"),
        "title": event.get("summary", "(no title)"),
        "description": (event.get("description") or "")[:200],
        "start": start.get("dateTime", start.get("date", "")),
        "end": end.get("dateTime", end.get("date", "")),
        "location": event.get("location", ""),
        "attendees": [
            a.get("email") for a in event.get("attendees", [])
        ],
        "status": event.get("status", "confirmed"),
        "meet_link": event.get("hangoutLink", ""),
        "url": event.get("htmlLink", ""),
    }


class CalendarListEventsTool(BaseTool):
    """List Google Calendar events dalam range waktu tertentu."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calendar_list_events",
            description=(
                "List Google Calendar events. Bisa query untuk hari ini, "
                "minggu ini, atau range tanggal tertentu. "
                "Gunakan ini saat user tanya 'jadwal gua hari ini apa?' "
                "atau 'ada meeting apa minggu ini?'"
            ),
            parameters=[
                ToolParameter(
                    name="time_range",
                    type="string",
                    description=(
                        "Range waktu: 'today', 'tomorrow', 'this_week', 'next_week', "
                        "atau 'custom' (pakai time_start dan time_end)."
                    ),
                    required=False,
                    default="today",
                    enum=["today", "tomorrow", "this_week", "next_week", "custom"],
                ),
                ToolParameter(
                    name="time_start",
                    type="string",
                    description="Waktu mulai untuk custom range. Format: 'YYYY-MM-DD' atau 'YYYY-MM-DDTHH:MM:SS'.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="time_end",
                    type="string",
                    description="Waktu selesai untuk custom range. Format: 'YYYY-MM-DD' atau 'YYYY-MM-DDTHH:MM:SS'.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="calendar_id",
                    type="string",
                    description="ID kalender (default: 'primary' = kalender utama lo).",
                    required=False,
                    default="primary",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah event maksimum (default: 10).",
                    required=False,
                    default=10,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        time_range: str = kwargs.get("time_range", "today")
        time_start: str = kwargs.get("time_start", "")
        time_end: str = kwargs.get("time_end", "")
        calendar_id: str = kwargs.get("calendar_id", "primary")
        limit: int = min(kwargs.get("limit", 10), 50)

        try:
            now = datetime.now(timezone.utc)

            # Resolve time range
            if time_range == "today":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
            elif time_range == "tomorrow":
                start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
            elif time_range == "this_week":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                days_until_sunday = 6 - now.weekday()
                end = start + timedelta(days=days_until_sunday + 1)
            elif time_range == "next_week":
                days_until_monday = 7 - now.weekday()
                start = (now + timedelta(days=days_until_monday)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                end = start + timedelta(days=7)
            else:  # custom
                if not time_start:
                    return ToolResult(
                        status=ToolStatus.ERROR,
                        output=None,
                        error="time_start wajib diisi untuk custom range.",
                    )
                start = _parse_datetime(time_start).replace(tzinfo=timezone.utc)
                end = _parse_datetime(time_end).replace(tzinfo=timezone.utc) if time_end else start + timedelta(days=7)

            service = _get_calendar_service()
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = events_result.get("items", [])

            if not events:
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    output=f"Tidak ada event di {time_range}.",
                    metadata={"count": 0},
                )

            formatted = [_format_event(e) for e in events]
            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=formatted,
                metadata={"count": len(formatted), "range": time_range},
            )

        except ValueError as e:
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("calendar_list_events failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class CalendarCreateEventTool(BaseTool):
    """Buat Google Calendar event baru."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calendar_create_event",
            description=(
                "Buat event baru di Google Calendar. "
                "Gunakan ini saat user minta 'tambahin meeting besok jam 2 siang' "
                "atau 'ingetin gua Jumat jam 3 untuk review PR'."
            ),
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Judul event.",
                ),
                ToolParameter(
                    name="start_datetime",
                    type="string",
                    description="Waktu mulai. Format: 'YYYY-MM-DDTHH:MM:SS' (contoh: '2026-06-25T14:00:00').",
                ),
                ToolParameter(
                    name="end_datetime",
                    type="string",
                    description="Waktu selesai. Format: 'YYYY-MM-DDTHH:MM:SS'. Kalau kosong, default 1 jam setelah start.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Deskripsi atau catatan untuk event.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="location",
                    type="string",
                    description="Lokasi event (bisa alamat atau link meet).",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="attendees",
                    type="array",
                    description="List email attendees (contoh: ['kaff@email.com', 'team@email.com']).",
                    required=False,
                ),
                ToolParameter(
                    name="add_google_meet",
                    type="boolean",
                    description="Tambahkan Google Meet link otomatis (default: false).",
                    required=False,
                    default=False,
                ),
                ToolParameter(
                    name="calendar_id",
                    type="string",
                    description="ID kalender (default: 'primary').",
                    required=False,
                    default="primary",
                ),
            ],
            permission=PermissionLevel.READ_WRITE,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        title: str = kwargs["title"]
        start_datetime: str = kwargs["start_datetime"]
        end_datetime: str = kwargs.get("end_datetime", "")
        description: str = kwargs.get("description", "")
        location: str = kwargs.get("location", "")
        attendees: list = kwargs.get("attendees", [])
        add_google_meet: bool = kwargs.get("add_google_meet", False)
        calendar_id: str = kwargs.get("calendar_id", "primary")

        try:
            start_dt = _parse_datetime(start_datetime)

            if end_datetime:
                end_dt = _parse_datetime(end_datetime)
            else:
                end_dt = start_dt + timedelta(hours=1)

            event_body: dict[str, Any] = {
                "summary": title,
                "description": description,
                "location": location,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "Asia/Jakarta",
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "Asia/Jakarta",
                },
            }

            if attendees:
                event_body["attendees"] = [{"email": e} for e in attendees]

            if add_google_meet:
                event_body["conferenceData"] = {
                    "createRequest": {
                        "requestId": f"prometheus-{datetime.now().timestamp()}",
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }

            service = _get_calendar_service()
            kwargs_api: dict[str, Any] = {
                "calendarId": calendar_id,
                "body": event_body,
            }
            if add_google_meet:
                kwargs_api["conferenceDataVersion"] = 1

            created = service.events().insert(**kwargs_api).execute()

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "id": created.get("id"),
                    "title": created.get("summary"),
                    "start": created.get("start", {}).get("dateTime"),
                    "end": created.get("end", {}).get("dateTime"),
                    "url": created.get("htmlLink"),
                    "meet_link": created.get("hangoutLink", ""),
                    "message": f"Event '{title}' berhasil dibuat.",
                },
                metadata={"event_id": created.get("id")},
            )

        except ValueError as e:
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("calendar_create_event failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class CalendarDeleteEventTool(BaseTool):
    """Hapus Google Calendar event."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calendar_delete_event",
            description=(
                "Hapus event dari Google Calendar. "
                "Gunakan event_id dari hasil calendar_list_events."
            ),
            parameters=[
                ToolParameter(
                    name="event_id",
                    type="string",
                    description="ID event yang akan dihapus (dari calendar_list_events).",
                ),
                ToolParameter(
                    name="calendar_id",
                    type="string",
                    description="ID kalender (default: 'primary').",
                    required=False,
                    default="primary",
                ),
            ],
            permission=PermissionLevel.DESTRUCTIVE,
            requires_confirmation=True,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        event_id: str = kwargs["event_id"]
        calendar_id: str = kwargs.get("calendar_id", "primary")

        try:
            service = _get_calendar_service()
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
            ).execute()

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={"message": f"Event '{event_id}' berhasil dihapus."},
            )

        except Exception as e:
            logger.exception("calendar_delete_event failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class CalendarCheckFreeBusyTool(BaseTool):
    """Check free/busy slots untuk smart scheduling."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="calendar_check_free_busy",
            description=(
                "Check kapan lo available (free) dalam periode tertentu. "
                "Gunakan ini untuk smart scheduling: 'kapan gua ada slot 2 jam minggu ini?'"
            ),
            parameters=[
                ToolParameter(
                    name="time_start",
                    type="string",
                    description="Mulai range check. Format: 'YYYY-MM-DDTHH:MM:SS'.",
                ),
                ToolParameter(
                    name="time_end",
                    type="string",
                    description="Akhir range check. Format: 'YYYY-MM-DDTHH:MM:SS'.",
                ),
                ToolParameter(
                    name="duration_minutes",
                    type="integer",
                    description="Durasi slot yang dicari dalam menit (default: 60).",
                    required=False,
                    default=60,
                ),
                ToolParameter(
                    name="working_hours_start",
                    type="integer",
                    description="Jam mulai kerja (default: 9 = jam 9 pagi).",
                    required=False,
                    default=9,
                ),
                ToolParameter(
                    name="working_hours_end",
                    type="integer",
                    description="Jam selesai kerja (default: 18 = jam 6 sore).",
                    required=False,
                    default=18,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        time_start: str = kwargs["time_start"]
        time_end: str = kwargs["time_end"]
        duration_minutes: int = kwargs.get("duration_minutes", 60)
        work_start: int = kwargs.get("working_hours_start", 9)
        work_end: int = kwargs.get("working_hours_end", 18)

        try:
            start_dt = _parse_datetime(time_start).replace(tzinfo=timezone.utc)
            end_dt = _parse_datetime(time_end).replace(tzinfo=timezone.utc)

            service = _get_calendar_service()

            # Get busy times
            body = {
                "timeMin": start_dt.isoformat(),
                "timeMax": end_dt.isoformat(),
                "items": [{"id": "primary"}],
            }
            result = service.freebusy().query(body=body).execute()
            busy_slots = result.get("calendars", {}).get("primary", {}).get("busy", [])

            # Find free slots
            busy_periods = [
                (
                    _parse_datetime(b["start"]).replace(tzinfo=timezone.utc),
                    _parse_datetime(b["end"]).replace(tzinfo=timezone.utc),
                )
                for b in busy_slots
            ]

            free_slots = []
            current = start_dt
            slot_duration = timedelta(minutes=duration_minutes)

            while current + slot_duration <= end_dt:
                slot_end = current + slot_duration

                # Check working hours
                if current.hour < work_start or current.hour >= work_end:
                    current += timedelta(minutes=30)
                    continue

                # Check if slot conflicts with busy periods
                is_free = all(
                    slot_end <= busy_start or current >= busy_end
                    for busy_start, busy_end in busy_periods
                )

                if is_free:
                    free_slots.append({
                        "start": current.strftime("%Y-%m-%d %H:%M"),
                        "end": slot_end.strftime("%Y-%m-%d %H:%M"),
                        "date": current.strftime("%A, %d %B %Y"),
                    })

                    if len(free_slots) >= 5:
                        break

                current += timedelta(minutes=30)

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "free_slots": free_slots,
                    "duration_minutes": duration_minutes,
                    "message": (
                        f"Ditemukan {len(free_slots)} slot kosong {duration_minutes} menit."
                        if free_slots
                        else "Tidak ada slot kosong dalam periode yang diminta."
                    ),
                },
                metadata={"slot_count": len(free_slots)},
            )

        except ValueError as e:
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("calendar_check_free_busy failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))