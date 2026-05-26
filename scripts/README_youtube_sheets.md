# Thalapathy Vijay YouTube Daily Scraper

This project collects YouTube videos and Shorts uploaded in the last 24 hours for Vijay / Thalapathy Vijay related keywords and writes them into Google Sheets.

## What It Creates

One Google Spreadsheet:

```text
Thalapathy Vijay YouTube Daily Monitor
```

Inside that spreadsheet, every day gets a separate worksheet/tab:

```text
2026-05-06
2026-05-07
2026-05-08
```

If you run the script again on the same day, it refreshes that day’s tab.

## Keywords Included

The scraper searches a broad set of likely terms, including:

- Thalapathy Vijay
- Vijay Thalapathy
- Actor Vijay
- Joseph Vijay
- TVK Vijay
- Tamilaga Vettri Kazhagam
- Tamizhaga Vetri Kazhagam
- Vijay politics
- Vijay Tamil Nadu politics
- Vijay CM
- Vijay 2026 election
- Vijay speech
- Vijay rally
- Vijay fans
- #ThalapathyVijay
- #ActorVijay
- #TVK
- #VijayPolitics
- #VijayCM

## Sheet Columns

- scraped time
- matched keyword
- Video or Shorts
- title
- channel
- video id
- YouTube link
- published time
- YouTube duration
- duration in seconds
- views
- likes
- comments
- description

## One-Time Setup

Install Python packages:

```bash
python3 -m pip install -r requirements.txt
```

Create `.env`:

```bash
cp .env.youtube.example .env
```

Fill these values in `.env`:

```env
YOUTUBE_API_KEY=your_youtube_api_key
GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/service-account.json
GOOGLE_SHARE_WITH_EMAIL=your_email@gmail.com
GOOGLE_SPREADSHEET_TITLE=Thalapathy Vijay YouTube Daily Monitor
REPORT_TIMEZONE=Asia/Kolkata
```

If `GOOGLE_SPREADSHEET_ID` is empty, the first run creates a spreadsheet automatically and remembers it in `data/youtube_scrape_state.json`.

## Run Once

```bash
python3 scripts/youtube_to_sheets.py
```

## Install Daily Automation

This installs a macOS LaunchAgent that runs every day at 9:00 AM local time.
It scrapes YouTube first and then analyzes the same rows:

```bash
scripts/install_daily_youtube_automation.sh
```

Logs:

```text
data/youtube_scraper.log
data/youtube_analyzer.log
data/youtube_scraper_error.log
```

## Run Sentiment Analyzer Manually

Analyze the current day tab:

```bash
python3 scripts/analyze_youtube_sheet.py
```

Analyze only 10 rows for testing:

```bash
python3 scripts/analyze_youtube_sheet.py --limit 10
```

Force re-analysis of already analyzed rows:

```bash
python3 scripts/analyze_youtube_sheet.py --reanalyze
```

The analyzer writes these columns back to the same row:

- analysis status
- sentiment
- positive %
- negative %
- neutral %
- reason
- summary
- transcript source
- analyzed time

## Remove Automation

```bash
scripts/uninstall_daily_youtube_automation.sh
```
