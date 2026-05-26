# APP.AI YouTube Live Deployment

This is the Railway-ready YouTube version of APP.AI.

## Services

- `web`: Flask dashboard and login.
- `worker`: automatic YouTube collection, 30K+ priority audio analysis, text fallback analysis, report generation.

## Railway Environment Variables

Use `.env.production.example` as the checklist. Do not commit real API keys.

Required:

- `LIVE_APP_SECRET`
- `LIVE_DATA_DIR=/data`
- `LIVE_ADMIN_EMAIL=admin@vcheck`
- `LIVE_ADMIN_PASSWORD=vcheck123`
- `LIVE_CLIENT_EMAIL=client@vcheck`
- `LIVE_CLIENT_PASSWORD=vcheck123`
- `YOUTUBE_API_KEY`
- `GEMINI_API_KEY`
- `GEMINI_MODEL=gemini-2.5-flash`

Optional Google Sheets export:

- `GOOGLE_SPREADSHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

## Railway Volume

Attach a Railway volume mounted at:

```text
/data
```

This keeps the SQLite database alive across deploys/restarts.

## Commands

Web:

```bash
gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 180 wsgi:application
```

Worker:

```bash
python3 scripts/live_youtube_worker.py
```

## Default Login

Admin:

```text
admin@vcheck
vcheck123
```

Client:

```text
client@vcheck
vcheck123
```

## Automatic Schedule

The worker runs continuously. Defaults:

- YouTube scan every 60 minutes.
- Scan window: last 24 hours.
- Audio analysis: 30K+ subscriber priority videos.
- Text fallback: remaining pending/failed rows.

Adjust with:

- `YOUTUBE_WORKER_INTERVAL_MINUTES`
- `YOUTUBE_SCAN_HOURS`
- `YOUTUBE_AUDIO_ANALYZE_LIMIT`
- `YOUTUBE_TEXT_ANALYZE_LIMIT`
