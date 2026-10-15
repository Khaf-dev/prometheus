"""
Script setup OAuth2 untuk Google Calendar.

Jalankan SEKALI untuk generate token:
    python scripts/setup_google_calendar.py

Prerequisites:
1. Buka https://console.cloud.google.com
2. Enable Google Calendar API
3. Buat OAuth2 credentials (Desktop app)
4. Download credentials JSON
5. Set path di .env: GOOGLE_CALENDAR_CREDENTIALS_FILE=path/ke/credentials.json
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds_file = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "")
    token_file = os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", "calendar_token.json")

    if not creds_file or not os.path.exists(creds_file):
        print(f"❌ Credentials file tidak ditemukan: {creds_file}")
        print("   Set GOOGLE_CALENDAR_CREDENTIALS_FILE di .env dulu.")
        return

    creds = None

    if os.path.exists(token_file):
        with open(token_file) as f:
            creds = Credentials.from_authorized_user_info(json.load(f), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        print("🔄 Refreshing token...")
        creds.refresh(Request())
    elif not creds or not creds.valid:
        print("🌐 Membuka browser untuk OAuth2...")
        flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
        creds = flow.run_local_server(port=0)

    with open(token_file, "w") as f:
        f.write(creds.to_json())

    print(f"✅ Token saved: {token_file}")

    # Test
    service = build("calendar", "v3", credentials=creds)
    result = service.calendarList().list().execute()
    print(f"\n📅 Kalender tersedia:")
    for cal in result.get("items", []):
        primary = " ← PRIMARY" if cal.get("primary") else ""
        print(f"   - {cal['summary']}{primary}")


if __name__ == "__main__":
    main()