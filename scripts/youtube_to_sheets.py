#!/usr/bin/env python3
"""
Collect YouTube videos/Shorts from the last 24 hours for Vijay / Thalapathy Vijay
keywords and write them into a date-wise Google Sheets worksheet.

Setup:
  pip install -r requirements.txt
  cp .env.youtube.example .env
  python scripts/youtube_to_sheets.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "youtube_scrape_state.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


DEFAULT_TARGET_NAME = "Thalapathy Vijay"
DEFAULT_KEYWORDS = [
    "Thalapathy Vijay",
    "Vijay Thalapathy",
    "Actor Vijay",
    "Vijay actor",
    "Joseph Vijay",
    "C. Joseph Vijay",
    "TVK Vijay",
    "Tamilaga Vettri Kazhagam",
    "Tamizhaga Vetri Kazhagam",
    "Tamilaga Vettri Kazhagam Vijay",
    "TVK",
    "Thalapathy",
    "Vijay",
    "Vijay politics",
    "Vijay Tamil Nadu politics",
    "Vijay CM",
    "Vijay Tamil Nadu CM",
    "Vijay 2026 election",
    "Vijay election",
    "Vijay political speech",
    "Vijay speech",
    "Vijay rally",
    "Vijay party",
    "Vijay latest news",
    "Vijay interview",
    "Vijay controversy",
    "Vijay public reaction",
    "Vijay fans",
    "Thalapathy Vijay fans",
    "Thalapathy Vijay shorts",
    "Thalapathy Vijay speech",
    "Thalapathy Vijay politics",
    "Thalapathy Vijay TVK",
    "Thalapathy Vijay CM",
    "#ThalapathyVijay",
    "#Vijay",
    "#ActorVijay",
    "#TVK",
    "#TamilagaVettriKazhagam",
    "#TamizhagaVetriKazhagam",
    "#VijayPolitics",
    "#VijayCM",
    "#Thalapathy",
    "#Thalapathy69",
    "#VijaySpeech",
    "#VijayFans",
]

RELEVANCE_TERMS = [
    "thalapathy",
    "actor vijay",
    "joseph vijay",
    "c. joseph vijay",
    "tvk",
    "tamilaga vettri kazhagam",
    "tamizhaga vetri kazhagam",
    "vijay cm",
    "vijay politics",
    "vijay speech",
    "vijay rally",
    "vijay party",
    "#thalapathyvijay",
    "#actorvijay",
    "#tvk",
]

NOISE_TERMS = [
    "vijay deverakonda",
    "vijay devarakonda",
    "vijay sethupathi",
    "vijay antony",
    "vijay tv serial",
]


HEADERS = [
    "scraped_at",
    "keyword",
    "youtube_type",
    "title",
    "channel_title",
    "video_id",
    "url",
    "published_at",
    "duration",
    "duration_seconds",
    "views",
    "likes",
    "comments",
    "description",
]


@dataclass
class VideoRow:
    scraped_at: str
    keyword: str
    youtube_type: str
    title: str
    channel_title: str
    video_id: str
    url: str
    published_at: str
    duration: str
    duration_seconds: str
    views: str
    likes: str
    comments: str
    description: str

    def as_values(self) -> list[str]:
        return [
            self.scraped_at,
            self.keyword,
            self.youtube_type,
            self.title,
            self.channel_title,
            self.video_id,
            self.url,
            self.published_at,
            self.duration,
            self.duration_seconds,
            self.views,
            self.likes,
            self.comments,
            self.description,
        ]


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--target-name", default=DEFAULT_TARGET_NAME)
    parser.add_argument("--worksheet-name", default="")
    parser.add_argument("--relevance-terms", default="")
    parser.add_argument("--noise-terms", default="")
    parser.add_argument("--disable-relevance-filter", action="store_true")
    parser.add_argument("--max-results-per-keyword", type=int, default=25)
    parser.add_argument("--timezone", default=os.getenv("REPORT_TIMEZONE", "Asia/Kolkata"))
    parser.add_argument("--append", action="store_true", help="Append to today's worksheet instead of refreshing it.")
    args = parser.parse_args()

    youtube_api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not youtube_api_key:
        print("Missing YOUTUBE_API_KEY in .env", file=sys.stderr)
        return 1

    keywords = parse_keywords(args.keywords) or DEFAULT_KEYWORDS
    report_tz = ZoneInfo(args.timezone)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(report_tz)
    published_after = now_utc - timedelta(hours=args.hours)
    scraped_at = now_utc.isoformat()
    worksheet_name = args.worksheet_name.strip() or daily_worksheet_name(now_local)

    youtube = build("youtube", "v3", developerKey=youtube_api_key)
    rows = collect_youtube_rows(
        youtube=youtube,
        keywords=keywords,
        published_after=published_after,
        scraped_at=scraped_at,
        max_results_per_keyword=args.max_results_per_keyword,
        relevance_terms=parse_keywords(args.relevance_terms) or RELEVANCE_TERMS,
        noise_terms=parse_keywords(args.noise_terms) or NOISE_TERMS,
        disable_relevance_filter=args.disable_relevance_filter,
    )

    sheets, drive = build_google_clients()
    previous_state = load_state()
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip() or previous_state.get("spreadsheet_id", "")
    spreadsheet_title = os.getenv("GOOGLE_SPREADSHEET_TITLE", "Thalapathy Vijay YouTube Daily Monitor").strip()

    if not spreadsheet_id:
        try:
            spreadsheet_id = create_spreadsheet(sheets, drive, spreadsheet_title, worksheet_name)
            print(f"Created Google Sheet: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")
        except HttpError as error:
            backup_path = write_csv_backup(worksheet_name, rows)
            print_google_permission_help(error, backup_path)
            return 1

    try:
        ensure_worksheet(sheets, spreadsheet_id, worksheet_name)
        if not args.append:
            clear_worksheet(sheets, spreadsheet_id, worksheet_name)
        ensure_header(sheets, spreadsheet_id, worksheet_name)
    except HttpError as error:
        backup_path = write_csv_backup(worksheet_name, rows)
        print_google_permission_help(error, backup_path)
        return 1

    existing_ids = load_existing_video_ids(sheets, spreadsheet_id, worksheet_name)
    new_rows = [row for row in rows if row.video_id not in existing_ids]

    if new_rows:
        try:
            append_rows(sheets, spreadsheet_id, worksheet_name, [row.as_values() for row in new_rows])
        except HttpError as error:
            backup_path = write_csv_backup(worksheet_name, new_rows)
            print_google_permission_help(error, backup_path)
            return 1

    save_state({
        "last_run_at": scraped_at,
        "spreadsheet_id": spreadsheet_id,
        "worksheet_name": worksheet_name,
        "target_name": args.target_name,
        "keywords": keywords,
        "found_rows": len(rows),
        "appended_rows": len(new_rows),
    })

    print(json.dumps({
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
        "worksheet_name": worksheet_name,
        "target": args.target_name,
        "keywords": len(keywords),
        "found_rows": len(rows),
        "appended_rows": len(new_rows),
        "since": published_after.isoformat(),
    }, indent=2))
    return 0


def collect_youtube_rows(
    youtube: Any,
    keywords: list[str],
    published_after: datetime,
    scraped_at: str,
    max_results_per_keyword: int,
    relevance_terms: list[str],
    noise_terms: list[str],
    disable_relevance_filter: bool = False,
) -> list[VideoRow]:
    by_video_id: dict[str, tuple[str, dict[str, Any]]] = {}

    for keyword in keywords:
        request = youtube.search().list(
            part="snippet",
            q=keyword,
            type="video",
            order="date",
            publishedAfter=published_after.isoformat().replace("+00:00", "Z"),
            maxResults=min(max_results_per_keyword, 50),
            regionCode="IN",
            relevanceLanguage="en",
            safeSearch="none",
        )
        response = request.execute()
        for item in response.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if video_id and video_id not in by_video_id:
                by_video_id[video_id] = (keyword, item)

    rows: list[VideoRow] = []
    video_ids = list(by_video_id.keys())
    for chunk in chunks(video_ids, 50):
        details = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(chunk),
        ).execute()
        for item in details.get("items", []):
            keyword, search_item = by_video_id[item["id"]]
            snippet = item.get("snippet", {})
            if not is_relevant_video(keyword, snippet, relevance_terms, noise_terms, disable_relevance_filter):
                continue
            stats = item.get("statistics", {})
            duration = item.get("contentDetails", {}).get("duration", "")
            seconds = parse_iso8601_duration(duration)
            youtube_type = "Shorts" if seconds is not None and seconds <= 60 else "Video"
            rows.append(VideoRow(
                scraped_at=scraped_at,
                keyword=keyword,
                youtube_type=youtube_type,
                title=snippet.get("title", search_item.get("snippet", {}).get("title", "")),
                channel_title=snippet.get("channelTitle", ""),
                video_id=item["id"],
                url=f"https://www.youtube.com/watch?v={item['id']}",
                published_at=snippet.get("publishedAt", ""),
                duration=duration,
                duration_seconds=str(seconds or ""),
                views=stats.get("viewCount", ""),
                likes=stats.get("likeCount", ""),
                comments=stats.get("commentCount", ""),
                description=snippet.get("description", ""),
            ))

    rows.sort(key=lambda row: row.published_at, reverse=True)
    return rows


def build_google_clients() -> tuple[Any, Any]:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    elif service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=SCOPES)
    else:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON in .env")

    return build("sheets", "v4", credentials=credentials), build("drive", "v3", credentials=credentials)


def create_spreadsheet(sheets: Any, drive: Any, title: str, worksheet_name: str) -> str:
    spreadsheet = sheets.spreadsheets().create(
        body={"properties": {"title": title}, "sheets": [{"properties": {"title": worksheet_name}}]},
        fields="spreadsheetId",
    ).execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]

    share_email = os.getenv("GOOGLE_SHARE_WITH_EMAIL", "").strip()
    if share_email:
        drive.permissions().create(
            fileId=spreadsheet_id,
            body={"type": "user", "role": "writer", "emailAddress": share_email},
            sendNotificationEmail=False,
        ).execute()

    return spreadsheet_id


def ensure_header(sheets: Any, spreadsheet_id: str, sheet_name: str) -> None:
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!A1:N1",
    ).execute().get("values", [])

    if values and values[0] == HEADERS:
        return

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!A1:N1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()


def load_existing_video_ids(sheets: Any, spreadsheet_id: str, sheet_name: str) -> set[str]:
    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!F2:F",
    ).execute()
    return {row[0] for row in result.get("values", []) if row}


def append_rows(sheets: Any, spreadsheet_id: str, sheet_name: str, values: list[list[str]]) -> None:
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!A:N",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()


def ensure_worksheet(sheets: Any, spreadsheet_id: str, sheet_name: str) -> None:
    spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {
        sheet.get("properties", {}).get("title")
        for sheet in spreadsheet.get("sheets", [])
    }
    if sheet_name in existing_titles:
        return

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
    ).execute()


def clear_worksheet(sheets: Any, spreadsheet_id: str, sheet_name: str) -> None:
    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!A:N",
        body={},
    ).execute()


def parse_keywords(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_relevant_video(
    keyword: str,
    snippet: dict[str, Any],
    relevance_terms: list[str],
    noise_terms: list[str],
    disable_relevance_filter: bool = False,
) -> bool:
    if disable_relevance_filter:
        return True
    text = " ".join([
        keyword,
        snippet.get("title", ""),
        snippet.get("description", ""),
        snippet.get("channelTitle", ""),
    ]).lower()

    if any(noise.lower() in text for noise in noise_terms):
        return False

    return any(term.lower() in text for term in relevance_terms)


def parse_iso8601_duration(value: str) -> int | None:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return None
    days, hours, minutes, seconds = [int(part or 0) for part in match.groups()]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def daily_worksheet_name(now_local: datetime) -> str:
    return now_local.strftime("%Y-%m-%d")


def quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_csv_backup(worksheet_name: str, rows: list[VideoRow]) -> Path:
    backup_dir = ROOT / "data" / "youtube_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{worksheet_name}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(HEADERS)
        writer.writerows(row.as_values() for row in rows)
    return path


def print_google_permission_help(error: HttpError, backup_path: Path) -> None:
    print("Google Sheets permission failed.")
    print(f"Error: {error}")
    print(f"CSV backup saved at: {backup_path}")
    print("Fix options:")
    print("1. Enable Google Sheets API and Google Drive API in the same Google Cloud project.")
    print("2. Or create a Google Sheet manually, share it with the service account email,")
    print("   then set GOOGLE_SPREADSHEET_ID in .env.")


if __name__ == "__main__":
    raise SystemExit(main())
