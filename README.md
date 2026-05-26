# Reputation Intelligence Dashboard

An AI-powered MVP for monitoring public sentiment around celebrities, movies, politicians, brands, and organizations.

The app scans public sources, scores positive/negative/neutral sentiment, summarizes important links, highlights risks, and suggests PR actions.

## What Is Included

- Frontend dashboard served from `public/`
- Backend REST API in `server/`
- JSON-file database for easy local development
- Scheduled scan every 6 hours
- Manual scan button
- Webhook alert delivery for medium/high reputation risks
- Search map generation for names, aliases, hashtags, handles, and risk topics
- Demo mode with realistic sample mentions
- Optional integrations:
  - Google News RSS for free live news/web mentions
  - OpenAI for stronger sentiment and recommendation analysis
  - SerpAPI for Google/news search
  - YouTube Data API for videos
  - Reddit public search when enabled

## Run Locally

```bash
cp .env.example .env
npm run seed
npm run dev
```

Open:

```text
http://localhost:3000
```

To restore a clean demo:

```bash
npm run reset-demo
```

## Environment Variables

`OPENAI_API_KEY`

Used for AI classification, summaries, and PR recommendations. Without it, the app uses a local rule-based analyzer.

`LIVE_NEWS_ENABLED`

Defaults to `true`. Uses Google News RSS to collect live public articles without a paid key.

`SERPAPI_API_KEY`

Used to search Google/news links. Without it, demo mentions are generated.

`YOUTUBE_API_KEY`

Used to search YouTube videos. Without it, demo video mentions are generated.

`SCAN_INTERVAL_HOURS`

Defaults to `6`.

`ALERT_WEBHOOK_URL`

Optional endpoint that receives risk alerts as JSON. This can be connected to Make, Zapier, Slack workflows, WhatsApp providers, or your own notification service.

`REDDIT_ENABLED`

Set to `true` to include Reddit public search. It is off by default so the local demo stays predictable.

## Live Data Notes

The app now uses live Google News RSS by default. That is good for public news/articles and fast demos.

For production-grade celebrity, movie, or political monitoring, you should add official or approved data sources:

- `SERPAPI_API_KEY` for wider Google/web discovery.
- `YOUTUBE_API_KEY` for YouTube video discovery.
- `REDDIT_ENABLED=true` for public Reddit search.
- A paid/social listening provider or official APIs for X, Instagram, Facebook, and high-volume comment monitoring.
- `OPENAI_API_KEY` for stronger multilingual sentiment, especially Telugu, Hinglish, sarcasm, and slang.

See [SOCIAL_SOURCES.md](SOCIAL_SOURCES.md) for the source strategy for Instagram, YouTube, X, videos, Shorts, Reels, posts, and stories.

## Thalapathy Vijay YouTube To Google Sheets Scraper

Standalone Python scraper:

```bash
pip install -r requirements.txt
cp .env.youtube.example .env
python3 scripts/youtube_to_sheets.py
```

It collects YouTube videos and Shorts uploaded in the last 24 hours for Thalapathy Vijay related keywords and writes them to Google Sheets.

It creates one worksheet/tab per day, such as `2026-05-06`, `2026-05-07`, and so on.
The analyzer then writes sentiment fields into the same rows: positive %, negative %, neutral %, sentiment label, reason, and summary.

Setup details are in [scripts/README_youtube_sheets.md](scripts/README_youtube_sheets.md).

## Deployment

This app can deploy anywhere that runs Node 18+.

### Render

1. Create a new Web Service.
2. Connect this repository.
3. Set build command to blank or `npm install`.
4. Set start command:

```bash
npm start
```

5. Add environment variables from `.env.example`.

### Railway / Fly.io / VPS

Use:

```bash
npm start
```

Persist the `data/` directory if you want JSON data to survive restarts. For production, replace the JSON store with PostgreSQL.

### Docker

```bash
docker build -t reputation-intel .
docker run -p 3000:3000 --env-file .env reputation-intel
```

## Production Upgrade Path

Recommended next steps:

- Replace JSON storage with PostgreSQL.
- Add user login and organization workspaces.
- Add WhatsApp, email, or Slack alerts.
- Add official X/Instagram APIs or approved social listening providers.
- Add influencer scoring based on engagement and follower count.
- Add Telugu/Hinglish model evaluation examples.
- Add background workers with BullMQ, Temporal, or Cloud Tasks.
