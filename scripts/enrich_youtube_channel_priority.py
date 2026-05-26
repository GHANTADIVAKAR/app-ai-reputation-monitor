#!/usr/bin/env python3
"""
Add channel subscriber counts and audio-priority flags to a YouTube sheet tab.

Priority rule:
  audio_priority = yes when subscriber_count >= threshold

Writes columns:
  AA channel_id
  AB subscriber_count
  AC audio_priority
  AD priority_reason
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PRIORITY_HEADERS = ["channel_id", "subscriber_count", "audio_priority", "priority_reason"]


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", required=True)
    parser.add_argument("--threshold", type=int, default=30000)
    args = parser.parse_args()

    youtube_api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    if not youtube_api_key:
        raise RuntimeError("Missing YOUTUBE_API_KEY in .env")
    if not spreadsheet_id:
        raise RuntimeError("Missing GOOGLE_SPREADSHEET_ID in .env")

    sheets = build_sheets_client()
    youtube = build("youtube", "v3", developerKey=youtube_api_key)

    ensure_priority_headers(sheets, spreadsheet_id, args.worksheet)
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(args.worksheet)}!A2:AD",
    ).execute().get("values", [])

    video_rows = []
    for index, row in enumerate(values, start=2):
        video_id = row[5].strip() if len(row) > 5 else ""
        if video_id:
            video_rows.append((index, video_id))

    video_to_channel = fetch_video_channels(youtube, [video_id for _, video_id in video_rows])
    channel_stats = fetch_channel_stats(youtube, sorted(set(video_to_channel.values())))

    updates = []
    priority_count = 0
    for row_number, video_id in video_rows:
        channel_id = video_to_channel.get(video_id, "")
        stats = channel_stats.get(channel_id, {})
        hidden = stats.get("hiddenSubscriberCount", False)
        subscriber_count = stats.get("subscriberCount")
        if subscriber_count is None:
            audio_priority = "unknown"
            reason = "subscriber count hidden or unavailable"
            sub_value = ""
        else:
            sub_value = str(subscriber_count)
            if subscriber_count >= args.threshold:
                audio_priority = "yes"
                priority_count += 1
                reason = f"channel has {subscriber_count} subscribers >= {args.threshold}"
            else:
                audio_priority = "no"
                reason = f"channel has {subscriber_count} subscribers < {args.threshold}"
        if hidden:
            reason = "subscriber count hidden by channel"
            audio_priority = "unknown"
        updates.append({
            "range": f"{quote_sheet_name(args.worksheet)}!AA{row_number}:AD{row_number}",
            "values": [[channel_id, sub_value, audio_priority, reason]],
        })

    if updates:
        for chunk in chunks(updates, 100):
            sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": chunk},
            ).execute()

    print(json.dumps({
        "worksheet": args.worksheet,
        "rows": len(video_rows),
        "threshold": args.threshold,
        "priority_rows": priority_count,
        "non_priority_or_unknown": len(video_rows) - priority_count,
    }, indent=2))
    return 0


def build_sheets_client() -> Any:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    elif service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=SCOPES)
    else:
        raise RuntimeError("Missing Google service account credentials in .env")
    return build("sheets", "v4", credentials=credentials)


def ensure_priority_headers(sheets: Any, spreadsheet_id: str, worksheet: str) -> None:
    ensure_column_capacity(sheets, spreadsheet_id, worksheet, 30)
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(worksheet)}!AA1:AD1",
        valueInputOption="RAW",
        body={"values": [PRIORITY_HEADERS]},
    ).execute()


def ensure_column_capacity(sheets: Any, spreadsheet_id: str, worksheet: str, min_columns: int) -> None:
    spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in spreadsheet.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") != worksheet:
            continue
        grid = props.get("gridProperties", {})
        current = grid.get("columnCount", 0)
        if current >= min_columns:
            return
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": props["sheetId"],
                        "gridProperties": {"columnCount": min_columns},
                    },
                    "fields": "gridProperties.columnCount",
                }
            }]},
        ).execute()
        return
    raise RuntimeError(f"Worksheet not found: {worksheet}")


def fetch_video_channels(youtube: Any, video_ids: list[str]) -> dict[str, str]:
    result = {}
    for chunk in chunks(video_ids, 50):
        response = youtube.videos().list(part="snippet", id=",".join(chunk)).execute()
        for item in response.get("items", []):
            result[item["id"]] = item.get("snippet", {}).get("channelId", "")
    return result


def fetch_channel_stats(youtube: Any, channel_ids: list[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for chunk in chunks(channel_ids, 50):
        response = youtube.channels().list(part="statistics", id=",".join(chunk)).execute()
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            hidden = stats.get("hiddenSubscriberCount", False)
            subscriber_count = None
            if not hidden and stats.get("subscriberCount") not in (None, ""):
                subscriber_count = int(stats["subscriberCount"])
            result[item["id"]] = {
                "hiddenSubscriberCount": hidden,
                "subscriberCount": subscriber_count,
            }
    return result


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
