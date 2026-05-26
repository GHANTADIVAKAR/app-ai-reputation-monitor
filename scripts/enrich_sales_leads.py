#!/usr/bin/env python3
"""
Public contact enrichment for sales leads.

This script collects only public business contact data:
- website
- public email
- office/public phone
- Instagram page URL
- LinkedIn page/profile URL

It does not bypass logins, scrape private data, or send messages.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = ROOT / "data" / "sales_outreach_leads.csv"
OUTPUT_CSV = ROOT / "data" / "sales_outreach_leads_enriched.csv"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
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
    "enrichment_status",
    "enrichment_source",
]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{4}|(?:0\d{2,4}[\s-]?\d{6,8})")
SOCIAL_RE = re.compile(r"https?://(?:www\.)?(instagram\.com|linkedin\.com)/(?:[^\s\"'<>]+)", re.I)
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


@dataclass
class Enrichment:
    website: str = ""
    public_email: str = ""
    office_phone: str = ""
    instagram: str = ""
    linkedin: str = ""
    status: str = "not_found"
    source: str = ""


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", default=str(INPUT_CSV))
    parser.add_argument("--output-csv", default=str(OUTPUT_CSV))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--write-sheet", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    rows = read_csv(Path(args.input_csv))
    enriched_rows = []
    processed = 0

    for row in rows:
        if args.limit and processed >= args.limit:
            enriched_rows.append(row)
            continue
        enrichment = enrich_row(row)
        enriched_rows.append(apply_enrichment(row, enrichment))
        processed += 1
        time.sleep(args.delay)

    output = Path(args.output_csv)
    write_csv(enriched_rows, output)

    sheet_url = ""
    if args.write_sheet:
        sheet_url = write_sheet(enriched_rows)

    print(json.dumps({
        "input_rows": len(rows),
        "processed_rows": processed,
        "output_csv": str(output),
        "google_sheet": sheet_url,
        "enriched_count": sum(1 for row in enriched_rows if row.get("enrichment_status") == "found"),
    }, indent=2))
    return 0


def enrich_row(row: dict[str, str]) -> Enrichment:
    website = normalize_url(row.get("website", ""))
    source = "website"

    if not website:
        website = discover_website(row)
        source = "search"

    if not website:
        return Enrichment(status="not_found", source="no_website")

    pages = crawl_contact_pages(website)
    emails: list[str] = []
    phones: list[str] = []
    instagram = ""
    linkedin = ""

    for page_url, text in pages:
        emails.extend(extract_emails(text))
        phones.extend(extract_phones(text))
        for social in extract_socials(text):
            if "instagram.com" in social and not instagram:
                instagram = clean_social_url(social)
            if "linkedin.com" in social and not linkedin:
                linkedin = clean_social_url(social)

    return Enrichment(
        website=website,
        public_email=first_clean_email(emails),
        office_phone=first_clean_phone(phones),
        instagram=instagram,
        linkedin=linkedin,
        status="found" if any([website, emails, phones, instagram, linkedin]) else "not_found",
        source=source,
    )


def discover_website(row: dict[str, str]) -> str:
    query = " ".join([
        row.get("company_name", ""),
        row.get("city", ""),
        row.get("category", ""),
        "contact",
    ]).strip()
    if not query:
        return ""

    # DuckDuckGo Lite returns public search result links without needing an API key.
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    html_text = fetch(url)
    if not html_text:
        return ""

    candidates = []
    for href in HREF_RE.findall(html_text):
        decoded = html.unescape(href)
        parsed = urllib.parse.urlparse(decoded)
        if "duckduckgo.com" in parsed.netloc:
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("uddg"):
                decoded = qs["uddg"][0]
        if is_business_site(decoded):
            candidates.append(decoded)

    return normalize_url(candidates[0]) if candidates else ""


def crawl_contact_pages(website: str) -> list[tuple[str, str]]:
    homepage = fetch(website)
    pages = [(website, homepage)] if homepage else []
    if not homepage:
        return pages

    base = urllib.parse.urlparse(website)
    links = []
    for href in HREF_RE.findall(homepage):
        href = html.unescape(href)
        absolute = urllib.parse.urljoin(website, href)
        lower = absolute.lower()
        if base.netloc not in urllib.parse.urlparse(absolute).netloc:
            continue
        if any(term in lower for term in ["contact", "about", "team", "people", "leadership"]):
            links.append(absolute)

    for link in unique(links)[:4]:
        text = fetch(link)
        if text:
            pages.append((link, text))
    return pages


def fetch(url: str, timeout: int = 12) -> str:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 ReputationLeadResearch/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text" not in content_type and "html" not in content_type:
                return ""
            raw = response.read(1_500_000)
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_emails(text: str) -> list[str]:
    clean = html.unescape(text).replace("[at]", "@").replace("(at)", "@").replace(" at ", "@")
    return unique(EMAIL_RE.findall(clean))


def extract_phones(text: str) -> list[str]:
    return unique(PHONE_RE.findall(html.unescape(text)))


def extract_socials(text: str) -> list[str]:
    return unique(match.group(0) for match in SOCIAL_RE.finditer(html.unescape(text)))


def first_clean_email(values: list[str]) -> str:
    blocked = {"example.com", "domain.com", "email.com"}
    for value in values:
        email = value.strip().strip(".;,").lower()
        if any(email.endswith(domain) for domain in blocked):
            continue
        if email.startswith(("info@", "contact@", "hello@", "business@", "pr@", "media@")):
            return email
    return values[0].strip().strip(".;,").lower() if values else ""


def first_clean_phone(values: list[str]) -> str:
    return values[0].strip() if values else ""


def clean_social_url(url: str) -> str:
    return url.split("?")[0].strip().rstrip("/")


def normalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def is_business_site(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    blocked = [
        "google.", "youtube.", "facebook.", "instagram.", "linkedin.", "twitter.", "x.com",
        "wikipedia.", "justdial.", "sulekha.", "indiamart.", "duckduckgo.",
    ]
    return parsed.netloc and not any(domain in parsed.netloc.lower() for domain in blocked)


def apply_enrichment(row: dict[str, str], enrichment: Enrichment) -> dict[str, str]:
    updated = dict(row)
    for key in HEADERS:
        updated.setdefault(key, "")
    if enrichment.website and not updated.get("website"):
        updated["website"] = enrichment.website
    if enrichment.public_email and not updated.get("public_email"):
        updated["public_email"] = enrichment.public_email
    if enrichment.office_phone and not updated.get("office_phone"):
        updated["office_phone"] = enrichment.office_phone
    if enrichment.instagram and not updated.get("instagram"):
        updated["instagram"] = enrichment.instagram
    if enrichment.linkedin and not updated.get("linkedin"):
        updated["linkedin"] = enrichment.linkedin
    updated["enrichment_status"] = enrichment.status
    updated["enrichment_source"] = enrichment.source
    return updated


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows([{key: row.get(key, "") for key in HEADERS} for row in rows])


def write_sheet(rows: list[dict[str, str]]) -> str:
    sheets = build_sheets_client()
    spreadsheet_id = os.getenv("SALES_SPREADSHEET_ID", "").strip() or os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    tab_name = os.getenv("SALES_SHEET_TAB", "Sales Leads").strip() or "Sales Leads"
    ensure_tab(sheets, spreadsheet_id, tab_name)
    quoted = quote_sheet_name(tab_name)
    values = [[row.get(header, "") for header in HEADERS] for row in rows]
    sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"{quoted}!A:AA", body={}).execute()
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
        body={"values": values},
    ).execute()
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


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


def unique(values) -> list[str]:
    seen = set()
    output = []
    for value in values:
        key = str(value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(str(value).strip())
    return output


if __name__ == "__main__":
    raise SystemExit(main())
