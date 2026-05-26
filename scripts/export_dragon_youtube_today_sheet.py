#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "live_app.sqlite3"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
TAB_NAME = "Dragon_YouTube_Today"


def main() -> int:
    load_dotenv(ROOT / ".env")
    rows = load_rows()
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID missing in .env")
    service = build("sheets", "v4", credentials=google_credentials())
    ensure_sheet(service, spreadsheet_id, TAB_NAME)
    headers = [
        "youtube_video_id", "url", "title", "channel_title", "subscriber_count", "audio_priority",
        "youtube_type", "published_at", "views", "likes", "comments", "keyword", "analysis_status",
        "sentiment", "positive_pct", "negative_pct", "neutral_pct", "confidence", "evidence_source",
        "provider", "narrative_label", "narrative_summary", "summary", "reason", "error",
    ]
    values = [
        ["APP.AI Dragon YouTube Today", "Generated", datetime.now(timezone.utc).isoformat(), "Rows", len(rows)],
        [],
        headers,
    ]
    for row in rows:
        values.append([row.get(header, "") for header in headers])
    quoted = f"'{TAB_NAME}'"
    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"{quoted}!A:Z").execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{quoted}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id(service, spreadsheet_id, TAB_NAME)}"
    print(url)
    print(f"rows={len(rows)}")
    return 0


def load_rows() -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute(
        """
        WITH latest_analysis AS (
          SELECT a.*, row_number() over(partition by a.video_id order by a.id desc) rn
          FROM analyses a
        )
        SELECT
          v.video_id AS youtube_video_id,
          v.url,
          v.title,
          v.channel_title,
          v.subscriber_count,
          v.audio_priority,
          v.youtube_type,
          v.published_at,
          v.views,
          v.likes,
          v.comments,
          v.keyword,
          coalesce(a.status, 'pending') AS analysis_status,
          coalesce(a.sentiment, 'pending') AS sentiment,
          coalesce(a.positive_pct, 0) AS positive_pct,
          coalesce(a.negative_pct, 0) AS negative_pct,
          coalesce(a.neutral_pct, 0) AS neutral_pct,
          coalesce(a.confidence, 0) AS confidence,
          coalesce(a.evidence_source, '') AS evidence_source,
          coalesce(a.provider, '') AS provider,
          coalesce(a.narrative_label, '') AS narrative_label,
          coalesce(a.narrative_summary, '') AS narrative_summary,
          coalesce(a.summary, '') AS summary,
          coalesce(a.reason, '') AS reason,
          coalesce(a.error, '') AS error
        FROM videos v
        LEFT JOIN latest_analysis a ON a.video_id = v.id AND a.rn = 1
        WHERE v.project_id = 1 AND v.published_at >= ?
        ORDER BY v.audio_priority = 'yes' DESC, coalesce(v.subscriber_count, 0) DESC, v.views DESC
        """,
        (since,),
    )]
    con.close()
    return rows


def google_credentials():
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_json:
        data = json.loads(service_account_json)
        return service_account.Credentials.from_service_account_info(data, scopes=SCOPES)
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or str(ROOT / "service-account.json")
    return service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)


def ensure_sheet(service, spreadsheet_id: str, title: str) -> None:
    workbook = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    if any(sheet["properties"]["title"] == title for sheet in workbook.get("sheets", [])):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()


def sheet_id(service, spreadsheet_id: str, title: str) -> int:
    workbook = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in workbook.get("sheets", []):
        if sheet["properties"]["title"] == title:
            return int(sheet["properties"]["sheetId"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
