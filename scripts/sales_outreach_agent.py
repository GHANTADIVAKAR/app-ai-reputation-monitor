#!/usr/bin/env python3
"""
Sales Lead + Outreach Draft Agent.

This does not send DMs or emails. It creates a Google Sheet/CSV with:
- lead research targets
- fit score
- pitch angle
- email / Instagram / WhatsApp / LinkedIn message drafts
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = ROOT / "data" / "sales_outreach_leads.csv"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

REGIONS = {
    "Andhra Pradesh": ["Vijayawada", "Visakhapatnam", "Guntur", "Tirupati"],
    "Telangana": ["Hyderabad", "Secunderabad", "Warangal"],
    "Karnataka": ["Bengaluru", "Mysuru", "Mangaluru"],
    "Mumbai": ["Mumbai", "Andheri", "Bandra", "Juhu"],
}

CATEGORIES = [
    "film PR agency",
    "political campaign consultant",
    "digital marketing agency",
    "celebrity management agency",
    "movie promotion agency",
    "public relations agency",
]

DECISION_ROLES = [
    "Founder",
    "Co-founder",
    "Managing Director",
    "PR Head",
    "Campaign Manager",
    "Digital Marketing Head",
    "Celebrity Manager",
    "Political Consultant",
    "Business Development Head",
]

HEADERS = [
    "state",
    "city",
    "category",
    "company_name",
    "website",
    "instagram",
    "linkedin",
    "office_phone",
    "public_email",
    "decision_maker",
    "decision_maker_role",
    "decision_maker_linkedin",
    "apollo_status",
    "fit_score",
    "pitch_angle",
    "email_subject",
    "email_body",
    "instagram_dm",
    "linkedin_dm",
    "whatsapp_message",
    "status",
    "last_contacted",
    "next_followup",
    "notes",
    "research_query",
    "google_search_url",
    "apollo_search_hint",
]


@dataclass
class Lead:
    state: str
    city: str
    category: str
    company_name: str = ""
    website: str = ""
    instagram: str = ""
    linkedin: str = ""
    office_phone: str = ""
    public_email: str = ""
    decision_maker: str = ""
    decision_maker_role: str = ""
    decision_maker_linkedin: str = ""
    apollo_status: str = "not_enriched"


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", default="")
    parser.add_argument("--write-sheet", action="store_true")
    parser.add_argument("--sheet-name", default="Sales Outreach Leads")
    args = parser.parse_args()

    leads = load_input_leads(Path(args.input_csv)) if args.input_csv else build_research_queue()
    rows = [build_row(lead) for lead in leads]
    write_csv(rows, OUTPUT_CSV)

    spreadsheet_url = ""
    if args.write_sheet:
        spreadsheet_url = write_google_sheet(rows, args.sheet_name)

    print(json.dumps({
        "csv": str(OUTPUT_CSV),
        "rows": len(rows),
        "google_sheet": spreadsheet_url,
        "note": "No messages were sent. These are lead targets and message drafts for human approval.",
    }, indent=2))
    return 0


def build_research_queue() -> list[Lead]:
    leads = []
    for state, cities in REGIONS.items():
        for city in cities:
            for category in CATEGORIES:
                leads.append(Lead(
                    state=state,
                    city=city,
                    category=category,
                    company_name=f"{city} {category.title()} Lead",
                ))
    return leads


def load_input_leads(path: Path) -> list[Lead]:
    leads = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            leads.append(Lead(
                state=row.get("state", ""),
                city=row.get("city", ""),
                category=row.get("category", ""),
                company_name=row.get("company_name", ""),
                website=row.get("website", ""),
                instagram=row.get("instagram", ""),
                linkedin=row.get("linkedin", ""),
                office_phone=row.get("office_phone", ""),
                public_email=row.get("public_email", ""),
                decision_maker=row.get("decision_maker", ""),
                decision_maker_role=row.get("decision_maker_role", ""),
                decision_maker_linkedin=row.get("decision_maker_linkedin", ""),
                apollo_status=row.get("apollo_status", "not_enriched"),
            ))
    return leads


def build_row(lead: Lead) -> list[str]:
    fit_score, pitch_angle = score_lead(lead)
    person = lead.decision_maker or "there"
    company = lead.company_name or f"{lead.city} {lead.category}"
    role = lead.decision_maker_role or "team"
    research_query = f"{lead.city} {lead.category} founder PR head contact"
    google_search_url = "https://www.google.com/search?q=" + research_query.replace(" ", "+")
    apollo_search_hint = f"Search Apollo: location={lead.city}, industry=marketing/pr/media, title=Founder OR PR Head OR Managing Director"

    subject = f"Free YouTube sentiment report for one client/campaign"
    email_body = (
        f"Hi {person},\n\n"
        f"I noticed {company} works in the {lead.category} space around {lead.city}.\n\n"
        "We built an AI-powered YouTube reputation monitor for public figures, campaigns, films, and brands. "
        "In one demo, it collected hundreds of YouTube videos/Shorts in 24 hours, analyzed positive/negative/neutral sentiment, "
        "and highlighted risky videos with direct links for PR or legal review.\n\n"
        "Would you be open to a free 3-day pilot report for one public figure, film, or campaign you care about?\n\n"
        "Regards,\n"
        "Divakar"
    )
    instagram_dm = (
        f"Hi {person}, we built a YouTube sentiment + risk monitor for PR/campaign teams. "
        "It tracks daily videos/Shorts, sentiment %, and risky links. "
        "Can I share a free 3-day demo report for one client/campaign?"
    )
    linkedin_dm = (
        f"Hi {person}, I’m building a YouTube reputation intelligence tool for PR and campaign teams. "
        f"Thought it may be useful for {company}. It gives daily sentiment, risk score, and risky video links. "
        "Open to seeing a quick demo?"
    )
    whatsapp_message = (
        f"Hi {person}, this is Divakar. We built an AI YouTube sentiment monitor for celebrities, politicians, films, and brands. "
        "It gives daily positive/negative/neutral %, risk score, and risky video links. "
        "Can I share a free 3-day pilot report?"
    )

    return [
        lead.state,
        lead.city,
        lead.category,
        company,
        lead.website,
        lead.instagram,
        lead.linkedin,
        lead.office_phone,
        lead.public_email,
        lead.decision_maker,
        role,
        lead.decision_maker_linkedin,
        lead.apollo_status,
        str(fit_score),
        pitch_angle,
        subject,
        email_body,
        instagram_dm,
        linkedin_dm,
        whatsapp_message,
        "not_contacted",
        "",
        (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        "Review contact details before sending. Do not auto-send without approval.",
        research_query,
        google_search_url,
        apollo_search_hint,
    ]


def score_lead(lead: Lead) -> tuple[int, str]:
    category = lead.category.lower()
    city = lead.city.lower()
    score = 50
    if "film" in category or "celebrity" in category or "movie" in category:
        score += 25
    if "political" in category or "campaign" in category:
        score += 30
    if "pr" in category or "public relations" in category:
        score += 20
    if city in {"hyderabad", "mumbai", "bengaluru", "vijayawada", "visakhapatnam"}:
        score += 10
    score = min(score, 100)

    if score >= 85:
        angle = "High-fit: pitch free 3-day monitoring for one public figure/campaign."
    elif score >= 70:
        angle = "Medium-high fit: pitch YouTube monitoring as an add-on service for clients."
    else:
        angle = "Medium fit: pitch as reputation analytics for agency retainers."
    return score, angle


def write_csv(rows: list[list[str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def write_google_sheet(rows: list[list[str]], title: str) -> str:
    sheets, drive = build_google_clients()
    spreadsheet_id = os.getenv("SALES_SPREADSHEET_ID", "").strip() or os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    tab_name = os.getenv("SALES_SHEET_TAB", "Sales Leads").strip() or "Sales Leads"

    if not spreadsheet_id:
        spreadsheet = sheets.spreadsheets().create(
            body={"properties": {"title": title}, "sheets": [{"properties": {"title": tab_name}}]},
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
    else:
        ensure_tab(sheets, spreadsheet_id, tab_name)

    quoted = quote_sheet_name(tab_name)
    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{quoted}!A:Y",
        body={},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{quoted}!A1:AA1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{quoted}!A:AA",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def ensure_tab(sheets: Any, spreadsheet_id: str, tab_name: str) -> None:
    spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = {sheet.get("properties", {}).get("title") for sheet in spreadsheet.get("sheets", [])}
    if tab_name in titles:
        return
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()


def quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def build_google_clients() -> tuple[Any, Any]:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    elif service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=SCOPES)
    else:
        raise RuntimeError("Missing Google service account credentials in .env")
    return build("sheets", "v4", credentials=credentials), build("drive", "v3", credentials=credentials)


if __name__ == "__main__":
    raise SystemExit(main())
